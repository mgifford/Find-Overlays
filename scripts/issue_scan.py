"""Prepare and validate GitHub issue scan requests.

The workflow calls ``prepare`` with the raw GitHub event payload.  This
module extracts addresses from the issue body, normalises and validates each
one, applies the public-address policy, and writes a request record plus a
newline-separated scan list.  It never performs an HTTP request itself.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from overlay_safety import (  # noqa: E402
    MAX_URLS_PER_REQUEST,
    NormalizedTarget,
    TargetValidation,
    normalize_target,
    validate_public_target,
)

SCHEMA_VERSION = "issue-scan/1"
URL_SECTION = re.compile(
    r"^###\s*(?:Site URLs|URLs)\s*\r?\n(.*?)(?=^###\s+|\Z)",
    re.IGNORECASE | re.MULTILINE | re.DOTALL,
)
MARKDOWN_LINK = re.compile(
    r"^\[[^\]]+\]\((https?://[^)\s]+)\)$",
    re.IGNORECASE,
)
BULLET = re.compile(r"^(?:[-*+]|\d+[.)])\s+")


@dataclass(frozen=True)
class IssueMetadata:
    """The parts of a GitHub issue that a scan request needs."""

    number: int
    title: str
    body: str
    url: str
    author: str

    @classmethod
    def from_event(cls, event: dict) -> "IssueMetadata":
        """Build metadata from a GitHub ``issues`` event payload."""
        issue = event.get("issue") or {}
        number = int(issue.get("number") or 0)
        if number <= 0:
            raise ValueError("event payload has no issue number")
        user = issue.get("user") or {}
        return cls(
            number=number,
            title=str(issue.get("title") or ""),
            body=str(issue.get("body") or ""),
            url=str(issue.get("html_url") or ""),
            author=str(user.get("login") or "unknown"),
        )


def extract_requested_urls(issue_body: str) -> list[str]:
    """Extract submitted entries from the Site URLs or URLs section.

    Only the recognised section is read.  Markdown bullets, numbered lists,
    Markdown links, surrounding backticks, and blank lines are accepted.
    """
    match = URL_SECTION.search(issue_body)
    if not match:
        return []

    entries: list[str] = []
    for line in match.group(1).splitlines():
        entry = line.strip()
        if not entry or entry in {"_No response_", "No response"}:
            continue
        if entry.startswith("<!--") or entry.startswith("-->"):
            continue

        bullet = BULLET.match(entry)
        if bullet:
            entry = entry[bullet.end():].strip()

        link = MARKDOWN_LINK.match(entry)
        if link:
            entry = link.group(1).strip()

        if entry.startswith("`") and entry.endswith("`") and len(entry) > 2:
            entry = entry[1:-1].strip()

        if entry:
            entries.append(entry)
    return entries


def request_fingerprint(issue_number: int, accepted_urls: list[str]) -> str:
    """Return a deterministic fingerprint for an issue and its URL set."""
    payload = {
        "issue": issue_number,
        "schema": SCHEMA_VERSION,
        "urls": sorted(set(accepted_urls)),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _collect_entries(
    entries: list[str],
    resolver: Callable[[NormalizedTarget], TargetValidation],
) -> tuple[list[dict], list[dict], int]:
    """Normalise and validate entries, returning accepted, rejected, duplicates."""
    accepted: list[dict] = []
    rejected: list[dict] = []
    seen: set[str] = set()
    duplicates = 0

    for raw_entry in entries:
        target, reason = normalize_target(raw_entry)
        if target is None:
            rejected.append({
                "entry": raw_entry,
                "stage": "validation",
                "reason": reason or "invalid URL",
            })
            continue
        if target.url in seen:
            duplicates += 1
            continue

        try:
            validation = resolver(target)
        except OSError:
            validation = TargetValidation(
                target, "dns_failure", "DNS resolution failed"
            )

        if validation.status == "private":
            rejected.append({
                "entry": raw_entry,
                "normalized": target.url,
                "stage": "validation",
                "reason": validation.reason or "private destination",
            })
            continue
        if validation.status not in {"public", "dns_failure"}:
            rejected.append({
                "entry": raw_entry,
                "normalized": target.url,
                "stage": "validation",
                "reason": validation.reason or "invalid URL",
            })
            continue

        seen.add(target.url)
        accepted.append({
            "entry": raw_entry,
            "url": target.url,
            "validation": validation.status,
        })
    return accepted, rejected, duplicates


def _apply_request_limit(
    accepted: list[dict],
    rejected: list[dict],
) -> list[dict]:
    """Move entries past the per-request limit into the rejection list."""
    if len(accepted) <= MAX_URLS_PER_REQUEST:
        return accepted
    for extra in accepted[MAX_URLS_PER_REQUEST:]:
        rejected.append({
            "entry": extra["entry"],
            "normalized": extra["url"],
            "stage": "limit",
            "reason": f"more than {MAX_URLS_PER_REQUEST} distinct URLs",
        })
    return accepted[:MAX_URLS_PER_REQUEST]


def build_request(
    issue: IssueMetadata,
    entries: list[str],
    *,
    resolver: Callable[[NormalizedTarget], TargetValidation] = validate_public_target,
) -> dict:
    """Validate *entries* and return the request record.

    Syntactically invalid and private destinations are rejected.  A public
    hostname that does not resolve is accepted so the scanner can report a
    real scan failure; it is not treated as a malicious destination.
    Duplicate entries collapse to one scan target.
    """
    accepted, rejected, duplicates = _collect_entries(entries, resolver)
    accepted = _apply_request_limit(accepted, rejected)
    accepted_urls = [item["url"] for item in accepted]
    prepared_at = datetime.now(timezone.utc).isoformat()
    status = "ready" if accepted_urls else "invalid"
    return {
        "schema_version": SCHEMA_VERSION,
        "issue": {
            "number": issue.number,
            "title": issue.title,
            "url": issue.url,
            "author": issue.author,
        },
        "prepared_at": prepared_at,
        "fingerprint": request_fingerprint(issue.number, accepted_urls),
        "status": status,
        "reuse": False,
        "previous_report_url": "",
        "scan": {
            "started_at": prepared_at,
            "completed_at": None,
            "status": "pending",
            "source": "github-issue",
        },
        "summary": {
            "submitted": len(entries),
            "accepted": len(accepted_urls),
            "rejected": len(rejected),
            "duplicates": duplicates,
        },
        "accepted": accepted,
        "accepted_urls": accepted_urls,
        "rejected_urls": rejected,
    }


def _load_json(path: Path) -> dict | None:
    """Return parsed JSON from *path*, or None when it is absent or invalid."""
    try:
        with path.open(encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _write_json(path: Path, payload: dict) -> None:
    """Write *payload* to *path* atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _write_urls(path: Path, urls: list[str]) -> None:
    """Write *urls* one per line, or an empty file when there are none."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(urls)
    if text:
        text += "\n"
    temporary = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    try:
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _set_github_output(name: str, value: object) -> None:
    """Append a single-line output to ``GITHUB_OUTPUT`` when it is set."""
    output_path = os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        return
    text = str(value).replace("\n", " ")
    with open(output_path, "a", encoding="utf-8") as handle:
        handle.write(f"{name}={text}\n")


def _previous_report_url(issue_number: int, previous: dict | None) -> str:
    """Return a stable report URL for an existing issue report."""
    if previous:
        issue = previous.get("issue") or {}
        if issue.get("report_url"):
            return str(issue["report_url"])
    return f"site/reports/issues/issue-{issue_number}/index.html"


def prepare(
    event: dict,
    previous_path: Path | None = None,
    *,
    resolver: Callable[[NormalizedTarget], TargetValidation] = validate_public_target,
) -> dict:
    """Build a request from one GitHub event and apply idempotency rules."""
    issue = IssueMetadata.from_event(event)
    request = build_request(
        issue,
        extract_requested_urls(issue.body),
        resolver=resolver,
    )

    action = str(event.get("action") or "")
    if action == "edited" and previous_path is not None:
        previous = _load_json(previous_path)
        previous_scan = (previous or {}).get("scan") or {}
        if (
            previous
            and previous.get("fingerprint") == request["fingerprint"]
            and previous_scan.get("status") == "completed"
        ):
            request["reuse"] = True
            request["previous_report_url"] = _previous_report_url(
                issue.number, previous
            )
            request["status"] = "reused"

    return request


def _prepare_command(args: argparse.Namespace) -> int:
    """Run the ``prepare`` subcommand."""
    with open(args.event, encoding="utf-8") as handle:
        event = json.load(handle)

    issue = IssueMetadata.from_event(event)
    if not issue.title.lower().startswith("scan:"):
        print("Issue title does not start with SCAN:; no scan requested.")
        _set_github_output("scan_needed", "false")
        _set_github_output("reuse", "false")
        _set_github_output("issue_number", issue.number)
        return 0
    if str((event.get("issue") or {}).get("state") or "open") != "open":
        print("Issue is not open; no scan requested.")
        _set_github_output("scan_needed", "false")
        _set_github_output("reuse", "false")
        _set_github_output("issue_number", issue.number)
        return 0

    previous_path = args.previous
    if previous_path is None:
        previous_path = Path(
            f"site/reports/issues/issue-{issue.number}/latest.json"
        )
    request = prepare(event, previous_path)
    _write_json(Path(args.request), request)
    _write_urls(Path(args.urls), request["accepted_urls"])

    scan_needed = bool(request["accepted_urls"]) and not request["reuse"]
    _set_github_output("scan_needed", str(scan_needed).lower())
    _set_github_output("reuse", str(request["reuse"]).lower())
    _set_github_output("issue_number", issue.number)
    _set_github_output("accepted", request["summary"]["accepted"])
    _set_github_output("submitted", request["summary"]["submitted"])
    _set_github_output("rejected", request["summary"]["rejected"])
    _set_github_output("status", request["status"])
    _set_github_output("fingerprint", request["fingerprint"])

    print(
        f"Issue #{issue.number}: {request['summary']['submitted']} submitted, "
        f"{request['summary']['accepted']} accepted, "
        f"{request['summary']['rejected']} rejected, "
        f"{request['summary']['duplicates']} duplicate(s)."
    )
    return 0


def _should_scan_command(args: argparse.Namespace) -> int:
    """Run the ``should-scan`` subcommand."""
    request = _load_json(Path(args.request))
    if request is None:
        print("Request file is missing or invalid.", file=sys.stderr)
        return 2
    needed = bool(request.get("accepted_urls")) and not request.get("reuse")
    print("true" if needed else "false")
    return 0 if needed else 1


def _empty_command(args: argparse.Namespace) -> int:
    """Run the ``empty`` subcommand."""
    payload = {
        "source": "github-issue",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "results": [],
    }
    _write_json(Path(args.output), payload)
    return 0


def _reuse_command(args: argparse.Namespace) -> int:
    """Run the ``reuse`` subcommand, creating a small reuse artifact."""
    request = _load_json(Path(args.request))
    if request is None:
        print("Request file is missing or invalid.", file=sys.stderr)
        return 2
    artifact = Path(args.artifact)
    artifact.mkdir(parents=True, exist_ok=True)
    _write_json(artifact / "reuse.json", {
        "reuse": True,
        "previous_report_url": request.get("previous_report_url", ""),
    })
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Return the command-line parser for issue request handling."""
    parser = argparse.ArgumentParser(description="Prepare a GitHub issue scan.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare = subparsers.add_parser("prepare", help="Parse and validate an issue")
    prepare.add_argument("--event", required=True, help="GitHub event JSON path")
    prepare.add_argument("--request", required=True, help="Request JSON output path")
    prepare.add_argument("--urls", required=True, help="Scan list output path")
    prepare.add_argument("--previous", type=Path, help="Previous latest.json path")
    prepare.set_defaults(func=_prepare_command)

    should_scan = subparsers.add_parser(
        "should-scan", help="Exit 0 when a network scan is needed"
    )
    should_scan.add_argument("--request", required=True)
    should_scan.set_defaults(func=_should_scan_command)

    empty = subparsers.add_parser("empty", help="Write an empty scanner result")
    empty.add_argument("--output", required=True)
    empty.set_defaults(func=_empty_command)

    reuse = subparsers.add_parser("reuse", help="Create a reuse artifact")
    reuse.add_argument("--request", required=True)
    reuse.add_argument("--artifact", required=True)
    reuse.set_defaults(func=_reuse_command)
    return parser


def main() -> None:
    """Run the requested issue-handling subcommand."""
    args = build_parser().parse_args()
    raise SystemExit(args.func(args))


if __name__ == "__main__":
    main()
