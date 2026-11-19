"""Render accessible HTML, JSON, and CSV reports for GitHub issue scans.

The scanner's JSON output is the only source of detection data.  This module
turns that output plus the validated request record into a stable report for
one issue, timestamped run history, and the public report index.
"""

from __future__ import annotations

import argparse
import csv
import html
import io
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from overlay_safety import (  # noqa: E402
    OUTCOME_BLOCKED_REDIRECT,
    OUTCOME_CONNECTION_ERROR,
    OUTCOME_DNS_ERROR,
    OUTCOME_HTTP_ERROR,
    OUTCOME_INSPECTED,
    OUTCOME_INVALID,
    OUTCOME_PRIVATE,
    OUTCOME_TIMEOUT,
    OUTCOME_TLS_ERROR,
    OUTCOME_TOO_LARGE,
    OUTCOME_UNSUPPORTED,
    normalize_target,
)

SCHEMA_VERSION = "issue-scan/1"
MAX_INDEX_REPORTS = 100
DEFAULT_REPOSITORY = "https://github.com/mgifford/Find-Overlays"


def safe_text(value: object) -> str:
    """Escape *value* for HTML text and quoted attributes."""
    text = "".join(character for character in str(value) if character.isprintable())
    return html.escape(text, quote=True)


def safe_href(value: object) -> str | None:
    """Return a normalised HTTP(S) URL, or None when it is unsafe."""
    target, _ = normalize_target(str(value))
    return target.url if target else None


def display_url(value: object) -> str:
    """Return a compact host-and-path label for an untrusted URL."""
    raw = str(value)
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return raw[:120]
    if not parsed.netloc:
        return raw[:120]
    host = parsed.hostname or parsed.netloc
    path = parsed.path.rstrip("/")
    return f"{host}{path}" if path else host


def _load_json(path: Path) -> dict:
    """Return parsed JSON from *path*, raising a clear error when invalid."""
    with path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"{path} does not contain a JSON object")
    return payload


def _write_json(path: Path, payload: object) -> None:
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


