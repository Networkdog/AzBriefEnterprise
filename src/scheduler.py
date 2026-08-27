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
    from src.email.service import EmailService
    from src.orchestrator import RunRecord, execute_run
    from src.rss.parser import AzureUpdateParser

    analyzer = HostedAgentAnalyzer()
    record = RunRecord(run_id=uuid.uuid4().hex, dry_run=dry_run)

    try:
        await execute_run(record, analyzer, EmailService(), AzureUpdateParser())
    finally:
        await _close_analyzer(analyzer)

    summary = record.to_dict()
    if record.status != "completed":
        logger.error("scheduled_run_failed", **summary)
        return 1
    logger.info("scheduled_run_complete", **summary)
    return 0


def main() -> None:
    """Console entry point for the Container Apps Job."""
    setup_logging(file_enabled=False)
    dry_run = os.environ.get("DRY_RUN", "false").strip().lower() == "true"
    sys.exit(asyncio.run(run_scheduled_digest(dry_run=dry_run)))


if __name__ == "__main__":
    main()
