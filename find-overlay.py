"""Scan websites for accessibility overlay products and report findings.

Reads a list of domains from a local CSV/XML file or a remote URL, fetches
each page, and checks the HTML for known overlay signatures.  Results are
printed to stdout and optionally saved to a timestamped CSV file.
"""

import argparse
import csv
import io
import json
import os
import re
import sys
import time
from collections import Counter
from datetime import date, datetime, timezone
from html import unescape
from urllib.parse import urlsplit

from defusedxml import DefusedXmlException, ElementTree as ET

import overlay_safety
from overlay_safety import (
    MAX_SOURCE_BYTES,
    OUTCOME_BLOCKED_REDIRECT,
    OUTCOME_CONNECTION_ERROR,
    OUTCOME_DNS_ERROR,
    OUTCOME_ERROR,
    OUTCOME_HTTP_ERROR,
    OUTCOME_INSPECTED,
    OUTCOME_INVALID,
    OUTCOME_PRIVATE,
    OUTCOME_TIMEOUT,
    OUTCOME_TLS_ERROR,
    OUTCOME_TOO_LARGE,
    OUTCOME_UNSUPPORTED,
    SOURCE_CONTENT_TYPES,
    FetchResult,
    safe_fetch,
)

# --- CONFIGURATION & SIGNATURES ---

OVERLAY_SIGNATURES = {
    "AccessiBe": ["accessibe.com", "acsbapp", "acsb.js"],
    "Accessibility Adapter": ["accessibilityadapter.com", "accessibility-adapter"],
    "Accessiblelink": ["accessiblelink.com"],
    "Accessiplus": ["accessiplus"],
    "Accessiway": ["accessiway"],
    "Adally": ["adally.com", "adally.js"],
    "Adapte Mon Web": ["adaptemonweb"],
    "AdaptifY": ["adaptify"],
    "Allyable": ["allyable.com", "allyable.js"],
    "Alchemy": ["alchemyai", "alchemyaccessibility"],
    "Amaze": ["amazeaccess", "amaze/accessibility"],
    "AudioEye": ["audioeye.com", "audioeye.js"],
    "Bakh Fix": ["bakhfix"],
    "DIGIaccess": ["digiaccess"],
    "Eye-Able": ["eye-able.com", "eye-able-cdn"],
    "Equally.ai": ["equally.ai"],
    "EqualWeb": ["equalweb.com", "nagishli"],
    "FACIL'iti": ["facil-iti", "facil_iti"],
    "MaxAccess": ["maxaccess"],
    "Poloda AI": ["poloda"],
    "Purple Lens (Pluro)": ["purplelens", "pluro"],
    "ReciteME": ["reciteme.com", "recite.js"],
    "RentCafe": ["rentcafe.com/accessibility"],
    "Sentinel": ["sentinel-accessibility"],
    "TruAbilities": ["truabilities"],
    "True Accessibility": ["trueaccessibility"],
    "UsableNet (Assistive)": ["usablenet.com", "usablenet_assistive"],
    "UserWay": ["userway.org", "userway.js"],
    "WebAbility": ["webability"],
}

OTHER_3RD_PARTY = {
    "Intercom": ["intercom.io", "intercom-widget"],
    "Drift": ["drift.com", "driftt"],
    "Zendesk": ["zdassets.com", "zendesk-widget"],
    "HubSpot": ["hs-scripts.com", "hubspot"],
}

VERSION_PATTERNS = [
    re.compile(r"[?&](?:v|ver|version)=([0-9][A-Za-z0-9._-]*)", re.I),
    re.compile(r"@([0-9]+(?:\.[0-9]+)+)"),
    re.compile(r"/v([0-9]+(?:\.[0-9]+)+)(?:/|$)"),
    re.compile(r"-([0-9]+(?:\.[0-9]+)+)(?:\.min)?\.js"),
    re.compile(r"/([0-9]+(?:\.[0-9]+)+)/"),
]

ASSET_PATTERN = re.compile(r"""(?:src|href)\s*=\s*["']([^"']+)["']""", re.I)
URL_PATTERN = re.compile(r"https?://[^\s\"'<>]+", re.I)


class SourceError(Exception):
    """Raised when a scan source cannot be read or parsed."""


