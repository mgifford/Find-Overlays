"""Tests for GitHub issue request parsing, validation, and idempotency."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from overlay_safety import TargetValidation
from scripts import issue_scan


FIXTURES = Path(__file__).parent / "fixtures"


def _public(target) -> TargetValidation:
    """Return a public validation for most test targets."""
    if target.host in {"10.0.0.1", "127.0.0.1"}:
        return TargetValidation(target, "private", "private destination")
    return TargetValidation(target, "public", "", ("93.184.216.34",))


def _dns_failure(target) -> TargetValidation:
    """Return a DNS failure validation."""
    return TargetValidation(target, "dns_failure", "DNS resolution failed")


def _issue_event(
    body: str,
    *,
    action: str = "opened",
    title: str = "SCAN: Test",
    number: int = 7,
) -> dict:
    """Build a minimal GitHub issues event payload."""
    return {
        "action": action,
        "issue": {
            "number": number,
            "title": title,
            "body": body,
            "state": "open",
            "html_url": f"https://github.com/mgifford/Find-Overlays/issues/{number}",
            "user": {"login": "alice"},
        },
    }


class TestExtractRequestedUrls:
    """Tests for extracting the URL section from issue Markdown."""

    def test_existing_uae_issue_format(self) -> None:
        """The body from issue #7 yields all 100 bare domains."""
        body = (FIXTURES / "issue-7-body.md").read_text(encoding="utf-8")
        entries = issue_scan.extract_requested_urls(body)
        assert len(entries) == 100
        assert entries[0] == "u.ae"
        assert entries[-1] == "uaqpolice.gov.ae"

    def test_standard_url_section_with_bullets(self) -> None:
        """Markdown bullets and the URLs heading are both accepted."""
        body = (
            "### URLs\n\n"
            "- https://example.org\n"
            "- example.gov\n"
        )
        assert issue_scan.extract_requested_urls(body) == [
            "https://example.org",
            "example.gov",
        ]

    def test_ignores_urls_outside_the_section(self) -> None:
        """A URL in Additional context is not treated as a scan target."""
        body = (
            "### Site URLs\n\nexample.gov\n\n"
            "### Additional context\n\n"
            "See https://overlayfactsheet.com/ for background.\n"
        )
        assert issue_scan.extract_requested_urls(body) == ["example.gov"]

    def test_blank_lines_and_markdown_links(self) -> None:
        """Blank lines are ignored and Markdown links unwrap to their URL."""
        body = (
            "### Site URLs\n\n"
            "\n"
            "[Example](https://example.org/about)\n"
            "`example.gov`\n"
        )
        assert issue_scan.extract_requested_urls(body) == [
            "https://example.org/about",
            "example.gov",
        ]

    def test_missing_section_returns_empty(self) -> None:
        """An issue without a recognised section yields nothing."""
        assert issue_scan.extract_requested_urls("### Context\n\nexample.gov") == []


class TestBuildRequest:
    """Tests for normalization, deduplication, limits, and reasons."""

    def test_bare_domains_and_paths_are_normalised(self) -> None:
        """Bare domains get HTTPS and paths are preserved."""
        issue = issue_scan.IssueMetadata(
            7, "SCAN: Test", "", "https://github.com/x/y/issues/7", "alice"
        )
        request = issue_scan.build_request(
            issue,
            ["example.gov", "example.gov/about?x=1", "https://example.org"],
            resolver=_public,
        )
        assert request["accepted_urls"] == [
            "https://example.gov",
            "https://example.gov/about?x=1",
            "https://example.org",
        ]
        assert request["summary"] == {
            "submitted": 3,
            "accepted": 3,
            "rejected": 0,
            "duplicates": 0,
        }

    def test_invalid_private_and_duplicate_entries(self) -> None:
        """Invalid, private, and duplicate entries are counted separately."""
        issue = issue_scan.IssueMetadata(
            7, "SCAN: Test", "", "https://github.com/x/y/issues/7", "alice"
        )
        request = issue_scan.build_request(
            issue,
            [
                "example.gov",
                "EXAMPLE.GOV",
                "not a host",
                "ftp://example.org",
                "10.0.0.1",
            ],
            resolver=_public,
        )
        assert request["accepted_urls"] == ["https://example.gov"]
        assert request["summary"]["submitted"] == 5
        assert request["summary"]["accepted"] == 1
        assert request["summary"]["rejected"] == 3
        assert request["summary"]["duplicates"] == 1
        reasons = {item["reason"] for item in request["rejected_urls"]}
        assert "unsupported protocol" in reasons
        assert "private destination" in reasons

    def test_dns_failure_is_accepted_for_scanner_reporting(self) -> None:
        """A valid but unresolvable host is a scan failure, not a rejection."""
        issue = issue_scan.IssueMetadata(
            7, "SCAN: Test", "", "https://github.com/x/y/issues/7", "alice"
        )
        request = issue_scan.build_request(
            issue, ["missing.example"], resolver=_dns_failure
        )
        assert request["accepted_urls"] == ["https://missing.example"]
        assert request["accepted"][0]["validation"] == "dns_failure"

    def test_more_than_one_hundred_distinct_urls(self) -> None:
        """Only the first 100 distinct URLs are accepted and extras are named."""
        issue = issue_scan.IssueMetadata(
            7, "SCAN: Test", "", "https://github.com/x/y/issues/7", "alice"
        )
        entries = [f"site{i}.example" for i in range(105)]
        request = issue_scan.build_request(issue, entries, resolver=_public)
        assert request["summary"]["accepted"] == 100
        assert request["summary"]["rejected"] == 5
        assert len(request["accepted_urls"]) == 100
        assert request["rejected_urls"][0]["stage"] == "limit"

    def test_idn_hostname_is_punycoded(self) -> None:
        """Unicode hostnames use an explicit punycode policy."""
        issue = issue_scan.IssueMetadata(
            7, "SCAN: Test", "", "https://github.com/x/y/issues/7", "alice"
        )
        request = issue_scan.build_request(
            issue, ["münich.de"], resolver=_public
        )
        assert request["accepted_urls"] == ["https://xn--mnich-kva.de"]

    def test_fingerprint_is_order_independent(self) -> None:
        """The same URL set in a different order has the same fingerprint."""
        first = issue_scan.request_fingerprint(
            7, ["https://a.example", "https://b.example"]
        )
        second = issue_scan.request_fingerprint(
            7, ["https://b.example", "https://a.example"]
        )
        assert first == second

    def test_fingerprint_changes_with_issue_or_url(self) -> None:
        """A different issue or URL set changes the fingerprint."""
        base = issue_scan.request_fingerprint(7, ["https://a.example"])
        assert base != issue_scan.request_fingerprint(8, ["https://a.example"])
        assert base != issue_scan.request_fingerprint(7, ["https://b.example"])


