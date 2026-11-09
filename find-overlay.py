"""Scan websites for accessibility overlay products and report findings.

Reads a list of domains from a local CSV/XML file or a remote URL, fetches
each page, and checks the HTML for known overlay signatures.  Results are
printed to stdout and optionally saved to a timestamped CSV file.
"""

import argparse
import csv
import io
import ipaddress
import json
import os
import re
import socket
import sys
from collections import Counter
from datetime import date, datetime, timezone
from html import unescape
from urllib.parse import urlparse

import requests
from defusedxml import DefusedXmlException, ElementTree as ET

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

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/91.0.4472.124 Safari/537.36"
    )
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


def get_full_url(domain_entry: str) -> str | None:
    """Return a fully-qualified URL for *domain_entry*, or None to skip it.

    Strips whitespace, lowercases the value, and prepends ``https://`` when
    no scheme is present.  Returns ``None`` for email-like strings.
    """
    domain_entry = domain_entry.strip().lower()
    if "@" in domain_entry:
        return None
    if not domain_entry.startswith("http"):
        return f"https://{domain_entry}"
    return domain_entry


def is_public_host(host: str) -> bool:
    """Return True when *host* resolves only to globally routable addresses."""
    host = host.lower().rstrip(".")
    if not host or host == "localhost":
        return False
    if host.endswith((".local", ".internal", ".localhost")):
        return False
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        try:
            infos = socket.getaddrinfo(host, None)
        except OSError:
            return False
        addresses = {info[4][0] for info in infos}
    else:
        addresses = {str(literal)}
    if not addresses:
        return False
    for address in addresses:
        try:
            if not ipaddress.ip_address(address).is_global:
                return False
        except ValueError:
            return False
    return True


