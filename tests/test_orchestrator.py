"""Tests for the orchestrated digest pipeline (enterprise profile)."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from src.archive.models import ArchiveReceipt
from src.config import get_settings
from src.orchestrator import (
    RunRecord,
    RunStore,
    _filter_updates,
    _WatermarkCursor,
    execute_run,
    parse_iso_utc,
)

UTC = timezone.utc


def _update(index: int, published: datetime) -> SimpleNamespace:
    return SimpleNamespace(id=f"u{index}", title=f"Update {index}", published_date=published)


def _targets(count: int) -> list[SimpleNamespace]:
    base = datetime(2026, 8, 1, tzinfo=UTC)
    return [_update(i, base + timedelta(hours=i)) for i in range(count)]


class TestWatermarkCursor:
    """Only the contiguous prefix of finished updates may be reported."""

    def test_no_watermark_before_the_first_finishes(self):
        cursor = _WatermarkCursor(_targets(3))
        cursor.finish(2)
        cursor.finish(1)
        assert cursor.watermark is None
        assert cursor.pending == 3

    def test_prefix_advances_when_the_gap_closes(self):
        targets = _targets(3)
        cursor = _WatermarkCursor(targets)
        cursor.finish(2)
        cursor.finish(0)
        assert cursor.watermark == targets[0].published_date
        cursor.finish(1)
        # Closing the gap releases everything up to the newest finished update.
        assert cursor.watermark == targets[2].published_date
        assert cursor.pending == 0

    def test_watermark_never_moves_backwards(self):
        targets = _targets(2)
        cursor = _WatermarkCursor(targets)
        cursor.finish(0)
        cursor.finish(1)
        first = cursor.watermark
        cursor.finish(1)
        assert cursor.watermark == first

    def test_missing_published_date_is_tolerated(self):
        targets = [_update(0, None), _update(1, datetime(2026, 8, 2, tzinfo=UTC))]
        cursor = _WatermarkCursor(targets)
        cursor.finish(0)
        assert cursor.watermark is None


class TestFilterUpdates:
    """Window selection mirrors the runbook's incremental behaviour."""

    def test_only_newer_updates_are_selected_and_sorted(self):
        since = datetime(2026, 8, 1, 2, tzinfo=UTC)
        targets = _targets(5)
        selected = _filter_updates(list(reversed(targets)), since)
        assert [u.id for u in selected] == ["u3", "u4"]

    def test_updates_without_a_date_are_skipped(self):
        since = datetime(2026, 1, 1, tzinfo=UTC)
        assert _filter_updates([_update(0, None)], since) == []

    def test_boundary_is_exclusive(self):
        targets = _targets(1)
        assert _filter_updates(targets, targets[0].published_date) == []


class TestParseIsoUtc:
    def test_none_and_empty_are_none(self):
        assert parse_iso_utc(None) is None
        assert parse_iso_utc("") is None

    def test_zulu_suffix_is_accepted(self):
        assert parse_iso_utc("2026-08-18T09:00:00Z") == datetime(2026, 8, 18, 9, tzinfo=UTC)

    def test_naive_input_is_treated_as_utc(self):
        assert parse_iso_utc("2026-08-18T09:00:00") == datetime(2026, 8, 18, 9, tzinfo=UTC)

    def test_malformed_input_raises(self):
        with pytest.raises(ValueError):
            parse_iso_utc("yesterday")


class TestRunStore:
    def test_oldest_records_are_evicted(self):
        store = RunStore(max_runs=3)
        created = [store.create(since=None) for _ in range(5)]
        assert store.get(created[0].run_id) is None
        assert store.get(created[-1].run_id) is not None

    def test_recent_is_newest_first(self):
        store = RunStore()
        first = store.create(since=None)
        second = store.create(since=None)
        assert [r.run_id for r in store.recent(2)] == [second.run_id, first.run_id]

    def test_active_count_tracks_unfinished_runs(self):
        store = RunStore()
        record = store.create(since=None)
        assert store.active_count == 1
        record.status = "completed"
        assert store.active_count == 0


class _FakeParser:
    def __init__(self, updates):
        self._updates = updates

    async def get_updates(self):
        return self._updates


class _FakeAnalyzer:
    """Analyzer stub; `fail_ids` makes specific updates raise."""

    def __init__(self, fail_ids=()):
        self.fail_ids = set(fail_ids)
        self.seen: list[str] = []

    async def analyze_update(self, update):
        self.seen.append(update.id)
        if update.id in self.fail_ids:
            raise RuntimeError("analysis exploded")
        return SimpleNamespace(should_notify=True, relevance=SimpleNamespace(value="action"))

    async def customize_for_subscriber(self, result, _subscriber, _update):
        return result


class _FakeEmailService:
    def __init__(self, delivered: bool = True):
        self.calls: list[dict] = []
        self._delivered = delivered

    async def send_digest_report(self, items, date_range=None, recipient=None, language=None):
        self.calls.append({"items": len(items), "recipient": recipient})
        return self._delivered


