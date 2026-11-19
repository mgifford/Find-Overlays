"""Tests for issue report rendering, publication, and index pages."""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

from overlay_safety import OUTCOME_HTTP_ERROR, OUTCOME_INSPECTED, TargetValidation
from scripts import issue_scan, render_issue_report


def _public(target) -> TargetValidation:
    """Return a public validation for test targets."""
    return TargetValidation(target, "public", "", ("93.184.216.34",))


def _request(
    entries: list[str],
    *,
    title: str = "SCAN: Test scan",
    number: int = 7,
) -> dict:
    """Build a validated request for report tests."""
    issue = issue_scan.IssueMetadata(
        number,
        title,
        "",
        f"https://github.com/mgifford/Find-Overlays/issues/{number}",
        "alice",
    )
    return issue_scan.build_request(issue, entries, resolver=_public)


def _scan(results: list[dict], generated_at: str = "2026-11-19T14:30:00+00:00") -> dict:
    """Build a scanner JSON document for report tests."""
    return {
        "source": "github-issue",
        "generated_at": generated_at,
        "results": results,
    }


def _result(
    url: str,
    *,
    status: int | str = 200,
    outcome: str = OUTCOME_INSPECTED,
    overlays: list[dict] | None = None,
) -> dict:
    """Build one scanner result."""
    overlays = overlays or []
    return {
        "url": url,
        "final_url": url,
        "status": status,
        "http_status": status if isinstance(status, int) else None,
        "outcome": outcome,
        "content_type": "text/html",
        "error": "",
        "detected_overlay": ", ".join(item["name"] for item in overlays)
        if overlays else "None Found",
        "overlays": overlays,
        "overlay_versions": "; ".join(
            f"{item['name']} {item['version'] or 'unknown'}" for item in overlays
        ),
        "other_widgets": "",
    }


def _site(tmp_path: Path) -> Path:
    """Create a minimal site directory with the shared stylesheet."""
    site = tmp_path / "site"
    site.mkdir()
    (site / "styles.css").write_text("", encoding="utf-8")
    return site


class TestBuildReport:
    """Tests for summary counting and report status."""

    def test_counts_and_status(self) -> None:
        """Counts distinguish inspected, failed, skipped, and rejected entries."""
        request = _request(
            ["example.gov", "example.org", "example.net", "bad entry"]
        )
        scan = _scan([
            _result("https://example.gov"),
            _result(
                "https://example.org",
                overlays=[
                    {
                        "name": "UserWay",
                        "version": "1.2.3",
                        "evidence": "https://cdn.userway.org/widget.js",
                    }
                ],
            ),
            _result(
                "https://example.net",
                status="Timeout",
                outcome="timeout",
            ),
        ])
        report = render_issue_report.build_report(request, scan)
        assert report["scan"]["status"] == "completed"
        assert report["summary"] == {
            "submitted": 4,
            "accepted": 3,
            "rejected": 1,
            "duplicates": 0,
            "attempted": 3,
            "successful": 2,
            "failed": 1,
            "skipped": 0,
            "sites_with_overlays": 1,
        }

    def test_invalid_request_has_invalid_status(self) -> None:
        """A request with no accepted URLs is reported as invalid."""
        request = _request(["bad entry"])
        report = render_issue_report.build_report(
            request, _scan([])
        )
        assert report["scan"]["status"] == "invalid"
        assert report["summary"]["accepted"] == 0

    def test_non_200_is_not_reported_as_clean(self) -> None:
        """A non-2xx result cannot contribute an overlay finding."""
        request = _request(["example.gov"])
        scan = _scan([
            _result(
                "https://example.gov",
                status=403,
                outcome=OUTCOME_HTTP_ERROR,
                overlays=[{"name": "UserWay", "version": "", "evidence": ""}],
            )
        ])
        report = render_issue_report.build_report(request, scan)
        assert report["summary"]["successful"] == 0
        assert report["summary"]["sites_with_overlays"] == 0
        assert report["summary"]["failed"] == 1

    def test_skipped_result_is_counted(self) -> None:
        """A time-budget skip is separate from an attempted failure."""
        request = _request(["example.gov"])
        scan = _scan([
            _result(
                "https://example.gov",
                status="Skipped (time budget)",
                outcome="skipped",
            )
        ])
        report = render_issue_report.build_report(request, scan)
        assert report["summary"]["attempted"] == 0
        assert report["summary"]["skipped"] == 1

    def test_missing_scanner_result_is_a_failure(self) -> None:
        """Every accepted URL appears in the report, even if the scan omitted it."""
        request = _request(["example.gov"])
        report = render_issue_report.build_report(request, _scan([]))
        assert report["results"][0]["outcome"] == "error"
        assert report["summary"]["failed"] == 1


