#!/usr/bin/env python
"""
AzBrief Local Test CLI

CLI tool for testing the Azure Update analysis Agent locally.
Directly invokes the Agent without an Automation Account.

Usage:
    # List recent updates
    python -m scripts.test_local list

    # Analyze a specific URL
    python -m scripts.test_local analyze --url "https://azure.microsoft.com/updates/..."

    # Analyze the most recent update
    python -m scripts.test_local analyze --latest

    # Analyze all updates after a specific date
    python -m scripts.test_local analyze --from 2026-02-01

    # Analyze all updates within a date range
    python -m scripts.test_local analyze --from 2026-02-01 --to 2026-02-10

    # Export per-update analysis to a local JSONL file (no email sent)
    python -m scripts.test_local analyze --from 2026-02-01 --to 2026-02-10 --jsonl results.jsonl
    python -m scripts.test_local analyze --latest --jsonl results.jsonl

    # View resource summary
    python -m scripts.test_local resources
"""

import argparse
import asyncio
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.agent.analyzer import AnalysisResult, AzureUpdateAnalyzer
from src.config import SPECIALIST_AGENT_ROLES, get_settings
from src.email.service import EmailService
from src.rss.parser import AzureUpdate, AzureUpdateParser
from src.services.resource_graph import ResourceGraphService

# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------


def _setup_logging() -> Path:
    """Configure logging via centralized module.

    CLI uses CRITICAL console level to keep terminal clean.
    All logs (DEBUG+) go to the file.
    """
    from src.logging_config import setup_logging

    log_file = setup_logging(console_level="CRITICAL")
    return log_file or Path("logs/azbrief.log")


def _truncate(text: str, max_len: int = 72) -> str:
    """Truncate text with ellipsis only when needed."""
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"


def _format_date(dt: datetime | None) -> str:
    """Format datetime for CLI display (YYYY-MM-DD HH:MM)."""
    if dt is None:
        return "N/A"
    return dt.strftime("%Y-%m-%d %H:%M")


def _format_elapsed(seconds: float) -> str:
    """Format elapsed time for display."""
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes = int(seconds) // 60
    secs = seconds - minutes * 60
    return f"{minutes}m {secs:.0f}s"


def _type_badge(update_type: str | None) -> str:
    """Return a short badge for update_type."""
    if not update_type:
        return ""
    badges = {
        "General Availability": "GA",
        "Public Preview": "Preview",
        "Private Preview": "Private Preview",
        "Retirement": "Retirement",
        "Breaking Change": "⚠ Breaking",
        "Security Update": "Security",
        "Feature": "Feature",
    }
    return badges.get(update_type, update_type)


def _clean_title(title: str) -> str:
    """Strip redundant status/type prefixes from update title.

    RSS titles often look like:
      '[Launched] Generally Available: Actual Title Here'
      '[In preview] Public Preview: Actual Title Here'
    When we already display badges, the prefixes are redundant.
    """
    # Remove leading status bracket: [Launched], [In preview], etc.
    cleaned = re.sub(r"^\[.*?\]\s*", "", title)

    # Remove leading type prefix: 'Generally Available:', 'Public Preview:', etc.
    type_prefixes = (
        "Generally Available:",
        "General Availability:",
        "Public Preview:",
        "Private Preview:",
        "Retirement:",
    )
    for prefix in type_prefixes:
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix) :].lstrip()
            break

    return cleaned or title


def _format_eta(remaining: int, avg_seconds: float) -> str:
    """Format estimated time remaining."""
    eta = remaining * avg_seconds
    if eta < 60:
        return f"~{eta:.0f}s"
    return f"~{eta / 60:.0f}m"


def _build_jsonl_record(update: AzureUpdate, result: AnalysisResult) -> dict:
    """Build a JSON-serializable record for one analyzed update.

    Combines the source update metadata with the full analysis result so each
    JSONL line is a self-contained, per-update report.

    Args:
        update: Original Azure Update
        result: Analysis result produced by the agent

    Returns:
        JSON-serializable dict with update metadata and the full analysis
    """
    return {
        "update": update.to_dict(),
        "analysis": result.model_dump(mode="json"),
        "analyzed_at": datetime.now(timezone.utc).isoformat(),
    }