def _write_text(path: Path, text: str) -> None:
    """Write *text* to *path* atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    try:
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _iso_now() -> str:
    """Return the current UTC time in ISO 8601 form."""
    return datetime.now(timezone.utc).isoformat()


def _timestamp_for(iso_value: object) -> str:
    """Return a filesystem-safe UTC timestamp for *iso_value*."""
    text = str(iso_value or "").strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        parsed = datetime.now(timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")


def _unique_directory(parent: Path, name: str) -> Path:
    """Return a child of *parent* named *name*, adding a suffix if needed."""
    candidate = parent / name
    counter = 2
    while candidate.exists():
        candidate = parent / f"{name}-{counter}"
        counter += 1
    return candidate


def _summary_counts(request: dict, scan: dict) -> tuple[dict, list[dict]]:
    """Return summary counts and the complete result list for a request."""
    accepted_urls = list(request.get("accepted_urls") or [])
    scan_results = list(scan.get("results") or [])
    by_url = {
        str(result.get("url", "")): result
        for result in scan_results
        if isinstance(result, dict)
    }

    results: list[dict] = []
    for url in accepted_urls:
        result = by_url.get(url)
        if result is None:
            result = {
                "url": url,
                "final_url": url,
                "status": "Missing result",
                "http_status": None,
                "outcome": "error",
                "content_type": "",
                "error": "Scanner did not return a result for this URL.",
                "detected_overlay": "None Found",
                "overlays": [],
                "overlay_versions": "",
                "other_widgets": "",
            }
        results.append(result)

    attempted = [
        result for result in results if result.get("outcome") != "skipped"
    ]
    successful = [
        result for result in attempted
        if result.get("outcome") == OUTCOME_INSPECTED
    ]
    failed = len(attempted) - len(successful)
    skipped = len(results) - len(attempted)
    with_overlays = sum(
        1 for result in successful if result.get("overlays")
    )
    submitted = int((request.get("summary") or {}).get("submitted") or 0)
    rejected = len(request.get("rejected_urls") or [])
    duplicates = int((request.get("summary") or {}).get("duplicates") or 0)
    summary = {
        "submitted": submitted,
        "accepted": len(accepted_urls),
        "rejected": rejected,
        "duplicates": duplicates,
        "attempted": len(attempted),
        "successful": len(successful),
        "failed": failed,
        "skipped": skipped,
        "sites_with_overlays": with_overlays,
    }
    return summary, results


def build_report(
    request: dict,
    scan: dict,
    *,
    pages_base_url: str = "",
    timestamp: str | None = None,
) -> dict:
    """Build the stable issue-scan report contract."""
    issue = dict(request.get("issue") or {})
    summary, results = _summary_counts(request, scan)
    completed_at = str(scan.get("generated_at") or _iso_now())
    started_at = str(
        (request.get("scan") or {}).get("started_at")
        or request.get("prepared_at")
        or completed_at
    )
    status = "completed" if summary["accepted"] else "invalid"
    report_time = timestamp or _timestamp_for(completed_at)

    number = int(issue.get("number") or 0)
    issue_url = str(issue.get("url") or f"{DEFAULT_REPOSITORY}/issues/{number}")
    if pages_base_url:
        issue["report_url"] = (
            f"{pages_base_url.rstrip('/')}/reports/issues/issue-{number}/index.html"
        )
        issue["csv_url"] = (
            f"{pages_base_url.rstrip('/')}/reports/issues/issue-{number}/results.csv"
        )
        issue["json_url"] = (
            f"{pages_base_url.rstrip('/')}/reports/issues/issue-{number}/latest.json"
        )
    issue["url"] = issue_url

    return {
        "schema_version": SCHEMA_VERSION,
        "issue": issue,
        "scan": {
            "started_at": started_at,
            "completed_at": completed_at,
            "status": status,
            "source": "github-issue",
            "timestamp": report_time,
        },
        "fingerprint": str(request.get("fingerprint") or ""),
        "summary": summary,
        "rejected_urls": list(request.get("rejected_urls") or []),
        "results": results,
    }


def _overlay_cell(result: dict) -> str:
    """Render overlay names and evidence links for one result row."""
    if result.get("outcome") != OUTCOME_INSPECTED:
        return '<span class="none">Not determined</span>'
    overlays = result.get("overlays") or []
    if not overlays:
        return '<span class="none">None found</span>'
    items = []
    for item in overlays:
        if not isinstance(item, dict):
            continue
        name = safe_text(item.get("name", ""))
        items.append(f'<span class="overlay">{name}</span>')
    return " ".join(items) or '<span class="none">Not determined</span>'


def _version_cell(result: dict) -> str:
    """Render the best-effort version column for one result row."""
    overlays = result.get("overlays") or []
    if result.get("outcome") != OUTCOME_INSPECTED or not overlays:
        return "Not applicable"
    versions = []
    for item in overlays:
        if not isinstance(item, dict):
            continue
        version = str(item.get("version") or "").strip()
        versions.append(version or "Unknown")
    return safe_text("; ".join(versions) or "Unknown")


def _http_label(result: dict) -> str:
    """Return a human-readable HTTP or failure label."""
    outcome = str(result.get("outcome") or "")
    status = result.get("status_code")
    if status is None:
        status = result.get("http_status")
    if outcome in {OUTCOME_INSPECTED, OUTCOME_HTTP_ERROR} and status is not None:
        return str(status)
    if not outcome and status is not None:
        return str(status)
    labels = {
        OUTCOME_INVALID: "Rejected: invalid URL",
        OUTCOME_PRIVATE: "Blocked: private destination",
        OUTCOME_DNS_ERROR: "DNS error",
        OUTCOME_TIMEOUT: "Timeout",
        OUTCOME_TLS_ERROR: "TLS error",
        OUTCOME_CONNECTION_ERROR: "Connection error",
        OUTCOME_BLOCKED_REDIRECT: "Blocked redirect",
        OUTCOME_UNSUPPORTED: "Unsupported content",
        OUTCOME_TOO_LARGE: "Response too large",
        OUTCOME_HTTP_ERROR: "HTTP error",
        "skipped": "Skipped (time budget)",
        "error": "Scan error",
    }
    return labels.get(outcome, str(result.get("status") or "Scan error"))


def _row_for_result(result: dict) -> str:
    """Render one accepted scan result as a table row."""
    raw_url = str(result.get("url") or "")
    href = safe_href(raw_url)
    if href:
        site = (
            f'<a href="{safe_text(href)}" rel="noopener noreferrer">'
            f"{safe_text(display_url(raw_url))}</a>"
        )
    else:
        site = safe_text(display_url(raw_url))
    return (
        "<tr>"
        f'<th scope="row">{site}</th>'
        f"<td>{safe_text(_http_label(result))}</td>"
        f"<td>{_overlay_cell(result)}</td>"
        f"<td>{_version_cell(result)}</td>"
        f"<td>{_evidence_cell(result)}</td>"
        "</tr>"
    )


def _evidence_cell(result: dict) -> str:
    """Render evidence links for one result row."""
    overlays = result.get("overlays") or []
    if result.get("outcome") != OUTCOME_INSPECTED:
        return '<span class="none">None</span>'
    links = []
    for item in overlays:
        if not isinstance(item, dict):
            continue
        evidence = safe_href(item.get("evidence", ""))
        if evidence:
            name = safe_text(item.get("name", ""))
            links.append(
                f'<a class="evidence" href="{safe_text(evidence)}" '
                'rel="noopener noreferrer">'
                f"Asset for {name}</a>"
            )
    return " ".join(links) or '<span class="none">None</span>'


def _row_for_rejection(rejection: dict) -> str:
    """Render one rejected submission as a table row."""
    entry = str(rejection.get("entry") or "")
    normalized = str(rejection.get("normalized") or "")
    reason = str(rejection.get("reason") or "invalid URL")
    return (
        "<tr>"
        f'<th scope="row">{safe_text(display_url(normalized or entry))}</th>'
        f"<td>{safe_text('Rejected: ' + reason)}</td>"
        '<td><span class="none">Not determined</span></td>'
        "<td>Not applicable</td>"
        '<td><span class="none">None</span></td>'
        "</tr>"
    )


def _stat_cards(summary: dict) -> str:
    """Render summary statistics as an accessible list of cards."""
    cards = [
        ("Sites requested", summary["submitted"]),
        ("Sites accepted", summary["accepted"]),
        ("Sites attempted", summary["attempted"]),
        ("Successfully inspected", summary["successful"]),
        ("Sites with overlays", summary["sites_with_overlays"]),
        ("Scan failures", summary["failed"]),
        ("Skipped (time budget)", summary["skipped"]),
        ("Rejected entries", summary["rejected"]),
    ]
    items = "".join(
        f'<li class="stat"><span class="stat-label">{safe_text(label)}</span>'
        f'<strong class="stat-value">{value}</strong></li>'
        for label, value in cards
    )
    return f'<ul class="stats">{items}</ul>'


def _findings_table(report: dict) -> str:
    """Render the findings table for *report*."""
    results = report.get("results") or []
    rejections = report.get("rejected_urls") or []
    rows = [_row_for_result(result) for result in results]
    rows.extend(_row_for_rejection(item) for item in rejections)
    if not rows:
        rows = [
            '<tr><td colspan="5">No sites were submitted or scanned.</td></tr>'
        ]
    return (
        '<div class="table-wrap" tabindex="0" role="region" '
        'aria-label="Overlay findings for this scan, '
        'scroll horizontally if needed">'
        "<table>"
        f"<caption>Overlay findings for issue "
        f"#{safe_text(report['issue']['number'])}</caption>"
        "<thead><tr>"
        '<th scope="col">Website</th>'
        '<th scope="col">HTTP result</th>'
        '<th scope="col">Overlay detected</th>'
        '<th scope="col">Version</th>'
        '<th scope="col">Evidence</th>'
        "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table></div>"
    )


def _methodology() -> str:
    """Return the methodology and limitations section."""
    return (
        "<section aria-labelledby=\"method-heading\">"
        '<h2 id="method-heading">Method and limitations</h2>'
        "<ul>"
        "<li>The scanner retrieves the initial HTML and inspects it for known "
        "overlay product references.</li>"
        "<li>It does not execute JavaScript or crawl linked pages.</li>"
        "<li>It is not a WCAG conformance test or an accessibility audit.</li>"
        "<li>Overlays injected at runtime may be missed.</li>"
        "<li>Version information is best-effort and may be missing or wrong.</li>"
        "<li>Sites that time out, block the request, or return an HTTP error "
        "have an unknown overlay status; they are not reported as clean.</li>"
        "<li>The absence of a detected overlay is not proof that no overlay "
        "is present.</li>"
        "</ul></section>"
    )


def render_report_html(
    report: dict,
    *,
    css_href: str,
    json_href: str,
    csv_href: str,
    home_href: str,
    reports_href: str,
    history_href: str | None = None,
) -> str:
    """Render an accessible standalone report page."""
    issue = report["issue"]
    scan = report["scan"]
    summary = report["summary"]
    title = f"Overlay scan: {issue.get('title') or 'issue ' + str(issue.get('number'))}"
    issue_url = safe_href(issue.get("url", ""))
    issue_link = (
        f'<a href="{safe_text(issue_url)}" rel="noopener noreferrer">'
        f"GitHub issue #{safe_text(issue.get('number'))}</a>"
        if issue_url
        else f"GitHub issue #{safe_text(issue.get('number'))}"
    )
    history_link = (
        f'<li><a href="{safe_text(history_href)}">Run history</a></li>'
        if history_href
        else ""
    )
    duplicate_note = ""
    if summary["duplicates"]:
        duplicate_note = (
            f'<p class="meta">{summary["duplicates"]} duplicate '
            "submission(s) were collapsed to one scan target.</p>"
        )
    status_note = ""
    if scan["status"] == "invalid":
        status_note = (
            '<p class="notice"><strong>No valid public URLs were submitted.</strong> '
            "The table records why each entry was rejected. Edit the issue and "
            "reopen it to try again.</p>"
        )
    else:
        status_note = (
            '<p class="notice"><strong>This is a signal, not a verdict.</strong> '
            "An overlay reference does not prove a site is inaccessible, and a "
            "missing reference does not prove it is accessible.</p>"
        )

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="description" content="Overlay scan results for GitHub issue
    #{safe_text(issue.get('number'))}.">
  <title>{safe_text(title)}</title>
  <link rel="stylesheet" href="{safe_text(css_href)}">
</head>
<body>
  <a class="skip-link" href="#main">Skip to main content</a>
  <header class="site-header"><div class="wrap">
    <p class="brand">Find Overlays</p>
    <nav aria-label="Report"><ul>
      <li><a href="{safe_text(home_href)}">Current monitoring</a></li>
      <li><a href="{safe_text(reports_href)}">Requested scans</a></li>
      {history_link}
      <li>{issue_link}</li>
    </ul></nav>
  </div></header>
  <main id="main" class="wrap">
    <h1>{safe_text(title)}</h1>
    <p class="lede">
      Requested by {safe_text(issue.get('author') or 'unknown')} in
      {issue_link}. Scan completed
      <time datetime="{safe_text(scan['completed_at'])}">
        {safe_text(scan['completed_at'])}</time>.
    </p>
    {status_note}
    <section aria-labelledby="summary-heading">
      <h2 id="summary-heading">Summary</h2>
      {_stat_cards(summary)}
      {duplicate_note}
    </section>
    <section aria-labelledby="findings-heading">
      <h2 id="findings-heading">Findings</h2>
      <p>One row per submitted address. Rejected entries are shown with their
        rejection reason so every submitted target has an outcome.</p>
      {_findings_table(report)}
    </section>
    <section aria-labelledby="data-heading">
      <h2 id="data-heading">Data downloads</h2>
      <p class="data-links">
        <a href="{safe_text(json_href)}">Download JSON</a> ·
        <a href="{safe_text(csv_href)}">Download CSV</a>
      </p>
    </section>
    {_methodology()}
  </main>
  <footer class="site-footer"><div class="wrap">
    <p><a href="{safe_text(home_href)}">Find Overlays</a> ·
    <a href="{safe_text(reports_href)}">Requested scans</a></p>
    <p>Scans inspect HTML references to known overlay products. They are not
    WCAG audits.</p>
  </div></footer>
</body>
</html>
"""


