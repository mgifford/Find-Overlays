"""Validate an approved scan request and add its URLs to the scan list."""

import argparse
import csv
import ipaddress
import json
import os
import re
import socket
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

MAX_URLS = 10
URL_PATTERN = re.compile(r"https?://[^\s<>()\[\]\"']+", re.I)
SECTION_PATTERN = re.compile(
    r"###\s*Site URLs\s*\n+(.*?)(?=\n###|\Z)", re.I | re.S
)


def gh(*args: str) -> str:
    """Run the GitHub CLI and return stdout."""
    result = subprocess.run(
        ["gh", *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "gh command failed")
    return result.stdout


def repository() -> str:
    """Return the owner/repository slug for the current workflow."""
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if not repo:
        raise RuntimeError("GITHUB_REPOSITORY is not set")
    return repo


def issue_body(issue_number: int) -> str:
    """Return the body of *issue_number*."""
    raw = gh(
        "issue", "view", str(issue_number),
        "--repo", repository(), "--json", "body",
    )
    return json.loads(raw).get("body", "")


def comment(issue_number: int, message: str) -> None:
    """Post *message* on *issue_number*."""
    gh(
        "issue", "comment", str(issue_number),
        "--repo", repository(), "--body", message,
    )


def extract_urls(body: str) -> list[str]:
    """Extract URLs from the Site URLs section of an issue form."""
    match = SECTION_PATTERN.search(body)
    section = match.group(1) if match else ""
    return URL_PATTERN.findall(section)


def is_public_host(host: str) -> bool:
    """Return True when *host* resolves only to public IP addresses."""
    host = host.lower().rstrip(".")
    if host in {"localhost"} or host.endswith((".local", ".internal", ".localhost")):
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
    for address in addresses:
        try:
            if not ipaddress.ip_address(address).is_global:
                return False
        except ValueError:
            return False
    return bool(addresses)


def normalise_url(raw: str) -> str | None:
    """Return a safe public http(s) URL, or None when *raw* is unsuitable."""
    raw = raw.strip().rstrip(".,;:)")
    if not raw or len(raw) > 2048:
        return None
    if not raw.lower().startswith(("http://", "https://")):
        raw = f"https://{raw}"
    try:
        parsed = urlparse(raw)
        port = parsed.port
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"}:
        return None
    if not parsed.hostname or parsed.username or parsed.password:
        return None
    host = parsed.hostname.lower()
    if not is_public_host(host):
        return None
    netloc = f"[{host}]" if ":" in host else host
    if port is not None:
        netloc = f"{netloc}:{port}"
    return parsed._replace(
        scheme=parsed.scheme.lower(), netloc=netloc, fragment=""
    ).geturl()


def read_sites(path: Path) -> list[str]:
    """Return the unique URLs currently in *path*."""
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        return sorted({
            row.get("url", "").strip()
            for row in reader
            if row.get("url", "").strip()
        })


def write_sites(path: Path, urls: list[str]) -> None:
    """Write *urls* to the scan list CSV."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["url"])
        writer.writeheader()
        writer.writerows({"url": url} for url in sorted(set(urls)))


def main() -> None:
    """Validate an approved issue and append its URLs to the scan list."""
    parser = argparse.ArgumentParser(description="Queue an approved scan request.")
    parser.add_argument("--issue", type=int, required=True)
    parser.add_argument("--sites", default="data/sites.csv")
    args = parser.parse_args()

    body = issue_body(args.issue)
    candidates = extract_urls(body)
    if not candidates:
        comment(args.issue, "No URLs were found in the Site URLs section.")
        sys.exit(1)
    if len(candidates) > MAX_URLS:
        comment(
            args.issue,
            f"Please submit at most {MAX_URLS} URLs per request.",
        )
        sys.exit(1)

    valid = [normalise_url(url) for url in candidates]
    invalid = [url for url, clean in zip(candidates, valid) if not clean]
    if invalid:
        listed = "\n".join(f"- `{url}`" for url in invalid[:MAX_URLS])
        comment(
            args.issue,
            "These entries are not public http(s) URLs and were not queued:\n\n"
            f"{listed}",
        )
        sys.exit(1)

    sites_path = Path(args.sites)
    existing = read_sites(sites_path)
    additions = sorted({url for url in valid if url not in existing})
    if not additions:
        print("No new URLs to queue.")
        return

    write_sites(sites_path, existing + additions)
    print("\n".join(additions))


if __name__ == "__main__":
    main()
