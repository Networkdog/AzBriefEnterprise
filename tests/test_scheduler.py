"""Tests for the Container Apps Job entry point.

The job replaced the Automation Runbook as the enterprise scheduler, so its exit
code is what Container Apps uses to mark an execution failed.
"""

from datetime import datetime, timezone

import pytest

import src.scheduler as scheduler

UTC = timezone.utc


class _FakeAnalyzer:
    def __init__(self):
        self.closed = []
        self._tools = [self]

    @property
    def learn_service(self):
        return self

    async def close(self):
        self.closed.append(True)


@pytest.fixture
def wired(monkeypatch):
    """Replace the heavy services and capture the record execute_run receives."""
    analyzer = _FakeAnalyzer()
    archive = object()
    captured = {}

    monkeypatch.setattr("src.agent.hosted_client.HostedAgentAnalyzer", lambda: analyzer)
    monkeypatch.setattr("src.email.service.EmailService", lambda: object())
    monkeypatch.setattr("src.archive.service.ArchiveService", lambda: archive)
    monkeypatch.setattr("src.rss.parser.AzureUpdateParser", lambda: object())

    def _install(status: str, watermark=None):
        async def _fake_execute(record, *args, **_kwargs):
            record.status = status
            record.watermark = watermark
            record.finished_at = datetime.now(UTC)
            captured["record"] = record
            captured["archive"] = args[3]
            return record

        monkeypatch.setattr("src.orchestrator.execute_run", _fake_execute)
        return captured, analyzer

    return _install


class TestRunScheduledDigest:
    @pytest.mark.asyncio
    async def test_completed_run_exits_zero(self, wired):
        captured, _ = wired("completed", datetime(2026, 8, 24, 2, 0, tzinfo=UTC))
        assert await scheduler.run_scheduled_digest() == 0
        assert captured["record"].dry_run is False
        assert captured["record"].source == "scheduled_digest"
        assert captured["archive"] is not None

    @pytest.mark.asyncio
    async def test_failed_run_exits_non_zero(self, wired):
        # A non-zero exit is what marks the job execution failed in Container Apps.
        wired("failed")
        assert await scheduler.run_scheduled_digest() == 1

    @pytest.mark.asyncio
    async def test_no_since_is_passed_so_the_checkpoint_decides(self, wired):
        captured, _ = wired("completed")
        await scheduler.run_scheduled_digest()
        assert captured["record"].since is None

    @pytest.mark.asyncio
    async def test_dry_run_is_forwarded(self, wired):
        captured, _ = wired("completed")
        await scheduler.run_scheduled_digest(dry_run=True)
        assert captured["record"].dry_run is True

    @pytest.mark.asyncio
    async def test_http_clients_are_released(self, wired):
        _, analyzer = wired("completed")
        await scheduler.run_scheduled_digest()
        assert analyzer.closed == [True]
