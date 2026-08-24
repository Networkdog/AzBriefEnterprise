"""Analysis pattern memory — cross-session "how to analyze" knowledge.

Complements two existing stores without overlapping them:

- ``history.py`` remembers *what was found* (conclusions, affected resources,
  trends) for a given update/service.
- ``kql_knowledge.py`` remembers *which KQL queries worked* against the schema.
- **This module remembers *how the agent should analyze*** a given Azure service:
  which tool combinations previously produced a grounded result, and which
  resource types typically end up affected.

The payoff is planning efficiency. When the planner sees "for Azure Kubernetes
Service updates, ``get_service_resource_details`` + ``get_resource_health`` +
``get_policy_compliance`` have historically been the productive tools", it writes
a stronger first plan — which reduces execute→revise churn and lifts the
trajectory (process-quality) score. This is the "knowledge stays in the
environment, not in a person" persistent-memory principle applied to AzBrief.

File-based (no DB), thread-safe, self-pruning. Never raises into the caller.
"""

from __future__ import annotations

import json
import threading
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from structlog import get_logger

logger = get_logger()

_DATA_DIR = Path(__file__).parent.parent.parent / "data"
_PATTERN_FILE = _DATA_DIR / "analysis_patterns.json"
_PATTERN_LOCK = threading.Lock()

# Keep the store bounded: at most this many service keys, pruned by sample count.
MAX_PATTERN_KEYS = 200
# How many tools / resource types to surface in a planning hint.
_TOP_TOOLS = 5
_TOP_RESOURCE_TYPES = 5
# Only emit a hint once a service has been seen at least this many times, so a
# single fluky run does not steer future planning.
_MIN_SAMPLES_FOR_HINT = 2


def _normalize_service(service: str) -> str:
    """Normalize a service name to a stable lookup key.

    "Azure Kubernetes Service (AKS)" and "azure kubernetes service" collapse to
    the same key so patterns accumulate rather than fragment.
    """
    s = (service or "").strip().lower()
    # Drop parenthetical acronyms and collapse whitespace.
    if "(" in s:
        s = s.split("(", 1)[0].strip()
    return " ".join(s.split())


def _service_keys(update: Any) -> list[str]:
    """Extract normalized service keys from an update (deduplicated, non-empty)."""
    services = getattr(update, "azure_services", None) or []
    keys: list[str] = []
    seen: set[str] = set()
    for svc in services:
        key = _normalize_service(str(svc))
        if key and key not in seen:
            seen.add(key)
            keys.append(key)
    return keys


