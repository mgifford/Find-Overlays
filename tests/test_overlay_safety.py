"""Tests for URL safety and bounded HTTP fetching."""

from __future__ import annotations

import ipaddress
import socket
from unittest.mock import patch

import pytest

import overlay_safety as safety


def _raw(
    status: int, headers: dict[str, str], body: bytes = b""
) -> safety._RawResponse:
    """Build a raw response for fetch tests."""
    return safety._RawResponse(status, headers, body, False)


def _public_resolver(host: str, port: int) -> tuple[tuple[str, ...], str]:
    """Resolve every test host to one public address."""
    if host in {"127.0.0.1", "localhost", "10.0.0.1"}:
        return (), "private destination"
    return ("93.184.216.34",), ""


class TestNormalizeTarget:
    """Tests for the pure URL normaliser."""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("example.gov", "https://example.gov"),
            ("EXAMPLE.GOV", "https://example.gov"),
            ("https://example.gov", "https://example.gov"),
            ("http://example.gov", "http://example.gov"),
            ("example.gov/about", "https://example.gov/about"),
            ("example.gov/about?Q=AbC", "https://example.gov/about?Q=AbC"),
            ("https://Example.GOV/CasePath", "https://example.gov/CasePath"),
            ("https://example.gov/#frag", "https://example.gov/"),
            ("https://example.gov:443", "https://example.gov"),
            ("http://example.gov:80", "http://example.gov"),
            ("example.gov:443", "https://example.gov"),
            ("example.gov:80", "http://example.gov"),
            ("httpbin.org", "https://httpbin.org"),
            ("https://example.gov/@handle", "https://example.gov/@handle"),
            ("https://example.gov/?q=a@b", "https://example.gov/?q=a@b"),
            ("münich.de", "https://xn--mnich-kva.de"),
            ("https://[2001:db8::1]/", "https://[2001:db8::1]/"),
        ],
    )
    def test_normalises(self, raw: str, expected: str) -> None:
        """Accepted entries normalise to the documented form."""
        target, reason = safety.normalize_target(raw)
        assert reason == ""
        assert target is not None
        assert target.url == expected

    @pytest.mark.parametrize(
        ("raw", "reason"),
        [
            ("", "empty entry"),
            ("ftp://example.gov", "unsupported protocol"),
            ("file:///etc/passwd", "unsupported protocol"),
            ("javascript:alert(1)", "unsupported protocol"),
            ("https://user:pass@example.gov", "credentials not allowed"),
            ("https://example.gov:8443", "unsafe port"),
            ("https://example.gov:80", "unsafe port"),
            ("http://example.gov:443", "unsafe port"),
            ("https://example.gov:notaport", "invalid port"),
            ("https://example.gov/a b", "invalid URL"),
            ("https://example.gov/\x1b[2J", "control characters"),
            ("//example.gov", "invalid URL"),
            ("https://" + "a" * 3000, "URL too long"),
        ],
    )
    def test_rejects(self, raw: str, reason: str) -> None:
        """Unsafe or malformed entries are rejected with a reason."""
        target, actual = safety.normalize_target(raw)
        assert target is None
        assert actual == reason


class TestPublicAddresses:
    """Tests for public-address classification and resolution."""

    def test_public_ipv4(self) -> None:
        """A globally routable IPv4 address is public."""
        assert safety.is_public_ip(ipaddress.ip_address("8.8.8.8"))

    def test_private_ipv4(self) -> None:
        """Private and link-local addresses are rejected."""
        assert not safety.is_public_ip(ipaddress.ip_address("10.0.0.1"))
        assert not safety.is_public_ip(ipaddress.ip_address("169.254.169.254"))

    def test_public_ipv6(self) -> None:
        """A globally routable IPv6 address is public."""
        assert safety.is_public_ip(
            ipaddress.ip_address("2001:4860:4860::8888")
        )

    def test_ipv4_mapped_private(self) -> None:
        """IPv4-mapped private addresses are rejected."""
        assert not safety.is_public_ip(
            ipaddress.ip_address("::ffff:127.0.0.1")
        )

    @pytest.mark.parametrize(
        "host",
        ["localhost", "foo.local", "service.internal", "printer", "::1"],
    )
    def test_blocked_hostnames(self, host: str) -> None:
        """Local and single-label names are not public destinations."""
        assert safety.is_public_host(host) is False

    def test_public_ip_literal(self) -> None:
        """A public IP literal is accepted without DNS."""
        assert safety.is_public_host("8.8.8.8") is True

    def test_public_ipv6_literal(self) -> None:
        """A public IPv6 literal is accepted without DNS."""
        assert safety.is_public_host("2001:4860:4860::8888") is True

    def test_resolution_rejects_mixed_private_answers(self) -> None:
        """If any DNS answer is private, the whole target is rejected."""
        answers = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.1", 443)),
        ]
        with patch("socket.getaddrinfo", return_value=answers):
            addresses, reason = safety.resolve_public_addresses(
                "mixed.example", 443
            )
        assert addresses == ()
        assert reason == "private destination"

    def test_resolution_failure(self) -> None:
        """A DNS failure is reported separately from a private destination."""
        with patch("socket.getaddrinfo", side_effect=socket.gaierror):
            addresses, reason = safety.resolve_public_addresses(
                "missing.example", 443
            )
        assert addresses == ()
        assert reason == "DNS resolution failed"


