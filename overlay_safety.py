"""URL validation and bounded HTTP fetching for Find-Overlays.

This module is the single source of truth for turning user-supplied
addresses into safe scan targets.  It deliberately separates three jobs:

* parsing and normalising an address (no network access),
* resolving and validating a public destination, and
* fetching a validated destination with redirects handled manually.

The HTTP client pins the validated IP address while keeping the original
hostname for the ``Host`` header, TLS SNI, and certificate verification.
That closes the DNS-rebinding gap that a hostname-only check leaves open.
"""

from __future__ import annotations

import ipaddress
import re
import socket
from dataclasses import dataclass
from typing import Iterable
from urllib.parse import SplitResult, quote, urljoin, urlsplit, urlunsplit

import certifi
from urllib3.connectionpool import HTTPConnectionPool, HTTPSConnectionPool
from urllib3.exceptions import (
    HTTPError,
    MaxRetryError,
    SSLError,
    TimeoutError as Urllib3TimeoutError,
)
from urllib3.util import Timeout

MAX_URLS_PER_REQUEST = 100
MAX_REDIRECTS = 5
MAX_URL_LENGTH = 2048

CONNECT_TIMEOUT_SECONDS = 5
READ_TIMEOUT_SECONDS = 10

MAX_RESPONSE_BYTES = 10_000_000
MAX_SOURCE_BYTES = 5_000_000

DEFAULT_PORTS = {"http": 80, "https": 443}
ALLOWED_PORTS = frozenset(DEFAULT_PORTS.values())
ALLOWED_SCHEMES = frozenset(DEFAULT_PORTS)

USER_AGENT = (
    "Find-Overlays/0.2 "
    "(+https://github.com/mgifford/Find-Overlays; accessibility overlay scanner)"
)

HTML_CONTENT_TYPES = (
    "text/html",
    "application/xhtml+xml",
    "text/plain",
)
SOURCE_CONTENT_TYPES = HTML_CONTENT_TYPES + (
    "text/csv",
    "application/csv",
    "text/xml",
    "application/xml",
)

OUTCOME_INSPECTED = "inspected"
OUTCOME_HTTP_ERROR = "http_error"
OUTCOME_TIMEOUT = "timeout"
OUTCOME_TLS_ERROR = "tls_error"
OUTCOME_CONNECTION_ERROR = "connection_error"
OUTCOME_DNS_ERROR = "dns_error"
OUTCOME_PRIVATE = "private_destination"
OUTCOME_INVALID = "invalid_url"
OUTCOME_BLOCKED_REDIRECT = "blocked_redirect"
OUTCOME_UNSUPPORTED = "unsupported_content"
OUTCOME_TOO_LARGE = "response_too_large"
OUTCOME_ERROR = "error"

REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
BLOCKED_HOST_SUFFIXES = (".localhost", ".local", ".internal", ".home.arpa")
BLOCKED_HOSTNAMES = frozenset({"localhost", "metadata.google.internal"})
BLOCKED_IPS = frozenset(
    {
        ipaddress.ip_address("169.254.169.254"),
        ipaddress.ip_address("169.254.170.2"),
        ipaddress.ip_address("100.100.100.200"),
    }
)

_LABEL_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_HOST_PORT_PATTERN = re.compile(r"^[A-Za-z0-9.-]+:\d+$")


@dataclass(frozen=True)
class NormalizedTarget:
    """A syntactically valid HTTP(S) target with a lower-case host."""

    url: str
    scheme: str
    host: str
    port: int

    @property
    def host_header(self) -> str:
        """Return the host in the form required by an HTTP ``Host`` header."""
        if ":" in self.host:
            return f"[{self.host}]"
        return self.host


@dataclass(frozen=True)
class TargetValidation:
    """The result of resolving and checking a normalised target."""

    target: NormalizedTarget | None
    status: str
    reason: str = ""
    addresses: tuple[str, ...] = ()


@dataclass(frozen=True)
class FetchResult:
    """The bounded result of one validated HTTP fetch."""

    requested_url: str
    final_url: str
    status_code: int | None
    outcome: str
    body: str = ""
    content_type: str = ""
    error: str = ""
    redirects: tuple[str, ...] = ()


@dataclass(frozen=True)
class _RawResponse:
    """A minimal response captured before content policy is applied."""

    status_code: int
    headers: dict[str, str]
    body: bytes
    too_large: bool


class FetchTransportError(Exception):
    """A network failure raised while performing one request."""

    def __init__(self, outcome: str, message: str) -> None:
        """Store the outcome label and human-readable message."""
        super().__init__(message)
        self.outcome = outcome
        self.message = message


