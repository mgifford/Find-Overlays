"""Build the static GitHub Pages site from a scan JSON file.

The scanner writes one JSON document per run.  This script merges that run with
the previous ``latest.json``, records changes, writes history snapshots, and
renders ``site/index.html`` from ``site/template.html``.
"""

import argparse
import html
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

CHANGE_LIMIT = 500
DISPLAY_CHANGE_LIMIT = 200


def load_json(path: Path, default: object) -> object:
    """Return parsed JSON from *path*, or *default* when it is missing."""
    if not path.exists():
        return default
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, payload: object) -> None:
    """Write *payload* as indented UTF-8 JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def overlay_map(result: dict) -> dict[str, str]:
    """Return a mapping of overlay name to version for one result."""
    overlays = result.get("overlays") or []
    return {
        item["name"]: item.get("version", "")
        for item in overlays
        if isinstance(item, dict) and item.get("name")
    }


def build_events(
    previous: dict[str, dict],
    results: list[dict],
    scan_date: str,
) -> list[dict]:
    """Return change events between *previous* results and *results*."""
    events = []
    current_by_url = {result["url"]: result for result in results}
    for url, result in current_by_url.items():
        prior = previous.get(url)
        if prior is None:
            events.append({"date": scan_date, "url": url, "kind": "site_added"})
            continue
        if str(prior.get("status")) != str(result.get("status")):
            events.append({
                "date": scan_date,
                "url": url,
                "kind": "status_changed",
                "from": str(prior.get("status", "")),
                "to": str(result.get("status", "")),
            })
        if str(prior.get("status")) != "200" or str(result.get("status")) != "200":
            continue
        old_overlays = overlay_map(prior)
        new_overlays = overlay_map(result)
        for name in sorted(new_overlays.keys() - old_overlays.keys()):
            events.append({
                "date": scan_date,
                "url": url,
                "kind": "overlay_added",
                "overlay": name,
                "to": new_overlays[name],
            })
        for name in sorted(old_overlays.keys() - new_overlays.keys()):
            events.append({
                "date": scan_date,
                "url": url,
                "kind": "overlay_removed",
                "overlay": name,
                "from": old_overlays[name],
            })
        for name in sorted(old_overlays.keys() & new_overlays.keys()):
            if old_overlays[name] != new_overlays[name]:
                events.append({
                    "date": scan_date,
                    "url": url,
                    "kind": "version_changed",
                    "overlay": name,
                    "from": old_overlays[name],
                    "to": new_overlays[name],
                })
    for url in sorted(previous.keys() - current_by_url.keys()):
        events.append({"date": scan_date, "url": url, "kind": "site_removed"})
    return events


def merge_changes(
    existing: list[dict],
    new_events: list[dict],
) -> list[dict]:
    """Merge events, drop duplicates, and return the most recent changes."""
    combined = new_events + existing
    seen = set()
    unique = []
    for event in combined:
        key = (
            event.get("date", ""),
            event.get("url", ""),
            event.get("kind", ""),
            event.get("overlay", ""),
            event.get("from", ""),
            event.get("to", ""),
        )
        if key not in seen:
            seen.add(key)
            unique.append(event)
    unique.sort(
        key=lambda event: (event.get("date", ""), event.get("url", "")),
        reverse=True,
    )
    return unique[:CHANGE_LIMIT]


def display_url(url: str) -> str:
    """Return a compact, human-readable form of *url*."""
    parsed = urlparse(url)
    if not parsed.netloc:
        return url
    path = parsed.path.rstrip("/")
    return f"{parsed.netloc}{path}" if path else parsed.netloc


def version_label(version: str) -> str:
    """Return escaped version text, or a plain-language unknown label."""
    if not version:
        return '<span class="version unknown">version unknown</span>'
    return f'<span class="version">v{html.escape(version)}</span>'


def overlay_cell(result: dict) -> str:
    """Render the overlay column for one result."""
    overlays = result.get("overlays") or []
    if not overlays:
        return '<span class="none">None found</span>'
    items = []
    for item in overlays:
        name = html.escape(str(item.get("name", "")))
        evidence = str(item.get("evidence") or "")
        link = ""
        if evidence.startswith(("http://", "https://")):
            link = (
                f' <a class="evidence" href="{html.escape(evidence, quote=True)}" '
                f'rel="noopener noreferrer" aria-label="Evidence for {name}">'
                "asset</a>"
            )
        items.append(
            f'<span class="overlay">{name} {version_label(item.get("version", ""))}'
            f"{link}</span>"
        )
    return " ".join(items)


def status_class(status: object) -> str:
    """Return a CSS class describing the scan status."""
    return "ok" if str(status) == "200" else "warn"


def change_description(event: dict) -> str:
    """Return a human-readable description of a change event."""
    kind = event.get("kind")
    overlay = html.escape(str(event.get("overlay", "")))
    old = html.escape(str(event.get("from", "")))
    new = html.escape(str(event.get("to", "")))
    if kind == "site_added":
        return "Added to the scan list"
    if kind == "site_removed":
        return "Removed from the scan list"
    if kind == "status_changed":
        return f"Status changed from {old} to {new}"
    if kind == "overlay_added":
        version = f" (v{new})" if new else ""
        return f"{overlay} detected{version}"
    if kind == "overlay_removed":
        return f"{overlay} no longer detected"
    if kind == "version_changed":
        return (
            f"{overlay} version changed from {old or 'unknown'} "
            f"to {new or 'unknown'}"
        )
    return "Changed"


def render_summary(summary: dict) -> str:
    """Render the summary statistics block."""
    cards = [
        ("Sites scanned", summary["sites"]),
        ("Sites with overlays", summary["with_overlays"]),
        ("Overlay occurrences", summary["overlay_count"]),
        ("Non-200 results", summary["errors"]),
    ]
    items = "".join(
        f'<li class="stat"><span class="stat-label">{html.escape(label)}</span>'
        f'<strong class="stat-value">{value}</strong></li>'
        for label, value in cards
    )
    return f'<ul class="stats">{items}</ul>'


def render_site_rows(results: list[dict]) -> str:
    """Render the findings table body."""
    if not results:
        return (
            '<tr><td colspan="5">No sites have been scanned yet. '
            "Request a scan to get started.</td></tr>"
        )
    rows = []
    for result in results:
        url = str(result.get("url", ""))
        rows.append(
            "<tr>"
            f'<th scope="row"><a href="{html.escape(url, quote=True)}" '
            f'rel="noopener noreferrer">{html.escape(display_url(url))}</a></th>'
            f'<td><span class="status {status_class(result.get("status"))}">'
            f'{html.escape(str(result.get("status", "")))}</span></td>'
            f"<td>{overlay_cell(result)}</td>"
            f'<td>{html.escape(str(result.get("first_seen", "")))}</td>'
            f'<td>{html.escape(str(result.get("last_seen", "")))}</td>'
            "</tr>"
        )
    return "\n".join(rows)


def render_change_rows(changes: list[dict]) -> str:
    """Render the recent change table body."""
    if not changes:
        return '<tr><td colspan="3">No changes recorded yet.</td></tr>'
    rows = []
    for event in changes[:DISPLAY_CHANGE_LIMIT]:
        url = str(event.get("url", ""))
        rows.append(
            "<tr>"
            f'<td>{html.escape(str(event.get("date", "")))}</td>'
            f'<td><a href="{html.escape(url, quote=True)}" '
            f'rel="noopener noreferrer">{html.escape(display_url(url))}</a></td>'
            f"<td>{change_description(event)}</td>"
            "</tr>"
        )
    return "\n".join(rows)


def render_general_concerns(providers: dict) -> str:
    """Render the shared concerns about overlay products."""
    items = []
    for concern in providers.get("general_concerns", []):
        title = html.escape(str(concern.get("title", "")))
        url = html.escape(str(concern.get("url", "")), quote=True)
        note = html.escape(str(concern.get("note", "")))
        items.append(
            f'<li><a href="{url}" rel="noopener noreferrer">{title}</a>'
            f"<p>{note}</p></li>"
        )
    return "\n".join(items) or "<li>No external references recorded.</li>"


def render_provider_rows(results: list[dict], providers: dict) -> str:
    """Render provider-specific context for every overlay in the results."""
    names = sorted({
        item.get("name", "")
        for result in results
        for item in (result.get("overlays") or [])
        if item.get("name")
    })
    if not names:
        return '<tr><td colspan="3">No overlays detected yet.</td></tr>'
    known = providers.get("providers", {})
    rows = []
    for name in names:
        info = known.get(name, {})
        website = str(info.get("website", ""))
        site_link = (
            f'<a href="{html.escape(website, quote=True)}" '
            f'rel="noopener noreferrer">website</a>'
            if website else "—"
        )
        concerns = []
        for concern in info.get("concerns", []):
            title = html.escape(str(concern.get("title", "")))
            url = html.escape(str(concern.get("url", "")), quote=True)
            concerns.append(
                f'<a href="{url}" rel="noopener noreferrer">{title}</a>'
            )
        concern_text = "; ".join(concerns) if concerns else (
            '<a href="#general-concerns">general concerns</a>'
        )
        rows.append(
            f'<tr><th scope="row">{html.escape(name)}</th>'
            f"<td>{site_link}</td><td>{concern_text}</td></tr>"
        )
    return "\n".join(rows)


def build_snapshot(scan: dict, previous: dict, scan_date: str) -> list[dict]:
    """Return current results enriched with first/last seen dates."""
    previous_by_url = {
        result.get("url"): result for result in previous.get("results", [])
    }
    results = []
    for result in scan.get("results", []):
        url = result.get("url", "")
        results.append({
            **result,
            "first_seen": previous_by_url.get(url, {}).get("first_seen", scan_date),
            "last_seen": scan_date,
        })
    results.sort(key=lambda item: item.get("url", ""))
    return results


def main() -> None:
    """Parse arguments, merge the scan, and write the site."""
    parser = argparse.ArgumentParser(description="Build the Find-Overlays site.")
    parser.add_argument("--scan", required=True, help="Scanner JSON output")
    parser.add_argument("--site", default="site", help="Site directory")
    parser.add_argument(
        "--providers", default="data/providers.json",
        help="Provider metadata JSON",
    )
    parser.add_argument("--date", help="Scan date (YYYY-MM-DD); defaults to today")
    args = parser.parse_args()

    site_dir = Path(args.site)
    scan = load_json(Path(args.scan), {})
    if not isinstance(scan, dict) or not scan.get("results"):
        raise SystemExit(f"Scan file has no results: {args.scan}")

    scan_date = args.date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    latest_path = site_dir / "data" / "latest.json"
    previous = load_json(latest_path, {})
    if not isinstance(previous, dict):
        previous = {}

    results = build_snapshot(scan, previous, scan_date)
    previous_by_url = {
        result.get("url"): result for result in previous.get("results", [])
    }
    events = build_events(previous_by_url, results, scan_date)

    with_overlays = sum(1 for result in results if result.get("overlays"))
    errors = sum(1 for result in results if str(result.get("status")) != "200")
    summary = {
        "sites": len(results),
        "with_overlays": with_overlays,
        "overlay_count": sum(len(result.get("overlays") or []) for result in results),
        "errors": errors,
    }
    latest = {
        "scan_date": scan_date,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": scan.get("source", ""),
        "summary": summary,
        "results": results,
    }
    write_json(latest_path, latest)
    write_json(site_dir / "data" / "history" / f"{scan_date}.json", latest)

    changes_path = site_dir / "data" / "changes.json"
    existing = load_json(changes_path, [])
    if not isinstance(existing, list):
        existing = []
    changes = merge_changes(existing, events)
    write_json(changes_path, changes)

    providers = load_json(Path(args.providers), {})
    if not isinstance(providers, dict):
        providers = {}
    template = (site_dir / "template.html").read_text(encoding="utf-8")
    replacements = {
        "{{SCAN_DATE}}": html.escape(scan_date),
        "{{GENERATED_AT}}": html.escape(latest["generated_at"]),
        "{{SUMMARY}}": render_summary(summary),
        "{{SITE_ROWS}}": render_site_rows(results),
        "{{CHANGE_ROWS}}": render_change_rows(changes),
        "{{PROVIDER_ROWS}}": render_provider_rows(results, providers),
        "{{GENERAL_CONCERNS}}": render_general_concerns(providers),
        "{{SITE_COUNT}}": str(len(results)),
        "{{CHANGE_COUNT}}": str(min(len(changes), DISPLAY_CHANGE_LIMIT)),
        "{{PROVIDER_UPDATED}}": html.escape(str(providers.get("updated", ""))),
    }
    for token, value in replacements.items():
        template = template.replace(token, value)
    (site_dir / "index.html").write_text(template, encoding="utf-8")
    print(
        f"Built {site_dir / 'index.html'}: {len(results)} sites, "
        f"{summary['overlay_count']} overlays, {len(events)} new events"
    )


if __name__ == "__main__":
    main()