class TestPrepare:
    """Tests for idempotency and the prepare CLI helper."""

    def _previous(
        self,
        request: dict,
        status: str = "completed",
    ) -> dict:
        """Turn a request into a published latest.json-like record."""
        previous = dict(request)
        previous["scan"] = {
            "started_at": "2026-11-19T14:00:00+00:00",
            "completed_at": "2026-11-19T14:10:00+00:00",
            "status": status,
            "source": "github-issue",
        }
        previous["issue"] = dict(previous["issue"])
        previous["issue"]["report_url"] = (
            "https://example.github.io/Find-Overlays/reports/issues/issue-7/index.html"
        )
        return previous

    def test_unchanged_edit_reuses_completed_report(self, tmp_path: Path) -> None:
        """An edited issue with the same URLs reuses the completed report."""
        body = "### Site URLs\n\nexample.gov\n"
        event = _issue_event(body, action="edited")
        request = issue_scan.prepare(event, resolver=_public)
        previous_path = tmp_path / "latest.json"
        previous_path.write_text(
            json.dumps(self._previous(request)), encoding="utf-8"
        )
        result = issue_scan.prepare(
            event, previous_path, resolver=_public
        )
        assert result["reuse"] is True
        assert result["status"] == "reused"
        assert "example.github.io" in result["previous_report_url"]

    def test_reopened_issue_forces_rescan(self, tmp_path: Path) -> None:
        """Reopening the issue always permits a fresh scan."""
        body = "### Site URLs\n\nexample.gov\n"
        event = _issue_event(body, action="reopened")
        request = issue_scan.prepare(event, resolver=_public)
        previous_path = tmp_path / "latest.json"
        previous_path.write_text(
            json.dumps(self._previous(request)), encoding="utf-8"
        )
        result = issue_scan.prepare(event, previous_path, resolver=_public)
        assert result["reuse"] is False

    def test_changed_urls_do_not_reuse(self, tmp_path: Path) -> None:
        """A changed URL set does not reuse the previous report."""
        old_event = _issue_event(
            "### Site URLs\n\nexample.gov\n", action="edited"
        )
        old_request = issue_scan.prepare(old_event, resolver=_public)
        previous_path = tmp_path / "latest.json"
        previous_path.write_text(
            json.dumps(self._previous(old_request)), encoding="utf-8"
        )
        new_event = _issue_event(
            "### Site URLs\n\nexample.org\n", action="edited"
        )
        result = issue_scan.prepare(new_event, previous_path, resolver=_public)
        assert result["reuse"] is False

    def test_previous_invalid_report_does_not_reuse(self, tmp_path: Path) -> None:
        """An invalid previous report is not reused."""
        event = _issue_event(
            "### Site URLs\n\nexample.gov\n", action="edited"
        )
        request = issue_scan.prepare(event, resolver=_public)
        previous_path = tmp_path / "latest.json"
        previous_path.write_text(
            json.dumps(self._previous(request, status="invalid")),
            encoding="utf-8",
        )
        result = issue_scan.prepare(event, previous_path, resolver=_public)
        assert result["reuse"] is False


class TestIssueMetadata:
    """Tests for the event metadata helper."""

    def test_from_event(self) -> None:
        """Issue metadata is read from the event payload."""
        metadata = issue_scan.IssueMetadata.from_event(_issue_event("body"))
        assert metadata.number == 7
        assert metadata.title == "SCAN: Test"
        assert metadata.author == "alice"

    def test_missing_issue_number_raises(self) -> None:
        """An event without an issue number is rejected."""
        with pytest.raises(ValueError):
            issue_scan.IssueMetadata.from_event({"issue": {}})

    def test_empty_body_is_valid_but_invalid_request(self) -> None:
        """An empty body produces an invalid request rather than a crash."""
        event = _issue_event("")
        request = issue_scan.prepare(event, resolver=_public)
        assert request["status"] == "invalid"
        assert request["accepted_urls"] == []


class TestIssue7Acceptance:
    """Offline acceptance tests for the format used by issue #7."""

    def test_all_100_domains_are_accepted(self) -> None:
        """The existing issue body yields 100 HTTPS targets without rewriting."""
        body = (FIXTURES / "issue-7-body.md").read_text(encoding="utf-8")
        event = _issue_event(body, title="SCAN: UAE Government Sites")
        request = issue_scan.prepare(event, resolver=_public)
        assert request["summary"]["submitted"] == 100
        assert request["summary"]["accepted"] == 100
        assert request["summary"]["rejected"] == 0
        assert len(request["accepted_urls"]) == 100
        assert all(url.startswith("https://") for url in request["accepted_urls"])