class TestHtmlReport:
    """Tests for accessible and safe HTML rendering."""

    def test_semantics_and_escaping(self, tmp_path: Path) -> None:
        """The report is a semantic, escaped document."""
        site = _site(tmp_path)
        request = _request(
            ["https://example.gov/?q=%3Cscript%3E"],
            title="SCAN: <script>alert(1)</script>",
        )
        scan = _scan([_result("https://example.gov/?q=%3Cscript%3E")])
        report = render_issue_report.write_report_artifact(
            request,
            scan,
            artifact_dir=tmp_path / "artifact",
            site_dir=site,
            pages_base_url="https://example.github.io/Find-Overlays",
        )
        html = (tmp_path / "artifact" / "issue-7" / "index.html").read_text()
        assert "<h1>" in html
        assert "<caption>" in html
        assert 'scope="col"' in html
        assert 'scope="row"' in html
        assert 'class="skip-link"' in html
        assert "<script>alert(1)</script>" not in html
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
        assert report["scan"]["timestamp"] == "2026-11-19T14-30-00Z"

    def test_evidence_link_text_contains_visible_label(self, tmp_path: Path) -> None:
        """Evidence links keep their visible text in the accessible name."""
        site = _site(tmp_path)
        request = _request(["example.gov"])
        scan = _scan([
            _result(
                "https://example.gov",
                overlays=[
                    {
                        "name": "UserWay",
                        "version": "1.2.3",
                        "evidence": "https://cdn.userway.org/widget.js",
                    }
                ],
            )
        ])
        render_issue_report.write_report_artifact(
            request,
            scan,
            artifact_dir=tmp_path / "artifact",
            site_dir=site,
        )
        html = (tmp_path / "artifact" / "issue-7" / "index.html").read_text()
        assert "Asset for UserWay" in html
        assert 'aria-label="Evidence for' not in html

    def test_rejected_entries_are_visible(self, tmp_path: Path) -> None:
        """Rejected entries appear with a readable reason."""
        site = _site(tmp_path)
        request = _request(["http://127.0.0.1"])
        scan = _scan([])
        render_issue_report.write_report_artifact(
            request,
            scan,
            artifact_dir=tmp_path / "artifact",
            site_dir=site,
        )
        html = (tmp_path / "artifact" / "issue-7" / "index.html").read_text()
        assert "Rejected" in html
        assert "127.0.0.1" in html


class TestReportFiles:
    """Tests for the stable and timestamped report files."""

    def test_stable_and_timestamped_files(self, tmp_path: Path) -> None:
        """The artifact contains stable files and a timestamped run directory."""
        site = _site(tmp_path)
        request = _request(["example.gov"])
        scan = _scan([_result("https://example.gov")])
        render_issue_report.write_report_artifact(
            request,
            scan,
            artifact_dir=tmp_path / "artifact",
            site_dir=site,
        )
        issue_dir = tmp_path / "artifact" / "issue-7"
        assert (issue_dir / "index.html").is_file()
        assert (issue_dir / "latest.json").is_file()
        assert (issue_dir / "results.csv").is_file()
        run_dir = issue_dir / "2026-11-19T14-30-00Z"
        assert (run_dir / "report.html").is_file()
        assert (run_dir / "report.json").is_file()
        assert (run_dir / "report.csv").is_file()

    def test_csv_matches_report_json(self, tmp_path: Path) -> None:
        """CSV rows and JSON results describe the same accepted URL."""
        site = _site(tmp_path)
        request = _request(["example.gov"])
        scan = _scan([_result("https://example.gov")])
        render_issue_report.write_report_artifact(
            request,
            scan,
            artifact_dir=tmp_path / "artifact",
            site_dir=site,
        )
        issue_dir = tmp_path / "artifact" / "issue-7"
        report = json.loads((issue_dir / "latest.json").read_text())
        rows = list(csv.DictReader(
            io.StringIO((issue_dir / "results.csv").read_text())
        ))
        assert len(rows) == len(report["results"])
        assert rows[0]["url"] == report["results"][0]["url"]
        assert rows[0]["outcome"] == report["results"][0]["outcome"]