def fetch_urls_from_source(source: str) -> list[str]:
    """Return a list of URLs parsed from *source* (local file or remote URL).

    Supports CSV files with a domain column, XML sitemaps, and plain-text
    lists.  Prints a status message before reading.
    """
    urls: list[str] = []
    content = ""
    print(f"Reading source: {source}...")

    if source.startswith("http"):
        try:
            r = requests.get(source, headers=HEADERS, timeout=10)
            r.raise_for_status()
            content = r.text
        except Exception as e:
            print(f"Error fetching source URL: {e}")
            return []
    else:
        try:
            with open(source, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
        except Exception as e:
            print(f"Error reading local file: {e}")
            return []

    if source.endswith(".xml") or "<?xml" in content[:100]:
        try:
            root = ET.fromstring(content)
            namespace = {'ns': 'http://www.sitemaps.org/schemas/sitemap/0.9'}
            tags = root.findall(".//ns:loc", namespace)
            if not tags:
                tags = root.findall(".//loc")
            urls = [t.text.strip() for t in tags if t.text]
        except (ET.ParseError, DefusedXmlException):
            print("XML Parse Error.")

    elif "Domain name" in content[:200] or "domain" in content[:200].lower():
        f = io.StringIO(content)
        try:
            reader = csv.DictReader(f)
            domain_col = None
            if reader.fieldnames:
                for col in reader.fieldnames:
                    if "domain" in col.lower() and "type" not in col.lower():
                        domain_col = col
                        break

            if domain_col:
                print(f"Detected CSV column: '{domain_col}'")
                for row in reader:
                    if row[domain_col]:
                        clean_url = get_full_url(row[domain_col])
                        if clean_url:
                            urls.append(clean_url)
            else:
                urls = parse_raw_text(content)
        except csv.Error:
            urls = parse_raw_text(content)
    else:
        urls = parse_raw_text(content)

    return urls


def parse_raw_text(content: str) -> list[str]:
    """Extract URL-like tokens from an unstructured text blob.

    Splits *content* on whitespace and commas, then keeps only tokens that
    look like hostnames or URLs (contain a dot, no ``@``, length > 4).
    """
    tokens = re.split(r"[,\s\n]+", content)
    return [
        f"https://{t}" if not t.startswith("http") else t
        for t in tokens
        if "." in t and "@" not in t and len(t) > 4
    ]


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


def scan_domain(url: str) -> dict:
    """Fetch *url* and check its HTML for known overlay and widget signatures.

    Returns a dict with ``url``, ``final_url``, ``status``,
    ``detected_overlay``, ``overlays``, ``overlay_versions``, and
    ``other_widgets``.  ``status`` is either the HTTP status code or a short
    error string (``"Timeout"``, ``"Conn Error"``, ``"Blocked"``, ``"Error"``).
    Each overlay entry contains ``name``, ``version``, and ``evidence``.
    """
    result = {
        "url": url,
        "final_url": url,
        "status": "Error",
        "detected_overlay": "None Found",
        "overlays": [],
        "overlay_versions": "",
        "other_widgets": ""
    }

    try:
        host = urlparse(url).hostname or ""
        if not is_public_host(host):
            result["status"] = "Blocked"
            return result
        r = requests.get(url, headers=HEADERS, timeout=10)
        html_lower = r.text.lower()
        result["status"] = r.status_code
        result["final_url"] = r.url

        overlays = detect_overlays(r.text)
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

    except requests.exceptions.Timeout:
        result["status"] = "Timeout"
    except requests.exceptions.RequestException:
        result["status"] = "Conn Error"
    except Exception as e:
        result["status"] = "Error"
        print(f"  Unexpected error scanning {url}: {e}")

    return result


def generate_filename(source_input: str) -> str:
    """Return a timestamped output filename derived from *source_input*.

    Remote URLs produce ``web-scan-overlays-YYYY-MM-DD.csv``; local files
    use their base name without extension.
    """
    today = date.today().strftime("%Y-%m-%d")

    # Handle URL inputs
    if source_input.startswith("http"):
        base_name = "web-scan"
    else:
        # Strip extension from local filename
        base_name = os.path.splitext(os.path.basename(source_input))[0]

    return f"{base_name}-overlays-{today}.csv"


def write_json_results(source: str, results: list[dict], filename: str) -> None:
    """Write full scan results and metadata to *filename* as JSON."""
    payload = {
        "source": source,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "results": results,
    }
    try:
        with open(filename, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
        print(f"JSON results saved to: {filename}")
    except OSError:
        print(f"Error: Could not write JSON to {filename}.")


def main() -> None:
    """Parse arguments, scan domains, and report results.

    Reads a source of URLs (local file or remote URL), scans each for overlay
    signatures, prints a live progress table, optionally writes CSV and JSON,
    and prints summary statistics.
    """
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

    if len(sys.argv) == 1:
        source_input = input("Enter path or URL to sitemap/csv: ")
        args = parser.parse_args([source_input])
    else:
        args = parser.parse_args()

    # 1. Get URLs
    raw_urls = fetch_urls_from_source(args.source)
    unique_urls = sorted(dict.fromkeys(raw_urls))
    if args.limit is not None and args.limit > 0:
        unique_urls = unique_urls[:args.limit]
    print(f"Found {len(unique_urls)} unique URLs to scan.")

    # 2. Stats Containers
    stats_status: Counter = Counter()
    stats_overlays: Counter = Counter()
    total_scanned = 0

    # 3. Scan
    results = []
    print(f"{'URL':<40} | {'Status':<10} | {'Overlay Detected'}")
    print("-" * 80)

    for url in unique_urls:
        total_scanned += 1
        data = scan_domain(url)
        results.append(data)

        # Update Stats
        stats_status[str(data["status"])] += 1

        if data["detected_overlay"] and data["detected_overlay"] != "None Found":
            for ov in data["detected_overlay"].split(", "):
                stats_overlays[ov] += 1

        display_overlay = (
            data["detected_overlay"]
            if data["detected_overlay"] != "None Found"
            else "-"
        )
        print(
            f"[{total_scanned}/{len(unique_urls)}] {url:<35} "
            f"| {str(data['status']):<10} | {display_overlay}"
        )

    # 4. Save to CSV (if not disabled)
    if not args.no_csv:
        final_filename = args.output if args.output else generate_filename(args.source)

        if args.simple:
            keys = ["url", "detected_overlay"]
        else:
            keys = [
                "url", "status", "detected_overlay",
                "overlay_versions", "other_widgets",
            ]
        csv_results = [
            {key: row.get(key, "") for key in keys}
            for row in results
        ]

        try:
            with open(final_filename, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=keys)
                writer.writeheader()
                writer.writerows(csv_results)
            print(f"\nScan complete. Results saved to: {final_filename}")
        except IOError:
            print(f"\nError: Could not write to {final_filename}. Check permissions.")
    else:
        print("\nScan complete. CSV generation skipped (--no-csv).")

    if args.json_output:
        write_json_results(args.source, results, args.json_output)

    # 5. Print Final Stats
    print("\n" + "=" * 40)
    print("FINAL STATISTICS")
    print("=" * 40)
    print(f"Total URLs Scanned: {total_scanned}")

    print("\n--- Status Codes ---")
    for status, count in stats_status.most_common():
        print(f"{status}: {count}")

    print("\n--- Overlays Detected ---")
    total_overlays_found = sum(stats_overlays.values())
    if total_overlays_found == 0:
        print("No overlays detected.")
    else:
        for overlay, count in stats_overlays.most_common():
            print(f"{overlay}: {count}")

    print("\n--- Summary ---")
    domains_with_overlays = sum(
        1 for r in results
        if r["detected_overlay"] and r["detected_overlay"] != "None Found"
    )
    percent = (domains_with_overlays / total_scanned) * 100 if total_scanned > 0 else 0
    print(f"Domains with Overlays: {domains_with_overlays} ({percent:.2f}%)")
    print("=" * 40)


if __name__ == "__main__":
    main()
