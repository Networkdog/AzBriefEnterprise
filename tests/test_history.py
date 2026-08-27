"""Tests for retirement tracking and countdown ordering in src/agent/history.py."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from src.agent import history


def _entry(update_id: str, days_offset: int | None) -> dict:
    """Build a tracker entry whose retirement_date is now+days_offset (or undated)."""
    if days_offset is None:
        rd = ""
    else:
        rd = (datetime.now(timezone.utc) + timedelta(days=days_offset)).strftime("%Y-%m-%d")
    return {"update_id": update_id, "title": update_id, "retirement_date": rd}


class TestRetirementCountdownOrdering:
    """Overdue retirements must lead; undated ones trail."""

    def test_overdue_first_then_soonest_then_undated(self, monkeypatch):
        entries = [
            _entry("future_far", 400),
            _entry("undated", None),
            _entry("overdue_mild", -10),
            _entry("future_soon", 20),
            _entry("overdue_severe", -200),
        ]
        monkeypatch.setattr(history, "load_retirement_tracker", lambda: entries)

        order = [c["update_id"] for c in history.get_retirement_countdown()]

        assert order == [
            "overdue_severe",  # a breached deadline is the most urgent item
            "overdue_mild",
            "future_soon",  # then the soonest upcoming deadline
            "future_far",
            "undated",  # undated (TBD) always trails
        ]

    def test_empty_tracker_returns_empty(self, monkeypatch):
        monkeypatch.setattr(history, "load_retirement_tracker", lambda: [])
        assert history.get_retirement_countdown() == []


def test_history_save_does_not_fail_analysis_when_directory_is_read_only(monkeypatch):
    def fail_mkdir():
        raise OSError(30, "Read-only file system")

    monkeypatch.setattr(history, "_ensure_data_dir", fail_mkdir)

    history.save_analysis_record(
        SimpleNamespace(
            update_id="update-1",
            update_title="Update",
            affected_resources=[],
            action_items=[],
        )
    )
    history.save_retirement_tracker([])
