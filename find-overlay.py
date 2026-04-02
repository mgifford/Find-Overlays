"""Scan websites for accessibility overlay products and report findings.

Reads a list of domains from a local CSV/XML file or a remote URL, fetches
each page, and checks the HTML for known overlay signatures.  Results are
printed to stdout and optionally saved to a timestamped CSV file.
"""

import csv
import io
import os
import re
import argparse
import sys
import xml.etree.ElementTree as ET
from datetime import date
from collections import Counter

import requests

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
        except ET.ParseError:
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


def scan_domain(url: str) -> dict:
    """Fetch *url* and check its HTML for known overlay and widget signatures.

    Returns a dict with keys: ``url``, ``status``, ``detected_overlay``, and
    ``other_widgets``.  ``status`` is either the HTTP status code or a short
    error string (``"Timeout"``, ``"Conn Error"``, ``"Error"``).
    """
    result = {
        "url": url,
        "status": "Error",
        "detected_overlay": "None Found",
        "other_widgets": []
    }

    try:
        r = requests.get(url, headers=HEADERS, timeout=10)
        html_lower = r.text.lower()
        result["status"] = r.status_code

        found_overlays = []
        for name, sigs in OVERLAY_SIGNATURES.items():
            for sig in sigs:
                if sig in html_lower:
                    found_overlays.append(name)
                    break

        if found_overlays:
            result["detected_overlay"] = ", ".join(found_overlays)
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


def main() -> None:
    """Parse arguments, scan domains, and report results.

    Reads a source of domain names (local file or remote URL), scans each for
    overlay signatures, prints a live progress table, optionally saves a CSV,
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
        help="Do not generate a CSV file (stats only)"
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Limit number of unique domains to scan"
    )

    if len(sys.argv) == 1:
        source_input = input("Enter path or URL to sitemap/csv: ")
        args = parser.parse_args([source_input])
    else:
        args = parser.parse_args()

    # 1. Get URLs
    raw_urls = fetch_urls_from_source(args.source)
    unique_urls = list(set(raw_urls))
    if args.limit is not None and args.limit > 0:
        unique_urls = unique_urls[:args.limit]
    print(f"Found {len(unique_urls)} unique domains to scan.")

    # 2. Stats Containers
    stats_status: Counter = Counter()
    stats_overlays: Counter = Counter()
    total_scanned = 0

    # 3. Scan
    results = []
    print(f"{'Domain':<40} | {'Status':<10} | {'Overlay Detected'}")
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
            csv_results = [{k: row[k] for k in keys} for row in results]
        else:
            keys = ["url", "status", "detected_overlay", "other_widgets"]
            csv_results = results

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