class TestSafeFetch:
    """Tests for redirect handling, bounds, and content policy."""

    def test_inspected_html(self) -> None:
        """A normal HTML response is returned as inspected."""
        with (
            patch.object(
                safety, "resolve_public_addresses", side_effect=_public_resolver
            ),
            patch.object(
                safety,
                "_request_once",
                return_value=_raw(
                    200,
                    {"content-type": "text/html; charset=utf-8"},
                    b"<html>ok</html>",
                ),
            ),
        ):
            result = safety.safe_fetch("https://example.com")
        assert result.outcome == safety.OUTCOME_INSPECTED
        assert result.status_code == 200
        assert result.body == "<html>ok</html>"

    def test_http_error_is_not_inspected(self) -> None:
        """A 404 is an HTTP error, not a clean result."""
        with (
            patch.object(
                safety, "resolve_public_addresses", side_effect=_public_resolver
            ),
            patch.object(
                safety,
                "_request_once",
                return_value=_raw(404, {"content-type": "text/html"}, b"not found"),
            ),
        ):
            result = safety.safe_fetch("https://example.com")
        assert result.outcome == safety.OUTCOME_HTTP_ERROR
        assert result.status_code == 404
        assert result.body == ""

    def test_timeout_is_reported(self) -> None:
        """A transport timeout becomes a timeout outcome."""
        with (
            patch.object(
                safety, "resolve_public_addresses", side_effect=_public_resolver
            ),
            patch.object(
                safety,
                "_request_once",
                side_effect=safety.FetchTransportError(
                    safety.OUTCOME_TIMEOUT, "timed out"
                ),
            ),
        ):
            result = safety.safe_fetch("https://example.com")
        assert result.outcome == safety.OUTCOME_TIMEOUT
        assert result.status_code is None

    def test_private_redirect_is_blocked(self) -> None:
        """A redirect to a private destination is not followed."""
        with (
            patch.object(
                safety, "resolve_public_addresses", side_effect=_public_resolver
            ),
            patch.object(
                safety,
                "_request_once",
                return_value=_raw(
                    302, {"location": "https://127.0.0.1/"}, b""
                ),
            ) as request,
        ):
            result = safety.safe_fetch("https://example.com")
        assert result.outcome == safety.OUTCOME_BLOCKED_REDIRECT
        assert "not public" in result.error
        assert request.call_count == 1

    def test_https_downgrade_is_blocked(self) -> None:
        """An HTTPS to HTTP redirect is rejected."""
        with (
            patch.object(
                safety, "resolve_public_addresses", side_effect=_public_resolver
            ),
            patch.object(
                safety,
                "_request_once",
                return_value=_raw(
                    302, {"location": "http://example.org/"}, b""
                ),
            ),
        ):
            result = safety.safe_fetch("https://example.com")
        assert result.outcome == safety.OUTCOME_BLOCKED_REDIRECT
        assert "downgrade" in result.error

    def test_redirect_chain_is_followed(self) -> None:
        """A public redirect is followed and the final URL is recorded."""
        responses = [
            _raw(302, {"location": "https://example.org/final"}, b""),
            _raw(200, {"content-type": "text/html"}, b"<html>final</html>"),
        ]
        with (
            patch.object(
                safety, "resolve_public_addresses", side_effect=_public_resolver
            ),
            patch.object(safety, "_request_once", side_effect=responses),
        ):
            result = safety.safe_fetch("https://example.com")
        assert result.outcome == safety.OUTCOME_INSPECTED
        assert result.final_url == "https://example.org/final"
        assert result.redirects == ("https://example.org/final",)

    def test_redirect_loop_is_blocked(self) -> None:
        """A redirect back to the current URL is blocked."""
        with (
            patch.object(
                safety, "resolve_public_addresses", side_effect=_public_resolver
            ),
            patch.object(
                safety,
                "_request_once",
                return_value=_raw(
                    302, {"location": "https://example.com"}, b""
                ),
            ),
        ):
            result = safety.safe_fetch("https://example.com")
        assert result.outcome == safety.OUTCOME_BLOCKED_REDIRECT
        assert "loop" in result.error.lower()

    def test_redirect_limit_is_enforced(self) -> None:
        """The redirect limit stops a long chain."""
        with (
            patch.object(
                safety, "resolve_public_addresses", side_effect=_public_resolver
            ),
            patch.object(
                safety,
                "_request_once",
                return_value=_raw(
                    302, {"location": "https://example.org/next"}, b""
                ),
            ),
        ):
            result = safety.safe_fetch(
                "https://example.com", max_redirects=1
            )
        assert result.outcome == safety.OUTCOME_BLOCKED_REDIRECT
        assert "Too many redirects" in result.error

    def test_unsupported_content_type(self) -> None:
        """A JSON response is not scanned as HTML."""
        with (
            patch.object(
                safety, "resolve_public_addresses", side_effect=_public_resolver
            ),
            patch.object(
                safety,
                "_request_once",
                return_value=_raw(
                    200, {"content-type": "application/json"}, b"{}"
                ),
            ),
        ):
            result = safety.safe_fetch("https://example.com")
        assert result.outcome == safety.OUTCOME_UNSUPPORTED
        assert "application/json" in result.error

    def test_oversized_body(self) -> None:
        """A response over the byte cap is rejected."""
        raw = safety._RawResponse(
            200,
            {"content-type": "text/html"},
            b"x" * 100,
            True,
        )
        with (
            patch.object(
                safety, "resolve_public_addresses", side_effect=_public_resolver
            ),
            patch.object(safety, "_request_once", return_value=raw),
        ):
            result = safety.safe_fetch("https://example.com", max_bytes=10)
        assert result.outcome == safety.OUTCOME_TOO_LARGE

    def test_invalid_url_never_requests(self) -> None:
        """An invalid URL is rejected before any network call."""
        with patch.object(safety, "_request_once") as request:
            result = safety.safe_fetch("ftp://example.com")
        request.assert_not_called()
        assert result.outcome == safety.OUTCOME_INVALID

    def test_dns_failure_is_not_a_private_destination(self) -> None:
        """A DNS failure is reported as a scan failure, not a block."""
        with patch.object(
            safety,
            "resolve_public_addresses",
            return_value=((), "DNS resolution failed"),
        ):
            result = safety.safe_fetch("https://missing.example")
        assert result.outcome == safety.OUTCOME_DNS_ERROR
        assert result.status_code is None


