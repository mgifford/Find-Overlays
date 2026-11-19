"""Static tests for the issue scan workflow and issue template."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "scan-request.yml"
TEMPLATE = ROOT / ".github" / "ISSUE_TEMPLATE" / "scan-request.yml"


def _yaml(path: Path) -> dict:
    """Load YAML, treating the ``on`` key as a string."""
    text = path.read_text(encoding="utf-8")
    return yaml.load(text, Loader=yaml.BaseLoader)


def test_issue_events_are_triggered() -> None:
    """The workflow runs for opened, edited, and reopened issues."""
    workflow = _yaml(WORKFLOW)
    triggers = workflow["on"]["issues"]["types"]
    assert triggers == ["opened", "edited", "reopened"]


def test_scan_job_is_read_only_and_publish_job_can_write() -> None:
    """Untrusted fetching has no write credentials; publication does."""
    workflow = _yaml(WORKFLOW)
    scan = workflow["jobs"]["scan"]
    publish = workflow["jobs"]["publish"]
    assert scan["permissions"] == {"contents": "read"}
    assert publish["permissions"]["contents"] == "write"
    assert publish["permissions"]["issues"] == "write"
    assert publish["permissions"]["pages"] == "write"


def test_scan_job_accepts_scan_titles() -> None:
    """The job condition requires an open SCAN: issue, not a label."""
    workflow = _yaml(WORKFLOW)
    condition = workflow["jobs"]["scan"]["if"]
    assert "SCAN:" in condition
    assert "approved" not in condition
    assert "needs-triage" not in condition


def test_concurrency_is_bounded() -> None:
    """Issue scans are serialised and publication shares the pages lock."""
    workflow = _yaml(WORKFLOW)
    assert workflow["concurrency"]["group"] == "issue-scan-execution"
    publish = workflow["jobs"]["publish"]
    assert publish["concurrency"]["group"] == "pages"


def test_actions_are_pinned_to_full_shas() -> None:
    """Every action reference uses a full commit SHA."""
    text = WORKFLOW.read_text(encoding="utf-8")
    uses = re.findall(r"uses:\s*([^\s#]+)", text)
    assert uses
    for action in uses:
        assert re.search(r"@[0-9a-f]{40}$", action), action


def test_workflow_calls_expected_commands() -> None:
    """The workflow invokes the parser, scanner, renderer, and publisher."""
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "scripts/issue_scan.py prepare" in text
    assert "find-overlay.py requested-sites.txt" in text
    assert "render_issue_report.py render" in text
    assert "render_issue_report.py publish" in text
    assert "gh issue comment" in text
    assert "gh issue close" in text


def test_workflow_has_failure_notification() -> None:
    """A failed stage comments on the issue instead of closing it."""
    workflow = _yaml(WORKFLOW)
    failure = workflow["jobs"]["notify-failure"]
    assert "failure()" in failure["if"]
    assert failure["permissions"] == {"issues": "write"}


def test_issue_body_is_not_interpolated_into_shell() -> None:
    """Issue content is read from the event payload by Python, not shell."""
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "${{ github.event.issue.body }}" not in text
    for job in _yaml(WORKFLOW)["jobs"].values():
        for step in job.get("steps", []):
            run = step.get("run", "")
            assert "github.event.issue.body" not in run
            assert "github.event.issue.title" not in run


def test_artifact_is_uploaded_and_downloaded() -> None:
    """The read-only scan job hands a report artifact to publication."""
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "actions/upload-artifact@" in text
    assert "actions/download-artifact@" in text
    assert "issue-scan-report" in text


def test_old_approval_queue_is_gone() -> None:
    """The approval-based queue workflow and script are removed."""
    assert not (ROOT / ".github/workflows/queue-scan-request.yml").exists()
    assert not (ROOT / "scripts/queue_submission.py").exists()


def test_scheduled_monitoring_is_unchanged() -> None:
    """The scheduled scan still uses the curated data/sites.csv."""
    scheduled = (ROOT / ".github/workflows/scan-and-publish.yml").read_text()
    assert "data/sites.csv" in scheduled
    assert "scripts/build_site.py" in scheduled


def test_issue_template_has_scan_title_and_no_labels() -> None:
    """The public form uses the SCAN: title and requires no labels."""
    template = _yaml(TEMPLATE)
    assert template["title"] == "SCAN: "
    assert template["labels"] == []
    fields = {field.get("id") for field in template["body"] if field.get("id")}
    assert "urls" in fields
    assert not any(field.get("type") == "checkboxes" for field in template["body"])


def test_issue_template_documents_bare_domains() -> None:
    """The form explains that bare domains and up to 100 URLs are accepted."""
    text = TEMPLATE.read_text(encoding="utf-8")
    assert "bare domains" in text
    assert "100" in text
    assert "WCAG" in text


def test_homepage_promotes_public_scan_requests() -> None:
    """The homepage links to the issue form and requested scans."""
    template = (ROOT / "site" / "template.html").read_text()
    assert "Scan websites for accessibility overlays" in template
    assert "issues/new?template=scan-request.yml" in template
    assert 'href="reports/index.html"' in template
    assert "No maintainer approval is required" in template


def test_reports_index_is_accessible_shell() -> None:
    """The requested-scans index has a caption, scoped headers, and no script."""
    index = (ROOT / "site" / "reports" / "index.html").read_text()
    assert "<caption>" in index
    assert 'scope="col"' in index
    assert 'class="skip-link"' in index
    assert "<script" not in index
    assert "issues/new?template=scan-request.yml" in index


def test_control_border_is_separate_from_table_border() -> None:
    """The form control has a darker border than decorative table rules."""
    css = (ROOT / "site" / "styles.css").read_text()
    assert "--control-border" in css
    assert "border: 2px solid var(--control-border)" in css