def _csv_safe(value: object) -> str:
    """Return a CSV cell that cannot be interpreted as a spreadsheet formula."""
    text = "".join(character for character in str(value) if character.isprintable())
    if text and text[0] in {"=", "+", "-", "@", "\t", "\r"}:
        return f"'{text}"
    return text


def _csv_text(report: dict) -> str:
    """Return the CSV representation of *report*."""
    rows = []
    for result in report.get("results") or []:
        rows.append({
            "url": result.get("url", ""),
            "status": result.get("status", ""),
            "outcome": result.get("outcome", ""),
            "detected_overlay": result.get("detected_overlay", ""),
            "overlay_versions": result.get("overlay_versions", ""),
            "other_widgets": result.get("other_widgets", ""),
            "error": result.get("error", ""),
        })
    for rejection in report.get("rejected_urls") or []:
        rows.append({
            "url": rejection.get("entry", ""),
            "status": "Rejected",
            "outcome": rejection.get("reason", ""),
            "detected_overlay": "Not determined",
            "overlay_versions": "",
            "other_widgets": "",
            "error": rejection.get("reason", ""),
        })
    buffer = io.StringIO(newline="")
    fieldnames = [
        "url",
        "status",
        "outcome",
        "detected_overlay",
        "overlay_versions",
        "other_widgets",
        "error",
    ]
    writer = csv.DictWriter(buffer, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(
        {key: _csv_safe(value) for key, value in row.items()}
        for row in rows
    )
    return buffer.getvalue()


def write_report_artifact(
    request: dict,
    scan: dict,
    *,
    artifact_dir: Path,
    site_dir: Path,
    pages_base_url: str = "",
) -> dict:
    """Write timestamped and stable report files below *artifact_dir*."""
    report = build_report(request, scan, pages_base_url=pages_base_url)
    issue_number = int(report["issue"]["number"])
    issue_dir = artifact_dir / f"issue-{issue_number}"
    run_dir = _unique_directory(issue_dir, report["scan"]["timestamp"])

    run_css = os.path.relpath(
        site_dir / "styles.css", site_dir / "reports" / "issues" / issue_dir.name
        / run_dir.name
    )
    stable_css = os.path.relpath(
        site_dir / "styles.css",
        site_dir / "reports" / "issues" / issue_dir.name,
    )
    run_html = render_report_html(
        report,
        css_href=run_css,
        json_href="report.json",
        csv_href="report.csv",
        home_href="../../../../index.html",
        reports_href="../../../index.html",
        history_href="../index.html",
    )
    stable_html = render_report_html(
        report,
        css_href=stable_css,
        json_href="latest.json",
        csv_href="results.csv",
        home_href="../../../index.html",
        reports_href="../../index.html",
        history_href="history.html",
    )

    _write_text(run_dir / "report.html", run_html)
    _write_json(run_dir / "report.json", report)
    _write_text(run_dir / "report.csv", _csv_text(report))
    _write_text(issue_dir / "index.html", stable_html)
    _write_json(issue_dir / "latest.json", report)
    _write_text(issue_dir / "results.csv", _csv_text(report))
    return report


def _comment_for(report: dict, *, pages_base_url: str) -> str:
    """Return a Markdown comment for the originating issue."""
    issue = report["issue"]
    summary = report["summary"]
    number = issue["number"]
    base = pages_base_url.rstrip("/")
    report_url = f"{base}/reports/issues/issue-{number}/index.html"
    csv_url = f"{base}/reports/issues/issue-{number}/results.csv"
    json_url = f"{base}/reports/issues/issue-{number}/latest.json"

    if report["scan"]["status"] == "invalid":
        return (
            "## No scan was run\n\n"
            "No valid public HTTP(S) URLs were found in the **Site URLs** "
            "section. The report lists each rejected entry and why.\n\n"
            f"- [View rejection report]({report_url})\n\n"
            "Correct the addresses, then edit or reopen this issue to try again. "
            "Bare domains and full URLs are both accepted.\n"
        )
    return (
        "## Overlay scan complete\n\n"
        f"**Requested websites:** {summary['submitted']}  \n"
        f"**Accepted:** {summary['accepted']}  \n"
        f"**Rejected:** {summary['rejected']}  \n"
        f"**Attempted:** {summary['attempted']}  \n"
        f"**Successfully inspected:** {summary['successful']}  \n"
        f"**Scan failures:** {summary['failed']}  \n"
        f"**Sites with overlays:** {summary['sites_with_overlays']}  \n"
        f"**Skipped due to time budget:** {summary['skipped']}\n\n"
        "### Results\n\n"
        f"- [View HTML report]({report_url})\n"
        f"- [Download CSV]({csv_url})\n"
        f"- [Download JSON]({json_url})\n\n"
        "The report lists detected overlay products, available version "
        "information, and evidence. This scan inspects HTML references to "
        "known overlay technologies. It is not a WCAG audit.\n\n"
        "Reopen this issue to request a new scan.\n"
    )


def _comment_for_reuse(request: dict, pages_base_url: str) -> str:
    """Return a Markdown comment when an unchanged edit reuses a report."""
    number = (request.get("issue") or {}).get("number")
    base = pages_base_url.rstrip("/")
    report_url = f"{base}/reports/issues/issue-{number}/index.html"
    return (
        "## No new scan needed\n\n"
        "The submitted URL set has not changed since the last completed scan, "
        "so the existing report is still current.\n\n"
        f"- [View the latest report]({report_url})\n\n"
        "Edit the URL list and reopen the issue to request a new scan.\n"
    )


def _default_repository_url() -> str:
    """Return the GitHub repository URL from the workflow environment."""
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    if "/" not in repository:
        return DEFAULT_REPOSITORY
    return f"https://github.com/{repository}"


def build_index(
    site_dir: Path,
    pages_base_url: str = "",
    repository_url: str = "",
) -> list[dict]:
    """Rebuild the requested-scans index and per-issue run history pages."""
    reports_dir = site_dir / "reports"
    issues_dir = reports_dir / "issues"
    entries: list[dict] = []
    if issues_dir.is_dir():
        for issue_dir in sorted(issues_dir.glob("issue-*")):
            latest = issue_dir / "latest.json"
            if not latest.is_file():
                continue
            try:
                report = _load_json(latest)
            except (OSError, ValueError):
                continue
            issue = report.get("issue") or {}
            scan = report.get("scan") or {}
            summary = report.get("summary") or {}
            number = int(issue.get("number") or 0)
            entries.append({
                "number": number,
                "title": str(issue.get("title") or f"Issue #{number}"),
                "completed": str(scan.get("completed_at") or ""),
                "submitted": summary.get("submitted", 0),
                "with_overlays": summary.get("sites_with_overlays", 0),
                "status": str(scan.get("status") or ""),
            })
            _write_history(issue_dir, number, site_dir)

    entries.sort(key=lambda item: item["completed"], reverse=True)
    entries = entries[:MAX_INDEX_REPORTS]
    index_html = _render_index(
        entries,
        site_dir,
        pages_base_url,
        repository_url or _default_repository_url(),
    )
    _write_text(reports_dir / "index.html", index_html)
    return entries


def _write_history(issue_dir: Path, issue_number: int, site_dir: Path) -> None:
    """Write a history page for one issue's timestamped runs."""
    runs = []
    for run_dir in sorted(issue_dir.iterdir(), reverse=True):
        report_path = run_dir / "report.json"
        if not run_dir.is_dir() or not report_path.is_file():
            continue
        try:
            report = _load_json(report_path)
        except (OSError, ValueError):
            continue
        scan = report.get("scan") or {}
        summary = report.get("summary") or {}
        runs.append({
            "timestamp": str(scan.get("timestamp") or run_dir.name),
            "completed": str(scan.get("completed_at") or ""),
            "submitted": summary.get("submitted", 0),
            "with_overlays": summary.get("sites_with_overlays", 0),
            "failed": summary.get("failed", 0),
            "href": f"{run_dir.name}/report.html",
        })
    css_href = os.path.relpath(
        site_dir / "styles.css", site_dir / "reports" / "issues" / issue_dir.name
    )
    rows = "".join(
        "<tr>"
        f'<td>{safe_text(run["completed"])}</td>'
        f'<td>{run["submitted"]}</td>'
        f'<td>{run["with_overlays"]}</td>'
        f'<td>{run["failed"]}</td>'
        f'<td><a href="{safe_text(run["href"])}">View report</a></td>'
        "</tr>"
        for run in runs
    ) or '<tr><td colspan="5">No completed runs recorded.</td></tr>'
    page = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Scan history for issue #{issue_number}</title>
  <link rel="stylesheet" href="{safe_text(css_href)}">
</head>
<body>
  <a class="skip-link" href="#main">Skip to main content</a>
  <header class="site-header"><div class="wrap">
    <p class="brand">Find Overlays</p>
    <nav aria-label="Report"><ul>
      <li><a href="../../../index.html">Current monitoring</a></li>
      <li><a href="../../index.html">Requested scans</a></li>
      <li><a href="index.html">Latest report</a></li>
    </ul></nav>
  </div></header>
  <main id="main" class="wrap">
    <h1>Scan history for issue #{issue_number}</h1>
    <p>Each row is a completed run for this issue. The newest run is listed first.</p>
    <div class="table-wrap" tabindex="0" role="region"
         aria-label="Scan history, scroll horizontally if needed">
      <table>
        <caption>Completed scans for issue #{issue_number}</caption>
        <thead><tr>
          <th scope="col">Completed</th><th scope="col">Requested</th>
          <th scope="col">With overlays</th><th scope="col">Failures</th>
          <th scope="col">Report</th>
        </tr></thead>
        <tbody>{rows}</tbody>
      </table>
    </div>
    <p><a href="index.html">Back to the latest report</a></p>
  </main>
</body>
</html>
"""
    _write_text(issue_dir / "history.html", page)


def _render_index(
    entries: list[dict],
    site_dir: Path,
    pages_base_url: str,
    repository_url: str,
) -> str:
    """Render the public requested-scans index page."""
    css_href = os.path.relpath(site_dir / "styles.css", site_dir / "reports")
    request_url = (
        f"{repository_url.rstrip('/')}/issues/new?template=scan-request.yml"
    )
    rows = "".join(
        "<tr>"
        f'<th scope="row"><a href="issues/issue-{entry["number"]}/index.html">'
        f'{safe_text(entry["title"])}</a></th>'
        f'<td>#{entry["number"]}</td>'
        f'<td>{entry["submitted"]}</td>'
        f'<td>{entry["with_overlays"]}</td>'
        f'<td>{safe_text(entry["completed"])}</td>'
        f'<td>{safe_text(entry["status"])}</td>'
        "</tr>"
        for entry in entries
    ) or (
        '<tr><td colspan="6">No requested scans have been published yet. '
        "Use the request form to submit the first one.</td></tr>"
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="description" content="Public one-time overlay scans requested
    through GitHub issues.">
  <title>Requested overlay scans</title>
  <link rel="stylesheet" href="{safe_text(css_href)}">
</head>
<body>
  <a class="skip-link" href="#main">Skip to main content</a>
  <header class="site-header"><div class="wrap">
    <p class="brand">Find Overlays</p>
    <nav aria-label="Primary"><ul>
      <li><a href="../index.html">Current monitoring</a></li>
      <li><a href="index.html">Requested scans</a></li>
      <li><a href="{safe_text(request_url)}">Request a scan</a></li>
      <li><a href="../index.html#method-heading">Methodology</a></li>
    </ul></nav>
  </div></header>
  <main id="main" class="wrap">
    <h1>Requested overlay scans</h1>
    <p class="lede">One-time scans requested through public GitHub issues. A
      request does not add a site to the recurring monitoring list.</p>
    <p><strong>Scan websites for accessibility overlays.</strong> Anyone with a
      GitHub account can submit up to 100 publicly accessible websites.
      Results are public and linked from the GitHub issue.</p>
    <p><a href="{safe_text(request_url)}">Request a scan</a></p>
    <div class="table-wrap" tabindex="0" role="region"
         aria-label="Requested scan reports, scroll horizontally if needed">
      <table>
        <caption>Published issue scan reports</caption>
        <thead><tr>
          <th scope="col">Scan</th><th scope="col">Issue</th>
          <th scope="col">Requested</th><th scope="col">With overlays</th>
          <th scope="col">Completed</th><th scope="col">Status</th>
        </tr></thead>
        <tbody>{rows}</tbody>
      </table>
    </div>
    <p>Showing the {MAX_INDEX_REPORTS} most recent requested scans. Older
      reports remain available from their GitHub issues.</p>
  </main>
  <footer class="site-footer"><div class="wrap">
    <p><a href="../index.html">Current monitoring</a> ·
    <a href="{safe_text(request_url)}">Request a scan</a></p>
  </div></footer>
</body>
</html>
"""


def publish(
    request: dict,
    *,
    artifact_dir: Path,
    site_dir: Path,
    pages_base_url: str,
    comment_path: Path,
) -> dict:
    """Copy a rendered issue report into the site and write its comment."""
    issue_number = int((request.get("issue") or {}).get("number") or 0)
    if request.get("reuse"):
        _write_text(comment_path, _comment_for_reuse(request, pages_base_url))
        return {
            "changed": False,
            "close_issue": True,
            "report_url": str(request.get("previous_report_url") or ""),
        }

    source = artifact_dir / f"issue-{issue_number}"
    if not source.is_dir():
        raise FileNotFoundError(f"missing rendered report: {source}")
    destination = site_dir / "reports" / "issues" / f"issue-{issue_number}"
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, destination, dirs_exist_ok=True)
    build_index(site_dir, pages_base_url, _default_repository_url())
    report = _load_json(destination / "latest.json")
    _write_text(comment_path, _comment_for(report, pages_base_url=pages_base_url))
    return {
        "changed": True,
        "close_issue": report["scan"]["status"] == "completed",
        "report_url": report["issue"].get("report_url", ""),
    }