class TestPublish:
    """Tests for publication into the site without touching monitoring data."""

    def test_publish_copies_report_and_builds_index(self, tmp_path: Path) -> None:
        """Publishing creates the issue report and the public index."""
        site = _site(tmp_path)
        monitoring = site / "data" / "latest.json"
        monitoring.parent.mkdir(parents=True)
        monitoring.write_text('{"sentinel": true}', encoding="utf-8")
        request = _request(["example.gov"])
        scan = _scan([_result("https://example.gov")])
        render_issue_report.write_report_artifact(
            request,
            scan,
            artifact_dir=tmp_path / "artifact",
            site_dir=site,
            pages_base_url="https://example.github.io/Find-Overlays",
        )
        result = render_issue_report.publish(
            request,
            artifact_dir=tmp_path / "artifact",
            site_dir=site,
            pages_base_url="https://example.github.io/Find-Overlays",
            comment_path=tmp_path / "comment.md",
        )
        assert result["changed"] is True
        assert result["close_issue"] is True
        assert (site / "reports" / "issues" / "issue-7" / "index.html").is_file()
        assert (site / "reports" / "index.html").is_file()
        assert (site / "reports" / "issues" / "issue-7" / "history.html").is_file()
        assert monitoring.read_text() == '{"sentinel": true}'
        comment = (tmp_path / "comment.md").read_text()
        assert "Overlay scan complete" in comment
        assert "reports/issues/issue-7/index.html" in comment

    def test_invalid_request_publishes_but_does_not_close(self, tmp_path: Path) -> None:
        """An invalid request gets a report and comment but stays open."""
        site = _site(tmp_path)
        request = _request(["bad entry"])
        scan = _scan([])
        render_issue_report.write_report_artifact(
            request,
            scan,
            artifact_dir=tmp_path / "artifact",
            site_dir=site,
            pages_base_url="https://example.github.io/Find-Overlays",
        )
        result = render_issue_report.publish(
            request,
            artifact_dir=tmp_path / "artifact",
            site_dir=site,
            pages_base_url="https://example.github.io/Find-Overlays",
            comment_path=tmp_path / "comment.md",
        )
        assert result["changed"] is True
        assert result["close_issue"] is False
        assert "No scan was run" in (tmp_path / "comment.md").read_text()

    def test_reuse_does_not_create_a_duplicate_report(self, tmp_path: Path) -> None:
        """A reused request writes a comment but no new site files."""
        site = _site(tmp_path)
        request = _request(["example.gov"])
        request["reuse"] = True
        request["previous_report_url"] = (
            "https://example.github.io/Find-Overlays/reports/issues/issue-7/index.html"
        )
        result = render_issue_report.publish(
            request,
            artifact_dir=tmp_path / "artifact",
            site_dir=site,
            pages_base_url="https://example.github.io/Find-Overlays",
            comment_path=tmp_path / "comment.md",
        )
        assert result["changed"] is False
        assert result["close_issue"] is True
        assert "No new scan needed" in (tmp_path / "comment.md").read_text()
        assert not (site / "reports" / "issues" / "issue-7").exists()


class TestIndex:
    """Tests for the public report index."""

    def test_index_lists_published_reports(self, tmp_path: Path) -> None:
        """Published issue reports appear in the index."""
        site = _site(tmp_path)
        for number, title in [(7, "SCAN: Seven"), (8, "SCAN: Eight")]:
            request = _request(["example.gov"], title=title, number=number)
            scan = _scan([_result("https://example.gov")])
            render_issue_report.write_report_artifact(
                request,
                scan,
                artifact_dir=tmp_path / f"artifact-{number}",
                site_dir=site,
                pages_base_url="https://example.github.io/Find-Overlays",
            )
            render_issue_report.publish(
                request,
                artifact_dir=tmp_path / f"artifact-{number}",
                site_dir=site,
                pages_base_url="https://example.github.io/Find-Overlays",
                comment_path=tmp_path / f"comment-{number}.md",
            )
        html = (site / "reports" / "index.html").read_text()
        assert "SCAN: Seven" in html
        assert "SCAN: Eight" in html
        assert "issues/issue-7/index.html" in html
        assert "issues/issue-8/index.html" in html

    def test_csv_formula_injection_is_neutralised(self, tmp_path: Path) -> None:
        """A rejected entry beginning with a formula marker is prefixed."""
        site = _site(tmp_path)
        request = _request(["=cmd|' /C calc'!A0"])
        render_issue_report.write_report_artifact(
            request,
            _scan([]),
            artifact_dir=tmp_path / "artifact",
            site_dir=site,
        )
        text = (tmp_path / "artifact" / "issue-7" / "results.csv").read_text()
        assert "'=cmd" in text


