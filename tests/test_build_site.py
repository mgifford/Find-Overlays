"""Tests for site history and change detection."""

from scripts import build_site


def _result(url: str, overlays: list[dict], status: object = 200) -> dict:
    """Build a minimal scan result for tests."""
    return {"url": url, "status": status, "overlays": overlays}


class TestBuildEvents:
    """Tests for build_events."""

    def test_new_site_is_recorded(self) -> None:
        """A URL absent from the previous scan is a site_added event."""
        events = build_site.build_events({}, [_result("https://a.example", [])],
                                         "2026-11-09")
        assert events == [{
            "date": "2026-11-09",
            "url": "https://a.example",
            "kind": "site_added",
        }]

    def test_version_change_is_recorded(self) -> None:
        """A changed overlay version is a version_changed event."""
        previous = {
            "https://a.example": _result(
                "https://a.example",
                [{"name": "UserWay", "version": "1.0.0", "evidence": ""}],
            )
        }
        current = [
            _result(
                "https://a.example",
                [{"name": "UserWay", "version": "2.0.0", "evidence": ""}],
            )
        ]
        events = build_site.build_events(previous, current, "2026-11-09")
        assert events == [{
            "date": "2026-11-09",
            "url": "https://a.example",
            "kind": "version_changed",
            "overlay": "UserWay",
            "from": "1.0.0",
            "to": "2.0.0",
        }]

    def test_overlay_removal_is_recorded(self) -> None:
        """Removing an overlay from a successful scan is an event."""
        previous = {
            "https://a.example": _result(
                "https://a.example",
                [{"name": "AudioEye", "version": "", "evidence": ""}],
                status=200,
            )
        }
        current = [_result("https://a.example", [], status=200)]
        events = build_site.build_events(previous, current, "2026-11-09")
        assert events == [{
            "date": "2026-11-09",
            "url": "https://a.example",
            "kind": "overlay_removed",
            "overlay": "AudioEye",
            "from": "",
        }]

    def test_non_200_scan_does_not_imply_overlay_removal(self) -> None:
        """A blocked or failed scan should not look like an overlay removal."""
        previous = {
            "https://a.example": _result(
                "https://a.example",
                [{"name": "AudioEye", "version": "", "evidence": ""}],
                status=200,
            )
        }
        current = [_result("https://a.example", [], status=403)]
        events = build_site.build_events(previous, current, "2026-11-09")
        assert events == [{
            "date": "2026-11-09",
            "url": "https://a.example",
            "kind": "status_changed",
            "from": "200",
            "to": "403",
        }]


class TestMergeChanges:
    """Tests for merge_changes."""

    def test_duplicate_events_are_collapsed(self) -> None:
        """The same event should appear once when a day is rebuilt."""
        event = {
            "date": "2026-11-09",
            "url": "https://a.example",
            "kind": "site_added",
        }
        merged = build_site.merge_changes([event], [event])
        assert merged == [event]

    def test_existing_history_is_retained(self) -> None:
        """Older events should remain when a new day is added."""
        old = {
            "date": "2026-11-02",
            "url": "https://a.example",
            "kind": "site_added",
        }
        new = {
            "date": "2026-11-09",
            "url": "https://a.example",
            "kind": "site_added",
        }
        merged = build_site.merge_changes([old], [new])
        assert merged == [new, old]
