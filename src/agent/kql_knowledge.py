"""KQL Knowledge Base — reuses and accumulates Resource Graph schema insights.

When the agent runs an exploratory query (e.g., sampling `properties` keys from a
resource type), the results are stored here so that future analyses can reference
the discovered column paths without re-exploring.

The checked-in JSON is a tenant-neutral seed. Runtime discoveries are loaded lazily
and persisted under ``AZBRIEF_DATA_DIR`` (or the ignored local ``data/`` directory).
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Optional

from structlog import get_logger

logger = get_logger()

# The checked-in file is a tenant-neutral seed. Runtime discoveries belong in
# the ignored data directory, never back in the source tree.
_SEED_PATH = Path(__file__).parent / "kql_knowledge_base.json"
_DEFAULT_DATA_DIR = Path(__file__).parent.parent.parent / "data"
_RUNTIME_FILE_NAME = "kql_knowledge_base.json"

# In-memory cache
_cache: Optional[dict] = None
_cache_path: Optional[Path] = None


def _bounded_scope_active() -> bool:
    """Return whether shared KQL memory is unsafe for the current analysis."""
    from src.agent.scope import current_analysis_scope

    return current_analysis_scope().is_bounded


def _get_path() -> Path:
    """Return the writable runtime knowledge path."""
    if _cache_path is not None:
        return _cache_path
    data_dir = Path(os.environ.get("AZBRIEF_DATA_DIR", str(_DEFAULT_DATA_DIR))).expanduser()
    return data_dir / _RUNTIME_FILE_NAME


def _load() -> dict:
    """Load knowledge base from disk, or return empty structure."""
    global _cache
    if _cache is not None:
        return _cache

    runtime_path = _get_path()
    source_path = runtime_path if runtime_path.exists() else _SEED_PATH
    if source_path.exists():
        try:
            _cache = json.loads(source_path.read_text(encoding="utf-8"))
            logger.debug("KQL knowledge base loaded", entries=len(_cache.get("schemas", {})))
        except Exception:
            logger.warning("Failed to load KQL knowledge base, starting fresh")
            _cache = {"schemas": {}, "queries": {}, "failed_queries": []}
    else:
        _cache = {"schemas": {}, "queries": {}, "failed_queries": []}
    return _cache


def _save() -> None:
    """Persist knowledge base to disk."""
    if _cache is None:
        return
    path = _get_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(_cache, indent=2, ensure_ascii=False), encoding="utf-8")
        logger.debug("KQL knowledge base saved", path=str(path))
    except Exception as e:
        logger.warning("Failed to save KQL knowledge base", error=str(e))


def record_schema(resource_type: str, property_paths: list[str]) -> None:
    """Record discovered property paths for a resource type.

    Args:
        resource_type: Normalized resource type (e.g., "microsoft.storage/storageaccounts")
        property_paths: List of discovered property paths (e.g., ["properties.minimumTlsVersion"])
    """
    if _bounded_scope_active():
        return
    kb = _load()
    key = resource_type.lower()
    existing = set(kb["schemas"].get(key, {}).get("paths", []))
    existing.update(property_paths)
    kb["schemas"][key] = {
        "paths": sorted(existing),
        "updated": time.strftime("%Y-%m-%d"),
    }
    _save()
    logger.info(
        "Schema recorded",
        resource_type=key,
        new_paths=len(property_paths),
        total_paths=len(existing),
    )


def record_successful_query(resource_type: str, purpose: str, query: str) -> None:
    """Record a successful KQL query for future reference.

    Args:
        resource_type: Resource type the query targets
        purpose: What this query achieves (e.g., "Get TLS version for storage accounts")
        query: The KQL query string
    """
    if _bounded_scope_active():
        return
    kb = _load()
    key = resource_type.lower()
    if key not in kb["queries"]:
        kb["queries"][key] = []

    # Avoid duplicates
    for existing in kb["queries"][key]:
        if existing.get("query", "").strip() == query.strip():
            return

    # Keep at most 5 queries per resource type
    kb["queries"][key].append(
        {
            "purpose": purpose,
            "query": query.strip(),
            "recorded": time.strftime("%Y-%m-%d"),
        }
    )
    kb["queries"][key] = kb["queries"][key][-5:]
    _save()


def record_failed_query(query: str, error: str) -> None:
    """Record a failed KQL query and its error for future avoidance.

    Args:
        query: The KQL query that failed
        error: The error message from Azure Resource Graph
    """
    if _bounded_scope_active():
        return
    kb = _load()
    if "failed_queries" not in kb:
        kb["failed_queries"] = []

    # Avoid duplicates (same query)
    for existing in kb["failed_queries"]:
        if existing.get("query", "").strip() == query.strip():
            return

    # Keep at most 20 failed queries (FIFO)
    kb["failed_queries"].append(
        {
            "query": query.strip(),
            "error": error[:300],
            "recorded": time.strftime("%Y-%m-%d %H:%M"),
        }
    )
    kb["failed_queries"] = kb["failed_queries"][-20:]
    _save()


def get_known_schema(resource_type: str) -> list[str]:
    """Get previously discovered property paths for a resource type.

    Args:
        resource_type: Resource type to look up

    Returns:
        List of known property paths, or empty list
    """
    if _bounded_scope_active():
        return []
    kb = _load()
    entry = kb["schemas"].get(resource_type.lower(), {})
    return entry.get("paths", [])


def get_known_queries(resource_type: str) -> list[dict]:
    """Get previously successful queries for a resource type.

    Args:
        resource_type: Resource type to look up

    Returns:
        List of query records [{purpose, query, recorded}]
    """
    if _bounded_scope_active():
        return []
    kb = _load()
    return kb["queries"].get(resource_type.lower(), [])


def build_context_for_prompt() -> str:
    """Build a text summary of accumulated KQL knowledge for injection into prompts.

    Returns:
        Formatted text block, or empty string if no knowledge exists.
    """
    if _bounded_scope_active():
        return ""
    kb = _load()
    has_schemas = bool(kb.get("schemas"))
    has_queries = bool(kb.get("queries"))
    has_failures = bool(kb.get("failed_queries"))

    if not has_schemas and not has_queries and not has_failures:
        return ""

    parts = ["## Previously Discovered Resource Graph Schema Knowledge\n"]
    parts.append(
        "Historical paths and query examples are discovery hints, not current tenant evidence "
        "or a fixed template. Adapt them to this update's question and validate decisive values "
        "against current results. Missing cached paths do not prove absence. Array [] notation "
        "requires mv-expand to inspect all elements.\n"
    )

    for rtype, info in sorted(kb.get("schemas", {}).items()):
        paths = info.get("paths", [])
        if not paths:
            continue
        parts.append(f"### {rtype}")
        parts.append(f"Historical properties (showing {min(len(paths), 30)} of {len(paths)}):")
        for p in paths[:30]:
            parts.append(f"  - {p}")

        # Attach known queries for this type
        queries = kb.get("queries", {}).get(rtype, [])
        if queries:
            parts.append("Historical query examples (adapt, then validate):")
            for q in queries:
                parts.append(f"  Purpose: {q['purpose']}")
                parts.append(f"  ```\n  {q['query']}\n  ```")
        parts.append("")

    # Include recent failed queries so the LLM avoids repeating them
    failed = kb.get("failed_queries", [])
    if failed:
        parts.append("## Previously Failed KQL Queries (diagnostic history)\n")
        parts.append(
            "Do not repeat an unchanged known failure. Inspect its specific error before "
            "correcting it; an old ParserFailure is not a ban on joins, arrays or projections, "
            "and transient/permission failures do not establish a syntax restriction.\n"
        )
        for fq in failed[-10:]:
            parts.append(f"- Query: `{fq['query'][:150]}`")
            parts.append(f"  Error: {fq['error'][:150]}")
        parts.append("")

    return "\n".join(parts)


def reset() -> None:
    """Clear all accumulated knowledge (for testing)."""
    global _cache
    _cache = {"schemas": {}, "queries": {}, "failed_queries": []}
    _save()
