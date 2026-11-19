"""Tests for find-overlay.py.

Covers get_full_url, parse_raw_text, fetch_urls_from_source, scan_domain,
and generate_filename using pytest and unittest.mock.
"""

import importlib.util
import json
import sys
import textwrap
import types
from pathlib import Path
from unittest.mock import patch

import pytest

from overlay_safety import (
    OUTCOME_CONNECTION_ERROR,
    OUTCOME_HTTP_ERROR,
    OUTCOME_INSPECTED,
    OUTCOME_PRIVATE,
    OUTCOME_TIMEOUT,
    FetchResult,
)


# ---------------------------------------------------------------------------
# Import the module under test.
# The filename uses a hyphen, so we load it via importlib rather than the
# normal import statement.
# ---------------------------------------------------------------------------

def _load_module() -> types.ModuleType:
    """Load find-overlay.py as a module and return it."""
    repo_root = Path(__file__).parent.parent
    spec = importlib.util.spec_from_file_location(
        "find_overlay",
        repo_root / "find-overlay.py",
    )
    # spec_from_file_location returns None only when the path is not a valid
    # file; we know the path is correct so spec is guaranteed to be non-None.
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules["find_overlay"] = module
    # spec.loader is None only for namespace packages; file-based specs always
    # provide a loader with exec_module.
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


fo = _load_module()

get_full_url = fo.get_full_url
parse_raw_text = fo.parse_raw_text
fetch_urls_from_source = fo.fetch_urls_from_source
scan_domain = fo.scan_domain
generate_filename = fo.generate_filename
extract_version = fo.extract_version
detect_overlays = fo.detect_overlays
is_public_host = fo.is_public_host


# ===========================================================================
# get_full_url
# ===========================================================================

class TestGetFullUrl:
    """Tests for the get_full_url helper."""

    def test_plain_domain_gets_https(self) -> None:
        """A bare hostname should receive an https:// prefix."""
        assert get_full_url("example.com") == "https://example.com"

    def test_strips_leading_trailing_whitespace(self) -> None:
        """Whitespace around the input should be stripped."""
        assert get_full_url("  example.com  ") == "https://example.com"

    def test_lowercases_domain(self) -> None:
        """The domain should be lower-cased."""
        assert get_full_url("EXAMPLE.COM") == "https://example.com"

    def test_http_url_returned_as_is(self) -> None:
        """A URL that already starts with http should be returned unchanged."""
        assert get_full_url("http://example.com") == "http://example.com"

    def test_https_url_returned_as_is(self) -> None:
        """A URL that already starts with https should be returned unchanged."""
        assert get_full_url("https://example.com") == "https://example.com"

    def test_email_returns_none(self) -> None:
        """An email-like string (containing @) should return None."""
        assert get_full_url("user@example.com") is None

    def test_email_with_whitespace_returns_none(self) -> None:
        """Email with surrounding whitespace should still return None."""
        assert get_full_url("  admin@example.com  ") is None

    def test_subdomain_gets_https(self) -> None:
        """A subdomain hostname should also be prefixed with https://."""
        assert get_full_url("www.example.com") == "https://www.example.com"

    def test_mixed_case_http_url(self) -> None:
        """URLs beginning with HTTP (upper-case) should still be lowercased."""
        assert get_full_url("HTTP://example.com") == "http://example.com"


# ===========================================================================
# parse_raw_text
# ===========================================================================

