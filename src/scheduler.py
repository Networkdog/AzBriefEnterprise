"""Scheduled digest control-plane run — entry point for the Container Apps Job.

The job owns RSS selection, checkpointing, and digest delivery. Each update's
model-mediated analysis runs behind the Foundry Hosted Agent endpoint, so this
process never constructs the LangGraph runtime itself.

The start point comes from the durable checkpoint and the watermark is written
back only when the run completes, so a failed execution (exit code 1) leaves the
window in place for the next one.

    python -m src.scheduler
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from datetime import datetime
from typing import Optional

from structlog import get_logger

from src.logging_config import setup_logging

logger = get_logger()


async def _close_analyzer(analyzer) -> None:
    """Release the analyzer proxy the way the app lifespan does."""
    close = getattr(analyzer, "close", None)
    if close:
        await close()


async def run_scheduled_digest(dry_run: bool = False) -> int:
    """Run one orchestrated digest and report a process exit code.

    Args:
        dry_run: Collect targets without analysing or sending email.

    Returns:
        0 when the run completed, 1 otherwise.
    """
    from src.agent.hosted_client import HostedAgentAnalyzer
    from src.archive.models import ArchiveSource
    from src.archive.service import ArchiveService
    from src.email.service import EmailService
    from src.orchestrator import RunRecord, execute_run
    from src.rss.parser import AzureUpdateParser

    analyzer = HostedAgentAnalyzer()
    record = RunRecord(
        run_id=uuid.uuid4().hex,
        source=ArchiveSource.SCHEDULED_DIGEST.value,
        dry_run=dry_run,
    )

    try:
        await execute_run(
            record,
            analyzer,
            EmailService(),
            AzureUpdateParser(),
            ArchiveService(),
        )
    finally:
        await _close_analyzer(analyzer)

    summary = record.to_dict()
    if record.status != "completed":
        logger.error("scheduled_run_failed", **summary)
        return 1
    logger.info("scheduled_run_complete", **summary)
    return 0


async def dispatch_scheduled_digest(
    dry_run: bool = False,
    now: Optional[datetime] = None,
) -> int:
    """Run only when one durable automatic schedule occurrence is due."""
    from src.admin.configuration import get_admin_configuration

    configuration = get_admin_configuration()
    try:
        lease = await configuration.claim_due_automatic_run(now=now)
    except Exception as exc:
        logger.error("scheduled_dispatch_claim_failed", error=str(exc))
        return 1

    if lease is None:
        logger.info("scheduled_dispatch_idle")
        return 0

    logger.info(
        "scheduled_dispatch_claimed",
        schedule_key=lease.schedule_key,
        scheduled_for=lease.scheduled_for.isoformat(),
    )
    result = 1
    try:
        result = await run_scheduled_digest(dry_run=dry_run)
    except Exception as exc:
        logger.error("scheduled_dispatch_run_failed", error=str(exc))
    try:
        await configuration.release_automatic_run(lease)
    except Exception as exc:
        logger.error("scheduled_dispatch_release_failed", error=str(exc))
        return 1
    return result


def main() -> None:
    """Console entry point for the Container Apps Job."""
    setup_logging(file_enabled=False)
    dry_run = os.environ.get("DRY_RUN", "false").strip().lower() == "true"
    dispatch_enabled = (
        os.environ.get("SCHEDULE_DISPATCH_ENABLED", "false").strip().lower() == "true"
    )
    run = dispatch_scheduled_digest if dispatch_enabled else run_scheduled_digest
    sys.exit(asyncio.run(run(dry_run=dry_run)))


if __name__ == "__main__":
    main()
