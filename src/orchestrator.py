"""Orchestrated digest control-plane runs.

A Container Apps Job (or an admin pressing "run now") drives RSS selection,
checkpointing, and delivery here. Each update is delegated through the analyzer
interface to the Foundry Hosted Agent; this module never owns the LangGraph runtime.

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
from typing import Any, Literal, Optional
from urllib.parse import urlparse

from structlog import get_logger

from src.admin.configuration import get_admin_configuration
from src.agent.scope import AnalysisScope
from src.config import get_settings
from src.i18n.labels import get_labels
from src.services.checkpoint import get_checkpoint_store

logger = get_logger()

MAX_TRACKED_RUNS = 50
MAX_CONSECUTIVE_FAILURES = 3
MAX_MANUAL_TARGETS = 100


@dataclass(frozen=True)
class RunSelection:
    """Bounded target selector for an orchestrated run."""

    mode: Literal["checkpoint", "date_range", "recent", "update_id", "update_url"] = "checkpoint"
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    recent_count: Optional[int] = None
    update_id: str = ""
    update_url: str = ""

    def __post_init__(self) -> None:
        if self.mode == "date_range":
            if self.start_date is None or self.end_date is None:
                raise ValueError("date_range requires start_date and end_date")
            if _ensure_utc(self.start_date) > _ensure_utc(self.end_date):
                raise ValueError("start_date must not be later than end_date")
        elif self.mode == "recent":
            if self.recent_count is None or not 1 <= self.recent_count <= MAX_MANUAL_TARGETS:
                raise ValueError(f"recent_count must be between 1 and {MAX_MANUAL_TARGETS}")
        elif self.mode == "update_id":
            if not self.update_id.isdigit():
                raise ValueError("update_id must contain digits only")
        elif self.mode == "update_url":
            parsed = urlparse(self.update_url)
            hostname = (parsed.hostname or "").lower()
            if (
                parsed.scheme != "https"
                or hostname != "azure.microsoft.com"
                or "updates" not in {part.lower() for part in parsed.path.split("/") if part}
            ):
                raise ValueError("update_url must be an HTTPS Azure Updates URL")

    def to_dict(self) -> dict[str, Any]:
        """Serialize the selector without exposing any sensitive value."""

        def iso(value: Optional[datetime]) -> Optional[str]:
            return _ensure_utc(value).isoformat() if value else None

        return {
            "mode": self.mode,
            "start_date": iso(self.start_date),
            "end_date": iso(self.end_date),
            "recent_count": self.recent_count,
            "update_id": self.update_id or None,
            "update_url": self.update_url or None,
        }


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


def _chronological(updates: list) -> list:
    """Return dated updates oldest-first, followed by undated single targets."""
    fallback = datetime.max.replace(tzinfo=timezone.utc)
    return sorted(updates, key=lambda item: _ensure_utc(item.published_date or fallback))


async def _select_targets(record: "RunRecord", rss_parser: Any) -> list:
    """Resolve one run selector through the parser's owning lookup method."""
    selection = record.selection
    if selection.mode == "checkpoint":
        since = await resolve_since(record.since)
        record.since = since
        return _filter_updates(await rss_parser.get_updates(), since)

    if selection.mode == "date_range":
        updates = await rss_parser.get_updates_by_date_range(
            selection.start_date,
            selection.end_date,
        )
    elif selection.mode == "recent":
        updates = sorted(
            await rss_parser.get_updates(),
            key=lambda item: (
                _ensure_utc(item.published_date)
                if item.published_date
                else datetime.min.replace(tzinfo=timezone.utc)
            ),
            reverse=True,
        )[: selection.recent_count]
    elif selection.mode == "update_id":
        update = await rss_parser.fetch_update_by_id(selection.update_id)
        updates = [update] if update else []
    else:
        update = await rss_parser.get_update_by_url(selection.update_url)
        if update is None:
            details = await rss_parser.fetch_update_details(selection.update_url)
            update = details.get("update")
            if update is None and (details.get("title") or details.get("content")):
                from src.rss.parser import AzureUpdate

                update = AzureUpdate(
                    id=selection.update_url,
                    title=details.get("title") or "Unknown Update",
                    description=details.get("content") or "",
                    link=selection.update_url,
                    published_date=None,
                    categories=[],
                    azure_services=[],
                    update_type=None,
                    status=None,
                )
        updates = [update] if update else []

    if not updates:
        raise ValueError(f"No Azure Updates matched selector '{selection.mode}'")
    if len(updates) > MAX_MANUAL_TARGETS:
        raise ValueError(
            f"Selector matched {len(updates)} updates; narrow it to {MAX_MANUAL_TARGETS} or fewer"
        )
    return _chronological(updates)


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
    source: str = "scheduled_digest"
    status: str = "queued"  # queued | running | completed | failed
    since: Optional[datetime] = None
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: Optional[datetime] = None
    watermark: Optional[datetime] = None
    total: int = 0
    analyzed: int = 0
    archived: int = 0
    archive_failed: int = 0
    failed: int = 0
    relevant: int = 0
    deferred: int = 0
    pending: int = 0
    email_sent: bool = False
    send_email: bool = True
    dry_run: bool = False
    selection: RunSelection = field(default_factory=RunSelection)
    commit_checkpoint: bool = True
    checkpoint_committed: bool = False
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize for the API. Contains no secrets."""

        def iso(value: Optional[datetime]) -> Optional[str]:
            return value.isoformat() if value else None

        elapsed = (self.finished_at or datetime.now(timezone.utc)) - self.started_at
        return {
            "run_id": self.run_id,
            "source": self.source,
            "status": self.status,
            "since": iso(self.since),
            "started_at": iso(self.started_at),
            "finished_at": iso(self.finished_at),
            "watermark": iso(self.watermark),
            "total": self.total,
            "analyzed": self.analyzed,
            "archived": self.archived,
            "archive_failed": self.archive_failed,
            "failed": self.failed,
            "relevant": self.relevant,
            "deferred": self.deferred,
            "pending": self.pending,
            "email_sent": self.email_sent,
            "send_email": self.send_email,
            "dry_run": self.dry_run,
            "selection": self.selection.to_dict(),
            "commit_checkpoint": self.commit_checkpoint,
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

    def create(
        self,
        since: Optional[datetime],
        dry_run: bool = False,
        selection: Optional[RunSelection] = None,
        commit_checkpoint: bool = True,
        send_email: bool = True,
    ) -> RunRecord:
        record = RunRecord(
            run_id=uuid.uuid4().hex,
            since=since,
            dry_run=dry_run,
            selection=selection or RunSelection(),
            commit_checkpoint=commit_checkpoint,
            send_email=send_email,
        )
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


def register_services(
    analyzer: Any,
    email_service: Any,
    rss_parser: Any,
    archive_service: Any = None,
) -> None:
    """Register the long-lived services an orchestrated run needs."""
    _services["analyzer"] = analyzer
    _services["email_service"] = email_service
    _services["rss_parser"] = rss_parser
    _services["archive_service"] = archive_service


def services_ready() -> bool:
    """True once the application lifespan has registered its services."""
    return bool(_services.get("analyzer") and _services.get("rss_parser"))


def start_run(
    since: Optional[datetime] = None,
    dry_run: bool = False,
    source: str = "api_orchestrate",
    selection: Optional[RunSelection] = None,
    commit_checkpoint: bool = True,
    send_email: bool = True,
) -> RunRecord:
    """Create a run record and drive it in the background.

    Args:
        since: Only analyse updates published after this instant. Defaults to
            the last 24 hours when the caller has no checkpoint.
        dry_run: Collect targets without analysing or sending email.
        send_email: Deliver a digest after analysis. Disable for an archive-only run.

    Returns:
        The newly created record, already queued.

    Raises:
        RuntimeError: When the application services are not registered yet.
    """
    if not services_ready():
        raise RuntimeError("Orchestrator services are not initialized")

    record = _run_store.create(
        since=since,
        dry_run=dry_run,
        selection=selection,
        commit_checkpoint=commit_checkpoint,
        send_email=send_email,
    )
    record.source = source
    task = asyncio.create_task(
        execute_run(
            record,
            _services["analyzer"],
            _services.get("email_service"),
            _services["rss_parser"],
            _services.get("archive_service"),
        )
    )
    _active_tasks.add(task)
    task.add_done_callback(_active_tasks.discard)
    logger.info(
        "orchestrator_run_started",
        run_id=record.run_id,
        since=since.isoformat() if since else None,
        selection=record.selection.mode,
        send_email=record.send_email,
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
    if record.watermark is None or record.dry_run or not record.commit_checkpoint:
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
    archive_service: Any = None,
) -> RunRecord:
    """Analyse every update since ``record.since`` and send the digest.

    Failures on a single update are isolated; the wall-clock budget defers the
    remainder to the next run instead of truncating an analysis mid-flight.

    Args:
        record: Run record to update in place.
        analyzer: Hosted Agent analyzer proxy (or a compatible test double).
        email_service: ``EmailService`` instance.
        rss_parser: ``AzureUpdateParser`` instance.

    Returns:
        The same record, completed or failed.
    """
    from src.agent.resilience import RunDeadline

    settings = get_settings()
    record.status = "running"
    started = time.time()
    try:
        targets = await _select_targets(record, rss_parser)
        record.total = len(targets)

        if not targets:
            record.status = "completed"
            record.finished_at = datetime.now(timezone.utc)
            logger.info(
                "orchestrator_run_empty",
                run_id=record.run_id,
                since=record.since.isoformat() if record.since else None,
            )
            return record

        cursor = _WatermarkCursor(targets)
        deadline = RunDeadline(budget_s=settings.run_time_budget_s)
        semaphore = asyncio.Semaphore(settings.max_concurrent_analyses)
        results_lock = asyncio.Lock()
        digest_items: list[dict] = []
        archive_errors: list[str] = []
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

                receipt = None
                if archive_service is not None:
                    try:
                        from src.archive.models import ArchiveSource

                        receipt = await archive_service.archive_analysis(
                            update,
                            result,
                            ArchiveSource(record.source),
                            run_id=record.run_id,
                        )
                        if archive_service.configured and not receipt.archived:
                            raise RuntimeError("configured archive did not persist the analysis")
                    except Exception as exc:
                        logger.error(
                            "orchestrator_archive_failed",
                            run_id=record.run_id,
                            update_id=getattr(update, "id", ""),
                            error=str(exc),
                        )
                        async with results_lock:
                            record.archive_failed += 1
                            archive_errors.append(str(exc)[:200])
                        return

                async with results_lock:
                    if receipt is not None and receipt.archived:
                        record.archived += 1
                    digest_items.append(
                        {
                            "update": update,
                            "result": result,
                            "skip_reason": "",
                            "archive_id": receipt.archive_id if receipt else "",
                            "archive_url": (
                                archive_service.detail_url(receipt.archive_id)
                                if receipt and receipt.archived
                                else ""
                            ),
                        }
                    )
                    slowest_s = max(slowest_s, time.time() - update_started)
                    cursor.finish(index)

        if record.dry_run:
            record.deferred = len(targets)
        else:
            await asyncio.gather(*[_analyze_one(i, update) for i, update in enumerate(targets)])
            if archive_errors:
                record.watermark = cursor.watermark
                record.pending = cursor.pending
                raise RuntimeError(
                    f"Archive persistence failed for {len(archive_errors)} analysis result(s)"
                )
            if record.send_email:
                record.email_sent = await _send_digest(
                    digest_items,
                    analyzer,
                    email_service,
                    deadline=deadline,
                    estimate_s=max(slowest_s, 1.0),
                )

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


async def _send_digest(
    digest_items: list[dict],
    analyzer: Any,
    email_service: Any,
    deadline: Any = None,
    estimate_s: float = 0.0,
) -> bool:
    """Send the consolidated digest, per subscriber when subscribers exist."""
    if not digest_items or email_service is None:
        return False

    subscribers = await get_admin_configuration().get_subscribers()
    date_range = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    try:
        if not subscribers:
            return bool(await email_service.send_digest_report(digest_items, date_range=date_range))

        def _scope_key(scope: AnalysisScope) -> tuple[tuple[str, ...], ...]:
            return (
                tuple(sorted(value.casefold() for value in scope.management_groups)),
                tuple(sorted(value.casefold() for value in scope.subscriptions)),
                tuple(sorted(value.casefold() for value in scope.resource_groups)),
            )

        subscriber_scopes = {
            subscriber.email: AnalysisScope.from_subscriber(subscriber)
            for subscriber in subscribers
        }
        unique_scopes: dict[tuple[tuple[str, ...], ...], AnalysisScope] = {}
        for scope in subscriber_scopes.values():
            if scope.is_bounded:
                unique_scopes.setdefault(_scope_key(scope), scope)

        scoped_results: dict[tuple[tuple[tuple[str, ...], ...], int], Any] = {}
        if unique_scopes:
            semaphore = asyncio.Semaphore(get_settings().max_concurrent_analyses)

            async def _analyze_scoped(item: dict, scope: AnalysisScope) -> Any:
                async with semaphore:
                    if deadline is not None and not deadline.has_budget_for(max(estimate_s, 1.0)):
                        raise RuntimeError("run deadline cannot fit another scoped analysis")
                    return await analyzer.analyze_update(item["update"], scope=scope)

            jobs = [
                (scope_key, index, _analyze_scoped(item, scope))
                for scope_key, scope in unique_scopes.items()
                for index, item in enumerate(digest_items)
                if item.get("result")
            ]
            outcomes = await asyncio.gather(
                *(job[2] for job in jobs),
                return_exceptions=True,
            )
            for (scope_key, index, _), outcome in zip(jobs, outcomes):
                scoped_results[(scope_key, index)] = outcome

        async def _customize_and_send(subscriber) -> bool:
            scope = subscriber_scopes[subscriber.email]
            labels = get_labels(subscriber.language)
            with_results: list[tuple[int, dict, Any]] = []
            items_by_index: dict[int, dict] = {}
            for index, item in enumerate(digest_items):
                if not item.get("result"):
                    items_by_index[index] = item
                    continue
                source_result = item["result"]
                if scope.is_bounded:
                    source_result = scoped_results.get((_scope_key(scope), index))
                    if isinstance(source_result, BaseException) or source_result is None:
                        logger.warning(
                            "subscriber_scoped_analysis_failed",
                            subscriber=subscriber.email,
                            update_id=getattr(item["update"], "id", ""),
                            error=(
                                type(source_result).__name__
                                if isinstance(source_result, BaseException)
                                else "missing_result"
                            ),
                        )
                        items_by_index[index] = {
                            **item,
                            "result": None,
                            "skip_reason": labels["subscriber_scope_analysis_failed"],
                            "archive_id": "",
                            "archive_url": "",
                            "subscriber_scope_bounded": True,
                        }
                        continue
                with_results.append((index, item, source_result))

            customized = await asyncio.gather(
                *[
                    analyzer.customize_for_subscriber(source_result, subscriber, item["update"])
                    for _, item, source_result in with_results
                ],
                return_exceptions=True,
            )
            for (index, item, source_result), result in zip(with_results, customized):
                if isinstance(result, BaseException):
                    items_by_index[index] = {
                        **item,
                        "result": source_result,
                        "archive_id": "" if scope.is_bounded else item.get("archive_id", ""),
                        "archive_url": "" if scope.is_bounded else item.get("archive_url", ""),
                        "subscriber_scope_bounded": scope.is_bounded,
                    }
                else:
                    items_by_index[index] = {
                        **item,
                        "result": result,
                        "archive_id": "" if scope.is_bounded else item.get("archive_id", ""),
                        "archive_url": "" if scope.is_bounded else item.get("archive_url", ""),
                        "subscriber_scope_bounded": scope.is_bounded,
                    }
            items = [items_by_index[index] for index in range(len(digest_items))]
            return bool(
                await email_service.send_digest_report(
                    items,
                    date_range=date_range,
                    recipient=subscriber.email,
                    language=subscriber.language,
                )
            )

        outcomes = await asyncio.gather(
            *[_customize_and_send(sub) for sub in subscribers],
            return_exceptions=True,
        )
        delivered = sum(1 for outcome in outcomes if outcome is True)
        if delivered < len(subscribers):
            logger.warning(
                "orchestrator_digest_partial",
                delivered=delivered,
                total=len(subscribers),
            )
        return delivered > 0
    except Exception as exc:
        logger.warning("orchestrator_digest_failed", error=str(exc))
        return False
