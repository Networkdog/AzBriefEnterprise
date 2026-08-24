"""Orchestrated digest runs.

A Container Apps Job (or an admin pressing "run now") drives a run here, inside
the Container App image, so the analysis is bounded by the job's replica timeout
rather than by a sandbox's fair-share limit.

The checkpoint is durable and lives in :mod:`src.services.checkpoint`. A run
resolves its start point from it and commits back only the watermark covering
the contiguous prefix of finished updates, so an interrupted run can never make
the next one skip an unanalysed update.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from structlog import get_logger

from src.config import get_settings
from src.services.checkpoint import get_checkpoint_store

logger = get_logger()

MAX_TRACKED_RUNS = 50
MAX_CONSECUTIVE_FAILURES = 3


def _ensure_utc(value: datetime) -> datetime:
    """Normalize a datetime to timezone-aware UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _filter_updates(updates: list, since: datetime) -> list:
    """Return updates published after ``since``, in chronological order."""
    fallback = datetime.min.replace(tzinfo=timezone.utc)
    selected = [u for u in updates if u.published_date and _ensure_utc(u.published_date) > since]
    return sorted(selected, key=lambda u: _ensure_utc(u.published_date or fallback))


class _WatermarkCursor:
    """Tracks the newest timestamp that is safe to report as processed.

    Updates are chronological but finish out of order under concurrency, so the
    newest finished update is not a safe watermark — everything behind it that
    is still running would be skipped forever. Only an unbroken prefix counts.
    """

    def __init__(self, targets: list):
        self._targets = targets
        self._finished: set[int] = set()
        self.cursor = 0
        self.watermark: Optional[datetime] = None

    def finish(self, index: int) -> None:
        """Mark target ``index`` (0-based) done and advance the prefix."""
        self._finished.add(index)
        advanced = False
        while self.cursor < len(self._targets) and self.cursor in self._finished:
            self.cursor += 1
            advanced = True
        if not advanced:
            return
        published = self._targets[self.cursor - 1].published_date
        if published is None:
            return
        candidate = _ensure_utc(published)
        if self.watermark is None or candidate > self.watermark:
            self.watermark = candidate

    @property
    def pending(self) -> int:
        return len(self._targets) - self.cursor


