"""Tests for the issue-form submission validator."""

from scripts import queue_submission


class TestExtractUrls:
    """Tests for extracting the Site URLs section."""

    def test_extracts_urls_from_section(self) -> None:
        """Only URLs under the Site URLs heading should be returned."""
        body = (
            "### Site URLs\n"
            "https://8.8.8.8\n"
            "https://1.1.1.1\n"
            "\n### Context\n"
            "See https://overlayfactsheet.com/\n"
        )
        assert queue_submission.extract_urls(body) == [
            "https://8.8.8.8",
            "https://1.1.1.1",
        ]

    def test_missing_section_returns_empty_list(self) -> None:
        """A body without the expected heading should yield no URLs."""
        assert queue_submission.extract_urls("https://8.8.8.8") == []


class TestNormaliseUrl:
    """Tests for URL normalisation and public-host checks."""

    def test_adds_https_to_bare_host(self) -> None:
        """A bare public IP should receive an https scheme."""
        assert queue_submission.normalise_url("8.8.8.8") == "https://8.8.8.8"

    def test_rejects_loopback(self) -> None:
        """Loopback addresses must not be queued."""
        assert queue_submission.normalise_url("http://127.0.0.1/") is None

    def test_rejects_private_address(self) -> None:
        """Private addresses must not be queued."""
        assert queue_submission.normalise_url("http://10.0.0.1/") is None

    def test_rejects_credentials(self) -> None:
        """URLs with embedded credentials must not be queued."""
        assert queue_submission.normalise_url(
            "https://user:pass@8.8.8.8/"
        ) is None

    def test_public_literal_is_allowed(self) -> None:
        """A globally routable literal address is allowed."""
        assert queue_submission.is_public_host("8.8.8.8") is True

    def test_documentation_range_is_rejected(self) -> None:
        """Reserved documentation addresses are not globally routable."""
        assert queue_submission.is_public_host("192.0.2.1") is False
