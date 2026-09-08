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


class TestDispatchScheduledDigest:
    @pytest.mark.asyncio
    async def test_idle_dispatch_does_not_construct_the_analysis_runtime(self, monkeypatch):
        class Configuration:
            async def claim_due_automatic_run(self, now=None):
                return None

        called = []
        monkeypatch.setattr(
            "src.admin.configuration.get_admin_configuration",
            lambda: Configuration(),
        )
        monkeypatch.setattr(
            scheduler, "run_scheduled_digest", lambda dry_run=False: called.append(1)
        )

        assert await scheduler.dispatch_scheduled_digest() == 0
        assert called == []

    @pytest.mark.asyncio
    async def test_due_dispatch_runs_once_and_releases_the_lease(self, monkeypatch):
        lease = type(
            "Lease",
            (),
            {
                "schedule_key": "admin:1",
                "scheduled_for": datetime(2026, 9, 1, 14, 30, tzinfo=UTC),
            },
        )()

        class Configuration:
            def __init__(self):
                self.released = []

            async def claim_due_automatic_run(self, now=None):
                return lease

            async def release_automatic_run(self, claimed):
                self.released.append(claimed)

        configuration = Configuration()

        async def run(dry_run=False):
            return 0

        monkeypatch.setattr(
            "src.admin.configuration.get_admin_configuration",
            lambda: configuration,
        )
        monkeypatch.setattr(scheduler, "run_scheduled_digest", run)

        assert await scheduler.dispatch_scheduled_digest() == 0
        assert configuration.released == [lease]
