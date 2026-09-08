"""Run one read-only smoke analysis through the deployed Hosted Agent."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Optional

from src.agent.hosted_client import HostedAgentAnalyzer
from src.config import get_settings
from src.rss.parser import AzureUpdate, AzureUpdateParser

_FAILURE_MARKERS = (
    "report generation failed:",
    "llm circuit breaker open",
)


async def _resolve_update(parser: AzureUpdateParser, url: str) -> AzureUpdate:
    if not url:
        updates = await parser.get_updates()
        if not updates:
            raise RuntimeError("Azure Update feed returned no updates")
        return updates[0]

    update = await parser.get_update_by_url(url)
    if update is not None:
        return update

    details = await parser.fetch_update_details(url)
    if details.get("update"):
        return details["update"]
    raise RuntimeError(f"Azure Update could not be resolved: {url}")


async def run_smoke(url: str = "") -> dict[str, object]:
    """Analyze one update through the deployed Hosted Agent and return a safe summary."""
    settings = get_settings()
    analyzer = HostedAgentAnalyzer(settings)
    update = await _resolve_update(AzureUpdateParser(), url)
    try:
        result = await analyzer.analyze_update(update)
    finally:
        await analyzer.close()

    if result.update_id != update.id:
        raise RuntimeError("Hosted Agent smoke result update_id does not match the request")
    analysis_text = result.relevance_reason.lower()
    if any(marker in analysis_text for marker in _FAILURE_MARKERS):
        raise RuntimeError("Hosted Agent returned a report-generation failure placeholder")

    relevance = getattr(result.relevance, "value", str(result.relevance))
    urgency = getattr(result.urgency, "value", str(result.urgency))
    return {
        "status": "completed",
        "hosted_agent": settings.foundry_hosted_agent_name,
        "trace_id": result._hosted_trace_id,
        "update_id": result.update_id,
        "relevance": relevance,
        "urgency": urgency,
        "importance": result.importance,
        "impact_level": result.impact_level,
        "affected_resources": len(result.affected_resources),
        "action_items": len(result.action_items),
        "references": len(result.reference_docs),
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--url",
        default="",
        help="Azure Update URL to analyze; defaults to the latest RSS item",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    summary = asyncio.run(run_smoke(args.url))
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