@dataclass
class RunRecord:
    """State of a single orchestrated digest run."""

    run_id: str
    status: str = "queued"  # queued | running | completed | failed
    since: Optional[datetime] = None
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: Optional[datetime] = None
    watermark: Optional[datetime] = None
    total: int = 0
    analyzed: int = 0
    failed: int = 0
    relevant: int = 0
    deferred: int = 0
    pending: int = 0
    email_sent: bool = False
    dry_run: bool = False
    checkpoint_committed: bool = False
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize for the API. Contains no secrets."""

        def iso(value: Optional[datetime]) -> Optional[str]:
            return value.isoformat() if value else None

        elapsed = (self.finished_at or datetime.now(timezone.utc)) - self.started_at
        return {
            "run_id": self.run_id,
            "status": self.status,
            "since": iso(self.since),
            "started_at": iso(self.started_at),
            "finished_at": iso(self.finished_at),
            "watermark": iso(self.watermark),
            "total": self.total,
            "analyzed": self.analyzed,
            "failed": self.failed,
            "relevant": self.relevant,
            "deferred": self.deferred,
            "pending": self.pending,
            "email_sent": self.email_sent,
            "dry_run": self.dry_run,
            "checkpoint_committed": self.checkpoint_committed,
            "elapsed_seconds": round(elapsed.total_seconds(), 1),
            "error": self.error,
        }


class RunStore:
    """Bounded in-memory registry of recent runs.

    Deliberately not durable: the checkpoint lives elsewhere, so losing a run
    record only means a poller stops seeing it. The next run re-covers whatever
    window the checkpoint still points at — duplicate work, never a skipped
    update.
    """

    def __init__(self, max_runs: int = MAX_TRACKED_RUNS):
        self._runs: OrderedDict[str, RunRecord] = OrderedDict()
        self._max_runs = max_runs

    def create(self, since: Optional[datetime], dry_run: bool = False) -> RunRecord:
        record = RunRecord(run_id=uuid.uuid4().hex, since=since, dry_run=dry_run)
        self._runs[record.run_id] = record
        while len(self._runs) > self._max_runs:
            self._runs.popitem(last=False)
        return record

    def get(self, run_id: str) -> Optional[RunRecord]:
        return self._runs.get(run_id)

    def recent(self, limit: int = 10) -> list[RunRecord]:
        return list(reversed(list(self._runs.values())))[:limit]

    @property
    def active_count(self) -> int:
        return sum(1 for r in self._runs.values() if r.status in ("queued", "running"))


_run_store = RunStore()

# Strong references to in-flight tasks; asyncio only holds weak ones.
_active_tasks: set[asyncio.Task] = set()

# Runtime services registered by the FastAPI lifespan handler.
_services: dict[str, Any] = {}


def get_run_store() -> RunStore:
    """Return the process-wide run registry."""
    return _run_store


def register_services(analyzer: Any, email_service: Any, rss_parser: Any) -> None:
    """Register the long-lived services an orchestrated run needs."""
    _services["analyzer"] = analyzer
    _services["email_service"] = email_service
    _services["rss_parser"] = rss_parser


def services_ready() -> bool:
    """True once the application lifespan has registered its services."""
    return bool(_services.get("analyzer") and _services.get("rss_parser"))


def start_run(since: Optional[datetime] = None, dry_run: bool = False) -> RunRecord:
    """Create a run record and drive it in the background.

    Args:
        since: Only analyse updates published after this instant. Defaults to
            the last 24 hours when the caller has no checkpoint.
        dry_run: Collect targets without analysing or sending email.

    Returns:
        The newly created record, already queued.

    Raises:
        RuntimeError: When the application services are not registered yet.
    """
    if not services_ready():
        raise RuntimeError("Orchestrator services are not initialized")

    record = _run_store.create(since=since, dry_run=dry_run)
    task = asyncio.create_task(
        execute_run(
            record,
            _services["analyzer"],
            _services.get("email_service"),
            _services["rss_parser"],
        )
    )
    _active_tasks.add(task)
    task.add_done_callback(_active_tasks.discard)
    logger.info(
        "orchestrator_run_started",
        run_id=record.run_id,
        since=since.isoformat() if since else None,
        dry_run=dry_run,
    )
    return record


def default_since() -> datetime:
    """Fallback window when neither the caller nor the checkpoint has a value."""
    return datetime.now(timezone.utc) - timedelta(hours=24)


async def resolve_since(explicit: Optional[datetime]) -> datetime:
    """Decide where a run starts: the caller's value, the checkpoint, or 24h ago.

    Args:
        explicit: Instant supplied by the caller, or None to resume.

    Returns:
        An aware UTC datetime.
    """
    if explicit is not None:
        return _ensure_utc(explicit)
    try:
        stored = await get_checkpoint_store().get()
    except Exception as exc:
        logger.warning("checkpoint_read_failed", error=str(exc))
        stored = None
    return stored or default_since()


async def _commit_checkpoint(record: RunRecord) -> None:
    """Advance the durable checkpoint. Never raises — not advancing is safe."""
    if record.watermark is None or record.dry_run:
        return
    try:
        record.checkpoint_committed = await get_checkpoint_store().advance(record.watermark)
    except Exception as exc:
        logger.warning("checkpoint_commit_failed", run_id=record.run_id, error=str(exc))


def parse_iso_utc(value: Optional[str]) -> Optional[datetime]:
    """Parse an ISO-8601 instant into aware UTC.

    Args:
        value: Timestamp string, optionally ending in ``Z``. Empty means None.

    Returns:
        An aware UTC datetime, or None when no value was supplied.

    Raises:
        ValueError: When the string is not a valid ISO-8601 timestamp.
    """
    if not value:
        return None
    parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


async def execute_run(
    record: RunRecord,
    analyzer: Any,
    email_service: Any,
    rss_parser: Any,
) -> RunRecord:
    """Analyse every update since ``record.since`` and send the digest.

    Failures on a single update are isolated; the wall-clock budget defers the
    remainder to the next run instead of truncating an analysis mid-flight.

    Args:
        record: Run record to update in place.
        analyzer: ``AzureUpdateAnalyzer`` instance.
        email_service: ``EmailService`` instance.
        rss_parser: ``AzureUpdateParser`` instance.

    Returns:
        The same record, completed or failed.
    """
    from src.agent.resilience import RunDeadline

    settings = get_settings()
    record.status = "running"
    started = time.time()
    since = await resolve_since(record.since)
    record.since = since

    try:
        updates = await rss_parser.get_updates()
        targets = _filter_updates(updates, since)
        record.total = len(targets)

        if not targets:
            record.status = "completed"
            record.finished_at = datetime.now(timezone.utc)
            logger.info("orchestrator_run_empty", run_id=record.run_id, since=since.isoformat())
            return record

        cursor = _WatermarkCursor(targets)
        deadline = RunDeadline(budget_s=settings.run_time_budget_s)
        semaphore = asyncio.Semaphore(settings.max_concurrent_analyses)
        results_lock = asyncio.Lock()
        digest_items: list[dict] = []
        consecutive_failures = 0
        slowest_s = 0.0

        async def _analyze_one(index: int, update) -> None:
            nonlocal consecutive_failures, slowest_s
            async with semaphore:
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    return
                if not deadline.has_budget_for(slowest_s):
                    async with results_lock:
                        record.deferred += 1
                    return

                update_started = time.time()
                try:
                    result = await analyzer.analyze_update(update)
                except Exception as exc:
                    logger.warning(
                        "orchestrator_update_failed",
                        run_id=record.run_id,
                        update_id=getattr(update, "id", ""),
                        error=str(exc),
                    )
                    async with results_lock:
                        record.failed += 1
                        consecutive_failures += 1
                        # A permanently broken update must not pin the watermark.
                        cursor.finish(index)
                    return

                async with results_lock:
                    record.analyzed += 1
                    consecutive_failures = 0
                    if result.should_notify:
                        record.relevant += 1
                    digest_items.append({"update": update, "result": result, "skip_reason": ""})
                    slowest_s = max(slowest_s, time.time() - update_started)
                    cursor.finish(index)

        if record.dry_run:
            record.deferred = len(targets)
        else:
            await asyncio.gather(*[_analyze_one(i, update) for i, update in enumerate(targets)])
            record.email_sent = await _send_digest(digest_items, analyzer, email_service)

        record.watermark = cursor.watermark
        record.pending = cursor.pending
        await _commit_checkpoint(record)
        record.status = "completed"
        record.finished_at = datetime.now(timezone.utc)
        logger.info(
            "orchestrator_run_complete",
            run_id=record.run_id,
            elapsed_s=round(time.time() - started, 1),
            **{
                k: v
                for k, v in record.to_dict().items()
                if k in ("total", "analyzed", "failed", "relevant", "deferred", "pending")
            },
        )
    except Exception as exc:
        record.status = "failed"
        record.error = str(exc)[:300]
        record.finished_at = datetime.now(timezone.utc)
        logger.error("orchestrator_run_failed", run_id=record.run_id, error=str(exc))

    return record


async def _send_digest(digest_items: list[dict], analyzer: Any, email_service: Any) -> bool:
    """Send the consolidated digest, per subscriber when subscribers exist."""
    if not digest_items or email_service is None:
        return False

    settings = get_settings()
    subscribers = settings.get_subscribers()
    date_range = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    try:
        if not subscribers:
            await email_service.send_digest_report(digest_items, date_range=date_range)
            return True

        async def _customize_and_send(subscriber) -> None:
            with_results = [item for item in digest_items if item["result"]]
            without_results = [item for item in digest_items if not item["result"]]
            customized = await asyncio.gather(
                *[
                    analyzer.customize_for_subscriber(item["result"], subscriber, item["update"])
                    for item in with_results
                ],
                return_exceptions=True,
            )
            items = []
            for item, result in zip(with_results, customized):
                if isinstance(result, BaseException):
                    items.append(item)
                else:
                    items.append({"update": item["update"], "result": result, "skip_reason": ""})
            items.extend(without_results)
            await email_service.send_digest_report(
                items,
                date_range=date_range,
                recipient=subscriber.email,
                language=subscriber.language,
            )

        await asyncio.gather(
            *[_customize_and_send(sub) for sub in subscribers],
            return_exceptions=True,
        )
        return True
    except Exception as exc:
        logger.warning("orchestrator_digest_failed", error=str(exc))
        return False
