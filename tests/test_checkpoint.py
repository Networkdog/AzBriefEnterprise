"""Tests for the durable digest checkpoint.

The checkpoint replaces the AzBrief_LastSuccessfulRunAt Automation Variable in
the enterprise profile, so the guarantees it used to provide have to hold here:
the value only ever moves forward, and anything unreadable degrades to "no
checkpoint" rather than to a wrong window.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional

import pytest

from src.config import Settings, get_settings
from src.services.checkpoint import (
    BlobCheckpointStore,
    CheckpointStore,
    FileCheckpointStore,
    PreconditionFailed,
    _parse_checkpoint,
    _serialize,
    build_checkpoint_store,
)

UTC = timezone.utc
_TENANT = "00000000-0000-0000-0000-000000000000"


def _settings(**overrides) -> Settings:
    base = {"azure_tenant_id": _TENANT}
    base.update(overrides)
    return Settings(_env_file=None, **base)


class TestSerialization:
    def test_round_trip(self):
        moment = datetime(2026, 8, 24, 2, 0, tzinfo=UTC)
        assert _parse_checkpoint(_serialize(moment)) == moment

    def test_naive_timestamp_is_read_as_utc(self):
        assert _parse_checkpoint('{"last_successful_run_at": "2026-08-24T02:00:00"}') == datetime(
            2026, 8, 24, 2, 0, tzinfo=UTC
        )

    def test_trailing_z_is_accepted(self):
        assert _parse_checkpoint('{"last_successful_run_at": "2026-08-24T02:00:00Z"}') == datetime(
            2026, 8, 24, 2, 0, tzinfo=UTC
        )

    @pytest.mark.parametrize(
        "payload",
        [
            "",
            "not json",
            "{}",
            '{"last_successful_run_at": null}',
            '{"last_successful_run_at": "??"}',
        ],
    )
    def test_unreadable_content_means_no_checkpoint(self, payload):
        # Falling back to the default window is recoverable; a wrong instant is not.
        assert _parse_checkpoint(payload) is None


class TestInertStore:
    @pytest.mark.asyncio
    async def test_reports_nothing_and_refuses_to_advance(self):
        store = CheckpointStore()
        assert store.configured is False
        assert await store.get() is None
        assert await store.advance(datetime.now(UTC)) is False


class TestFileStore:
    @pytest.mark.asyncio
    async def test_missing_file_reads_as_no_checkpoint(self, tmp_path):
        store = FileCheckpointStore(str(tmp_path / "state" / "checkpoint.json"))
        assert await store.get() is None

    @pytest.mark.asyncio
    async def test_advance_creates_and_persists(self, tmp_path):
        path = tmp_path / "state" / "checkpoint.json"
        store = FileCheckpointStore(str(path))
        moment = datetime(2026, 8, 24, 2, 0, tzinfo=UTC)

        assert await store.advance(moment) is True
        assert path.exists()
        assert await FileCheckpointStore(str(path)).get() == moment

    @pytest.mark.asyncio
    async def test_never_moves_backwards(self, tmp_path):
        store = FileCheckpointStore(str(tmp_path / "checkpoint.json"))
        newer = datetime(2026, 8, 24, 2, 0, tzinfo=UTC)
        await store.advance(newer)

        assert await store.advance(newer - timedelta(hours=6)) is False
        assert await store.get() == newer

    @pytest.mark.asyncio
    async def test_equal_watermark_is_not_rewritten(self, tmp_path):
        store = FileCheckpointStore(str(tmp_path / "checkpoint.json"))
        moment = datetime(2026, 8, 24, 2, 0, tzinfo=UTC)
        await store.advance(moment)
        assert await store.advance(moment) is False


class _FakeBlob:
    """Minimal in-memory stand-in for the blob REST surface."""

    def __init__(self):
        self.content: Optional[str] = None
        self.etag = "etag-0"
        self.writes: list[tuple[str, Optional[str]]] = []
        self.fail_next_write = False

    def read(self):
        if self.content is None:
            return None, None
        return _parse_checkpoint(self.content), self.etag

    def write(self, watermark, etag):
        self.writes.append((watermark.isoformat(), etag))
        if self.fail_next_write:
            self.fail_next_write = False
            # Simulate another writer winning the race.
            self.content = _serialize(watermark - timedelta(hours=1))
            self.etag = "etag-1"
            raise PreconditionFailed("conflict")
        self.content = _serialize(watermark)
        self.etag = f"etag-{len(self.writes)}"


def _wire(store: BlobCheckpointStore, blob: _FakeBlob) -> None:
    async def _read():
        return blob.read()

    async def _write(watermark, etag):
        blob.write(watermark, etag)

    store._read = _read  # type: ignore[method-assign]
    store._write = _write  # type: ignore[method-assign]


class TestBlobStore:
    @pytest.mark.asyncio
    async def test_first_write_uses_if_none_match(self):
        blob = _FakeBlob()
        store = BlobCheckpointStore("https://acct.blob.core.windows.net/state/checkpoint.json")
        _wire(store, blob)

        assert await store.advance(datetime(2026, 8, 24, 2, 0, tzinfo=UTC)) is True
        assert blob.writes[0][1] is None

    @pytest.mark.asyncio
    async def test_later_write_carries_the_etag(self):
        blob = _FakeBlob()
        store = BlobCheckpointStore("https://acct.blob.core.windows.net/state/checkpoint.json")
        _wire(store, blob)

        await store.advance(datetime(2026, 8, 24, 2, 0, tzinfo=UTC))
        etag_after_first = blob.etag
        await store.advance(datetime(2026, 8, 25, 2, 0, tzinfo=UTC))

        # If-Match, not If-None-Match: an existing blob may only be overwritten
        # while it still holds the value this store read.
        assert blob.writes[1][1] == etag_after_first

    @pytest.mark.asyncio
    async def test_a_lost_race_is_retried_against_the_new_value(self):
        blob = _FakeBlob()
        blob.content = _serialize(datetime(2026, 8, 23, 2, 0, tzinfo=UTC))
        blob.fail_next_write = True
        store = BlobCheckpointStore("https://acct.blob.core.windows.net/state/checkpoint.json")
        _wire(store, blob)

        assert await store.advance(datetime(2026, 8, 24, 2, 0, tzinfo=UTC)) is True
        assert len(blob.writes) == 2

    @pytest.mark.asyncio
    async def test_never_moves_backwards(self):
        blob = _FakeBlob()
        blob.content = _serialize(datetime(2026, 8, 24, 2, 0, tzinfo=UTC))
        store = BlobCheckpointStore("https://acct.blob.core.windows.net/state/checkpoint.json")
        _wire(store, blob)

        assert await store.advance(datetime(2026, 8, 20, 2, 0, tzinfo=UTC)) is False
        assert blob.writes == []


class TestStoreSelection:
    @pytest.fixture(autouse=True)
    def _clear_cache(self):
        get_settings.cache_clear()
        yield
        get_settings.cache_clear()

    def test_nothing_configured_gives_the_inert_store(self, monkeypatch):
        monkeypatch.setattr("src.services.checkpoint.get_settings", _settings)
        store = build_checkpoint_store()
        assert type(store) is CheckpointStore
        assert store.configured is False

    def test_blob_url_wins_over_file_path(self, monkeypatch):
        monkeypatch.setattr(
            "src.services.checkpoint.get_settings",
            lambda: _settings(
                checkpoint_blob_url="https://acct.blob.core.windows.net/state/checkpoint.json",
                checkpoint_file_path="data/checkpoint.json",
            ),
        )
        assert isinstance(build_checkpoint_store(), BlobCheckpointStore)

    def test_file_path_is_the_development_fallback(self, monkeypatch):
        monkeypatch.setattr(
            "src.services.checkpoint.get_settings",
            lambda: _settings(checkpoint_file_path="data/checkpoint.json"),
        )
        assert isinstance(build_checkpoint_store(), FileCheckpointStore)

    def test_plain_http_is_rejected(self, monkeypatch):
        # The managed-identity token would otherwise travel in clear text.
        monkeypatch.setattr(
            "src.services.checkpoint.get_settings",
            lambda: _settings(checkpoint_blob_url="http://acct.blob.core.windows.net/s/c.json"),
        )
        with pytest.raises(ValueError, match="https"):
            build_checkpoint_store()