def get_full_url(domain_entry: str) -> str | None:
    """Return a fully-qualified URL for *domain_entry*, or None to skip it.

    Bare hostnames receive ``https://``.  Only the scheme and host are
    lower-cased; paths and query strings keep their case.  Email-like entries
    and unsupported schemes are rejected.
    """
    target, _ = overlay_safety.normalize_target(domain_entry)
    return target.url if target else None


def is_public_host(host: str) -> bool:
    """Return True when *host* resolves only to globally routable addresses."""
    return overlay_safety.is_public_host(host)


def _csv_column(content: str) -> str | None:
    """Return the domain/URL column name when *content* has a CSV header."""
    try:
        reader = csv.DictReader(io.StringIO(content))
        fieldnames = reader.fieldnames or []
    except csv.Error:
        return None
    accepted = {
        "url",
        "urls",
        "website",
        "website url",
        "site",
        "site url",
        "domain",
        "domain name",
        "domainname",
    }
    for name in fieldnames:
        normalized = (name or "").strip().lower().lstrip("\ufeff")
        if normalized in accepted:
            return name
    return None


def _read_source(source: str) -> str:
    """Read a local or remote source, raising SourceError on failure."""
    parsed_source = urlsplit(source)
    if parsed_source.scheme.lower() in {"http", "https"}:
        result = safe_fetch(
            source,
            allowed_content_types=SOURCE_CONTENT_TYPES,
            max_bytes=MAX_SOURCE_BYTES,
        )
        if result.outcome != OUTCOME_INSPECTED:
            raise SourceError(f"Could not read source {source}: {result.error}")
        return result.body
    if "://" in source:
        raise SourceError(f"Unsupported source URL scheme: {source}")
    try:
        with open(source, "r", encoding="utf-8", errors="replace") as handle:
            content = handle.read(MAX_SOURCE_BYTES + 1)
    except OSError as exc:
        raise SourceError(f"Could not read source file {source}: {exc}") from exc
    if len(content) > MAX_SOURCE_BYTES:
        raise SourceError(f"Source file is too large: {source}")
    return content