def _default_pages_base() -> str:
    """Return the GitHub Pages base URL from the workflow environment."""
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    if "/" not in repository:
        return ""
    owner, name = repository.split("/", 1)
    return f"https://{owner}.github.io/{name}"


def _set_github_output(name: str, value: object) -> None:
    """Append a single-line output to ``GITHUB_OUTPUT`` when it is set."""
    output_path = os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        return
    with open(output_path, "a", encoding="utf-8") as handle:
        handle.write(f"{name}={str(value).replace(chr(10), ' ')}\n")


def _render_command(args: argparse.Namespace) -> int:
    """Run the ``render`` subcommand."""
    request = _load_json(Path(args.request))
    scan = _load_json(Path(args.scan))
    report = write_report_artifact(
        request,
        scan,
        artifact_dir=Path(args.artifact),
        site_dir=Path(args.site),
        pages_base_url=args.pages_base_url or _default_pages_base(),
    )
    _set_github_output("timestamp", report["scan"]["timestamp"])
    _set_github_output("status", report["scan"]["status"])
    print(f"Rendered issue #{report['issue']['number']} report.")
    return 0


def _index_command(args: argparse.Namespace) -> int:
    """Run the ``index`` subcommand."""
    entries = build_index(
        Path(args.site), args.pages_base_url or _default_pages_base()
    )
    print(f"Indexed {len(entries)} requested scan report(s).")
    return 0