class TestParseRawText:
    """Tests for the parse_raw_text helper."""

    def test_comma_separated_domains(self) -> None:
        """Comma-separated hostnames should each become an https URL."""
        result = parse_raw_text("example.com,test.org,another.net")
        assert "https://example.com" in result
        assert "https://test.org" in result
        assert "https://another.net" in result

    def test_newline_separated_domains(self) -> None:
        """Newline-separated hostnames should each become an https URL."""
        result = parse_raw_text("example.com\ntest.org\nanother.net")
        assert "https://example.com" in result
        assert "https://test.org" in result

    def test_whitespace_separated_domains(self) -> None:
        """Space-separated hostnames should each become an https URL."""
        result = parse_raw_text("example.com test.org another.net")
        assert "https://example.com" in result

    def test_existing_http_urls_kept_as_is(self) -> None:
        """Tokens that already start with http should not be double-prefixed."""
        result = parse_raw_text("http://example.com https://test.org")
        assert "http://example.com" in result
        assert "https://test.org" in result

    def test_email_addresses_are_excluded(self) -> None:
        """Tokens containing @ (email addresses) should be filtered out."""
        result = parse_raw_text("user@example.com test.org")
        urls = [u for u in result if "@" in u]
        assert urls == []
        assert "https://test.org" in result

    def test_short_tokens_are_excluded(self) -> None:
        """Tokens with 4 or fewer characters should be filtered out."""
        result = parse_raw_text("ab.c example.com")
        short = [u for u in result if "ab.c" in u]
        assert short == []

    def test_tokens_without_dot_are_excluded(self) -> None:
        """Tokens with no dot should be filtered out."""
        result = parse_raw_text("localhost example.com")
        no_dot = [u for u in result if "localhost" in u]
        assert no_dot == []

    def test_empty_string_returns_empty_list(self) -> None:
        """An empty string should return an empty list."""
        assert parse_raw_text("") == []

    def test_mixed_valid_and_invalid_tokens(self) -> None:
        """Only valid domain-like tokens should appear in the result."""
        result = parse_raw_text(
            "good.example.com,bad,short.c,user@mail.com,ok.net"
        )
        assert "https://good.example.com" in result
        assert "https://ok.net" in result
        assert len([u for u in result if "bad" in u]) == 0


# ===========================================================================
# extract_version / detect_overlays
# ===========================================================================

class TestVersionDetection:
    """Tests for best-effort overlay version extraction."""

    def test_query_string_version(self) -> None:
        """A ?v= query parameter should be read as the version."""
        assert extract_version("https://cdn.example.com/a.js?v=1.2.3") == "1.2.3"

    def test_path_version(self) -> None:
        """A /v2.0.1/ path segment should be read as the version."""
        assert extract_version(
            "https://cdn.example.com/v2.0.1/widget.js"
        ) == "2.0.1"

    def test_at_version(self) -> None:
        """An @4.5.6 package-style segment should be read as the version."""
        assert extract_version(
            "https://cdn.example.com/@4.5.6/widget.js"
        ) == "4.5.6"

    def test_filename_version(self) -> None:
        """A widget-3.4.5.min.js filename should be read as the version."""
        assert extract_version(
            "https://cdn.example.com/widget-3.4.5.min.js"
        ) == "3.4.5"

    def test_missing_version_returns_empty_string(self) -> None:
        """A URL without a version marker should return an empty string."""
        assert extract_version("https://cdn.example.com/widget.js") == ""

    def test_detect_overlays_includes_version_and_evidence(self) -> None:
        """Detected overlays should carry the version and asset URL."""
        html = '<script src="https://cdn.userway.org/widget.js?v=2.1.0"></script>'
        overlays = detect_overlays(html)
        userway = next(item for item in overlays if item["name"] == "UserWay")
        assert userway["version"] == "2.1.0"
        assert "cdn.userway.org" in userway["evidence"]

    def test_detect_overlays_without_version(self) -> None:
        """An unversioned asset should still be detected with a blank version."""
        html = '<script src="https://acsbapp.com/apps/app.js"></script>'
        overlays = detect_overlays(html)
        accessibe = next(
            item for item in overlays if item["name"] == "AccessiBe"
        )
        assert accessibe["version"] == ""