class _FakeArchiveService:
    configured = True

    def __init__(self, events=None, fail_ids=()):
        self.events = events if events is not None else []
        self.fail_ids = set(fail_ids)
        self.seen: list[tuple[str, str, str]] = []

    async def archive_analysis(self, update, _result, source, run_id=""):
        self.events.append(f"archive:{update.id}")
        self.seen.append((update.id, source.value, run_id))
        if update.id in self.fail_ids:
            raise RuntimeError("archive unavailable")
        return ArchiveReceipt(
            archived=True,
            archive_id=f"8211694095999-{int(update.id[1:]) + 1:032x}",
            object_name=f"entries/{update.id}.json",
        )

    def detail_url(self, archive_id):
        return f"https://archive.example/{archive_id}"


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class TestExecuteRun:
    @pytest.mark.asyncio
    async def test_empty_window_completes_without_email(self):
        record = RunRecord(run_id="r1", since=datetime(2026, 8, 5, tzinfo=UTC))
        email = _FakeEmailService()
        await execute_run(record, _FakeAnalyzer(), email, _FakeParser(_targets(3)))
        assert record.status == "completed"
        assert record.total == 0
        assert email.calls == []

    @pytest.mark.asyncio
    async def test_all_updates_analysed_and_digest_sent(self):
        targets = _targets(3)
        record = RunRecord(run_id="r2", since=datetime(2026, 7, 1, tzinfo=UTC))
        analyzer = _FakeAnalyzer()
        email = _FakeEmailService()
        await execute_run(record, analyzer, email, _FakeParser(targets))

        assert record.status == "completed"
        assert record.analyzed == 3
        assert record.relevant == 3
        assert record.failed == 0
        assert record.email_sent is True
        assert record.watermark == targets[-1].published_date
        assert email.calls == [{"items": 3, "recipient": None}]

    @pytest.mark.asyncio
    async def test_archive_is_committed_before_digest_and_cursor_completion(self):
        events = []

        class _OrderedEmailService(_FakeEmailService):
            async def send_digest_report(self, items, **kwargs):
                events.append("email")
                assert all(item["archive_id"] for item in items)
                return await super().send_digest_report(items, **kwargs)

        targets = _targets(2)
        record = RunRecord(
            run_id="archive-order",
            source="scheduled_digest",
            since=datetime(2026, 7, 1, tzinfo=UTC),
        )
        archive = _FakeArchiveService(events=events)

        await execute_run(
            record,
            _FakeAnalyzer(),
            _OrderedEmailService(),
            _FakeParser(targets),
            archive,
        )

        assert events == ["archive:u0", "archive:u1", "email"]
        assert record.archived == 2
        assert record.archive_failed == 0
        assert archive.seen[0][1:] == ("scheduled_digest", "archive-order")

    @pytest.mark.asyncio
    async def test_subscriber_customization_does_not_archive_twice(self, monkeypatch):
        import json

        monkeypatch.setenv(
            "SUBSCRIBERS",
            json.dumps([{"email": "reader@co.com", "name": "Reader", "language": "ko"}]),
        )
        get_settings.cache_clear()
        target = _targets(1)
        record = RunRecord(
            run_id="archive-once",
            source="scheduled_digest",
            since=datetime(2026, 7, 1, tzinfo=UTC),
        )
        archive = _FakeArchiveService()

        await execute_run(
            record,
            _FakeAnalyzer(),
            _FakeEmailService(),
            _FakeParser(target),
            archive,
        )

        assert [item[0] for item in archive.seen] == ["u0"]

    @pytest.mark.asyncio
    async def test_a_rejected_digest_is_not_reported_as_sent(self):
        """A transport rejection must reach the record instead of reading as delivered."""
        targets = _targets(3)
        record = RunRecord(run_id="r2b", since=datetime(2026, 7, 1, tzinfo=UTC))
        email = _FakeEmailService(delivered=False)

        await execute_run(record, _FakeAnalyzer(), email, _FakeParser(targets))

        assert record.analyzed == 3
        assert record.email_sent is False
        assert email.calls == [{"items": 3, "recipient": None}]

    @pytest.mark.asyncio
    async def test_a_failing_update_does_not_pin_the_watermark(self):
        # A permanently broken update must not block the window forever.
        targets = _targets(3)
        record = RunRecord(run_id="r3", since=datetime(2026, 7, 1, tzinfo=UTC))
        await execute_run(
            record, _FakeAnalyzer(fail_ids={"u0"}), _FakeEmailService(), _FakeParser(targets)
        )
        assert record.failed == 1
        assert record.analyzed == 2
        assert record.watermark == targets[-1].published_date

    @pytest.mark.asyncio
    async def test_dry_run_skips_analysis_and_email(self):
        targets = _targets(2)
        record = RunRecord(run_id="r4", since=datetime(2026, 7, 1, tzinfo=UTC), dry_run=True)
        analyzer = _FakeAnalyzer()
        email = _FakeEmailService()
        await execute_run(record, analyzer, email, _FakeParser(targets))

        assert record.status == "completed"
        assert record.total == 2
        assert record.deferred == 2
        assert analyzer.seen == []
        assert email.calls == []

    @pytest.mark.asyncio
    async def test_feed_failure_marks_the_run_failed(self):
        class _BrokenParser:
            async def get_updates(self):
                raise RuntimeError("feed down")

        record = RunRecord(run_id="r5")
        await execute_run(record, _FakeAnalyzer(), _FakeEmailService(), _BrokenParser())
        assert record.status == "failed"
        assert "feed down" in record.error

    @pytest.mark.asyncio
    async def test_to_dict_is_serializable_and_secret_free(self):
        record = RunRecord(run_id="r6", since=datetime(2026, 8, 1, tzinfo=UTC))
        payload = record.to_dict()
        assert payload["run_id"] == "r6"
        assert payload["since"] == "2026-08-01T00:00:00+00:00"
        assert "api_key" not in payload