def _publish_command(args: argparse.Namespace) -> int:
    """Run the ``publish`` subcommand."""
    request = _load_json(Path(args.request))
    result = publish(
        request,
        artifact_dir=Path(args.artifact),
        site_dir=Path(args.site),
        pages_base_url=args.pages_base_url or _default_pages_base(),
        comment_path=Path(args.comment),
    )
    _set_github_output("changed", str(result["changed"]).lower())
    _set_github_output("close_issue", str(result["close_issue"]).lower())
    _set_github_output("report_url", result["report_url"])
    print(json.dumps(result))
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Return the command-line parser for issue report rendering."""
    parser = argparse.ArgumentParser(description="Render issue scan reports.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    render = subparsers.add_parser("render", help="Render one issue artifact")
    render.add_argument("--request", required=True)
    render.add_argument("--scan", required=True)
    render.add_argument("--artifact", required=True)
    render.add_argument("--site", default="site")
    render.add_argument("--pages-base-url", default="")
    render.set_defaults(func=_render_command)

    index = subparsers.add_parser("index", help="Rebuild the report index")
    index.add_argument("--site", default="site")
    index.add_argument("--pages-base-url", default="")
    index.set_defaults(func=_index_command)

    publish_parser = subparsers.add_parser(
        "publish", help="Copy an artifact into the site and write a comment"
    )
    publish_parser.add_argument("--request", required=True)
    publish_parser.add_argument("--artifact", required=True)
    publish_parser.add_argument("--site", default="site")
    publish_parser.add_argument("--comment", default="issue-comment.md")
    publish_parser.add_argument("--pages-base-url", default="")
    publish_parser.set_defaults(func=_publish_command)
    return parser


def main() -> None:
    """Run the requested report subcommand."""
    args = build_parser().parse_args()
    raise SystemExit(args.func(args))


if __name__ == "__main__":
    main()