class TestPublicHost:
    """Tests for the public-address guard (literal addresses only)."""

    def test_loopback_is_not_public(self) -> None:
        """Loopback addresses are not public."""
        assert is_public_host("127.0.0.1") is False

    def test_private_address_is_not_public(self) -> None:
        """RFC 1918 addresses are not public."""
        assert is_public_host("10.0.0.1") is False

    def test_documentation_address_is_not_public(self) -> None:
        """Reserved documentation addresses are not public."""
        assert is_public_host("192.0.2.1") is False

    def test_public_literal_is_public(self) -> None:
        """A globally routable literal address is public."""
        assert is_public_host("8.8.8.8") is True


# ===========================================================================
# fetch_urls_from_source  (local file paths only – no live network)
# ===========================================================================

class TestFetchUrlsFromSource:
    """Tests for fetch_urls_from_source using temporary files."""

    def test_csv_with_domain_name_column(self, tmp_path: Path) -> None:
        """CSV with 'Domain name' header should produce https URLs."""
        csv_file = tmp_path / "domains.csv"
        csv_file.write_text("Domain name\nexample.com\ntest.org\n")
        result = fetch_urls_from_source(str(csv_file))
        assert "https://example.com" in result
        assert "https://test.org" in result

    def test_csv_with_lowercase_domain_column(self, tmp_path: Path) -> None:
        """CSV with 'domain' header (lower-case) should also be detected."""
        csv_file = tmp_path / "domains.csv"
        csv_file.write_text("domain\nexample.com\ntest.org\n")
        result = fetch_urls_from_source(str(csv_file))
        assert "https://example.com" in result

    def test_csv_skips_email_entries(self, tmp_path: Path) -> None:
        """Email rows in the domain column should be skipped."""
        csv_file = tmp_path / "domains.csv"
        csv_file.write_text(
            "Domain name\nexample.com\nuser@example.com\ntest.org\n"
        )
        result = fetch_urls_from_source(str(csv_file))
        assert all("@" not in u for u in result)
        assert "https://example.com" in result

    def test_xml_sitemap(self, tmp_path: Path) -> None:
        """XML sitemap with <loc> tags should have all URLs extracted."""
        xml_content = textwrap.dedent("""\
            <?xml version="1.0" encoding="UTF-8"?>
            <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
                <url><loc>https://example.com/page1</loc></url>
                <url><loc>https://example.com/page2</loc></url>
            </urlset>
        """)
        xml_file = tmp_path / "sitemap.xml"
        xml_file.write_text(xml_content)
        result = fetch_urls_from_source(str(xml_file))
        assert "https://example.com/page1" in result
        assert "https://example.com/page2" in result

    def test_xml_sitemap_without_namespace(self, tmp_path: Path) -> None:
        """XML sitemaps with no namespace should still parse <loc> tags."""
        xml_content = textwrap.dedent("""\
            <?xml version="1.0"?>
            <urlset>
                <url><loc>https://no-ns.example.com/</loc></url>
            </urlset>
        """)
        xml_file = tmp_path / "sitemap.xml"
        xml_file.write_text(xml_content)
        result = fetch_urls_from_source(str(xml_file))
        assert "https://no-ns.example.com/" in result

    def test_plain_text_file(self, tmp_path: Path) -> None:
        """A plain-text file of domains should produce https URLs."""
        txt_file = tmp_path / "domains.txt"
        txt_file.write_text("example.com\ntest.org\n")
        result = fetch_urls_from_source(str(txt_file))
        assert "https://example.com" in result
        assert "https://test.org" in result

    def test_file_not_found_returns_empty_list(self) -> None:
        """A missing local file should return an empty list (no exception)."""
        result = fetch_urls_from_source("/nonexistent/path/domains.csv")
        assert result == []

    def test_invalid_xml_returns_empty_list(self, tmp_path: Path) -> None:
        """Malformed XML should be caught and return an empty list."""
        bad_xml = tmp_path / "bad.xml"
        bad_xml.write_text("<?xml version='1.0'?><unclosed>")
        result = fetch_urls_from_source(str(bad_xml))
        assert result == []

    def test_xml_entity_expansion_is_rejected(self, tmp_path: Path) -> None:
        """XML entity declarations should be rejected, never expanded."""
        entity_xml = textwrap.dedent("""\
            <?xml version="1.0"?>
            <!DOCTYPE urlset [
                <!ENTITY lol "lol">
            ]>
            <urlset>
                <url><loc>https://example.com/&lol;</loc></url>
            </urlset>
        """)
        xml_file = tmp_path / "entity.xml"
        xml_file.write_text(entity_xml)
        result = fetch_urls_from_source(str(xml_file))
        assert result == []

    def test_remote_url_success(self) -> None:
        """A successful remote fetch should return parsed URLs."""
        fetched = FetchResult(
            "https://remote.example.com/list",
            "https://remote.example.com/list",
            200,
            OUTCOME_INSPECTED,
            body="example.com\ntest.org\n",
            content_type="text/plain",
        )
        with patch.object(fo, "safe_fetch", return_value=fetched):
            result = fetch_urls_from_source("https://remote.example.com/list")
        assert "https://example.com" in result

    def test_remote_url_error_returns_empty_list(self) -> None:
        """A failed remote fetch should return an empty list (no exception)."""
        fetched = FetchResult(
            "https://remote.example.com/list",
            "https://remote.example.com/list",
            None,
            OUTCOME_TIMEOUT,
            error="connection or read timed out",
        )
        with patch.object(fo, "safe_fetch", return_value=fetched):
            result = fetch_urls_from_source("https://remote.example.com/list")
        assert result == []

    def test_remote_csv_with_domain_column(self) -> None:
        """A remote CSV containing 'domain' should be parsed like a local CSV."""
        csv_body = "domain\nexample.com\ntest.org\n"
        fetched = FetchResult(
            "https://remote.example.com/list.csv",
            "https://remote.example.com/list.csv",
            200,
            OUTCOME_INSPECTED,
            body=csv_body,
            content_type="text/csv",
        )
        with patch.object(fo, "safe_fetch", return_value=fetched):
            result = fetch_urls_from_source("https://remote.example.com/list.csv")
        assert "https://example.com" in result

    def test_csv_no_recognizable_domain_column_falls_back_to_raw(
        self, tmp_path: Path
    ) -> None:
        """CSV with 'domain_type' column falls back to raw parse.

        The column 'domain_type' contains both 'domain' and 'type', so the
        column-detection filter skips it; the code falls through to
        parse_raw_text.
        """
        csv_file = tmp_path / "domains.csv"
        csv_file.write_text("domain_type\nexample.com\ntest.org\n")
        result = fetch_urls_from_source(str(csv_file))
        # parse_raw_text still extracts the hostnames from the raw content
        assert "https://example.com" in result

    def test_csv_error_falls_back_to_raw_parse(self, tmp_path: Path) -> None:
        """When csv.DictReader raises csv.Error the content is parsed as raw text."""
        import csv as csv_mod

        csv_file = tmp_path / "domains.csv"
        csv_file.write_text("domain\nexample.com\n")
        with patch.object(csv_mod, "DictReader", side_effect=csv_mod.Error("bad")):
            result = fetch_urls_from_source(str(csv_file))
        # Raw text parsing should still find 'example.com'
        assert "https://example.com" in result