class _RecordingCheckpointStore:
    """Checkpoint double that records what the orchestrator asks of it."""

    def __init__(self, stored=None, fail=False):
        self.stored = stored
        self.fail = fail
        self.advanced: list[datetime] = []

    async def get(self):
        if self.fail:
            raise RuntimeError("blob unreachable")
        return self.stored

    async def advance(self, watermark):
        if self.fail:
            raise RuntimeError("blob unreachable")
        self.advanced.append(watermark)
        self.stored = watermark
        return True


class TestCheckpointIntegration:
    """The durable checkpoint replaces the Automation Variable the runbook owned."""

    @pytest.fixture
    def store(self, monkeypatch):
        def _install(instance):
            monkeypatch.setattr("src.orchestrator.get_checkpoint_store", lambda: instance)
            return instance

        return _install

    @pytest.mark.asyncio
    async def test_a_run_without_since_resumes_from_the_checkpoint(self, store):
        targets = _targets(3)
        store(_RecordingCheckpointStore(stored=targets[0].published_date))
        record = RunRecord(run_id="c1")

        await execute_run(record, _FakeAnalyzer(), _FakeEmailService(), _FakeParser(targets))

        # The stored instant is exclusive, so the update that produced it is not redone.
        assert record.since == targets[0].published_date
        assert record.total == 2

    @pytest.mark.asyncio
    async def test_completed_run_commits_the_watermark(self, store):
        targets = _targets(3)
        recorder = store(_RecordingCheckpointStore())
        record = RunRecord(run_id="c2", since=datetime(2026, 7, 1, tzinfo=UTC))

        await execute_run(record, _FakeAnalyzer(), _FakeEmailService(), _FakeParser(targets))

        assert recorder.advanced == [targets[-1].published_date]
        assert record.checkpoint_committed is True

    @pytest.mark.asyncio
    async def test_dry_run_never_commits(self, store):
        recorder = store(_RecordingCheckpointStore())
        record = RunRecord(run_id="c3", since=datetime(2026, 7, 1, tzinfo=UTC), dry_run=True)

        await execute_run(record, _FakeAnalyzer(), _FakeEmailService(), _FakeParser(_targets(2)))

        assert recorder.advanced == []
        assert record.checkpoint_committed is False

    @pytest.mark.asyncio
    async def test_an_unreachable_store_does_not_fail_the_run(self, store):
        # Not advancing repeats a window; failing the run would lose the digest.
        store(_RecordingCheckpointStore(fail=True))
        targets = _targets(2)
        record = RunRecord(run_id="c4", since=datetime(2026, 7, 1, tzinfo=UTC))

        await execute_run(record, _FakeAnalyzer(), _FakeEmailService(), _FakeParser(targets))

        assert record.status == "completed"
        assert record.checkpoint_committed is False

    @pytest.mark.asyncio
    async def test_an_unreadable_store_falls_back_to_the_default_window(self, store):
        store(_RecordingCheckpointStore(fail=True))
        record = RunRecord(run_id="c5")

        await execute_run(record, _FakeAnalyzer(), _FakeEmailService(), _FakeParser([]))

        assert record.since is not None
        assert record.since > datetime.now(UTC) - timedelta(hours=25)

    @pytest.mark.asyncio
    async def test_archive_failure_blocks_email_and_checkpoint(self, store):
        targets = _targets(2)
        checkpoint = store(_RecordingCheckpointStore())
        email = _FakeEmailService()
        archive = _FakeArchiveService(fail_ids={"u0"})
        record = RunRecord(
            run_id="archive-failure",
            source="scheduled_digest",
            since=datetime(2026, 7, 1, tzinfo=UTC),
        )

        await execute_run(
            record,
            _FakeAnalyzer(),
            email,
            _FakeParser(targets),
            archive,
        )

        assert record.status == "failed"
        assert record.archive_failed == 1
        assert record.pending == 2
        assert email.calls == []
        assert checkpoint.advanced == []