def _has_control_characters(value: str) -> bool:
    """Return True when *value* contains ASCII or C1 control characters."""
    return any(
        ord(character) < 32 or 127 <= ord(character) <= 159
        for character in value
    )


def _valid_hostname(host: str) -> bool:
    """Return True when *host* is an ASCII DNS name with valid labels."""
    if not host or len(host) > 253:
        return False
    labels = host.rstrip(".").split(".")
    return all(_LABEL_PATTERN.fullmatch(label) for label in labels)


def _parse_entry(raw: str) -> tuple[SplitResult | None, str]:
    """Parse and apply syntax, scheme, credential, and port checks."""
    entry = raw.strip()
    if not entry:
        return None, "empty entry"
    if len(entry) > MAX_URL_LENGTH:
        return None, "URL too long"
    if _has_control_characters(entry):
        return None, "control characters"
    if "\\" in entry or any(character in entry for character in ' \t\r\n"<>'):
        return None, "invalid URL"
    if entry.startswith("//"):
        return None, "invalid URL"

    if _HOST_PORT_PATTERN.fullmatch(entry):
        bare_port = int(entry.rsplit(":", 1)[1])
        bare_scheme = "http" if bare_port == 80 else "https"
        entry = f"{bare_scheme}://{entry}"

    try:
        parsed = urlsplit(entry)
    except ValueError:
        return None, "invalid URL"
    if not parsed.scheme:
        entry = f"https://{entry}"
        try:
            parsed = urlsplit(entry)
        except ValueError:
            return None, "invalid URL"

    scheme = parsed.scheme.lower()
    if scheme not in ALLOWED_SCHEMES:
        return None, "unsupported protocol"
    if parsed.username is not None or parsed.password is not None:
        return None, "credentials not allowed"
    try:
        host = parsed.hostname
    except ValueError:
        return None, "malformed hostname"
    if not host:
        return None, "invalid URL"
    try:
        port = parsed.port
    except ValueError:
        return None, "invalid port"
    if port is not None and port not in ALLOWED_PORTS:
        return None, "unsafe port"
    if port is not None and port != DEFAULT_PORTS[scheme]:
        return None, "unsafe port"
    return parsed, ""


def _normalise_hostname(host: str) -> tuple[str | None, str]:
    """Lower-case, IDNA-encode, and validate a hostname or IP literal."""
    host = host.lower().rstrip(".")
    if not host:
        return None, "malformed hostname"
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        try:
            host = host.encode("idna").decode("ascii").lower()
        except UnicodeError:
            return None, "malformed hostname"
        if not _valid_hostname(host):
            return None, "malformed hostname"
        return host, ""
    return literal.compressed, ""


def normalize_target(raw: str) -> tuple[NormalizedTarget | None, str]:
    """Normalise a submitted address, or return ``(None, reason)``.

    Bare hostnames and bare host/path values receive ``https://``.  Existing
    schemes are never downgraded.  Paths and query strings keep their case;
    only the scheme and host are lower-cased.  Fragments are removed because
    they are not sent in an HTTP request.
    """
    parsed, reason = _parse_entry(raw)
    if parsed is None:
        return None, reason
    scheme = parsed.scheme.lower()
    host, reason = _normalise_hostname(parsed.hostname or "")
    if host is None:
        return None, reason
    port = parsed.port
    netloc = f"[{host}]" if ":" in host else host
    path = quote(parsed.path, safe="/%:@!$&'()*+,;=-._~")
    query = quote(parsed.query, safe="=&%:@!$'()*+,;/?-._~")
    url = urlunsplit((scheme, netloc, path, query, ""))
    return NormalizedTarget(url, scheme, host, port or DEFAULT_PORTS[scheme]), ""