# ===========================================================================
# scan_domain
# ===========================================================================

class TestScanDomain:
    """Tests for scan_domain using mocked fetch results."""

    def _make_fetch(
        self,
        html: str = "",
        status_code: int = 200,
        outcome: str = OUTCOME_INSPECTED,
        error: str = "",
        content_type: str = "text/html",
    ) -> FetchResult:
        """Build a FetchResult for a mocked network call."""
        return FetchResult(
            "https://example.com",
            "https://example.com",
            status_code,
            outcome,
            body=html if outcome == OUTCOME_INSPECTED else "",
            content_type=content_type,
            error=error,
        )

    def _scan(self, fetched: FetchResult) -> dict:
        """Run scan_domain with safe_fetch patched to return *fetched*."""
        with patch.object(fo, "safe_fetch", return_value=fetched):
            return scan_domain("https://example.com")

    def test_no_overlay_detected(self) -> None:
        """A page with no overlay signatures returns 'None Found'."""
        result = self._scan(self._make_fetch("<html><body>Hello World</body></html>"))
        assert result["detected_overlay"] == "None Found"
        assert result["status"] == 200

    def test_single_overlay_detected(self) -> None:
        """A page containing an AccessiBe signature reports that overlay."""
        html = '<script src="https://acsbapp.com/apps/app/dist/js/app.js"></script>'
        result = self._scan(self._make_fetch(html))
        assert "AccessiBe" in result["detected_overlay"]
        assert result["status"] == 200

    def test_userway_overlay_detected(self) -> None:
        """A page containing a UserWay signature is detected."""
        html = '<script src="https://cdn.userway.org/widget.js"></script>'
        result = self._scan(self._make_fetch(html))
        assert "UserWay" in result["detected_overlay"]

    def test_audioeye_overlay_detected(self) -> None:
        """A page containing an AudioEye signature is detected."""
        html = '<script src="https://webar.audioeye.com/ae.js"></script>'
        result = self._scan(self._make_fetch(html))
        assert "AudioEye" in result["detected_overlay"]

    def test_multiple_overlays_detected(self) -> None:
        """A page with multiple overlay signatures lists all of them."""
        html = (
            '<script src="https://acsbapp.com/app.js"></script>'
            '<script src="https://cdn.userway.org/widget.js"></script>'
        )
        result = self._scan(self._make_fetch(html))
        assert "AccessiBe" in result["detected_overlay"]
        assert "UserWay" in result["detected_overlay"]

    def test_other_widget_detected(self) -> None:
        """Third-party widgets (e.g. Intercom) are captured in other_widgets."""
        html = '<script src="https://widget.intercom.io/widget.js"></script>'
        result = self._scan(self._make_fetch(html))
        assert "Intercom" in result["other_widgets"]
        assert result["detected_overlay"] == "None Found"

    def test_zendesk_widget_detected(self) -> None:
        """Zendesk widget signatures are captured in other_widgets."""
        html = (
            '<script src="https://static.zdassets.com/web_widget/latest/lc.js">'
            "</script>"
        )
        result = self._scan(self._make_fetch(html))
        assert "Zendesk" in result["other_widgets"]

    def test_overlay_detection_is_case_insensitive(self) -> None:
        """Overlay detection should match regardless of HTML capitalisation."""
        html = "<SCRIPT SRC='HTTPS://ACSBAPP.COM/APP.JS'></SCRIPT>"
        result = self._scan(self._make_fetch(html))
        assert "AccessiBe" in result["detected_overlay"]

    def test_timeout_returns_timeout_status(self) -> None:
        """A timeout should result in status='Timeout'."""
        result = self._scan(
            self._make_fetch(
                status_code=None,
                outcome=OUTCOME_TIMEOUT,
                error="connection or read timed out",
            )
        )
        assert result["status"] == "Timeout"
        assert result["detected_overlay"] == "Not determined"

    def test_connection_error_returns_conn_error_status(self) -> None:
        """A connection failure should produce status='Conn Error'."""
        result = self._scan(
            self._make_fetch(
                status_code=None,
                outcome=OUTCOME_CONNECTION_ERROR,
                error="connection failed",
            )
        )
        assert result["status"] == "Conn Error"

    def test_unexpected_exception_returns_error_status(self) -> None:
        """An unexpected exception should produce status='Error'."""
        with patch.object(fo, "safe_fetch", side_effect=RuntimeError("boom")):
            result = scan_domain("https://example.com")
        assert result["status"] == "Error"
        assert "boom" in result["error"]

    def test_non_public_host_is_blocked(self) -> None:
        """A private address should be blocked by the shared fetch layer."""
        fetched = self._make_fetch(
            status_code=None,
            outcome=OUTCOME_PRIVATE,
            error="private destination",
        )
        result = self._scan(fetched)
        assert result["status"] == "Blocked"
        assert result["outcome"] == OUTCOME_PRIVATE

    def test_non_200_status_code_is_recorded(self) -> None:
        """A 404 response status code should be stored in result['status']."""
        result = self._scan(
            self._make_fetch(
                html="<html>overlay acsbapp</html>",
                status_code=404,
                outcome=OUTCOME_HTTP_ERROR,
                error="HTTP 404",
            )
        )
        assert result["status"] == 404
        assert result["overlays"] == []
        assert result["detected_overlay"] == "Not determined"

    def test_result_contains_correct_url(self) -> None:
        """The result dict should carry back the requested URL."""
        fetched = self._make_fetch("<html></html>")
        with patch.object(fo, "safe_fetch", return_value=fetched):
            result = scan_domain("https://my-site.example.com")
        assert result["url"] == "https://my-site.example.com"

    def test_equalweb_nagishli_signature(self) -> None:
        """EqualWeb is also identified by the 'nagishli' signature."""
        html = '<script src="https://cdn.nagishli.com/app/widget.js"></script>'
        result = self._scan(self._make_fetch(html))
        assert "EqualWeb" in result["detected_overlay"]