class TestIssueHistory:
    """Tests that repeated scans retain earlier timestamped reports."""

    def test_second_run_keeps_first_run(self, tmp_path: Path) -> None:
        """A newer run updates the stable page and retains the older report."""
        site = _site(tmp_path)
        request = _request(["example.gov"])
        first = _scan(
            [_result("https://example.gov")],
            generated_at="2026-11-19T14:30:00+00:00",
        )
        render_issue_report.write_report_artifact(
            request,
            first,
            artifact_dir=tmp_path / "artifact-1",
            site_dir=site,
        )
        render_issue_report.publish(
            request,
            artifact_dir=tmp_path / "artifact-1",
            site_dir=site,
            pages_base_url="https://example.github.io/Find-Overlays",
            comment_path=tmp_path / "comment-1.md",
        )
        second = _scan(
            [_result("https://example.gov")],
            generated_at="2026-11-19T15:30:00+00:00",
        )
        render_issue_report.write_report_artifact(
            request,
            second,
            artifact_dir=tmp_path / "artifact-2",
            site_dir=site,
        )
        render_issue_report.publish(
            request,
            artifact_dir=tmp_path / "artifact-2",
            site_dir=site,
            pages_base_url="https://example.github.io/Find-Overlays",
            comment_path=tmp_path / "comment-2.md",
        )
        issue_dir = site / "reports" / "issues" / "issue-7"
        assert (issue_dir / "2026-11-19T14-30-00Z" / "report.html").is_file()
        assert (issue_dir / "2026-11-19T15-30-00Z" / "report.html").is_file()
        history = (issue_dir / "history.html").read_text()
        assert "2026-11-19T14-30-00Z" in history
        assert "2026-11-19T15-30-00Z" in history


class TestIssue7Report:
    """Offline acceptance test for rendering the existing 100-domain request."""

    def test_all_100_targets_have_an_outcome(self, tmp_path: Path) -> None:
        """Every submitted target appears, including partial failures."""
        body = (Path(__file__).parent / "fixtures" / "issue-7-body.md").read_text(
            encoding="utf-8"
        )
        issue = issue_scan.IssueMetadata(
            7,
            "SCAN: UAE Government Sites",
            body,
            "https://github.com/mgifford/Find-Overlays/issues/7",
            "mgifford",
        )
        request = issue_scan.build_request(
            issue,
            issue_scan.extract_requested_urls(body),
            resolver=_public,
        )
        urls = request["accepted_urls"]
        results = [_result(url) for url in urls[:95]]
        results.extend(
            _result(url, status="Timeout", outcome="timeout")
            for url in urls[95:]
        )
        scan = _scan(results)
        site = _site(tmp_path)
        report = render_issue_report.write_report_artifact(
            request,
            scan,
            artifact_dir=tmp_path / "artifact",
            site_dir=site,
        )
        assert report["summary"]["submitted"] == 100
        assert report["summary"]["accepted"] == 100
        assert report["summary"]["attempted"] == 100
        assert report["summary"]["successful"] == 95
        assert report["summary"]["failed"] == 5
        assert len(report["results"]) == 100
        html = (tmp_path / "artifact" / "issue-7" / "index.html").read_text()
        assert html.count('<th scope="row">') == 100


class TestStatusLabels:
    """Tests for honest labels on incomplete scans."""

    def test_blocked_redirect_is_not_shown_as_302(self) -> None:
        """A blocked redirect is labelled as a block, not a success code."""
        label = render_issue_report._http_label({
            "outcome": "blocked_redirect",
            "http_status": 302,
        })
        assert label == "Blocked redirect"

    def test_unsupported_content_is_not_shown_as_200(self) -> None:
        """An unsupported body is labelled even though HTTP succeeded."""
        label = render_issue_report._http_label({
            "outcome": "unsupported_content",
            "http_status": 200,
        })
        assert label == "Unsupported content"

    def test_http_error_keeps_its_status(self) -> None:
        """A real HTTP error keeps the status code for diagnosis."""
        label = render_issue_report._http_label({
            "outcome": "http_error",
            "http_status": 404,
        })
        assert label == "404"


class TestSafeLinks:
    """Tests for URL handling in generated HTML."""

    def test_javascript_href_is_rejected(self) -> None:
        """Only HTTP(S) URLs become clickable links."""
        assert render_issue_report.safe_href("javascript:alert(1)") is None
        assert render_issue_report.safe_href("data:text/html,hello") is None
        assert render_issue_report.safe_href("https://example.gov/") is not None

    def test_display_url_strips_credentials(self) -> None:
        """A display label never exposes embedded credentials."""
        label = render_issue_report.display_url("https://user:pass@example.gov/a")
        assert "user" not in label
        assert "pass" not in label
        assert "example.gov" in label
