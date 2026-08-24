"""Analysis history store for cross-update intelligence.

Stores analysis results in a JSONL file for:
- Related update detection (same service, similar title)
- Trend detection (repeated patterns over time)
- Retirement tracking with D-day countdown

File-based storage (no database dependency). Thread-safe writes.
"""

import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from structlog import get_logger

logger = get_logger()

# Default paths
_DATA_DIR = Path(__file__).parent.parent.parent / "data"
_HISTORY_FILE = _DATA_DIR / "analysis_results.jsonl"
_RETIREMENT_FILE = _DATA_DIR / "retirement_tracker.json"
_HISTORY_LOCK = threading.Lock()
_RETIREMENT_LOCK = threading.Lock()

# Retention policy
MAX_HISTORY_DAYS = 90
MAX_HISTORY_RECORDS = 500


def _ensure_data_dir() -> None:
    """Ensure the data directory exists."""
    _DATA_DIR.mkdir(parents=True, exist_ok=True)


# =========================================================================
# Analysis History (JSONL append-only store)
# =========================================================================


def save_analysis_record(result: Any) -> None:
    """Append an analysis result summary to the history file.

    Args:
        result: AnalysisResult instance (or any object with matching attributes)
    """
    _ensure_data_dir()

    record = {
        "update_id": getattr(result, "update_id", ""),
        "update_title": getattr(result, "update_title", ""),
        "update_category": getattr(result, "update_category", ""),
        "analyzed_at": datetime.now(timezone.utc).isoformat(),
        "urgency": (
            result.urgency.value
            if hasattr(result, "urgency") and hasattr(result.urgency, "value")
            else str(getattr(result, "urgency", ""))
        ),
        "importance": getattr(result, "importance", ""),
        "impact_level": getattr(result, "impact_level", ""),
        "relevance": (
            result.relevance.value
            if hasattr(result, "relevance") and hasattr(result.relevance, "value")
            else str(getattr(result, "relevance", ""))
        ),
        "blast_radius_score": getattr(result, "blast_radius_score", 0),
        "one_line_summary": getattr(result, "one_line_summary", ""),
        "affected_resource_count": len(getattr(result, "affected_resources", [])),
        "affected_services": _extract_services(result),
        "affected_resource_types": _extract_resource_types(result),
        "action_item_count": len(getattr(result, "action_items", [])),
    }

    with _HISTORY_LOCK:
        try:
            with open(_HISTORY_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
            logger.debug(
                "analysis_history_saved",
                update_id=record["update_id"],
                category=record["update_category"],
            )
        except Exception as e:
            logger.warning("analysis_history_save_failed", error=str(e))


def _extract_services(result: Any) -> list[str]:
    """Extract unique service names from affected resources."""
    services = set()
    for r in getattr(result, "affected_resources", []):
        rtype = r.get("type", "")
        if rtype:
            # Extract service from type like "Microsoft.Compute/virtualMachines"
            parts = rtype.split("/")
            if len(parts) >= 2:
                services.add(parts[0] + "/" + parts[1])
    return sorted(services)


def _extract_resource_types(result: Any) -> list[str]:
    """Extract unique resource types from affected resources."""
    types = set()
    for r in getattr(result, "affected_resources", []):
        rtype = r.get("type", "")
        if rtype:
            types.add(rtype.lower())
    return sorted(types)


def load_recent_history(days: int = 30, max_records: int = 50) -> list[dict]:
    """Load recent analysis history records.

    Args:
        days: Number of days to look back
        max_records: Maximum number of records to return

    Returns:
        List of history records, most recent first
    """
    if not _HISTORY_FILE.exists():
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    records = []

    try:
        with open(_HISTORY_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                    analyzed_at = record.get("analyzed_at", "")
                    if analyzed_at:
                        dt = datetime.fromisoformat(analyzed_at)
                        if dt >= cutoff:
                            records.append(record)
                except (json.JSONDecodeError, ValueError):
                    continue
    except Exception as e:
        logger.warning("analysis_history_load_failed", error=str(e))
        return []

    # Sort by analyzed_at descending, limit to max_records
    records.sort(key=lambda r: r.get("analyzed_at", ""), reverse=True)
    return records[:max_records]


def find_related_updates(
    services: list[str],
    categories: list[str] | None = None,
    title_keywords: list[str] | None = None,
    days: int = 30,
    max_results: int = 10,
    exclude_update_id: str = "",
) -> list[dict]:
    """Find related updates from history.

    Args:
        services: Service names or resource types to match
        categories: Update categories to filter (optional)
        title_keywords: Keywords to search in titles (optional)
        days: Number of days to look back
        max_results: Maximum results
        exclude_update_id: Update ID to exclude (current update)

    Returns:
        List of matching history records
    """
    history = load_recent_history(days=days, max_records=200)
    if not history:
        return []

    matches = []
    services_lower = {s.lower() for s in services}
    keywords_lower = [kw.lower() for kw in (title_keywords or [])]

    for record in history:
        if record.get("update_id") == exclude_update_id:
            continue

        score = 0

        # Service match (strongest signal)
        record_services = {s.lower() for s in record.get("affected_services", [])}
        record_types = {t.lower() for t in record.get("affected_resource_types", [])}
        if services_lower & record_services or services_lower & record_types:
            score += 3

        # Title keyword match
        title = record.get("update_title", "").lower()
        for kw in keywords_lower:
            if kw in title:
                score += 1

        # Category match
        if categories and record.get("update_category") in categories:
            score += 1

        if score > 0:
            record["_relevance_score"] = score
            matches.append(record)

    # Sort by relevance score then date
    matches.sort(key=lambda r: (-r.get("_relevance_score", 0), r.get("analyzed_at", "")))
    return matches[:max_results]


def detect_trends(days: int = 90) -> list[dict]:
    """Detect update trends from history.

    Identifies patterns like:
    - Services with frequent retirement notices
    - Repeated security updates for a service
    - Increasing update frequency for a category

    Args:
        days: Number of days to analyze

    Returns:
        List of trend observations with service, pattern, count, description
    """
    history = load_recent_history(days=days, max_records=500)
    if len(history) < 3:
        return []

    # Count updates by service × category
    service_category_counts: dict[str, dict[str, int]] = {}
    service_total: dict[str, int] = {}

    for record in history:
        for svc in record.get("affected_services", []):
            svc_key = svc.lower()
            service_total[svc_key] = service_total.get(svc_key, 0) + 1
            cat = record.get("update_category", "unknown")
            if svc_key not in service_category_counts:
                service_category_counts[svc_key] = {}
            service_category_counts[svc_key][cat] = service_category_counts[svc_key].get(cat, 0) + 1

    trends = []

    # Trend 1: Services with multiple retirements
    for svc, cats in service_category_counts.items():
        retirement_count = cats.get("retirement", 0)
        if retirement_count >= 2:
            trends.append(
                {
                    "service": svc,
                    "pattern": "frequent_retirements",
                    "count": retirement_count,
                    "description": (
                        f"{svc}: {retirement_count} retirement notices in the last {days} days "
                        f"— large-scale deprecation in progress"
                    ),
                }
            )

    # Trend 2: Services with high update frequency
    for svc, total in service_total.items():
        if total >= 5:
            cats = service_category_counts.get(svc, {})
            cat_summary = ", ".join(
                f"{c}: {n}" for c, n in sorted(cats.items(), key=lambda x: -x[1])
            )
            trends.append(
                {
                    "service": svc,
                    "pattern": "high_frequency",
                    "count": total,
                    "description": (
                        f"{svc}: {total} updates in the last {days} days ({cat_summary}) "
                        f"— rapid evolution phase"
                    ),
                }
            )

    # Trend 3: Category-wide patterns
    category_counts: dict[str, int] = {}
    for record in history:
        cat = record.get("update_category", "unknown")
        category_counts[cat] = category_counts.get(cat, 0) + 1

    total_updates = len(history)
    for cat, count in category_counts.items():
        ratio = count / total_updates if total_updates > 0 else 0
        if cat == "retirement" and ratio > 0.3:
            trends.append(
                {
                    "service": "all",
                    "pattern": "retirement_wave",
                    "count": count,
                    "description": (
                        f"Retirement wave: {count}/{total_updates} updates ({ratio:.0%}) "
                        f"are retirements in the last {days} days"
                    ),
                }
            )

    return trends


def rotate_history() -> None:
    """Remove old records beyond retention policy.

    Called periodically (e.g., at the start of a batch analysis).
    """
    if not _HISTORY_FILE.exists():
        return

    cutoff = datetime.now(timezone.utc) - timedelta(days=MAX_HISTORY_DAYS)
    kept_records = []

    try:
        with open(_HISTORY_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                    analyzed_at = record.get("analyzed_at", "")
                    if analyzed_at:
                        dt = datetime.fromisoformat(analyzed_at)
                        if dt >= cutoff:
                            kept_records.append(line)
                except (json.JSONDecodeError, ValueError):
                    continue

        # Keep only MAX_HISTORY_RECORDS most recent
        if len(kept_records) > MAX_HISTORY_RECORDS:
            kept_records = kept_records[-MAX_HISTORY_RECORDS:]

        with _HISTORY_LOCK:
            with open(_HISTORY_FILE, "w", encoding="utf-8") as f:
                for line in kept_records:
                    f.write(line + "\n")

        logger.info(
            "analysis_history_rotated",
            kept=len(kept_records),
            max_days=MAX_HISTORY_DAYS,
            max_records=MAX_HISTORY_RECORDS,
        )
    except Exception as e:
        logger.warning("analysis_history_rotation_failed", error=str(e))


# =========================================================================
# Retirement Tracker (JSON file)
# =========================================================================


def load_retirement_tracker() -> list[dict]:
    """Load the retirement tracker.

    Returns:
        List of active retirement entries
    """
    if not _RETIREMENT_FILE.exists():
        return []
    try:
        with open(_RETIREMENT_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception as e:
        logger.warning("retirement_tracker_load_failed", error=str(e))
        return []


def save_retirement_tracker(entries: list[dict]) -> None:
    """Save the retirement tracker.

    Args:
        entries: List of retirement entries
    """
    _ensure_data_dir()
    with _RETIREMENT_LOCK:
        try:
            with open(_RETIREMENT_FILE, "w", encoding="utf-8") as f:
                json.dump(entries, f, ensure_ascii=False, indent=2)
            logger.debug("retirement_tracker_saved", count=len(entries))
        except Exception as e:
            logger.warning("retirement_tracker_save_failed", error=str(e))


def update_retirement_tracker(result: Any) -> None:
    """Update the retirement tracker with a new analysis result.

    Only processes retirement-category updates. Adds new entries or
    updates existing ones with latest affected resource counts.

    Args:
        result: AnalysisResult instance
    """
    if getattr(result, "update_category", "") != "retirement":
        return

    update_id = getattr(result, "update_id", "")
    update_title = getattr(result, "update_title", "")
    if not update_id:
        return

    entries = load_retirement_tracker()

    # Check if this retirement is already tracked
    existing = next((e for e in entries if e.get("update_id") == update_id), None)

    # Try to extract retirement date from action items
    retirement_date = ""
    for action in getattr(result, "action_items", []):
        deadline = (
            getattr(action, "deadline", "")
            if hasattr(action, "deadline")
            else action.get("deadline", "")
        )
        if deadline:
            retirement_date = deadline
            break

    affected_count = len(getattr(result, "affected_resources", []))
    services = _extract_services(result)

    if existing:
        # Update existing entry
        existing["affected_resource_count"] = affected_count
        existing["last_checked"] = datetime.now(timezone.utc).isoformat()
        existing["services"] = services
        if retirement_date and not existing.get("retirement_date"):
            existing["retirement_date"] = retirement_date
    else:
        # Add new entry
        entries.append(
            {
                "update_id": update_id,
                "title": update_title,
                "services": services,
                "retirement_date": retirement_date,
                "affected_resource_count": affected_count,
                "migration_status": "not_started",
                "first_seen": datetime.now(timezone.utc).isoformat(),
                "last_checked": datetime.now(timezone.utc).isoformat(),
                "notes": "",
            }
        )

    # Clean up: remove entries with retirement dates in the past (> 30 days ago)
    now = datetime.now(timezone.utc)
    active_entries = []
    for entry in entries:
        rd = entry.get("retirement_date", "")
        if rd:
            try:
                # Parse various date formats
                for fmt in ("%Y-%m-%d", "%B %d, %Y", "%Y-%m"):
                    try:
                        dt = datetime.strptime(rd, fmt).replace(tzinfo=timezone.utc)
                        if dt < now - timedelta(days=30):
                            continue  # Skip expired entries
                        break
                    except ValueError:
                        continue
            except Exception:
                pass
        active_entries.append(entry)

    save_retirement_tracker(active_entries)


def get_retirement_countdown() -> list[dict]:
    """Get active retirements with days remaining.

    Returns:
        List of dicts with title, retirement_date, days_remaining,
        affected_resource_count, migration_status, sorted by urgency
    """
    entries = load_retirement_tracker()
    if not entries:
        return []

    now = datetime.now(timezone.utc)
    countdowns = []

    for entry in entries:
        rd = entry.get("retirement_date", "")
        days_remaining = None
        if rd:
            for fmt in ("%Y-%m-%d", "%B %d, %Y", "%Y-%m"):
                try:
                    dt = datetime.strptime(rd, fmt).replace(tzinfo=timezone.utc)
                    days_remaining = (dt - now).days
                    break
                except ValueError:
                    continue

        countdowns.append(
            {
                "update_id": entry.get("update_id", ""),
                "title": entry.get("title", ""),
                "services": entry.get("services", []),
                "retirement_date": rd,
                "days_remaining": days_remaining,
                "affected_resource_count": entry.get("affected_resource_count", 0),
                "migration_status": entry.get("migration_status", "not_started"),
            }
        )

    # Sort by urgency for a CSA: an already-breached (overdue) retirement whose
    # migration is still open is the single most urgent item, so it must lead —
    # NOT be buried behind far-future deadlines. Order: overdue (most-overdue
    # first) → upcoming (soonest first) → undated (TBD) last.
    def _urgency_key(x: dict) -> tuple[int, int]:
        d = x["days_remaining"]
        if d is None:
            return (2, 0)  # undated → last
        if d < 0:
            return (0, d)  # overdue → first; more-negative (more overdue) leads
        return (1, d)  # upcoming → soonest first

    countdowns.sort(key=_urgency_key)

    return countdowns


def build_history_context_for_prompt(
    services: list[str],
    update_id: str = "",
    max_related: int = 5,
) -> str:
    """Build a context section for the analysis prompt from history.

    Args:
        services: Service names to search for related updates
        update_id: Current update ID (to exclude from results)
        max_related: Maximum related updates to include

    Returns:
        Formatted string for prompt injection (empty if no history)
    """
    related = find_related_updates(
        services=services,
        days=30,
        max_results=max_related,
        exclude_update_id=update_id,
    )
    trends = detect_trends(days=90)

    if not related and not trends:
        return ""

    parts = ["\n## Analysis History Context\n"]

    if related:
        parts.append("### Related Recent Updates")
        parts.append(
            "The following related updates were analyzed recently. "
            "Consider their cumulative impact in your analysis.\n"
        )
        for r in related:
            parts.append(
                f"- **{r.get('update_title', '?')}** ({r.get('update_category', '?')}, "
                f"{r.get('analyzed_at', '?')[:10]}) — "
                f"urgency: {r.get('urgency', '?')}, "
                f"affected: {r.get('affected_resource_count', 0)} resources, "
                f"blast radius: {r.get('blast_radius_score', 0)}"
            )
            if r.get("one_line_summary"):
                parts.append(f"  Summary: {r['one_line_summary']}")
        parts.append("")

    if trends:
        parts.append("### Detected Trends")
        for t in trends:
            parts.append(f"- {t['description']}")
        parts.append("")

    return "\n".join(parts)