# ===========================================================================
# generate_filename
# ===========================================================================

class TestGenerateFilename:
    """Tests for generate_filename."""

    def test_local_csv_file(self) -> None:
        """A local .csv source should embed the base name in the output file."""
        name = generate_filename("my-domains.csv")
        assert name.startswith("my-domains-overlays-")
        assert name.endswith(".csv")

    def test_local_xml_file(self) -> None:
        """A local .xml source should embed the base name without extension."""
        name = generate_filename("sitemap.xml")
        assert name.startswith("sitemap-overlays-")
        assert name.endswith(".csv")

    def test_remote_url(self) -> None:
        """A remote http URL source should produce a 'web-scan' filename."""
        name = generate_filename("https://example.com/sitemap.xml")
        assert name.startswith("web-scan-overlays-")
        assert name.endswith(".csv")

    def test_filename_contains_today_date(self) -> None:
        """The filename should contain today's date in YYYY-MM-DD format."""
        from datetime import date

        today = date.today().strftime("%Y-%m-%d")
        name = generate_filename("domains.csv")
        assert today in name

    def test_file_with_path_prefix(self) -> None:
        """Only the base filename (not the directory) should appear in output."""
        name = generate_filename("/some/path/to/data.csv")
        assert "data-overlays-" in name
        assert "/some/path" not in name

    def test_remote_http_url(self) -> None:
        """An http:// (non-https) remote URL should also produce web-scan prefix."""
        name = generate_filename("http://example.com/list")
        assert name.startswith("web-scan-overlays-")