def _load() -> dict[str, Any]:
    """Load the pattern store, returning an empty structure on any problem."""
    if not _PATTERN_FILE.exists():
        return {"patterns": {}}
    try:
        with open(_PATTERN_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or "patterns" not in data:
            return {"patterns": {}}
        return data
    except Exception as e:  # pragma: no cover - corrupt file is non-fatal
        logger.warning("pattern_memory_load_failed", error=str(e))
        return {"patterns": {}}


def _save(data: dict[str, Any]) -> None:
    """Persist the pattern store (best-effort, pruned to MAX_PATTERN_KEYS)."""
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    patterns = data.get("patterns", {})
    if len(patterns) > MAX_PATTERN_KEYS:
        # Prune the least-sampled keys first (weakest evidence).
        ranked = sorted(
            patterns.items(),
            key=lambda kv: kv[1].get("samples", 0),
            reverse=True,
        )
        data["patterns"] = dict(ranked[:MAX_PATTERN_KEYS])
    try:
        with open(_PATTERN_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:  # pragma: no cover - disk problem is non-fatal
        logger.warning("pattern_memory_save_failed", error=str(e))


def record_analysis_pattern(
    update: Any,
    result: Any,
    successful_tools: list[str],
) -> None:
    """Record which tools worked (and what was affected) for an update's services.

    Args:
        update: The analyzed :class:`AzureUpdate` (reads ``azure_services``).
        result: The :class:`AnalysisResult` (reads ``affected_resources``,
            ``update_category``).
        successful_tools: ``tool_name`` of every task that completed successfully.
    """
    service_keys = _service_keys(update)
    if not service_keys or not successful_tools:
        return

    # Resource types the analysis actually flagged as affected.
    resource_types: list[str] = []
    for r in getattr(result, "affected_resources", None) or []:
        rtype = r.get("type", "") if isinstance(r, dict) else ""
        if rtype:
            resource_types.append(str(rtype).lower())
    category = getattr(result, "update_category", "") or ""

    with _PATTERN_LOCK:
        data = _load()
        patterns = data["patterns"]
        now = datetime.now(timezone.utc).isoformat()
        for key in service_keys:
            entry = patterns.get(key) or {
                "tool_combos": {},
                "resource_types": {},
                "categories": {},
                "samples": 0,
                "last_updated": now,
            }
            tool_counter = Counter(entry.get("tool_combos", {}))
            tool_counter.update(successful_tools)
            entry["tool_combos"] = dict(tool_counter)

            rtype_counter = Counter(entry.get("resource_types", {}))
            rtype_counter.update(resource_types)
            entry["resource_types"] = dict(rtype_counter)

            if category:
                cat_counter = Counter(entry.get("categories", {}))
                cat_counter.update([category])
                entry["categories"] = dict(cat_counter)

            entry["samples"] = int(entry.get("samples", 0)) + 1
            entry["last_updated"] = now
            patterns[key] = entry
        _save(data)

    logger.debug(
        "analysis_pattern_recorded",
        services=service_keys,
        tools=successful_tools,
    )


def build_pattern_hint_for_prompt(update: Any) -> str:
    """Build a planning hint from accumulated patterns for this update's services.

    Returns an empty string when no service has enough samples yet, so the
    planning prompt is unchanged during the cold-start period.

    Args:
        update: The :class:`AzureUpdate` about to be analyzed.

    Returns:
        A markdown hint block, or "" when there is nothing confident to say.
    """
    service_keys = _service_keys(update)
    if not service_keys:
        return ""

    data = _load()
    patterns = data.get("patterns", {})

    lines: list[str] = []
    for key in service_keys:
        entry = patterns.get(key)
        if not entry or int(entry.get("samples", 0)) < _MIN_SAMPLES_FOR_HINT:
            continue
        tools = Counter(entry.get("tool_combos", {})).most_common(_TOP_TOOLS)
        if not tools:
            continue
        rtypes = Counter(entry.get("resource_types", {})).most_common(_TOP_RESOURCE_TYPES)
        tool_str = ", ".join(f"`{name}`" for name, _ in tools)
        line = (
            f"- **{key}** (from {entry.get('samples', 0)} past analyses): "
            f"productive tools → {tool_str}"
        )
        if rtypes:
            rtype_str = ", ".join(name for name, _ in rtypes)
            line += f"; commonly-affected types → {rtype_str}"
        lines.append(line)

    if not lines:
        return ""

    header = (
        "\n## Prior Analysis Patterns (planning hint)\n"
        "Tools that historically produced grounded findings for these services. "
        "Use as a prior — still tailor tasks to THIS update; do not blindly copy.\n"
    )
    return header + "\n".join(lines) + "\n"


def extract_successful_tools(final_state: dict[str, Any]) -> list[str]:
    """Pull the ``tool_name`` of every completed task from a LangGraph final state.

    Convenience for the analyzer: turns the executed plan into the
    ``successful_tools`` list that :func:`record_analysis_pattern` expects.
    """
    plan = final_state.get("analysis_plan") or {}
    tasks = plan.get("tasks", []) if isinstance(plan, dict) else []
    tools: list[str] = []
    for t in tasks:
        if not isinstance(t, dict):
            continue
        if t.get("status") == "completed" and t.get("tool_name"):
            tools.append(str(t["tool_name"]))
    return tools