def _write_jsonl_records(path: Path, records: list[dict]) -> int:
    """Append analysis records to a JSONL file (one JSON object per line).

    Uses append mode to match the project's JSONL convention and to avoid
    destroying existing exports.

    Args:
        path: Destination JSONL file path
        records: List of JSON-serializable record dicts

    Returns:
        Number of records written
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return len(records)


async def list_updates(limit: int = 10) -> None:
    """Display a list of recent Azure Updates."""
    print("\n📡 Fetching Azure Update RSS feed...")

    try:
        parser = AzureUpdateParser()
        updates = await parser.get_updates()
    except Exception as e:
        print(f"❌ Unable to fetch RSS feed: {e}")
        return

    display = updates[:limit]
    print(f"\n📋 Recent Azure Updates ({len(display)}/{len(updates)})")
    print("=" * 80)

    for i, update in enumerate(display, 1):
        badge = _type_badge(update.update_type)
        badge_str = f" [{badge}]" if badge else ""
        title = _clean_title(update.title)
        services = ", ".join(update.azure_services[:3]) if update.azure_services else "N/A"

        print(f"\n[{i}]{badge_str} {_truncate(title, 72)}")
        print(f"    📅 {_format_date(update.published_date)}  🏷️  {services}")
        print(f"    🔗 {update.link}")

    print("\n" + "=" * 80)
    print(f"Total {len(updates)} updates found.")


async def analyze_update(url: str = None, latest: bool = False, jsonl_path: str = None) -> None:
    """Analyze an Azure Update.

    Args:
        url: Azure Update URL to analyze
        latest: Analyze the most recent update
        jsonl_path: When set, write the analysis result to this JSONL file and
            skip all email delivery
    """
    parser = AzureUpdateParser()

    if latest:
        print("\n📡 Fetching latest Azure Update...")
        updates = await parser.get_updates()
        if not updates:
            print("❌ No updates found.")
            return
        update = updates[0]
        print(f"✅ Latest update selected: {_truncate(update.title, 60)}")
    elif url:
        print(f"\n📡 Fetching update info: {url}")
        update = await parser.get_update_by_url(url)

        if not update:
            # Fetch info directly from URL (individual RSS API or HTML parsing)
            details = await parser.fetch_update_details(url)

            # Use the update object fetched from the individual RSS API if available
            if details.get("update"):
                update = details["update"]
            else:
                update = AzureUpdate(
                    id=url,
                    title=details.get("title", "Unknown Update"),
                    description=details.get("content", ""),
                    link=url,
                    published_date=None,
                    categories=[],
                    azure_services=[],
                    update_type=None,
                    status=None,
                )
    else:
        print("❌ Please specify --url or --latest option.")
        return

    print("\n" + "=" * 80)
    print("📢 Update to analyze")
    print("=" * 80)
    print(f"Title: {update.title}")
    print(f"Description: {_truncate(update.description, 200)}")
    print(f"Published: {_format_date(update.published_date)}")
    print(f"Type: {_type_badge(update.update_type) or 'N/A'}")
    print(f"Services: {', '.join(update.azure_services) if update.azure_services else 'N/A'}")
    print(f"Link: {update.link}")
    print("=" * 80)

    print("\n🤖 Starting AI Agent analysis...")
    print("-" * 40)

    try:
        analyzer = AzureUpdateAnalyzer()

        # Check resource query status first
        print("\n📊 Querying resources...")
        resource_summary, resource_query_success = await analyzer.get_resource_summary()
        if resource_query_success:
            print("✅ Resource query succeeded")
            # Resource summary preview (first 500 chars)
            preview = (
                resource_summary[:500] + "..." if len(resource_summary) > 500 else resource_summary
            )
            print(f"\n{preview}")
        else:
            print("❌ Resource query failed — AI will analyze with 'unknown' relevance")
            print(f"\n{resource_summary}")
        print("-" * 40)

        print("\n🔄 Running AI Agent analysis...")
        analysis_start = time.monotonic()
        result = await analyzer.analyze_update(update)
        analysis_elapsed = time.monotonic() - analysis_start

        # Set urgency badge
        urgency_badges = {
            "critical": "🔴 CRITICAL",
            "high": "🟠 HIGH",
            "medium": "🟡 MEDIUM",
            "low": "🟢 LOW",
        }
        urgency_badge = urgency_badges.get(result.urgency.value, "🟡 MEDIUM")

        print("\n" + "=" * 80)
        print(f"📊 Analysis result | {urgency_badge} | ⏱️ {_format_elapsed(analysis_elapsed)}")
        print("=" * 80)

        # One-line summary (if available)
        if result.one_line_summary:
            print(f"\n💬 {result.one_line_summary}")

        print(f"\n📌 Category: {getattr(result, 'update_category', 'N/A')}")
        print(f"📌 Urgency: {result.urgency.value.upper()}")
        print(f"📌 Relevance: {result.relevance.value}")
        print(f"📌 Notification required: {'Yes' if result.should_notify else 'No'}")

        print(f"\n📝 Detailed analysis:")
        print("-" * 40)
        print(result.relevance_reason)
        print("-" * 40)

        # Impact analysis details (new fields)
        if result.impact_details:
            print(f"\n💰 Impact analysis:")
            print(f"   💵 Cost: {result.impact_details.cost_impact}")
            print(f"   🔒 Security: {result.impact_details.security_impact}")
            print(f"   ⚡ Performance: {result.impact_details.performance_impact}")
            print(f"   🔧 Operations: {result.impact_details.operational_impact}")

        if result.affected_resources:
            print(f"\n🎯 Affected resources ({len(result.affected_resources)}):")
            display_limit = 100
            for res in result.affected_resources[:display_limit]:
                name = res.get("name", "Unknown")
                res_type = res.get("type", "Unknown")
                reason = res.get("reason", "")
                if reason:
                    print(f"   - {name} ({res_type})")
                    print(f"     {reason}")
                else:
                    print(f"   - {name} ({res_type})")
            if len(result.affected_resources) > display_limit:
                print(f"   ... and {len(result.affected_resources) - display_limit} more")

        # Action items (new format)
        if result.action_items:
            print(f"\n✅ Action items ({len(result.action_items)}):")
            for item in result.action_items:
                urgency_icon = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🟢"}.get(
                    item.urgency, "🟡"
                )
                print(f"\n   {urgency_icon} [{item.priority}] {item.task}")
                if item.target_resources:
                    print(f"      Target: {', '.join(item.target_resources[:5])}")
                if item.procedure:
                    print(f"      Procedure: {item.procedure}")
                if item.cli_command:
                    print(f"      CLI: {item.cli_command}")
                if item.estimated_time:
                    print(f"      Estimated time: {item.estimated_time}")
                if item.deadline:
                    print(f"      Deadline: {item.deadline}")
                if item.risk_if_not_done:
                    print(f"      ⚠️ Risk if not addressed: {item.risk_if_not_done}")
        elif result.recommendations:
            print(f"\n💡 Recommendations:")
            for i, rec in enumerate(result.recommendations, 1):
                print(f"   {i}. {rec}")

        if result.impact_summary and not result.impact_details:
            # Skip if impact_summary is the same as or a prefix of relevance_reason
            if (
                result.impact_summary.strip()
                != result.relevance_reason.strip()[: len(result.impact_summary.strip())]
            ):
                print(f"\n📋 Impact summary:")
                print(f"   {result.impact_summary[:500]}")

        if result.reference_docs:
            print(f"\n📚 Reference docs:")
            for doc in result.reference_docs[:5]:
                if isinstance(doc, dict):
                    title = doc.get("title", "Document")
                    url = doc.get("url", "")
                    related = doc.get("related_content", "")
                    print(f"   - {title}")
                    if related:
                        print(f"     → {related}")
                    if url:
                        print(f"     {url}")
                elif isinstance(doc, str):
                    print(f"   - {doc}")

        # Additional checks required (new fields)
        if result.additional_checks:
            print(f"\n⚠️ Additional checks required:")
            for check in result.additional_checks:
                print(f"   - {check}")

        print("\n" + "=" * 80)

        # JSONL export mode: write the result locally and skip all email delivery
        if jsonl_path:
            out_path = Path(jsonl_path)
            count = _write_jsonl_records(out_path, [_build_jsonl_record(update, result)])
            print(f"\n💾 JSON report saved: {out_path} ({count} record, append mode)")
            print("   (Email delivery skipped — --jsonl mode)")
            print("=" * 80)
            return

        # Per-subscriber customization test
        subscribers = get_settings().get_subscribers()
        if subscribers and (result.should_notify or not get_settings().report_filtering_enabled):
            print(f"\n👥 Subscriber customization ({len(subscribers)} subscribers)")
            print("-" * 40)
            email_service = EmailService()
            for si, subscriber in enumerate(subscribers, 1):
                print(f"\n  [{si}/{len(subscribers)}] 🧑‍💼 {subscriber.name}")
                print(f"     Role: {subscriber.role or '(No role specified)'}")
                cust_start = time.monotonic()
                try:
                    customized = await analyzer.customize_for_subscriber(result, subscriber, update)
                    cust_elapsed = time.monotonic() - cust_start
                    print(f"     ⏱️ Customization done: {_format_elapsed(cust_elapsed)}")

                    # Compare customization results
                    if customized.one_line_summary != result.one_line_summary:
                        print(f"     💬 Custom summary: {customized.one_line_summary}")
                    if customized.urgency != result.urgency:
                        urgency_badges = {
                            "critical": "🔴",
                            "high": "🟠",
                            "medium": "🟡",
                            "low": "🟢",
                        }
                        badge = urgency_badges.get(customized.urgency.value, "🟡")
                        print(f"     📌 Custom urgency: {badge} {customized.urgency.value.upper()}")
                    if len(customized.action_items) != len(result.action_items):
                        print(
                            f"     ✅ Custom action items: {len(customized.action_items)} (Original: {len(result.action_items)})"
                        )

                    # Send via email/console
                    await email_service.send_analysis_report(
                        update,
                        customized,
                        recipient=subscriber.email,
                        language=subscriber.language,
                    )
                except Exception as e:
                    cust_elapsed = time.monotonic() - cust_start
                    print(f"     ❌ Customization failed ({_format_elapsed(cust_elapsed)}): {e}")
            print("-" * 40)
        elif result.should_notify:
            # No subscribers configured — use default behavior
            email_service = EmailService()
            await email_service.send_analysis_report(update, result)
        else:
            print("[ℹ️ Update deemed not relevant — notification was not sent]")
            print("=" * 80)

    except Exception as e:
        print(f"\n❌ Error during analysis: {e}")
        import traceback

        traceback.print_exc()


async def analyze_date_range(from_date: str, to_date: str = None, jsonl_path: str = None) -> None:
    """Analyze all Azure Updates within a date range (concurrently).

    Uses asyncio.Semaphore to run multiple analyses in parallel,
    controlled by the MAX_CONCURRENT_ANALYSES setting (default: 3).

    Args:
        from_date: Start date (YYYY-MM-DD)
        to_date: End date (YYYY-MM-DD). If None, analyzes all updates from start date onward.
        jsonl_path: When set, write per-update analysis results to this JSONL file
            and skip all email delivery (no subscriber customization, no digest).
    """
    # Parse dates
    try:
        start_date = datetime.strptime(from_date, "%Y-%m-%d")
    except ValueError:
        print(f"❌ Invalid date format: {from_date} (use YYYY-MM-DD)")
        return

    end_date = None
    if to_date:
        try:
            end_date = datetime.strptime(to_date, "%Y-%m-%d")
        except ValueError:
            print(f"❌ Invalid date format: {to_date} (use YYYY-MM-DD)")
            return

        if end_date < start_date:
            print(f"❌ End date ({to_date}) cannot be before start date ({from_date}).")
            return

    date_range_str = f"{from_date} ~ {to_date}" if to_date else f"{from_date} ~ present"
    print(f"\n📡 Fetching Azure Update RSS feed...")
    print(f"📅 Analysis period: {date_range_str}")

    parser = AzureUpdateParser()
    updates = await parser.get_updates_by_date_range(start_date, end_date)

    if not updates:
        print(f"\n⚠️ No Azure Updates published in the period ({date_range_str}).")
        print(
            "   ℹ️ The live RSS feed only keeps a rolling window of recent updates. "
            "For older periods, refresh the local history archive with:\n"
            "      python -m scripts.crawl_azure_updates"
        )
        return

    print(f"\n📋 Target updates: {len(updates)}")
    print("=" * 80)
    for i, update in enumerate(updates, 1):
        badge = _type_badge(update.update_type)
        badge_str = f" [{badge}]" if badge else ""
        title = _clean_title(update.title)
        services = ", ".join(update.azure_services[:3]) if update.azure_services else ""
        svc_str = f"  🏷️ {services}" if services else ""
        print(f"  [{i}]{badge_str} {_truncate(title, 68)}")
        print(f"      📅 {_format_date(update.published_date)}{svc_str}")
    print("=" * 80)

    # Initialize shared services once
    settings = get_settings()
    max_concurrent = settings.max_concurrent_analyses
    analyzer = AzureUpdateAnalyzer()
    email_service = EmailService()
    subscribers = settings.get_subscribers()
    if subscribers:
        names = ", ".join(f"{s.name}({s.role or '-'})" for s in subscribers)
        print(f"\n👥 {len(subscribers)} subscribers registered: {names}")

    if jsonl_path:
        print(f"\n💾 JSONL export mode → {jsonl_path} (email delivery disabled)")

    # Pre-fetch resource summary once (shared across all analyses)
    print("\n📊 Querying resources (reused for all analyses)...")
    resource_summary, resource_query_success = await analyzer.get_resource_summary()
    if resource_query_success:
        print("✅ Resource query succeeded")
    else:
        print("❌ Resource query failed — AI will analyze with 'unknown' relevance")

    # Suppress per-analysis verbose output in concurrent mode
    concurrent_mode = max_concurrent > 1 and len(updates) > 1
    if concurrent_mode:
        os.environ["AZBRIEF_VERBOSE"] = "false"
        print(f"\n🔄 Concurrent analysis: {len(updates)} updates × {max_concurrent} parallel")
    else:
        print(f"\n🔄 Sequential analysis: {len(updates)} updates")
    print("-" * 80)

    # ── Concurrent analysis with semaphore ──────────────────
    semaphore = asyncio.Semaphore(max_concurrent)
    results_lock = asyncio.Lock()  # Protect shared mutable state
    results_summary: list[dict] = []
    digest_items_default: list[dict] = []
    digest_items_by_subscriber: dict[str, dict] = {}
    jsonl_records: list[tuple[int, dict]] = []  # (index, record) for JSONL export mode
    completed_count = 0
    notify_count = 0

    async def _process_one(idx: int, update: AzureUpdate) -> None:
        """Analyze a single update and collect results (thread-safe)."""
        nonlocal completed_count, notify_count

        async with semaphore:
            item_start = time.monotonic()
            title = _truncate(_clean_title(update.title), 50)
            if concurrent_mode:
                print(f"  ▶ [{idx}/{len(updates)}] {title}")

            try:
                result = await analyzer.analyze_update(update)
                elapsed = time.monotonic() - item_start

                # Subscriber customization — run in parallel for this update
                # Always customize for ALL subscribers (not just should_notify)
                # to ensure language consistency in the digest email.
                # customize_for_subscriber fast-paths when subscriber language
                # matches report_language and the update is not_relevant.
                analyzed_item = {"update": update, "result": result, "skip_reason": ""}
                sub_customizations: dict[str, dict] = {}

                if subscribers and not jsonl_path:
                    cust_tasks = []
                    for sub in subscribers:
                        cust_tasks.append(analyzer.customize_for_subscriber(result, sub, update))
                    cust_results = await asyncio.gather(*cust_tasks, return_exceptions=True)
                    for sub, cust_result in zip(subscribers, cust_results):
                        if isinstance(cust_result, Exception):
                            sub_customizations[sub.email] = analyzed_item
                        else:
                            sub_customizations[sub.email] = {
                                "update": update,
                                "result": cust_result,
                                "skip_reason": "",
                            }

                # Collect results under lock
                async with results_lock:
                    completed_count += 1
                    urgency_icon = {
                        "critical": "🔴",
                        "high": "🟠",
                        "medium": "🟡",
                        "low": "🟢",
                    }.get(result.urgency.value, "🟡")
                    notify_mark = "📧" if result.should_notify else "  "
                    summary_text = _truncate(result.one_line_summary or "", 45)
                    print(
                        f"  ✅ [{completed_count}/{len(updates)}] {urgency_icon} "
                        f"{result.relevance.value:<12} {notify_mark} "
                        f"{title}  ({_format_elapsed(elapsed)})"
                    )
                    if summary_text:
                        print(f"     💬 {summary_text}")

                    if result.should_notify:
                        notify_count += 1

                    # Collect JSONL records (export mode) or digest items
                    if jsonl_path:
                        jsonl_records.append((idx, _build_jsonl_record(update, result)))
                    elif subscribers:
                        # sub_customizations always populated when subscribers exist
                        for sub in subscribers:
                            sub_key = sub.email
                            if sub_key not in digest_items_by_subscriber:
                                digest_items_by_subscriber[sub_key] = {
                                    "subscriber": sub,
                                    "items": [],
                                }
                            digest_items_by_subscriber[sub_key]["items"].append(
                                sub_customizations.get(sub_key, analyzed_item)
                            )
                    else:
                        digest_items_default.append(analyzed_item)

                    results_summary.append(
                        {
                            "index": idx,
                            "title": _truncate(_clean_title(update.title), 42),
                            "urgency": result.urgency.value,
                            "relevance": result.relevance.value,
                            "affected": len(result.affected_resources),
                            "notify": result.should_notify,
                            "summary": result.one_line_summary or "",
                            "status": "✅",
                            "elapsed": elapsed,
                        }
                    )

            except Exception as e:
                elapsed = time.monotonic() - item_start
                async with results_lock:
                    completed_count += 1
                    print(
                        f"  ❌ [{completed_count}/{len(updates)}] {title}  ({_format_elapsed(elapsed)}) {e}"
                    )
                    results_summary.append(
                        {
                            "index": idx,
                            "title": _truncate(_clean_title(update.title), 42),
                            "urgency": "-",
                            "relevance": "-",
                            "affected": 0,
                            "notify": False,
                            "summary": "",
                            "status": "❌",
                            "elapsed": elapsed,
                        }
                    )

    # Launch all tasks (semaphore controls concurrency)
    batch_start = time.monotonic()
    tasks = [_process_one(i, update) for i, update in enumerate(updates, 1)]
    await asyncio.gather(*tasks)
    total_elapsed = time.monotonic() - batch_start

    # Restore verbose mode
    if concurrent_mode:
        os.environ["AZBRIEF_VERBOSE"] = "true"

    # Sort results by original index for display
    results_summary.sort(key=lambda r: r["index"])

    # Print final summary
    success_count = sum(1 for r in results_summary if r["status"] == "✅")
    fail_count = sum(1 for r in results_summary if r["status"] == "❌")
    relevant_count = sum(
        1 for r in results_summary if r["relevance"] in ("relevant", "opportunity")
    )
    print(f"\n\n{'=' * 80}")
    print(f"📊 Batch analysis summary ({date_range_str})")
    print(f"{'=' * 80}")
    concurrency_note = f" (×{max_concurrent} concurrent)" if concurrent_mode else ""
    print(
        f"  Total {len(updates)} | Analyzed {success_count} | Failed {fail_count}"
        f" | Relevant {relevant_count} | Notified {notify_count}"
        f" | Elapsed {_format_elapsed(total_elapsed)}{concurrency_note}"
    )
    hr = "\u2500" * 80
    print(hr)

    for r in results_summary:
        urgency_icon = {
            "critical": "\U0001f534",
            "high": "\U0001f7e0",
            "medium": "\U0001f7e1",
            "low": "\U0001f7e2",
            "-": " ",
        }.get(r.get("urgency", "-"), "\U0001f7e1")
        notify_mark = "\U0001f4e7" if r["notify"] else "  "
        elapsed_str = _format_elapsed(r["elapsed"])
        prefix_len = 11 + min(len(r["relevance"]), 12) + 1 + 3 + 1
        suffix_len = len(elapsed_str) + 4
        max_title = max(80 - prefix_len - suffix_len, 20)
        print(
            f"  {r['index']:>2}. {r['status']} {urgency_icon} "
            f"{r['relevance']:<12} {notify_mark} "
            f"{_truncate(r['title'], max_title)}  ({elapsed_str})"
        )
        if r["summary"]:
            print(f"      \U0001f4ac {_truncate(r['summary'], 68)}")

    print(f"{'=' * 80}\n")

    # ─── Write JSONL export or send consolidated digest email ───
    if jsonl_path:
        out_path = Path(jsonl_path)
        jsonl_records.sort(key=lambda x: x[0])
        count = _write_jsonl_records(out_path, [rec for _, rec in jsonl_records])
        print(f"💾 JSON reports saved: {out_path} ({count} records, append mode)")
        print("   (Email delivery skipped — --jsonl mode)")
    elif digest_items_by_subscriber:
        print("📧 Sending daily digest emails...")
        for sub_email, data in digest_items_by_subscriber.items():
            sub = data["subscriber"]
            sub_items = data["items"]
            try:
                sent = await email_service.send_digest_report(
                    sub_items,
                    date_range=date_range_str,
                    recipient=sub.email,
                    language=sub.language,
                )
                status = "✅" if sent else "⏭️"
                print(f"  {status} {sub.name} ({len(sub_items)} items)")
            except Exception as e:
                print(f"  ❌ {sub.name}: {e}")
    elif digest_items_default:
        print("📧 Sending daily digest email...")
        try:
            await email_service.send_digest_report(
                digest_items_default,
                date_range=date_range_str,
            )
        except Exception as e:
            print(f"  ❌ Digest send failed: {e}")


async def show_resources() -> None:
    """Display resource summary for the subscription."""
    print("\n📊 Fetching Azure resource summary...")

    try:
        analyzer = AzureUpdateAnalyzer()
        summary, success = await analyzer.get_resource_summary()

        print("\n" + "=" * 80)
        print("🏢 Azure Resource overview")
        print("=" * 80)
        if success:
            print("✅ Resource query succeeded")
        else:
            print("❌ Resource query failed")
        print("-" * 40)
        print(summary)
        print("=" * 80)

    except Exception as e:
        print(f"\n❌ Error during resource query: {e}")
        import traceback

        traceback.print_exc()


async def test_rss() -> None:
    """Test RSS parsing."""
    print("\n🧪 RSS parsing test...")

    parser = AzureUpdateParser()

    try:
        updates = await parser.get_updates()
        print(f"✅ RSS parsing succeeded: {len(updates)} updates")

        if updates:
            sample = updates[0]
            print(f"\nSample update:")
            print(f"  - ID: {sample.id}")
            print(f"  - Title: {_truncate(sample.title, 60)}")
            print(f"  - Services: {sample.azure_services}")
            print(f"  - Type: {_type_badge(sample.update_type) or 'N/A'}")
    except Exception as e:
        print(f"❌ RSS parsing failed: {e}")


async def check_config() -> None:
    """Check current configuration."""
    print("\n⚙️ Configuration check...")
    print("=" * 80)

    settings = get_settings()

    print(f"Azure Tenant ID: {settings.azure_tenant_id}")
    print(f"Azure Subscription ID: {settings.azure_subscription_id}")
    print(f"Azure Client ID: {settings.azure_client_id or '(not set - using az login)'}")
    print(f"Foundry Project Endpoint: {settings.foundry_project_endpoint or '(not set)'}")
    role_labels = {
        "coordinator": "Coordinator",
        "resource_graph": "Resource Graph",
        "azure_mcp": "Azure MCP",
        "azure_api": "Azure API",
        "report_writer": "Report Writer",
        "quality_reviewer": "Quality Reviewer",
    }
    for role in SPECIALIST_AGENT_ROLES:
        label = role_labels[role]
        print(f"Foundry {label} Agent: {settings.foundry_agent_for_role(role) or '(not set)'}")
    print(f"Foundry Ready: {'Yes' if settings.use_foundry else 'No'}")
    print(
        "Specialist Roster Complete: "
        f"{'Yes' if settings.has_complete_specialist_roster else 'No'}"
    )
    print(f"Use Email: {'Yes' if settings.use_email else 'No (console output mode)'}")
    print(f"Log Level: {settings.log_level}")

    print("=" * 80)


def main():
    parser = argparse.ArgumentParser(
        description="AzBrief Local Test CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m scripts.test_local config          # Configuration check
  python -m scripts.test_local list            # Recent update list
  python -m scripts.test_local list -n 20      # Recent 20 updates
  python -m scripts.test_local resources       # Resource summary
  python -m scripts.test_local analyze --latest    # Analyze latest update
  python -m scripts.test_local analyze --url "..." # Analyze specific URL
  python -m scripts.test_local analyze --from 2026-02-01                # Analyze all after date
  python -m scripts.test_local analyze --from 2026-02-01 --to 2026-02-10  # Analyze date range
  python -m scripts.test_local analyze --latest --jsonl out.jsonl        # Export to JSONL (no email)
  python -m scripts.test_local rss             # RSS parsing test
        """,
    )

    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # config command
    subparsers.add_parser("config", help="Configuration check")

    # list command
    list_parser = subparsers.add_parser("list", help="Recent Azure Update list")
    list_parser.add_argument(
        "-n", "--limit", type=int, default=10, help="Number of items to display (default: 10)"
    )

    # analyze command
    analyze_parser = subparsers.add_parser("analyze", help="Analyze Azure Update")
    analyze_group = analyze_parser.add_mutually_exclusive_group()
    analyze_group.add_argument("--url", type=str, help="Azure Update URL to analyze")
    analyze_group.add_argument("--latest", action="store_true", help="Analyze latest update")
    analyze_group.add_argument(
        "--from",
        dest="from_date",
        type=str,
        metavar="YYYY-MM-DD",
        help="Start date (analyze all updates after this date, can be used with --to)",
    )
    analyze_parser.add_argument(
        "--to",
        dest="to_date",
        type=str,
        metavar="YYYY-MM-DD",
        help="End date (use with --from, omit for all updates from start date onward)",
    )
    analyze_parser.add_argument(
        "--jsonl",
        dest="jsonl_path",
        type=str,
        metavar="FILE",
        help="Write per-update analysis results to a local JSONL file instead of sending email",
    )

    # resources command
    subparsers.add_parser("resources", help="Azure resource summary")

    # rss command
    subparsers.add_parser("rss", help="RSS parsing test")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    # 로그 설정: 콘솔(CRITICAL) + 파일(DEBUG)
    log_file = _setup_logging()
    print(f"📝 Log file: {log_file}")

    # Async execution
    if args.command == "config":
        asyncio.run(check_config())
    elif args.command == "list":
        asyncio.run(list_updates(args.limit))
    elif args.command == "analyze":
        if args.from_date:
            asyncio.run(
                analyze_date_range(
                    from_date=args.from_date,
                    to_date=args.to_date,
                    jsonl_path=args.jsonl_path,
                )
            )
        elif args.to_date and not args.from_date:
            print("❌ --to option must be used together with --from.")
        else:
            asyncio.run(
                analyze_update(url=args.url, latest=args.latest, jsonl_path=args.jsonl_path)
            )
    elif args.command == "resources":
        asyncio.run(show_resources())
    elif args.command == "rss":
        asyncio.run(test_rss())


if __name__ == "__main__":
    main()