class _FakeResponse:
    """Minimal urllib3 response used to inspect pool arguments."""

    def __init__(self) -> None:
        """Create a response with a small HTML body."""
        self.status = 200
        self.headers = {"content-type": "text/html"}

    def read(self, amount: int | None = None, decode_content: bool = False) -> bytes:
        """Return the body."""
        return b"<html>ok</html>"

    def close(self) -> None:
        """Close the fake response."""


class _FakePool:
    """Capture pool constructor arguments for the pinning test."""

    last: "_FakePool | None" = None

    def __init__(self, **kwargs: object) -> None:
        """Capture keyword arguments and record the instance."""
        self.kwargs = kwargs
        self.request_headers: dict[str, str] = {}
        self.request_path = ""
        _FakePool.last = self

    def urlopen(self, method: str, path: str, **kwargs: object) -> _FakeResponse:
        """Capture the request and return a fake response."""
        self.request_path = path
        self.request_headers = dict(kwargs.get("headers") or {})
        return _FakeResponse()

    def close(self) -> None:
        """Close the fake pool."""


class TestPinnedTransport:
    """Tests that the transport pins the validated IP but keeps the hostname."""

    def test_https_uses_pinned_address_and_hostname(self) -> None:
        """HTTPS connects to the IP while verifying and sending the hostname."""
        target, _ = safety.normalize_target("https://example.com/path?q=1")
        assert target is not None
        with patch.object(safety, "HTTPSConnectionPool", _FakePool):
            safety._request_once(target, "93.184.216.34")
        pool = _FakePool.last
        assert pool is not None
        assert pool.kwargs["host"] == "93.184.216.34"
        assert pool.kwargs["assert_hostname"] == "example.com"
        assert pool.kwargs["server_hostname"] == "example.com"
        assert pool.kwargs["cert_reqs"] == "CERT_REQUIRED"
        assert pool.request_headers["Host"] == "example.com"
        assert "Find-Overlays" in pool.request_headers["User-Agent"]
        assert pool.request_path == "/path?q=1"

    def test_http_uses_pinned_address_and_host_header(self) -> None:
        """HTTP connects to the IP and sets the original Host header."""
        target, _ = safety.normalize_target("http://example.com/")
        assert target is not None
        with patch.object(safety, "HTTPConnectionPool", _FakePool):
            safety._request_once(target, "93.184.216.34")
        pool = _FakePool.last
        assert pool is not None
        assert pool.kwargs["host"] == "93.184.216.34"
        assert pool.request_headers["Host"] == "example.com"