def _xml_urls(content: str) -> list[str]:
    """Parse <loc> values from an XML sitemap."""
    root = ET.fromstring(content)
    namespace = {"ns": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    tags = root.findall(".//ns:loc", namespace)
    if not tags:
        tags = root.findall(".//loc")
    return [
        clean for tag in tags if tag.text and (clean := get_full_url(tag.text))
    ]


def _csv_urls(content: str, domain_col: str) -> list[str]:
    """Parse URL values from a CSV column, falling back to raw text."""
    urls: list[str] = []
    try:
        for row in csv.DictReader(io.StringIO(content)):
            clean_url = get_full_url(row.get(domain_col) or "")
            if clean_url:
                urls.append(clean_url)
    except csv.Error:
        return parse_raw_text(content)
    return urls


def fetch_urls_from_source(source: str, *, strict: bool = False) -> list[str]:
    """Return a list of URLs parsed from *source* (local file or remote URL).

    Supports CSV files with a URL/domain column, XML sitemaps, and plain-text
    lists.  Prints a status message before reading.  When *strict* is true, a
    read or parse failure raises :class:`SourceError` instead of returning an
    empty list.
    """
    print(f"Reading source: {source}...")
    try:
        content = _read_source(source)
    except SourceError as exc:
        if strict:
            raise
        print(str(exc), file=sys.stderr)
        return []

    lowered = content.lstrip().lower()
    is_xml = (
        source.lower().endswith(".xml")
        or lowered.startswith("<?xml")
        or "<urlset" in lowered[:300]
        or "<sitemapindex" in lowered[:300]
    )
    if is_xml:
        try:
            return _xml_urls(content)
        except (ET.ParseError, DefusedXmlException) as exc:
            message = f"Could not parse XML source {source}"
            if strict:
                raise SourceError(message) from exc
            print(message, file=sys.stderr)
            return []

    domain_col = _csv_column(content)
    if domain_col:
        print(f"Detected CSV column: '{domain_col}'")
        return _csv_urls(content, domain_col)
    return parse_raw_text(content)


def parse_raw_text(content: str) -> list[str]:
    """Extract URL-like tokens from an unstructured text blob.

    Splits *content* on whitespace and commas, strips common Markdown and
    punctuation decoration, then keeps tokens that normalise to a safe
    HTTP(S) URL.
    """
    urls: list[str] = []
    for token in re.split(r"[,\s\n]+", content):
        token = token.strip().strip("`").rstrip(".,;:)")
        if len(token) <= 4 or "." not in token:
            continue
        target, _ = overlay_safety.normalize_target(token)
        if target is not None:
            urls.append(target.url)
    return urls


def extract_version(value: str) -> str:
    """Return a version-like string found in *value*, or an empty string."""
    for pattern in VERSION_PATTERNS:
        match = pattern.search(value)
        if match:
            return match.group(1).strip(".-")
    return ""


def extract_asset_url(html: str, signature: str) -> str:
    """Return the URL of the asset that contains *signature*, if available."""
    for match in ASSET_PATTERN.finditer(html):
        url = unescape(match.group(1))
        if signature in url.lower():
            return url[:300]

    index = html.lower().find(signature)
    if index == -1:
        return ""
    window = html[max(0, index - 250):index + 250]
    for match in URL_PATTERN.finditer(window):
        url = unescape(match.group(0)).rstrip("),;")
        if signature in url.lower():
            return url[:300]
    return ""


def overlay_version_label(overlays: list[dict]) -> str:
    """Format overlay names and versions for the CSV version column."""
    return "; ".join(
        f"{item['name']} {item['version'] or 'unknown'}"
        for item in overlays
    )


def detect_overlays(html: str) -> list[dict]:
    """Return detected overlays with best-effort version and evidence URL."""
    html_lower = html.lower()
    found = []
    for name, signatures in OVERLAY_SIGNATURES.items():
        for signature in signatures:
            if signature not in html_lower:
                continue
            evidence = extract_asset_url(html, signature)
            found.append({
                "name": name,
                "version": extract_version(evidence),
                "evidence": evidence,
            })
            break
    return found


def _status_for_fetch(fetched: FetchResult) -> int | str:
    """Return the public status value for a fetch outcome."""
    if fetched.outcome in {OUTCOME_INSPECTED, OUTCOME_HTTP_ERROR}:
        if fetched.status_code is not None:
            return fetched.status_code
    labels = {
        OUTCOME_INVALID: "Invalid URL",
        OUTCOME_PRIVATE: "Blocked",
        OUTCOME_DNS_ERROR: "DNS Error",
        OUTCOME_TIMEOUT: "Timeout",
        OUTCOME_TLS_ERROR: "TLS Error",
        OUTCOME_CONNECTION_ERROR: "Conn Error",
        OUTCOME_BLOCKED_REDIRECT: "Blocked Redirect",
        OUTCOME_UNSUPPORTED: "Unsupported Content",
        OUTCOME_TOO_LARGE: "Response Too Large",
        OUTCOME_ERROR: "Error",
    }
    return labels.get(fetched.outcome, "Error")


def scan_domain(url: str) -> dict:
    """Fetch *url* and check its HTML for known overlay and widget signatures.

    Returns a dict with ``url``, ``final_url``, ``status``, ``http_status``,
    ``outcome``, ``content_type``, ``error``, ``detected_overlay``,
    ``overlays``, ``overlay_versions``, and ``other_widgets``.  ``status`` is
    either the HTTP status code or a short error string such as
    ``"Timeout"``, ``"DNS Error"``, ``"Blocked Redirect"``, or ``"Blocked"``.
    Overlay detection only runs when the response was successfully inspected;
    a failed or non-2xx scan is never reported as a clean result.
    """
    result = {
        "url": url,
        "final_url": url,
        "status": "Error",
        "http_status": None,
        "outcome": OUTCOME_ERROR,
        "content_type": "",
        "error": "",
        "detected_overlay": "Not determined",
        "overlays": [],
        "overlay_versions": "",
        "other_widgets": "",
    }

    try:
        fetched = safe_fetch(url)
    except Exception as exc:  # pragma: no cover - last-resort batch isolation
        result["error"] = f"Unexpected scanner error: {exc}"
        print(f"  Unexpected error scanning {url}: {exc}", file=sys.stderr)
        return result

    result["final_url"] = fetched.final_url
    result["http_status"] = fetched.status_code
    result["outcome"] = fetched.outcome
    result["content_type"] = fetched.content_type
    result["error"] = fetched.error
    result["status"] = _status_for_fetch(fetched)

    if fetched.outcome != OUTCOME_INSPECTED:
        return result

    html_lower = fetched.body.lower()
    overlays = detect_overlays(fetched.body)
    result["overlays"] = overlays
    result["overlay_versions"] = overlay_version_label(overlays)

    if overlays:
        result["detected_overlay"] = ", ".join(
            item["name"] for item in overlays
        )
    else:
        result["detected_overlay"] = "None Found"

    found_others = []
    for name, sigs in OTHER_3RD_PARTY.items():
        for sig in sigs:
            if sig in html_lower:
                found_others.append(name)
                break
    result["other_widgets"] = ", ".join(found_others)
    return result


def generate_filename(source_input: str) -> str:
    """Return a timestamped output filename derived from *source_input*.

    Remote URLs produce ``web-scan-overlays-YYYY-MM-DD.csv``; local files
    use their base name without extension.
    """
    today = date.today().strftime("%Y-%m-%d")

    if urlsplit(source_input).scheme.lower() in {"http", "https"}:
        base_name = "web-scan"
    else:
        base_name = os.path.splitext(os.path.basename(source_input))[0]

    return f"{base_name}-overlays-{today}.csv"


def _atomic_write_text(filename: str, text: str) -> None:
    """Write *text* to *filename* atomically using a sibling temporary file."""
    temporary = f"{filename}.tmp-{os.getpid()}"
    try:
        with open(temporary, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        os.replace(temporary, filename)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def write_json_results(source: str, results: list[dict], filename: str) -> bool:
    """Write full scan results and metadata to *filename* as JSON."""
    payload = {
        "source": source,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "results": results,
    }
    try:
        _atomic_write_text(
            filename,
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        )
    except OSError as exc:
        print(f"Error: Could not write JSON to {filename}: {exc}", file=sys.stderr)
        return False
    print(f"JSON results saved to: {filename}")
    return True


def write_csv_results(results: list[dict], filename: str, simple: bool) -> bool:
    """Write scan *results* to *filename* and return True on success."""
    if simple:
        keys = ["url", "detected_overlay"]
    else:
        keys = [
            "url", "status", "detected_overlay",
            "overlay_versions", "other_widgets",
        ]
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=keys)
    writer.writeheader()
    writer.writerows({key: row.get(key, "") for key in keys} for row in results)
    try:
        _atomic_write_text(filename, buffer.getvalue())
    except OSError as exc:
        print(
            f"Error: Could not write to {filename}. Check permissions: {exc}",
            file=sys.stderr,
        )
        return False
    print(f"\nScan complete. Results saved to: {filename}")
    return True


def _safe_display(value: object) -> str:
    """Return *value* with control characters replaced for terminal output."""
    text = str(value)
    return "".join(
        character
        if character.isprintable() and character not in "\r\n"
        else "?"
        for character in text
    )


def _skipped_result(url: str) -> dict:
    """Return a result for a URL skipped by the total time budget."""
    return {
        "url": url,
        "final_url": url,
        "status": "Skipped (time budget)",
        "http_status": None,
        "outcome": "skipped",
        "content_type": "",
        "error": "Total scan time budget reached before this URL was attempted.",
        "detected_overlay": "Not determined",
        "overlays": [],
        "overlay_versions": "",
        "other_widgets": "",
    }


def _scan_urls(
    urls: list[str],
    *,
    time_budget: int | None = None,
    delay: float = 0.0,
) -> list[dict]:
    """Scan every URL in order, respecting an optional total time budget."""
    results: list[dict] = []
    deadline = time.monotonic() + time_budget if time_budget else None
    print(f"{'URL':<40} | {'Status':<10} | {'Overlay Detected'}")
    print("-" * 80)

    for position, url in enumerate(urls, start=1):
        if deadline is not None and time.monotonic() >= deadline:
            data = _skipped_result(url)
        else:
            data = scan_domain(url)
        results.append(data)

        display_overlay = (
            data["detected_overlay"]
            if data["detected_overlay"] != "None Found"
            else "-"
        )
        print(
            f"[{position}/{len(urls)}] {_safe_display(url):<35} "
            f"| {_safe_display(data['status']):<10} | "
            f"{_safe_display(display_overlay)}"
        )
        if delay and position < len(urls):
            time.sleep(delay)
    return results


def _print_summary(results: list[dict]) -> None:
    """Print the status, overlay, and percentage summary."""
    stats_status: Counter = Counter()
    stats_overlays: Counter = Counter()
    skipped = 0
    for data in results:
        stats_status[str(data["status"])] += 1
        if data.get("outcome") == "skipped":
            skipped += 1
        if data["detected_overlay"] and data["detected_overlay"] != "None Found":
            for overlay in data["detected_overlay"].split(", "):
                stats_overlays[overlay] += 1

    print("\n" + "=" * 40)
    print("FINAL STATISTICS")
    print("=" * 40)
    print(f"Total URLs Scanned: {len(results)}")
    if skipped:
        print(f"Skipped due to time budget: {skipped}")

    print("\n--- Status Codes ---")
    for status, count in stats_status.most_common():
        print(f"{status}: {count}")

    print("\n--- Overlays Detected ---")
    if not stats_overlays:
        print("No overlays detected.")
    else:
        for overlay, count in stats_overlays.most_common():
            print(f"{overlay}: {count}")

    print("\n--- Summary ---")
    domains_with_overlays = sum(
        1 for result in results
        if result["detected_overlay"] and result["detected_overlay"] != "None Found"
    )
    percent = (
        (domains_with_overlays / len(results)) * 100 if results else 0
    )
    print(f"Domains with Overlays: {domains_with_overlays} ({percent:.2f}%)")
    print("=" * 40)


def _build_parser() -> argparse.ArgumentParser:
    """Return the command-line parser for the scanner."""
    parser = argparse.ArgumentParser(
        description="Scan websites for Accessibility Overlays."
    )
    parser.add_argument(
        "source", help="Path to local file (.csv/.xml) or URL to sitemap/csv"
    )
    parser.add_argument("--output", help="Custom output filename (optional)")
    parser.add_argument(
        "--simple", action="store_true", help="Output only URL and Overlay columns"
    )
    parser.add_argument(
        "--no-csv", action="store_true",
        help="Do not generate a CSV file"
    )
    parser.add_argument(
        "--json", dest="json_output", metavar="PATH",
        help="Write full results, including overlay versions, as JSON"
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Limit number of unique URLs to scan"
    )
    parser.add_argument(
        "--time-budget", type=int, default=None, metavar="SECONDS",
        help="Stop starting new scans after this many seconds"
    )
    parser.add_argument(
        "--delay", type=float, default=0.0, metavar="SECONDS",
        help="Pause between requests (default: 0)"
    )
    return parser


def _parse_args() -> argparse.Namespace:
    """Parse arguments, prompting for a source only when none is supplied."""
    parser = _build_parser()
    if len(sys.argv) == 1:
        try:
            source_input = input("Enter path or URL to sitemap/csv: ")
        except (EOFError, KeyboardInterrupt):
            print("\nNo source supplied.", file=sys.stderr)
            raise SystemExit(1)
        args = parser.parse_args([source_input])
    else:
        args = parser.parse_args()

    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be at least 1")
    if args.time_budget is not None and args.time_budget < 0:
        parser.error("--time-budget cannot be negative")
    if args.delay < 0:
        parser.error("--delay cannot be negative")
    return args


def main() -> None:
    """Parse arguments, scan domains, and report results.

    Reads a source of URLs (local file or remote URL), scans each for overlay
    signatures, prints a live progress table, optionally writes CSV and JSON,
    and prints summary statistics.  Source and output failures exit nonzero.
    """
    args = _parse_args()

    try:
        raw_urls = fetch_urls_from_source(args.source, strict=True)
    except SourceError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        print(
            "Check the source path or URL, then try again.",
            file=sys.stderr,
        )
        raise SystemExit(1) from exc

    unique_urls = list(dict.fromkeys(raw_urls))
    if args.limit is not None:
        unique_urls = unique_urls[:args.limit]
    if not unique_urls:
        print(
            f"Error: no URLs were found in {args.source}. "
            "Check the file or sitemap and try again.",
            file=sys.stderr,
        )
        raise SystemExit(1)
    print(f"Found {len(unique_urls)} unique URLs to scan.")

    results = _scan_urls(
        unique_urls,
        time_budget=args.time_budget,
        delay=args.delay,
    )

    write_ok = True
    if not args.no_csv:
        final_filename = args.output if args.output else generate_filename(args.source)
        write_ok = write_csv_results(results, final_filename, args.simple) and write_ok
    else:
        print("\nScan complete. CSV generation skipped (--no-csv).")

    if args.json_output:
        write_ok = (
            write_json_results(args.source, results, args.json_output) and write_ok
        )

    _print_summary(results)
    if not write_ok:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