def is_public_ip(candidate: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Return True when *candidate* is a globally routable unicast address."""
    if candidate in BLOCKED_IPS:
        return False
    mapped = getattr(candidate, "ipv4_mapped", None)
    if mapped is not None:
        return is_public_ip(mapped)
    return bool(candidate.is_global and not candidate.is_multicast)


def _blocked_hostname(host: str) -> bool:
    """Return True for names that must never be resolved for a public scan."""
    if host in BLOCKED_HOSTNAMES:
        return True
    if host.endswith(BLOCKED_HOST_SUFFIXES):
        return True
    return "." not in host


def resolve_public_addresses(host: str, port: int) -> tuple[tuple[str, ...], str]:
    """Resolve *host* and return only globally routable addresses.

    Returns ``(addresses, reason)``.  If any resolved address is private or
    otherwise non-global, the whole target is rejected.  This avoids choosing
    a public address while a second address points at an internal service.
    """
    host = host.lower().rstrip(".")
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        if _blocked_hostname(host):
            return (), "private destination"
        try:
            infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except OSError:
            return (), "DNS resolution failed"
        addresses: list[str] = []
        for info in infos:
            try:
                address = ipaddress.ip_address(info[4][0])
            except ValueError:
                return (), "private destination"
            if not is_public_ip(address):
                return (), "private destination"
            if str(address) not in addresses:
                addresses.append(str(address))
        if not addresses:
            return (), "DNS resolution failed"
        return tuple(addresses), ""

    if not is_public_ip(literal):
        return (), "private destination"
    return (str(literal),), ""


def validate_public_target(target: NormalizedTarget) -> TargetValidation:
    """Resolve *target* and report whether it is publicly routable."""
    addresses, reason = resolve_public_addresses(target.host, target.port)
    if reason == "DNS resolution failed":
        return TargetValidation(target, "dns_failure", reason)
    if reason:
        return TargetValidation(target, "private", reason)
    return TargetValidation(target, "public", "", addresses)


def is_public_host(host: str) -> bool:
    """Return True when *host* resolves only to globally routable addresses."""
    host = host.lower().rstrip(".")
    authority = f"[{host}]" if ":" in host and not host.startswith("[") else host
    try:
        normalized, _ = normalize_target(f"https://{authority}")
    except ValueError:
        return False
    if normalized is None:
        return False
    return validate_public_target(normalized).status == "public"


def _content_type_allowed(content_type: str, allowed: Iterable[str]) -> bool:
    """Return True when *content_type* is missing or in *allowed*."""
    value = content_type.split(";", 1)[0].strip().lower()
    if not value:
        return True
    return value in allowed


def _decode_body(body: bytes, content_type: str) -> str:
    """Decode a bounded response body using the declared charset."""
    charset = "utf-8"
    for part in content_type.split(";")[1:]:
        key, _, value = part.strip().partition("=")
        if key.lower() == "charset" and value:
            charset = value.strip("\"'")
            break
    try:
        return body.decode(charset, errors="replace")
    except LookupError:
        return body.decode("utf-8", errors="replace")


def _request_path(target: NormalizedTarget) -> str:
    """Return the origin-form request target for *target*."""
    parsed = urlsplit(target.url)
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    return path


def _build_pool(
    target: NormalizedTarget,
    address: str,
    timeout: Timeout,
) -> HTTPConnectionPool | HTTPSConnectionPool:
    """Build a connection pool pinned to a validated address."""
    if target.scheme == "https":
        return HTTPSConnectionPool(
            host=address,
            port=target.port,
            timeout=timeout,
            maxsize=1,
            block=True,
            cert_reqs="CERT_REQUIRED",
            ca_certs=certifi.where(),
            assert_hostname=target.host,
            server_hostname=target.host,
        )
    return HTTPConnectionPool(
        host=address,
        port=target.port,
        timeout=timeout,
        maxsize=1,
        block=True,
    )


def _transport_failure(exc: BaseException) -> FetchTransportError:
    """Translate a transport exception into a stable fetch failure."""
    if isinstance(exc, FetchTransportError):
        return exc
    reason = getattr(exc, "reason", None)
    if isinstance(exc, (Urllib3TimeoutError, TimeoutError)) or isinstance(
        reason, (Urllib3TimeoutError, TimeoutError)
    ):
        return FetchTransportError(OUTCOME_TIMEOUT, "connection or read timed out")
    if isinstance(exc, SSLError) or isinstance(reason, SSLError):
        return FetchTransportError(OUTCOME_TLS_ERROR, "TLS verification failed")
    if isinstance(exc, (HTTPError, OSError, MaxRetryError)):
        return FetchTransportError(OUTCOME_CONNECTION_ERROR, "connection failed")
    return FetchTransportError(OUTCOME_ERROR, "unexpected transport error")


def _read_raw_response(response: object, max_bytes: int) -> _RawResponse:
    """Read a bounded, decompressed body from an urllib3 response."""
    status_code = int(getattr(response, "status"))
    headers_obj = getattr(response, "headers")
    response_headers = {
        str(key).lower(): str(value) for key, value in headers_obj.items()
    }
    chunks: list[bytes] = []
    remaining = max_bytes + 1
    while remaining > 0:
        chunk = response.read(min(65536, remaining), decode_content=True)
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    body = b"".join(chunks)
    too_large = len(body) > max_bytes
    if too_large:
        body = body[:max_bytes]
    return _RawResponse(status_code, response_headers, body, too_large)


def _request_once(
    target: NormalizedTarget,
    address: str,
    *,
    max_bytes: int = MAX_RESPONSE_BYTES,
) -> _RawResponse:
    """Fetch one URL from a pinned *address* without following redirects."""
    headers = {
        "Host": target.host_header,
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.1",
        "Accept-Encoding": "identity",
        "Connection": "close",
    }
    timeout = Timeout(
        connect=CONNECT_TIMEOUT_SECONDS,
        read=READ_TIMEOUT_SECONDS,
    )
    pool = _build_pool(target, address, timeout)
    response: object | None = None
    try:
        try:
            response = pool.urlopen(
                "GET",
                _request_path(target),
                headers=headers,
                redirect=False,
                retries=False,
                preload_content=False,
                decode_content=True,
            )
        except Exception as exc:
            raise _transport_failure(exc) from exc
        try:
            return _read_raw_response(response, max_bytes)
        except Exception as exc:
            raise _transport_failure(exc) from exc
        finally:
            response.close()
    finally:
        pool.close()


def _failure(
    requested_url: str,
    final_url: str,
    outcome: str,
    error: str,
    *,
    status_code: int | None = None,
    content_type: str = "",
    redirects: tuple[str, ...] = (),
) -> FetchResult:
    """Build a failed fetch result with consistent fields."""
    return FetchResult(
        requested_url=requested_url,
        final_url=final_url,
        status_code=status_code,
        outcome=outcome,
        content_type=content_type,
        error=error,
        redirects=redirects,
    )


def _check_current(
    raw_url: str,
    current: NormalizedTarget,
    redirects: list[str],
) -> tuple[TargetValidation | None, FetchResult | None]:
    """Validate the current hop and return a failure when it is not public."""
    validation = validate_public_target(current)
    if validation.status == "private":
        if redirects:
            return None, _failure(
                raw_url,
                current.url,
                OUTCOME_BLOCKED_REDIRECT,
                f"Redirect target is not public: {validation.reason}",
                redirects=tuple(redirects),
            )
        return None, _failure(
            raw_url,
            current.url,
            OUTCOME_PRIVATE,
            validation.reason,
            redirects=tuple(redirects),
        )
    if validation.status != "public" or not validation.addresses:
        if redirects:
            return None, _failure(
                raw_url,
                current.url,
                OUTCOME_BLOCKED_REDIRECT,
                "Redirect target did not resolve: "
                f"{validation.reason or 'DNS resolution failed'}",
                redirects=tuple(redirects),
            )
        return None, _failure(
            raw_url,
            current.url,
            OUTCOME_DNS_ERROR,
            validation.reason or "DNS resolution failed",
            redirects=tuple(redirects),
        )
    return validation, None


def _next_redirect(
    raw_url: str,
    current: NormalizedTarget,
    raw_response: _RawResponse,
    *,
    hop: int,
    max_redirects: int,
    visited: set[str],
    redirects: list[str],
) -> tuple[NormalizedTarget | None, FetchResult | None]:
    """Validate and return the next redirect target, or a failure result."""
    content_type = raw_response.headers.get("content-type", "")
    location = raw_response.headers.get("location", "").strip()
    if not location:
        return None, _failure(
            raw_url,
            current.url,
            OUTCOME_HTTP_ERROR,
            f"HTTP {raw_response.status_code} without a Location header",
            status_code=raw_response.status_code,
            content_type=content_type,
            redirects=tuple(redirects),
        )
    if hop >= max_redirects:
        return None, _failure(
            raw_url,
            current.url,
            OUTCOME_BLOCKED_REDIRECT,
            f"Too many redirects (maximum {max_redirects})",
            status_code=raw_response.status_code,
            content_type=content_type,
            redirects=tuple(redirects),
        )

    next_url = urljoin(current.url, location)
    next_target, next_reason = normalize_target(next_url)
    if next_target is None:
        return None, _failure(
            raw_url,
            current.url,
            OUTCOME_BLOCKED_REDIRECT,
            f"Redirect rejected: {next_reason}",
            status_code=raw_response.status_code,
            content_type=content_type,
            redirects=tuple(redirects),
        )
    if current.scheme == "https" and next_target.scheme == "http":
        return None, _failure(
            raw_url,
            current.url,
            OUTCOME_BLOCKED_REDIRECT,
            "Refusing to downgrade HTTPS to HTTP",
            status_code=raw_response.status_code,
            content_type=content_type,
            redirects=tuple(redirects),
        )
    if next_target.url in visited:
        return None, _failure(
            raw_url,
            current.url,
            OUTCOME_BLOCKED_REDIRECT,
            "Redirect loop detected",
            status_code=raw_response.status_code,
            content_type=content_type,
            redirects=tuple(redirects),
        )
    return next_target, None


def _inspect_final(
    raw_url: str,
    current: NormalizedTarget,
    raw_response: _RawResponse,
    *,
    allowed_content_types: Iterable[str],
    max_bytes: int,
    redirects: list[str],
) -> FetchResult:
    """Apply status, content type, and size policy to a final response."""
    content_type = raw_response.headers.get("content-type", "")
    if not 200 <= raw_response.status_code < 300:
        return _failure(
            raw_url,
            current.url,
            OUTCOME_HTTP_ERROR,
            f"HTTP {raw_response.status_code}",
            status_code=raw_response.status_code,
            content_type=content_type,
            redirects=tuple(redirects),
        )
    if not _content_type_allowed(content_type, allowed_content_types):
        return _failure(
            raw_url,
            current.url,
            OUTCOME_UNSUPPORTED,
            f"Unsupported content type: {content_type or 'missing'}",
            status_code=raw_response.status_code,
            content_type=content_type,
            redirects=tuple(redirects),
        )
    if raw_response.too_large:
        return _failure(
            raw_url,
            current.url,
            OUTCOME_TOO_LARGE,
            f"Response exceeds {max_bytes} bytes",
            status_code=raw_response.status_code,
            content_type=content_type,
            redirects=tuple(redirects),
        )
    return FetchResult(
        requested_url=raw_url,
        final_url=current.url,
        status_code=raw_response.status_code,
        outcome=OUTCOME_INSPECTED,
        body=_decode_body(raw_response.body, content_type),
        content_type=content_type,
        redirects=tuple(redirects),
    )


def safe_fetch(
    raw_url: str,
    *,
    allowed_content_types: Iterable[str] = HTML_CONTENT_TYPES,
    max_bytes: int = MAX_RESPONSE_BYTES,
    max_redirects: int = MAX_REDIRECTS,
) -> FetchResult:
    """Fetch *raw_url* safely, following only validated redirects.

    Each hop is normalised, resolved, and checked against the public-address
    policy before a connection is made.  The validated address is pinned for
    the connection, so a DNS answer that changes after validation cannot
    redirect the request to a private service.
    """
    normalized, reason = normalize_target(raw_url)
    if normalized is None:
        return _failure(raw_url, raw_url, OUTCOME_INVALID, reason)

    current = normalized
    visited = {current.url}
    redirects: list[str] = []

    for hop in range(max_redirects + 1):
        validation, failure = _check_current(raw_url, current, redirects)
        if failure is not None:
            return failure
        if validation is None:  # pragma: no cover - defensive invariant
            return _failure(
                raw_url,
                current.url,
                OUTCOME_ERROR,
                "validation did not produce an address",
                redirects=tuple(redirects),
            )

        try:
            raw_response = _request_once(
                current,
                validation.addresses[0],
                max_bytes=max_bytes,
            )
        except FetchTransportError as exc:
            return _failure(
                raw_url,
                current.url,
                exc.outcome,
                exc.message,
                redirects=tuple(redirects),
            )

        if raw_response.status_code in REDIRECT_STATUSES:
            next_target, failure = _next_redirect(
                raw_url,
                current,
                raw_response,
                hop=hop,
                max_redirects=max_redirects,
                visited=visited,
                redirects=redirects,
            )
            if failure is not None:
                return failure
            if next_target is None:  # pragma: no cover - defensive invariant
                return _failure(
                    raw_url,
                    current.url,
                    OUTCOME_BLOCKED_REDIRECT,
                    "Redirect target was not usable",
                    redirects=tuple(redirects),
                )
            visited.add(next_target.url)
            redirects.append(next_target.url)
            current = next_target
            continue

        return _inspect_final(
            raw_url,
            current,
            raw_response,
            allowed_content_types=allowed_content_types,
            max_bytes=max_bytes,
            redirects=redirects,
        )

    return _failure(  # pragma: no cover - loop always returns
        raw_url,
        current.url,
        OUTCOME_BLOCKED_REDIRECT,
        "Redirect limit reached",
        redirects=tuple(redirects),
    )