# ===========================================================================
# main() – integration-level smoke test
# ===========================================================================

class TestMain:
    """Smoke tests for the main() entry point."""

    def _run_main(
        self,
        tmp_path: Path,
        extra_args: list[str] | None = None,
    ) -> None:
        """Helper: write a tiny CSV, patch sys.argv, run main(), clean up."""
        csv_file = tmp_path / "smoke.csv"
        csv_file.write_text("Domain name\nexample.com\n")

        scan_result = {
            "url": "https://example.com",
            "status": 200,
            "detected_overlay": "None Found",
            "other_widgets": "",
        }
        argv = ["find-overlay.py", str(csv_file)] + (extra_args or [])
        with (
            patch("sys.argv", argv),
            patch.object(fo, "scan_domain", return_value=scan_result),
        ):
            fo.main()

    def test_main_runs_without_error(self, tmp_path: Path) -> None:
        """main() should complete without raising an exception."""
        self._run_main(tmp_path, ["--no-csv"])

    def test_main_creates_csv_file(self, tmp_path: Path) -> None:
        """Without --no-csv, main() should write an output CSV file."""
        csv_file = tmp_path / "smoke.csv"
        csv_file.write_text("Domain name\nexample.com\n")

        scan_result = {
            "url": "https://example.com",
            "status": 200,
            "detected_overlay": "None Found",
            "other_widgets": "",
        }
        output_file = tmp_path / "out.csv"
        argv = [
            "find-overlay.py", str(csv_file),
            "--output", str(output_file),
        ]
        with (
            patch("sys.argv", argv),
            patch.object(fo, "scan_domain", return_value=scan_result),
        ):
            fo.main()
        assert output_file.exists()

    def test_main_writes_json_output(self, tmp_path: Path) -> None:
        """--json should write results and metadata to the requested file."""
        csv_file = tmp_path / "smoke.csv"
        csv_file.write_text("Domain name\nexample.com\n")
        json_file = tmp_path / "scan.json"
        scan_result = {
            "url": "https://example.com",
            "final_url": "https://example.com",
            "status": 200,
            "detected_overlay": "UserWay",
            "overlays": [
                {
                    "name": "UserWay",
                    "version": "2.1.0",
                    "evidence": "https://cdn.userway.org/widget.js?v=2.1.0",
                }
            ],
            "overlay_versions": "UserWay 2.1.0",
            "other_widgets": "",
        }
        with (
            patch(
                "sys.argv",
                [
                    "find-overlay.py",
                    str(csv_file),
                    "--json",
                    str(json_file),
                    "--no-csv",
                ],
            ),
            patch.object(fo, "scan_domain", return_value=scan_result),
        ):
            fo.main()

        payload = json.loads(json_file.read_text())
        assert payload["results"][0]["url"] == "https://example.com"
        assert payload["results"][0]["overlays"][0]["version"] == "2.1.0"

    def test_main_csv_includes_overlay_versions(self, tmp_path: Path) -> None:
        """The default CSV should include the overlay_versions column."""
        csv_file = tmp_path / "smoke.csv"
        csv_file.write_text("Domain name\nexample.com\n")
        output_file = tmp_path / "versions.csv"
        scan_result = {
            "url": "https://example.com",
            "final_url": "https://example.com",
            "status": 200,
            "detected_overlay": "UserWay",
            "overlays": [
                {
                    "name": "UserWay",
                    "version": "2.1.0",
                    "evidence": "https://cdn.userway.org/widget.js?v=2.1.0",
                }
            ],
            "overlay_versions": "UserWay 2.1.0",
            "other_widgets": "",
        }
        with (
            patch(
                "sys.argv",
                [
                    "find-overlay.py",
                    str(csv_file),
                    "--output",
                    str(output_file),
                ],
            ),
            patch.object(fo, "scan_domain", return_value=scan_result),
        ):
            fo.main()

        with open(output_file) as handle:
            header = handle.readline().strip()
        assert header == (
            "url,status,detected_overlay,overlay_versions,other_widgets"
        )

    def test_main_simple_flag(self, tmp_path: Path) -> None:
        """--simple flag should produce a CSV with only url/overlay columns."""
        csv_file = tmp_path / "smoke.csv"
        csv_file.write_text("Domain name\nexample.com\n")

        scan_result = {
            "url": "https://example.com",
            "status": 200,
            "detected_overlay": "None Found",
            "other_widgets": "",
        }
        output_file = tmp_path / "simple-out.csv"
        with (
            patch(
                "sys.argv",
                [
                    "find-overlay.py",
                    str(csv_file),
                    "--simple",
                    "--output",
                    str(output_file),
                ],
            ),
            patch.object(fo, "scan_domain", return_value=scan_result),
        ):
            fo.main()

        with open(output_file) as f:
            header = f.readline().strip()
        assert header == "url,detected_overlay"

    def test_main_limit_flag(self, tmp_path: Path) -> None:
        """--limit 1 should scan at most one URL."""
        csv_file = tmp_path / "smoke.csv"
        csv_file.write_text("Domain name\nexample.com\ntest.org\n")

        scan_result = {
            "url": "https://example.com",
            "status": 200,
            "detected_overlay": "None Found",
            "other_widgets": "",
        }
        with (
            patch(
                "sys.argv",
                ["find-overlay.py", str(csv_file), "--limit", "1", "--no-csv"],
            ),
            patch.object(fo, "scan_domain", return_value=scan_result) as mock_scan,
        ):
            fo.main()
        assert mock_scan.call_count == 1

    def test_main_with_overlay_detected(self, tmp_path: Path) -> None:
        """main() should correctly tally overlay counts in summary stats."""
        csv_file = tmp_path / "smoke.csv"
        csv_file.write_text("Domain name\nexample.com\n")

        scan_result = {
            "url": "https://example.com",
            "status": 200,
            "detected_overlay": "AccessiBe",
            "other_widgets": "",
        }
        with (
            patch("sys.argv", ["find-overlay.py", str(csv_file), "--no-csv"]),
            patch.object(fo, "scan_domain", return_value=scan_result),
            patch("builtins.print") as mock_print,
        ):
            fo.main()

        printed = " ".join(str(c.args) for c in mock_print.call_args_list)
        assert "AccessiBe" in printed

    def test_main_interactive_input(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When sys.argv has no arguments, main() prompts for a source path."""
        monkeypatch.chdir(tmp_path)
        csv_file = tmp_path / "smoke.csv"
        csv_file.write_text("Domain name\nexample.com\n")

        scan_result = {
            "url": "https://example.com",
            "status": 200,
            "detected_overlay": "None Found",
            "other_widgets": "",
        }
        # Simulate running with just the script name (no positional arg)
        with (
            patch("sys.argv", ["find-overlay.py"]),
            patch("builtins.input", return_value=str(csv_file)),
            patch.object(fo, "scan_domain", return_value=scan_result),
        ):
            fo.main()

    def test_main_csv_write_failure_exits_nonzero(self, tmp_path: Path) -> None:
        """A CSV write failure should leave the process with a failure status."""
        csv_file = tmp_path / "smoke.csv"
        csv_file.write_text("Domain name\nexample.com\n")

        scan_result = {
            "url": "https://example.com",
            "status": 200,
            "detected_overlay": "None Found",
            "other_widgets": "",
        }
        with (
            patch(
                "sys.argv",
                ["find-overlay.py", str(csv_file), "--output", "/no/perm/out.csv"],
            ),
            patch.object(fo, "scan_domain", return_value=scan_result),
            patch.object(fo, "write_csv_results", return_value=False),
        ):
            with pytest.raises(SystemExit) as exc_info:
                fo.main()

        assert exc_info.value.code == 1

    def test_main_limit_uses_input_order(self, tmp_path: Path) -> None:
        """--limit should select the first URLs in the source, not sorted ones."""
        source = tmp_path / "ordered.txt"
        source.write_text("zeta.example\nalpha.example\n")
        seen: list[str] = []

        def fake_scan(url: str) -> dict:
            """Record the URL and return a minimal scan result."""
            seen.append(url)
            return {
                "url": url,
                "status": 200,
                "detected_overlay": "None Found",
                "other_widgets": "",
            }

        with (
            patch(
                "sys.argv",
                ["find-overlay.py", str(source), "--limit", "1", "--no-csv"],
            ),
            patch.object(fo, "scan_domain", side_effect=fake_scan),
        ):
            fo.main()

        assert seen == ["https://zeta.example"]

    def test_main_empty_source_exits_nonzero(self, tmp_path: Path) -> None:
        """An input list with no usable URLs fails instead of writing empty output."""
        empty = tmp_path / "empty.txt"
        empty.write_text("\n\n", encoding="utf-8")
        with patch("sys.argv", ["find-overlay.py", str(empty), "--no-csv"]):
            with pytest.raises(SystemExit) as exc_info:
                fo.main()
        assert exc_info.value.code == 1
