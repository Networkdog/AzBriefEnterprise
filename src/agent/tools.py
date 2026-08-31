"""LangChain tools for Azure Update analysis."""

import asyncio
import json
import re
from typing import Any, Optional, Type

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field
from structlog import get_logger

from src.agent.kql_knowledge import (
    get_known_queries,
    get_known_schema,
    record_failed_query,
    record_schema,
    record_successful_query,
)
from src.agent.resilience import (
    TOOL_RESULT_BUDGET_CHARS,
    CircuitBreaker,
    calculate_backoff,
    truncate_tool_result,
)
from src.services.billing import BillingService
from src.services.cost_management import CostManagementService
from src.services.log_analytics import LogAnalyticsService
from src.services.microsoft_learn import MicrosoftLearnService
from src.services.resource_graph import ResourceGraphQueryBuilder, ResourceGraphService

logger = get_logger()

# ---------------------------------------------------------------------------
# Concurrency-safety registry
# ---------------------------------------------------------------------------
# Names of tools that MUTATE state (create/update/delete Azure resources or send
# side effects). The execution scheduler runs read-only tools in parallel and
# any tool listed here SERIALLY, one at a time, after the parallel batch.
#
# AzBrief is intentionally read-only: every current tool issues Resource Graph
# queries, REST GETs, doc searches, or cost/log reads — so this set is empty and
# all tasks run in parallel. Register a tool's ``name`` here the moment a
# write-capable tool is added, and the scheduler will fail-closed to serial
# execution for it without any other change. See ``AzureUpdateAnalyzer._execution_node``.
WRITE_TOOL_NAMES: frozenset[str] = frozenset()

# ---------------------------------------------------------------------------
# KQL routing registry
# ---------------------------------------------------------------------------
# Names of tools whose arguments carry a KQL query (Resource Graph or Log
# Analytics). LLM-assisted repair of these arguments MUST use the Resource
# Graph specialist, never the coordinator or quality reviewer.
KQL_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "query_azure_resources",
        "query_log_analytics",
        "explore_resource_schema",
    }
)

# Maximum retry attempts for Resource Graph queries
MAX_QUERY_RETRIES = 8

# Maximum result-driven (semantic) improvements for a query that runs successfully
# but returns an empty result (over-strict / wrong filter). Bounded to avoid loops.
MAX_RESULT_IMPROVEMENTS = 2

# Module-level circuit breaker for KQL query fixer LLM calls
_kql_fixer_circuit_breaker = CircuitBreaker(failure_threshold=3, reset_timeout=120)


async def execute_kql_with_retry(
    service: ResourceGraphService,
    query: str,
    *,
    max_retries: int = MAX_QUERY_RETRIES,
    enrich_subscriptions: bool = True,
) -> dict:
    """Execute a KQL query with automatic retry and query fixing.

    Centralises the retry-fix-retry loop used by all KQL-based tools.
    On InvalidQuery/ParserFailure/BadRequest errors, uses the singleton
    ``ResourceGraphQueryFixer`` (LLM + Microsoft Learn) to repair the query.

    Args:
        service: ResourceGraphService instance
        query: Initial KQL query string (will be sanitized)
        max_retries: Maximum number of attempts
        enrich_subscriptions: Whether to resolve subscriptionId to display name

    Returns:
        Raw query result dict from ResourceGraphService

    Raises:
        RuntimeError: If all retries are exhausted
    """
    import time as _time

    current_query = sanitize_kql(query)
    fixer = get_query_fixer()
    last_error: Exception | None = None
    _result_improvements = 0
    _query_t0 = _time.time()

    for attempt in range(1, max_retries + 1):
        try:
            _attempt_t0 = _time.time()
            result = await service.query_resources(current_query)
            _attempt_elapsed = _time.time() - _attempt_t0

            # Result-driven (semantic) improvement: a syntactically-valid query
            # that returned an empty result may have an over-strict or wrong
            # filter. If the resource type actually has resources, analyze the
            # real data and improve the filter, then re-execute. Fail-safe: any
            # error in this path falls through to returning the (empty) result.
            _rows = result.get("count") or len(result.get("data") or [])
            if (
                _rows == 0
                and _result_improvements < MAX_RESULT_IMPROVEMENTS
                and _query_has_property_filter(current_query)
            ):
                try:
                    _improved = await _try_result_improvement(service, fixer, current_query)
                except Exception as _imp_err:
                    logger.warning("kql_result_improve_error", error=str(_imp_err)[:200])
                    _improved = None
                if _improved and _improved.strip() != current_query.strip():
                    _result_improvements += 1
                    logger.info(
                        "kql_result_improved",
                        improvement=_result_improvements,
                        old_query=current_query[:200],
                        new_query=_improved[:200],
                    )
                    current_query = _improved
                    continue  # re-execute with the improved query

            if attempt > 1 or _result_improvements > 0:
                total_elapsed = _time.time() - _query_t0
                logger.info(
                    "kql_query_succeeded_after_retry",
                    attempt=attempt,
                    result_improvements=_result_improvements,
                    elapsed_s=round(_attempt_elapsed, 2),
                    total_elapsed_s=round(total_elapsed, 2),
                    result_count=result.get("count", 0),
                    query=current_query[:300],
                )
                # Record fixed / result-improved query for future reuse
                import re as _re

                _type_match = _re.search(r"type\s*=~\s*'([^']+)'", current_query, _re.IGNORECASE)
                if _type_match:
                    _purpose = (
                        "Result-improved query (was empty)"
                        if _result_improvements > 0
                        else "Custom query (auto-fixed)"
                    )
                    record_successful_query(
                        resource_type=_type_match.group(1),
                        purpose=_purpose,
                        query=current_query,
                    )

            # Enrich subscription names
            if enrich_subscriptions:
                data = result.get("data", [])
                if isinstance(data, list) and data:
                    service.enrich_subscription_names(data)

            return result

        except Exception as e:
            last_error = e
            error_msg = str(e)
            logger.warning(
                "kql_query_failed",
                attempt=attempt,
                max_attempts=max_retries,
                error=error_msg[:300],
                query=current_query[:300],
            )

            if attempt >= max_retries:
                break

            if any(kw in error_msg for kw in ("InvalidQuery", "ParserFailure", "BadRequest")):
                # Check circuit breaker before LLM-assisted fix
                if _kql_fixer_circuit_breaker.is_open:
                    logger.warning(
                        "kql_fixer_circuit_open",
                        attempt=attempt,
                        reason="Too many consecutive LLM fix failures",
                    )
                    # Fall back to rule-based sanitization only
                    current_query = sanitize_kql(current_query)
                else:
                    try:
                        current_query = await fixer.fix_query(current_query, error_msg, attempt)
                        _kql_fixer_circuit_breaker.record_success()
                    except Exception as fix_err:
                        _kql_fixer_circuit_breaker.record_failure()
                        logger.warning(
                            "kql_fixer_llm_failed",
                            error=str(fix_err)[:200],
                            fallback="rule-based sanitization",
                        )
                        current_query = sanitize_kql(current_query)
            else:
                # Transient error: use exponential backoff with jitter
                delay = calculate_backoff(attempt, base_delay=0.5, max_delay=16.0)
                logger.info(
                    "kql_transient_retry",
                    attempt=attempt,
                    delay_s=round(delay, 2),
                )
                await asyncio.sleep(delay)

    total_elapsed = _time.time() - _query_t0
    logger.error(
        "kql_query_exhausted",
        attempts=max_retries,
        total_elapsed_s=round(total_elapsed, 2),
        error=str(last_error),
    )
    record_failed_query(query, str(last_error))
    raise RuntimeError(f"Query failed after {max_retries} retries: {last_error}")


# Pre-compiled regex patterns for sanitize_kql (compiled once at module load)
_RE_TOP_WITHOUT_BY = re.compile(r"\|\s*top\s+(\d+)\b(?!\s+by\b)", re.IGNORECASE)
_RE_STRAY_TOP = re.compile(r"(?<!\|)\s+top\s+(\d+)\b(?!\s+by\b)", re.IGNORECASE)
_RE_KIND_TOSTRING = re.compile(r"\bkind\s*=\s*tostring\(kind\)")
_RE_PROJECT_EXCEPT = re.compile(r"\bproject-except\b", re.IGNORECASE)
_RE_LET_STATEMENT = re.compile(r"^\s*let\s+\w+\s*=.*?;\s*\n?", re.MULTILINE)
# Captures the (name, value) of each `let NAME = VALUE;` so the value can be
# inlined into later references before the declaration is stripped.
_RE_LET_DECL = re.compile(r"^\s*let\s+(\w+)\s*=\s*(.*?);", re.MULTILINE)
_RE_STARTS_WITH_PIPE = re.compile(r"^\w")
_RE_RENDER = re.compile(r"\|\s*render\s+\w+", re.IGNORECASE)
_RE_DATATABLE = re.compile(r"\bdatatable\b|\bexternaldata\b", re.IGNORECASE)
_RE_DATATABLE_BLOCK = re.compile(r"\bdatatable\s*\(.*?\)\s*\[.*?\]", re.DOTALL | re.IGNORECASE)
_RE_PROJECT_CLAUSE = re.compile(r"\|\s*project\b(.+?)(?:\||$)", re.DOTALL)
_RE_INLINE_ASSIGN = re.compile(
    r"(\w+)\s*=\s*(tostring\([^)]+\)|tolower\([^)]+\)|toupper\([^)]+\)"
    r"|array_length\([^)]+\)|coalesce\([^)]+\)|iff\([^)]+\)"
    r"|properties\.\w+(?:\.\w+)*)"
)
_RE_MISSING_PIPE = re.compile(
    r"(?<!\|)\s+(order by|summarize|limit|take|project|extend|mv-expand|distinct)\s",
    re.IGNORECASE,
)
_RE_DUPLICATE_PIPES = re.compile(r"\|\s*\|")

# Pre-compiled patterns for _rule_based_fix and query parsing
_RE_JOIN = re.compile(r"\|\s*join\b", re.IGNORECASE)
_RE_TYPE_MATCH = re.compile(r"type\s*=~\s*'([^']+)'", re.IGNORECASE)
_RE_KIND_EQ_KIND = re.compile(r"\bkind\s*=\s*kind\b")
_RE_MARKDOWN_FENCE_START = re.compile(r"^```(?:kql)?\s*\n?")
_RE_MARKDOWN_FENCE_END = re.compile(r"\n?```\s*$")
_RE_FAILED_RESOLVE_COL = re.compile(r"Failed to resolve scalar expression named '(\w+)'")
_RE_COLUMN_ERROR = re.compile(r"column '(\w+)'")
_RE_MV_EXPAND_ALIAS = re.compile(r"mv-expand\s+(\w+)\s*=")
_RE_EXTEND_BLOCK = re.compile(r"\|\s*extend[^|]+")
_RE_PROJECT_BLOCK = re.compile(r"\|\s*project[^|]+")


def _strip_unreferenced_extends(query: str) -> str:
    """Remove ``| extend alias = expr`` blocks whose alias is unused downstream.

    Last-resort simplification for unidentifiable ParserFailures. An extend whose
    alias still feeds a later clause (e.g. a ``project`` column) is preserved so the
    simplification never orphans a projection and produces another broken query.

    Args:
        query: KQL query string, possibly containing computed extend blocks.

    Returns:
        The query with unreferenced extend blocks removed.
    """

    def _replace(match: re.Match) -> str:
        block = match.group(0)
        alias_match = re.match(r"\|\s*extend\s+(\w+)\s*=", block)
        if alias_match:
            alias = alias_match.group(1)
            after = query[match.end() :]
            if re.search(rf"\b{re.escape(alias)}\b", after):
                return block  # alias still referenced downstream → keep
        return ""

    return _RE_EXTEND_BLOCK.sub(_replace, query)


# --- Result-driven (semantic) query improvement helpers ---------------------
_RE_TYPE_EXTRACT = re.compile(r"type\s*=~\s*'([^']+)'", re.IGNORECASE)
_RE_WHERE_CLAUSE = re.compile(r"\bwhere\b([^|]+)", re.IGNORECASE)


def _query_has_property_filter(query: str) -> bool:
    """True if the query filters on a property beyond a bare ``where type =~ '...'``.

    Used to decide whether an empty result warrants a result-driven improvement: a
    bare type-only query returning empty means the type is genuinely absent, whereas
    a property-filtered query returning empty may have an over-strict / wrong filter.
    """
    for m in _RE_WHERE_CLAUSE.finditer(query):
        clause = re.sub(r"type\s*=~\s*'[^']+'", "", m.group(1), flags=re.IGNORECASE)
        if re.search(
            r"properties\.|[!<>]=|==|=~|\bcontains\b|\bhas\b|\bstartswith\b|\bendswith\b|\bin~?\s*\(",
            clause,
            re.IGNORECASE,
        ):
            return True
    return False


def _build_type_probe_query(query: str) -> Optional[str]:
    """Build a minimal 'does this resource type have any resources?' probe.

    Returns a small-sample query for the resource type with its identifying fields,
    or None if no resource type can be extracted from the query.
    """
    m = _RE_TYPE_EXTRACT.search(query)
    if not m:
        return None
    rtype = m.group(1)
    return (
        f"Resources | where type =~ '{rtype}' "
        f"| project name, type, kind, sku, properties | limit 5"
    )


async def _try_result_improvement(service, fixer, query: str) -> Optional[str]:
    """Probe whether the resource type exists; if so, LLM-improve the empty query.

    Returns an improved query only when the type has resources but the filter matched
    none (so the filter is wrong). Returns None when the type is genuinely absent
    (the empty result is correct) or no improvement is available.
    """
    probe_query = _build_type_probe_query(query)
    if not probe_query:
        return None
    probe = await service.query_resources(sanitize_kql(probe_query))
    probe_data = probe.get("data") or []
    probe_count = probe.get("count") or len(probe_data)
    if not probe_count:
        return None  # type genuinely absent → the empty result is correct
    import json as _json

    sample = _json.dumps(probe_data[:3], ensure_ascii=False, default=str)
    return await fixer.improve_query_for_empty_result(query, sample)


def sanitize_kql(query: str) -> str:
    """Pre-process KQL query to fix common LLM-generated syntax errors before execution.

    This prevents many first-attempt failures, reducing retry overhead.
    Uses pre-compiled regex patterns for performance.

    Args:
        query: Raw KQL query string (potentially from LLM)

    Returns:
        Sanitized KQL query
    """
    # 1. Fix '| top N' without 'by' clause → '| take N'
    query = _RE_TOP_WITHOUT_BY.sub(r"| take \1", query)

    # 2. Fix stray 'top N' not preceded by pipe → '| take N'
    query = _RE_STRAY_TOP.sub(r" | take \1", query)

    # 3. Fix 'kind=tostring(kind)' in project (reserved field alias collision)
    query = _RE_KIND_TOSTRING.sub("kindValue=tostring(kind)", query)

    # 4. Fix 'project-except' (not supported in Resource Graph) → 'project-away'
    query = _RE_PROJECT_EXCEPT.sub("project-away", query)

    # 5. Fix 'let' statements (not supported in Resource Graph).
    #    Inline each `let NAME = VALUE;` into later NAME references BEFORE removing
    #    the declaration. Removing the let alone leaves NAME as a dangling
    #    identifier, so the re-query fails again ("Failed to resolve ... 'NAME'").
    let_decls = _RE_LET_DECL.findall(query)
    if let_decls:
        query = _RE_LET_STATEMENT.sub("", query)
        for _name, _value in let_decls:
            query = re.sub(rf"\b{re.escape(_name)}\b", lambda _m, _v=_value: _v, query)

    # 6. Fix missing table name — query must start with a table reference
    stripped = query.strip()
    if stripped and not _RE_STARTS_WITH_PIPE.match(stripped):
        query = "Resources\n" + query

    # 7. Fix 'render' operator (not supported in Resource Graph)
    query = _RE_RENDER.sub("", query)

    # 8. Fix 'datatable' and 'externaldata' (not supported)
    if _RE_DATATABLE.search(query):
        query = _RE_DATATABLE_BLOCK.sub("", query)

    # 9. Fix inline expressions in project that should be in extend
    project_match = _RE_PROJECT_CLAUSE.search(query)
    if project_match:
        project_clause = project_match.group(1)
        inline_assigns = _RE_INLINE_ASSIGN.findall(project_clause)
        if inline_assigns:
            extends = " | ".join([f"extend {name}={expr}" for name, expr in inline_assigns])
            fixed_project = project_clause
            for name, expr in inline_assigns:
                fixed_project = fixed_project.replace(f"{name}={expr}", name)
            query = query.replace(project_match.group(0), f"| {extends} | project{fixed_project}")

    # 10. Fix missing pipe before operators (order by, summarize, etc.)
    query = _RE_MISSING_PIPE.sub(r" | \1 ", query)

    # 11. Remove duplicate pipes (|| or | |)
    query = _RE_DUPLICATE_PIPES.sub("|", query)

    # 12. Strip trailing semicolons
    query = query.rstrip("; \n")

    return query.strip()


class ResourceGraphQueryFixer:
    """Fixes invalid Resource Graph KQL queries using Microsoft Learn docs and LLM."""

    SYSTEM_PROMPT = """You are an Azure Resource Graph KQL query expert.
Your job is to fix invalid KQL queries that failed against Azure Resource Graph.

Rules:
- Return the specialist JSON envelope required by your standing output schema.
- On success, return exactly one claim whose `text` is ONLY the corrected KQL query,
  whose evidence contains `query:corrected-kql`, and whose confidence is high.
- On failure, return no claims and one concrete gap. Never place JSON inside KQL text.
- Use only tables and columns that exist in Azure Resource Graph (Resources, advisorresources, servicehealthresources, etc.).
- Always use `extend` before referencing a nested property in `project`.
- Use `=~` for case-insensitive type comparisons.
- Avoid `mv-expand` on non-array fields.
- When a column doesn't exist, remove it or replace with a valid alternative.
- If the error mentions a specific column or function, fix that specific issue.
- Keep the query intent the same as the original.
- Always add `| limit 200` if not present.
- NEVER use `| top N` without an ORDER BY (by) clause. Use `| take N` or `| limit N` instead.
  - WRONG: `| top 50`
  - RIGHT: `| take 50` or `| top 50 by name asc`
"""

    def __init__(
        self,
        llm=None,
        learn_service: Optional[MicrosoftLearnService] = None,
    ):
        self._llm = llm  # Resource Graph specialist, reused to avoid duplicate instances
        self._llm_unavailable = False
        self.learn_service = learn_service or MicrosoftLearnService()

    @staticmethod
    def _is_availability_error(error_str: str) -> bool:
        """True if the error means the model/deployment is unavailable (not transient)."""
        return any(
            marker in error_str
            for marker in (
                "chatCompletion",
                "does not work with the specified model",
                "The requested operation is unsupported",
                "unsupported",
                "DeploymentNotFound",
            )
        )

    async def _ainvoke_specialist(self, messages):
        """Invoke only the Resource Graph specialist."""
        return await self._get_llm().ainvoke(messages)

    @staticmethod
    def _extract_kql_response(text: str) -> str:
        """Extract KQL from a raw response or the specialist evidence envelope."""
        candidate = ResourceGraphQueryFixer._strip_markdown_fences(text)
        query_pattern = r"^\s*(?:Resources|ResourceContainers|[A-Za-z][A-Za-z0-9_]*resources)\b"
        if re.match(query_pattern, candidate, re.IGNORECASE):
            return candidate
        try:
            payload = json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            return ""
        if not isinstance(payload, dict):
            return ""
        for claim in payload.get("claims") or []:
            if not isinstance(claim, dict):
                continue
            query = ResourceGraphQueryFixer._strip_markdown_fences(str(claim.get("text") or ""))
            if re.match(query_pattern, query, re.IGNORECASE):
                return query
        return ""

    def _get_llm(self):
        """Get the Foundry agent used for query fixing."""
        if self._llm is None:
            from src.config import get_settings

            settings = get_settings()
            from src.agent.foundry_backend import create_foundry_chat_model

            self._llm = create_foundry_chat_model(settings, "resource_graph")
        return self._llm

    async def fix_query(
        self,
        original_query: str,
        error_message: str,
        attempt: int,
    ) -> str:
        """Fix an invalid Resource Graph query using Microsoft Learn docs + LLM.

        Args:
            original_query: The KQL query that failed.
            error_message: The error message from Azure Resource Graph.
            attempt: Current retry attempt number.

        Returns:
            A corrected KQL query string.
        """
        # 1. Search Microsoft Learn for relevant Resource Graph documentation
        doc_context = await self._search_docs_for_fix(original_query, error_message)

        # 2. Use LLM to generate a corrected query (skip if previously failed)
        if not self._llm_unavailable:
            try:
                import time as _time

                _fix_t0 = _time.time()
                fixed = await self._llm_fix_query(
                    original_query, error_message, doc_context, attempt
                )
                _fix_elapsed = _time.time() - _fix_t0
                # Sanitize: strip markdown fences if LLM included them
                fixed = self._strip_markdown_fences(fixed)
                if fixed and fixed.strip():
                    logger.info(
                        "kql_fix_by_llm",
                        attempt=attempt,
                        original_len=len(original_query),
                        fixed_len=len(fixed),
                        fix_elapsed_s=round(_fix_elapsed, 2),
                        strategy="llm",
                    )
                    return sanitize_kql(fixed.strip())
            except asyncio.CancelledError:
                logger.warning("kql_fix_cancelled", strategy="llm")
            except Exception as e:
                error_str = str(e)
                # If the model doesn't support chatCompletion or returns 400, cache it
                # to skip future attempts. The 400 'unsupported' error means the
                # codex deployment doesn't support the API operation.
                if any(
                    marker in error_str
                    for marker in (
                        "chatCompletion",
                        "does not work with the specified model",
                        "The requested operation is unsupported",
                        "unsupported",
                        "DeploymentNotFound",
                    )
                ):
                    self._llm_unavailable = True
                    logger.info(
                        "kql_fix_llm_unavailable", error=error_str[:100], switching_to="rule_based"
                    )
                else:
                    logger.warning("kql_fix_llm_failed", error=error_str[:200], strategy="llm")

        # 3. Fallback: rule-based simplification (also sanitize output)
        logger.debug("kql_fix_fallback", strategy="rule_based", attempt=attempt)
        return sanitize_kql(self._rule_based_fix(original_query, error_message, attempt))

    async def _search_docs_for_fix(self, query: str, error_message: str) -> str:
        """Search Microsoft Learn for docs to help fix the query."""
        try:
            # Extract resource type from query for targeted search
            type_match = re.search(r"type\s*=~\s*'([^']+)'", query, re.IGNORECASE)
            resource_type = type_match.group(1) if type_match else ""

            # Extract the core error hint
            error_hint = error_message[:150]

            search_query = f"Azure Resource Graph KQL query {resource_type} {error_hint}"
            result = await self.learn_service.search_azure_docs(query=search_query, top=3)

            docs = result.get("results", [])
            if not docs:
                return "No relevant documentation found."

            context_parts = []
            for doc in docs:
                title = doc.get("title", "")
                desc = doc.get("description", "")
                url = doc.get("url", "")
                context_parts.append(f"- {title}: {desc} ({url})")

            return "\n".join(context_parts)
        except asyncio.CancelledError:
            logger.warning("Doc search for query fix cancelled")
            return "Documentation search cancelled."
        except Exception as e:
            logger.warning("Doc search for query fix failed", error=str(e))
            return "Documentation search failed."

    async def _llm_fix_query(
        self,
        original_query: str,
        error_message: str,
        doc_context: str,
        attempt: int,
    ) -> str:
        """Use LLM to generate a corrected query."""
        user_prompt = f"""Fix this Azure Resource Graph KQL query that failed with an error.

## Failed Query (attempt {attempt})
```kql
{original_query}
```

## Error Message
{error_message[:500]}

## Relevant Microsoft Learn Documentation
{doc_context}

## Instructions
- Fix ONLY the issue described in the error message.
- If the error is about an unknown column, remove it or use a valid alternative.
- NEVER use `kind=tostring(kind)` in a project statement — `kind` is a reserved top-level field. Use it directly (e.g., `| project name, kind, ...`) or alias it differently (e.g., `| extend kindValue = tostring(kind)`).
- For ParserFailure errors, simplify the query: use `extend` for computed columns before `project`, or remove complex inline expressions from `project`.
- NEVER use `| top N` without a `by` clause. Use `| take N` or `| limit N` instead.
- Preserve the original intent of the query.
- Return the required specialist JSON envelope. Put ONLY the corrected KQL query in the
    single claim's `text`; use `query:corrected-kql` as its evidence value. If you cannot
    correct it, return a concrete gap and no claim.
"""

        response = await self._ainvoke_specialist(
            [
                SystemMessage(content=self.SYSTEM_PROMPT),
                HumanMessage(content=user_prompt),
            ]
        )

        return self._extract_kql_response(response.content or "")

    async def improve_query_for_empty_result(self, query: str, probe_sample: str) -> Optional[str]:
        """Improve a valid query that returned zero rows, using real sample data.

        The resource type DOES have resources (shown by ``probe_sample``), but the
        query's filter matched none — so the filter is too strict or uses a wrong
        property path / value / casing. Ask the LLM to correct the filter against the
        actual data (this is the semantic complement to error-driven ``fix_query``).

        Args:
            query: The KQL query that returned an empty result.
            probe_sample: JSON sample of actual resources of the same type.

        Returns:
            An improved KQL query, or None if unavailable / no change produced.
        """
        if self._llm_unavailable:
            return None

        user_prompt = f"""This Azure Resource Graph KQL query is syntactically valid but returned ZERO rows:

```kql
{query}
```

Resources of this type DO exist. Here is a sample of the ACTUAL data returned by a broader probe of the same resource type:

```json
{probe_sample[:1500]}
```

The query's WHERE filter is therefore too strict or uses a wrong property path / value / casing (e.g. filtering `kind == 'Storage'` when the real value is `BlobStorage`, or a `properties.*` path that does not exist in the sample).

Rewrite the query so its filter correctly matches the intended resources AGAINST THE REAL PROPERTY VALUES shown in the sample. Keep the same projected columns. Output ONLY the corrected KQL query.
"""
        try:
            response = await self._ainvoke_specialist(
                [
                    SystemMessage(content=self.SYSTEM_PROMPT),
                    HumanMessage(content=user_prompt),
                ]
            )
            improved = self._extract_kql_response(response.content or "")
            if improved and improved.strip():
                return sanitize_kql(improved.strip())
        except asyncio.CancelledError:
            logger.warning("kql_result_improve_cancelled")
        except Exception as e:
            error_str = str(e)
            if self._is_availability_error(error_str):
                self._llm_unavailable = True
            logger.warning("kql_result_improve_llm_failed", error=error_str[:200])
        return None

    @staticmethod
    def _strip_markdown_fences(text: str) -> str:
        """Strip markdown code fences from LLM output."""
        text = text.strip()
        text = _RE_MARKDOWN_FENCE_START.sub("", text)
        text = _RE_MARKDOWN_FENCE_END.sub("", text)
        return text.strip()

    def _rule_based_fix(self, query: str, error_message: str, attempt: int) -> str:
        """Progressive rule-based query simplification as fallback."""
        # Fix join queries early — Resource Graph's KQL subset often fails
        # on complex join + mv-expand combinations. Remove the join clause
        # and keep only the primary table query.
        if "join" in query.lower() and attempt <= 3:
            # Extract the part before the join clause
            join_match = _RE_JOIN.search(query)
            if join_match:
                primary_part = query[: join_match.start()].rstrip()
                # Extract resource type from primary part
                type_match = _RE_TYPE_MATCH.search(primary_part)
                if type_match:
                    resource_type = type_match.group(1)
                    query = (
                        f"Resources\n| where type =~ '{resource_type}'\n"
                        f"| project name, type, resourceGroup, subscriptionId, location, sku, properties\n"
                        f"| limit 100"
                    )
                    logger.info(
                        "kql_fix_removed_join",
                        resource_type=resource_type,
                        attempt=attempt,
                        strategy="rule_based",
                    )
                    return query

        # Fix complex mv-expand queries that cause ParserFailure
        # When mv-expand + many extend lines fail, simplify to a flat query
        # with just the raw properties (let the report interpret them).
        if "mv-expand" in query.lower() and "ParserFailure" in error_message and attempt <= 4:
            type_match = _RE_TYPE_MATCH.search(query)
            if type_match:
                resource_type = type_match.group(1)
                query = (
                    f"Resources\n| where type =~ '{resource_type}'\n"
                    f"| project name, type, resourceGroup, subscriptionId, location, sku, properties\n"
                    f"| limit 100"
                )
                logger.info(
                    "kql_fix_simplified_mv_expand",
                    resource_type=resource_type,
                    attempt=attempt,
                    strategy="rule_based",
                )
                return query

        # Fix '| top N' without ORDER BY → '| take N'
        query = _RE_TOP_WITHOUT_BY.sub(r"| take \1", query)
        # Fix missing pipe: 'project ... top 50' → 'project ... | take 50'
        query = _RE_STRAY_TOP.sub(r" | take \1", query)

        # Fix common inline alias issues in project statements that cause ParserFailure
        # e.g., kind=tostring(kind) -> kindValue=tostring(kind)
        query = _RE_KIND_TOSTRING.sub("kindValue=tostring(kind)", query)

        # Fix duplicate column names in project (e.g., project name, kind, kind → remove dupe)
        query = _RE_KIND_EQ_KIND.sub("kind", query)

        # Fix inline expressions in project that need extend first
        # e.g., `| project ..., foo=tostring(bar)` → move to extend
        project_match = _RE_PROJECT_CLAUSE.search(query)
        if project_match and attempt <= 2:
            project_clause = project_match.group(1)
            # Find inline assignments like `col=tostring(...)` or `col=properties.x`
            inline_assigns = re.findall(
                r"(\w+)\s*=\s*(tostring\([^)]+\)|tolower\([^)]+\)|properties\.\w+)", project_clause
            )
            if inline_assigns:
                # Move them to extend statements before project
                extends = " | ".join([f"extend {name}={expr}" for name, expr in inline_assigns])
                # Replace inline assignments with just the alias name in project
                fixed_project = project_clause
                for name, expr in inline_assigns:
                    fixed_project = fixed_project.replace(f"{name}={expr}", name)
                query = query.replace(
                    project_match.group(0), f"| {extends} | project{fixed_project}"
                )

        if attempt <= 3:
            # Handle mv-expand field reference errors: after mv-expand, columns
            # from the expanded object need tostring() wrappers
            error_col_match = _RE_FAILED_RESOLVE_COL.search(error_message)
            if error_col_match:
                col = error_col_match.group(1)
                # Check if this column came from an mv-expand alias
                mv_match = _RE_MV_EXPAND_ALIAS.search(query)
                if mv_match:
                    parent = mv_match.group(1)
                    # Replace bare column refs in where/extend with tostring(parent.col)
                    query = re.sub(
                        rf"\btolower\({col}\)",
                        f"tolower(tostring({parent}.{col}))",
                        query,
                    )
                    query = re.sub(
                        rf"(?<!tostring\()(?<!\.)\b{col}\b(?!\s*=\s*tostring)",
                        f"tostring({parent}.{col})",
                        query,
                    )
                else:
                    # Remove only the extend that references this column
                    query = re.sub(rf"\|\s*extend\s+[^|]*\b{col}\b[^|]*", "", query)
            else:
                error_col_generic = _RE_COLUMN_ERROR.search(error_message)
                if error_col_generic:
                    col = error_col_generic.group(1)
                    query = re.sub(rf"\|\s*extend\s+[^|]*\b{col}\b[^|]*", "", query)
                else:
                    # Unidentifiable ParserFailure: strip computed extends to
                    # simplify, but KEEP any extend whose alias is still referenced
                    # downstream (e.g. a moved `kindValue=tostring(kind)` feeding
                    # `project name, kindValue`). Blindly removing every extend would
                    # orphan such a projection alias and produce another broken query.
                    query = _strip_unreferenced_extends(query)
        else:
            # attempts 4+: the targeted fixes above didn't resolve it. Prefer a
            # known-good builder query for this resource type — it preserves the
            # domain projections (TLS / privateEndpoint / publicNetworkAccess /
            # ACNS / backup mode) that a generic raw-properties dump would lose.
            # A 3-month audit of the KQL knowledge base showed 57% of recorded
            # fallbacks had degraded to such dumps, directly causing reports to
            # hedge on facts that were in fact queryable.
            type_match = _RE_TYPE_MATCH.search(query)
            builder_query = (
                ResourceGraphQueryBuilder.get_query_for_resource_type(type_match.group(1))
                if type_match
                else None
            )
            count_fallback = (
                "Resources\n| summarize count() by type\n| order by count_ desc\n| limit 50"
            )
            if builder_query:
                query = builder_query
            elif attempt <= 6:
                # No builder for this type: simplify projection to safe fields
                query = _RE_PROJECT_BLOCK.sub(
                    "| project name, type, resourceGroup, subscriptionId, location, properties",
                    query,
                )
            elif attempt <= 10 and type_match:
                # No builder: build a minimal raw query from the resource type
                resource_type = type_match.group(1)
                query = (
                    f"Resources\n| where type =~ '{resource_type}'\n"
                    f"| project name, type, resourceGroup, subscriptionId, location, sku, properties\n"
                    f"| limit 100"
                )
            else:
                # Ultimate fallback: just count by type
                query = count_fallback
        return query


# Singleton query fixer instance (lazy-initialized)
_query_fixer: Optional[ResourceGraphQueryFixer] = None


def get_query_fixer(llm=None) -> ResourceGraphQueryFixer:
    """Get or create the singleton query fixer.

    Args:
           llm: Optional Resource Graph specialist instance to inject.
             Only used on first call when creating the singleton.
    """
    global _query_fixer
    if _query_fixer is None:
        _query_fixer = ResourceGraphQueryFixer(llm=llm)
    else:
        if llm is not None and _query_fixer._llm is None:
            _query_fixer._llm = llm
    return _query_fixer


class ResourceGraphQueryInput(BaseModel):
    """Input for Resource Graph query tool."""

    query: str = Field(description="Azure Resource Graph KQL query")


def format_rg_result(result: dict, label: str) -> str:
    """Render a Resource Graph result one row per line.

    The dict repr put every row on a single line, so a budget cut landed
    mid-row and the stored full result could not be searched line by line.

    Args:
        result: Raw ``{"data": [...], "count": N, "total_records": N}`` payload.
        label: Human-readable prefix for the header line.

    Returns:
        A header line followed by one compact JSON object per row.
    """
    if not isinstance(result, dict):
        return str(result)

    rows = result.get("data") or []
    meta = {k: v for k, v in result.items() if k != "data"}
    meta_text = ", ".join(f"{k}={v}" for k, v in meta.items())
    head = f"{label}: {len(rows)} rows" + (f" ({meta_text})" if meta_text else "")
    if not rows:
        return head + ". No matching resources."
    body = [json.dumps(row, ensure_ascii=False, default=str) for row in rows]
    return "\n".join([head + ":"] + body)


class ResourceGraphQueryTool(BaseTool):
    """Tool to execute Azure Resource Graph queries."""

    name: str = "query_azure_resources"
    description: str = """Queries Azure resources using Azure Resource Graph.
    Takes a KQL (Kusto Query Language) query as input and executes it.
    
    Usage examples:
    - Query all Virtual Machines: Resources | where type =~ 'Microsoft.Compute/virtualMachines'
    - Count resources by type: Resources | summarize count() by type
    - Find resources with a specific tag: Resources | where isnotnull(tags['environment'])
    """
    args_schema: Type[BaseModel] = ResourceGraphQueryInput

    _service: Optional[ResourceGraphService] = None

    def __init__(self, service: Optional[ResourceGraphService] = None, **kwargs):
        """Initialize with optional service."""
        super().__init__(**kwargs)
        self._service = service or ResourceGraphService()

    def _run(self, query: str) -> str:
        """Sync execution not supported."""
        raise NotImplementedError("Use async version")

    async def _arun(self, query: str) -> str:
        """Execute Resource Graph query asynchronously with retry logic.

        On InvalidQuery errors, uses Microsoft Learn docs + LLM to fix the query
        and retries up to MAX_QUERY_RETRIES times.
        """
        try:
            result = await execute_kql_with_retry(self._service, query)
            return format_rg_result(result, "Resource Graph query")
        except RuntimeError as e:
            return f"Query execution error: {e}"


class GetResourceTypeSummaryInput(BaseModel):
    """Input for resource type summary tool."""

    pass


class GetResourceTypeSummaryTool(BaseTool):
    """Tool to get summary of resource types in subscription."""

    name: str = "get_resource_type_summary"
    description: str = """Retrieves the count of resources by type in Azure subscriptions.
    Useful for understanding what kinds of resources administrators have.
    """
    args_schema: Type[BaseModel] = GetResourceTypeSummaryInput

    _service: Optional[ResourceGraphService] = None

    def __init__(self, service: Optional[ResourceGraphService] = None, **kwargs):
        """Initialize with optional service."""
        super().__init__(**kwargs)
        self._service = service or ResourceGraphService()

    def _run(self) -> str:
        """Sync execution not supported."""
        raise NotImplementedError("Use async version")

    async def _arun(self) -> str:
        """Get resource type summary asynchronously with retry logic."""
        try:
            query = ResourceGraphQueryBuilder.get_resource_types_summary()
            result = await execute_kql_with_retry(self._service, query, enrich_subscriptions=False)
            return format_rg_result(result, "Resource type summary")
        except RuntimeError as e:
            return f"Resource summary query error: {e}"


class FindRelatedResourcesInput(BaseModel):
    """Input for finding related resources."""

    service_keywords: list[str] = Field(
        description="List of service keywords to search",
        alias="keyword",
    )

    model_config = {"populate_by_name": True}

    @classmethod
    def _validate_keywords(cls, v):
        if isinstance(v, str):
            return [v]
        return v

    def __init__(self, **data):
        # LLM이 string을 보내는 경우 list로 변환
        for key in ("service_keywords", "keyword"):
            if key in data and isinstance(data[key], str):
                data[key] = [data[key]]
        super().__init__(**data)


class FindRelatedResourcesTool(BaseTool):
    """Tool to find resources related to given service keywords."""

    name: str = "find_related_resources"
    description: str = """Searches for Azure resources related to the given service keywords.
    
    Usage examples:
    - service_keywords: ["compute", "vm"] -> Search for compute-related resources
    - service_keywords: ["storage", "blob"] -> Search for storage-related resources
    - service_keywords: ["batch"] -> Search for Batch-related resources
    
    IMPORTANT: The parameter name is 'service_keywords' (a list of strings).
    """
    args_schema: Type[BaseModel] = FindRelatedResourcesInput

    _service: Optional[ResourceGraphService] = None

    def __init__(self, service: Optional[ResourceGraphService] = None, **kwargs):
        """Initialize with optional service."""
        super().__init__(**kwargs)
        self._service = service or ResourceGraphService()

    def _run(self, service_keywords: list[str] = None, **kwargs) -> str:
        """Sync execution not supported."""
        raise NotImplementedError("Use async version")

    async def _arun(self, service_keywords: list[str] = None, **kwargs) -> str:
        """Find related resources asynchronously with retry logic."""
        # LangChain may pass keyword= instead of service_keywords= due to alias
        if service_keywords is None:
            service_keywords = kwargs.get("keyword", kwargs.get("service_keywords", []))
        if isinstance(service_keywords, str):
            service_keywords = [service_keywords]
        try:
            query = ResourceGraphQueryBuilder.find_related_resources(service_keywords)
            result = await execute_kql_with_retry(self._service, query)
        except RuntimeError as e:
            return f"Related resource search error: {e}"
        return self._format_related(service_keywords, result)

    @staticmethod
    def _format_related(service_keywords: list[str], result: dict) -> str:
        """Render matches grouped by resource type.

        The raw ``str(result)`` dict repr overflowed the prompt budget on every
        call, so rows past the cutoff were dropped before the model saw them.
        """
        keywords = ", ".join(service_keywords) or "(none)"
        rows = result.get("data", []) or []
        if not rows:
            return f"No resources found matching keywords: {keywords}."

        by_type: dict[str, list[dict]] = {}
        for row in rows:
            by_type.setdefault(str(row.get("type", "?")), []).append(row)

        subscriptions = {str(r.get("subscriptionId", "")) for r in rows if r.get("subscriptionId")}
        multi_sub = len(subscriptions) > 1

        lines = [
            f"Found {len(rows)} resources matching keywords: {keywords} "
            f"({len(by_type)} resource types).",
            "",
            "### Type distribution (complete — survives any preview cut)",
        ]
        for rtype in sorted(by_type, key=lambda t: (-len(by_type[t]), t)):
            lines.append(f"- {rtype}: {len(by_type[rtype])}")

        for rtype in sorted(by_type):
            items = by_type[rtype]
            lines.append(f"\n### {rtype} ({len(items)})")
            for row in items:
                parts = [
                    str(row.get("name", "?")),
                    str(row.get("location", "?")),
                    f"rg={row.get('resourceGroup', '?')}",
                ]
                if multi_sub:
                    parts.append(f"sub={row.get('subscriptionId', '?')}")
                lines.append(f"- {' | '.join(parts)}")
        return "\n".join(lines)


# ============================================================================
# Advanced Resource Graph Tools
# ============================================================================


class GetServiceResourceDetailsInput(BaseModel):
    """Input for getting detailed resource information for a service."""

    service_name: str = Field(
        description="Azure service name (e.g., 'Blob Storage', 'Virtual Machines', 'AKS', 'Function Apps')"
    )


class GetServiceResourceDetailsTool(BaseTool):
    """Tool to get detailed resource information for a specific Azure service."""

    name: str = "get_service_resource_details"
    description: str = """Retrieves detailed information about resources related to a specific Azure service.
    
    This tool uses optimized queries per service to retrieve detailed properties:
    - Storage: SKU, SFTP enabled, HNS (hierarchical namespace), TLS version, etc.
    - Virtual Machines: VM size, OS type, provisioning state, license, etc.
    - AKS: Kubernetes version, node pool count, network plugin, etc.
    - Function Apps: runtime version, HTTPS setting, hosting plan, etc.
    - SQL Database: SKU, max size, zone redundancy, etc.
    
    Usage examples:
    - service_name: "Blob Storage" -> Full Storage Account details
    - service_name: "Virtual Machines" -> VM details (size, OS, extensions, etc.)
    - service_name: "AKS" -> AKS cluster details
    """
    args_schema: Type[BaseModel] = GetServiceResourceDetailsInput

    _service: Optional[ResourceGraphService] = None

    def __init__(self, service: Optional[ResourceGraphService] = None, **kwargs):
        """Initialize with optional service."""
        super().__init__(**kwargs)
        self._service = service or ResourceGraphService()

    def _run(self, service_name: str) -> str:
        """Sync execution not supported."""
        raise NotImplementedError("Use async version")

    async def _arun(self, service_name: str) -> str:
        """Get detailed resource information asynchronously with retry logic."""
        current_query = ResourceGraphQueryBuilder.get_query_for_update_service(service_name)

        try:
            result = await execute_kql_with_retry(self._service, current_query)
        except RuntimeError as e:
            return f"Resource details query error: {e}"

        data = result.get("data", [])
        count = result.get("count", 0)

        if not data:
            return (
                f"No related resources found for '{service_name}'. "
                f"This means no {service_name} resources exist in the queried subscriptions "
                f"(query executed successfully, 0 results returned)."
            )

        # Record successful query to knowledge base
        record_successful_query(
            resource_type=service_name,
            purpose=f"Get {service_name} resource details",
            query=current_query,
        )

        # Format output
        output_lines = [f"## {service_name} related resource details ({count})" "\n"]

        for i, resource in enumerate(data[:20], 1):  # Limit to 20
            output_lines.append(f"### {i}. {resource.get('name', 'Unknown')}")
            for key, value in resource.items():
                if key in ("name", "subscriptionId") or value is None:
                    continue
                # Show subscriptionName as "subscription"
                if key == "subscriptionName":
                    output_lines.append(f"- subscription: {value}")
                else:
                    output_lines.append(f"- {key}: {value}")
            output_lines.append("")

        if count > 20:
            output_lines.append(f"\n... and {count - 20} more resources")

        return "\n".join(output_lines)


class GetSecurityPostureInput(BaseModel):
    """Input for getting security posture information."""

    check_type: str = Field(
        default="all",
        description="Security check type: 'public_access', 'https' (HTTPS/TLS), 'all'",
    )


class GetSecurityPostureTool(BaseTool):
    """Tool to analyze security posture of resources."""

    name: str = "get_security_posture"
    description: str = """Analyzes the security posture of resources.
    
    Check items:
    - public_access: Search for resources with public network access enabled
    - https: Search for resources without HTTPS or below TLS 1.2
    - all: Perform all security checks
    
    This tool is useful when analyzing security-related Azure Updates.
    Understand the current security settings of resources and determine the need for update application.
    """
    args_schema: Type[BaseModel] = GetSecurityPostureInput

    _service: Optional[ResourceGraphService] = None

    def __init__(self, service: Optional[ResourceGraphService] = None, **kwargs):
        """Initialize with optional service."""
        super().__init__(**kwargs)
        self._service = service or ResourceGraphService()

    def _run(self, check_type: str = "all") -> str:
        """Sync execution not supported."""
        raise NotImplementedError("Use async version")

    async def _arun(self, check_type: str = "all") -> str:
        """Analyze security posture asynchronously with retry logic."""
        results = []

        async def _run_check(query: str, label: str) -> str:
            """Run a single security check query."""
            try:
                result = await execute_kql_with_retry(self._service, query)
                data = result.get("data", [])
                return (label, data, None)
            except RuntimeError as e:
                return (label, [], str(e))

        if check_type in ["public_access", "all"]:
            query = ResourceGraphQueryBuilder.get_resources_with_public_access()
            label, data, err = await _run_check(query, "Resources with public network access")
            if err:
                results.append(f"## {label}\n⚠️ query error: {err[:100]}")
            elif data:
                self._service.enrich_subscription_names(data)
                results.append(f"## {label} ({len(data)})")
                for r in data[:10]:
                    sub = r.get("subscriptionName", "")
                    sub_info = f" [{sub}]" if sub else ""
                    results.append(
                        f"- {r.get('name')} ({r.get('type')}){sub_info} - public access: {r.get('publicAccess', 'N/A')}"
                    )
            else:
                results.append(f"## {label}\n✅ None found")

        if check_type in ["https", "all"]:
            query = ResourceGraphQueryBuilder.get_resources_needing_https_upgrade()
            label, data, err = await _run_check(query, "Resources needing HTTPS/TLS upgrade")
            if err:
                results.append(f"\n## {label}\n⚠️ query error: {err[:100]}")
            elif data:
                self._service.enrich_subscription_names(data)
                results.append(f"\n## {label} ({len(data)})")
                for r in data[:10]:
                    sub = r.get("subscriptionName", "")
                    sub_info = f" [{sub}]" if sub else ""
                    results.append(
                        f"- {r.get('name')} ({r.get('type')}){sub_info} - HTTPS: {r.get('httpsOnly', 'N/A')}, TLS: {r.get('minTls', 'N/A')}"
                    )
            else:
                results.append(f"\n## {label}\n✅ None found")

        return "\n".join(results) if results else "No security check results."


class SearchResourceGraphDocsInput(BaseModel):
    """Input for searching Resource Graph documentation."""

    query: str = Field(
        description="Search content (e.g., 'Storage Account query examples', 'Virtual Machine properties')"
    )


class SearchResourceGraphDocsTool(BaseTool):
    """Tool to search Resource Graph documentation and query examples."""

    name: str = "search_resource_graph_docs"
    description: str = """Searches for Azure Resource Graph query examples and documentation on Microsoft Learn.
    
    Use this tool to:
    1. Search for Resource Graph query examples for specific resource types
    2. Check resource properties and schema information
    3. Learn how to write advanced KQL queries
    
    Usage examples:
    - query: "Storage Account Resource Graph query" -> Storage-related query examples
    - query: "Virtual Machine properties schema" -> VM property schema information
    - query: "Resource Graph starter queries" -> Basic query example collection
    
    You can write more refined queries based on the information found by this tool.
    """
    args_schema: Type[BaseModel] = SearchResourceGraphDocsInput

    _service: Optional[MicrosoftLearnService] = None

    def __init__(self, service: Optional[MicrosoftLearnService] = None, **kwargs):
        """Initialize with optional service."""
        super().__init__(**kwargs)
        self._service = service or MicrosoftLearnService()

    def _run(self, query: str) -> str:
        """Sync execution not supported."""
        raise NotImplementedError("Use async version")

    async def _arun(self, query: str) -> str:
        """Search Resource Graph documentation asynchronously."""
        try:
            # Add Resource Graph context to search
            search_query = f"Azure Resource Graph {query}"
            result = await self._service.search_azure_docs(
                query=search_query,
                top=5,
            )

            if not result.get("results"):
                # Fallback with more specific terms
                return self._get_resource_graph_documentation()

            # Format results
            output_lines = [f"## Resource Graph document search results: '{query}'\n"]
            for i, doc in enumerate(result["results"], 1):
                output_lines.append(f"### {i}. {doc.get('title', 'No title')}")
                output_lines.append(f"- URL: {doc.get('url', '')}")
                output_lines.append(f"- Description: {doc.get('description', 'No description')}")
                output_lines.append("")

            # Add helpful links
            output_lines.append("\n### 📚 Additional references")
            output_lines.append(
                "- [Resource Graph starter queries](https://learn.microsoft.com/azure/governance/resource-graph/samples/starter)"
            )
            output_lines.append(
                "- [Advanced query examples](https://learn.microsoft.com/azure/governance/resource-graph/samples/advanced)"
            )
            output_lines.append(
                "- [Resource property reference](https://learn.microsoft.com/azure/governance/resource-graph/reference/supported-tables-resources)"
            )

            return "\n".join(output_lines)
        except Exception as e:
            logger.error("Resource Graph docs search failed", error=str(e))
            return self._get_resource_graph_documentation()

    def _get_resource_graph_documentation(self) -> str:
        """Return static Resource Graph documentation."""
        return """## Azure Resource Graph Query Guide

### Basic query patterns

1. **Basic resource query**
```kql
Resources
| where type =~ 'Microsoft.Compute/virtualMachines'
| project name, location, resourceGroup, properties
```

2. **Property extension (extend)**
```kql
Resources
| where type =~ 'Microsoft.Storage/storageAccounts'
| extend skuName = sku.name
| extend accessTier = properties.accessTier
| project name, skuName, accessTier
```

3. **Aggregation (summarize)**
```kql
Resources
| summarize count() by type, location
| order by count_ desc
```

### Key resource properties

- **Storage Accounts**: sku.name, sku.tier, properties.accessTier, properties.isHnsEnabled, properties.isSftpEnabled
- **Virtual Machines**: properties.hardwareProfile.vmSize, properties.storageProfile.osDisk.osType
- **AKS**: properties.kubernetesVersion, properties.networkProfile.networkPlugin
- **Web Apps**: properties.state, properties.httpsOnly, properties.siteConfig.minTlsVersion

### Reference docs
- https://learn.microsoft.com/azure/governance/resource-graph/overview
- https://learn.microsoft.com/azure/governance/resource-graph/samples/starter
- https://learn.microsoft.com/azure/governance/resource-graph/samples/advanced
"""


# ============================================================================
# Microsoft Learn Documentation Tools
# ============================================================================


class SearchAzureDocsInput(BaseModel):
    """Input for searching Azure documentation."""

    query: str = Field(
        description="Search content (e.g., 'Blob Storage SFTP', 'Virtual Machine security')"
    )
    service_name: Optional[str] = Field(
        default=None,
        description="Specific Azure service name (e.g., 'Storage', 'Virtual Machines')",
    )


class SearchAzureDocsTool(BaseTool):
    """Tool to search Microsoft Learn Azure documentation."""

    name: str = "search_azure_docs"
    description: str = """Searches for Azure-related documents on Microsoft Learn.
    
    Use this to search official documentation to better understand Azure Update content.
    
    Usage examples:
    - query: "Blob Storage SFTP setup" -> Search for Blob Storage SFTP documents
    - query: "Virtual Machine security best practices", service_name: "Virtual Machines" -> Search for VM security documents
    - query: "Container Apps deployment" -> Search for Container Apps deployment documents
    
    Use this tool to:
    1. Check detailed information about features mentioned in Azure Updates
    2. Understand how to use and configure new features
    3. Look up best practices and recommendations
    """
    args_schema: Type[BaseModel] = SearchAzureDocsInput

    _service: Optional[MicrosoftLearnService] = None

    def __init__(self, service: Optional[MicrosoftLearnService] = None, **kwargs):
        """Initialize with optional service."""
        super().__init__(**kwargs)
        self._service = service or MicrosoftLearnService()

    def _run(self, query: str, service_name: Optional[str] = None) -> str:
        """Sync execution not supported."""
        raise NotImplementedError("Use async version")

    async def _arun(self, query: str, service_name: Optional[str] = None) -> str:
        """Search Azure documentation asynchronously."""
        try:
            result = await self._service.search_azure_docs(
                query=query,
                service_name=service_name,
                top=5,
            )

            if not result.get("results"):
                return f"No search results found for '{query}'."

            # Format results for LLM
            output_lines = [f"## Microsoft Learn search results: '{query}'\n"]
            for i, doc in enumerate(result["results"], 1):
                output_lines.append(f"### {i}. {doc.get('title', 'No title')}")
                output_lines.append(f"- URL: {doc.get('url', '')}")
                output_lines.append(f"- Description: {doc.get('description', 'No description')}")
                output_lines.append("")

            return "\n".join(output_lines)
        except Exception as e:
            logger.error("Azure docs search failed", error=str(e))
            return f"Document search error: {str(e)}"


class GetServiceDocumentationInput(BaseModel):
    """Input for getting service documentation."""

    service_name: str = Field(
        description="Azure service name (e.g., 'Blob Storage', 'Virtual Machines', 'Container Apps')"
    )
    topics: Optional[list[str]] = Field(
        default=None, description="Query topics list (e.g., ['setup', 'security', 'monitoring'])"
    )


class GetServiceDocumentationTool(BaseTool):
    """Tool to get comprehensive documentation for an Azure service."""

    name: str = "get_service_documentation"
    description: str = """Retrieves comprehensive documentation for a specific Azure service.
    
    Use this when you need a deeper understanding of a service mentioned in an Azure Update.
    
    Usage examples:
    - service_name: "Blob Storage" -> Retrieve general Blob Storage documentation
    - service_name: "Virtual Machines", topics: ["security", "backup"] -> Retrieve VM security and backup documentation
    
    This tool returns multiple documents including service overview, features, and best practices.
    """
    args_schema: Type[BaseModel] = GetServiceDocumentationInput

    _service: Optional[MicrosoftLearnService] = None

    def __init__(self, service: Optional[MicrosoftLearnService] = None, **kwargs):
        """Initialize with optional service."""
        super().__init__(**kwargs)
        self._service = service or MicrosoftLearnService()

    def _run(self, service_name: str, topics: Optional[list[str]] = None) -> str:
        """Sync execution not supported."""
        raise NotImplementedError("Use async version")

    async def _arun(self, service_name: str, topics: Optional[list[str]] = None) -> str:
        """Get service documentation asynchronously."""
        try:
            result = await self._service.get_service_documentation(
                service_name=service_name,
                topics=topics,
            )

            if not result.get("results"):
                return f"No documents found for '{service_name}'."

            # Format results for LLM
            output_lines = [f"## {service_name} related Microsoft Learn documents\n"]

            if topics:
                output_lines.append(f"Query topics: {', '.join(topics)}\n")

            for i, doc in enumerate(result["results"], 1):
                output_lines.append(f"### {i}. {doc.get('title', 'No title')}")
                output_lines.append(f"- URL: {doc.get('url', '')}")
                output_lines.append(f"- Description: {doc.get('description', 'No description')}")
                if doc.get("products"):
                    output_lines.append(f"- Related products: {', '.join(doc.get('products', []))}")
                output_lines.append("")

            return "\n".join(output_lines)
        except Exception as e:
            logger.error("Service documentation fetch failed", error=str(e))
            return f"Service document query error: {str(e)}"


class SearchUpdateRelatedDocsInput(BaseModel):
    """Input for searching update-related documentation."""

    update_title: str = Field(description="Azure Update title")
    update_services: list[str] = Field(description="List of Azure services related to the update")
    key_features: Optional[list[str]] = Field(
        default=None, description="Key features or keywords mentioned in the update"
    )


class SearchUpdateRelatedDocsTool(BaseTool):
    """Tool to search documentation related to a specific Azure Update."""

    name: str = "search_update_related_docs"
    description: str = """Comprehensively searches all documents related to an Azure Update.
    
    Use this tool when analyzing an Azure Update to collect related documents at once.
    Input the update's title, related services, and key features to automatically search for related documents.
    
    Usage examples:
    - update_title: "Generally Available: Azure Blob Storage SFTP - Resumable Uploads"
    - update_services: ["Blob Storage", "Storage"]
    - key_features: ["SFTP", "Resumable Uploads"]
    
    This tool collects the following information:
    1. Official documentation about features mentioned in the update
    2. Setup and configuration guides for related services
    3. Best practices and troubleshooting guides
    """
    args_schema: Type[BaseModel] = SearchUpdateRelatedDocsInput

    _service: Optional[MicrosoftLearnService] = None

    def __init__(self, service: Optional[MicrosoftLearnService] = None, **kwargs):
        """Initialize with optional service."""
        super().__init__(**kwargs)
        self._service = service or MicrosoftLearnService()

    def _run(
        self,
        update_title: str,
        update_services: list[str],
        key_features: Optional[list[str]] = None,
    ) -> str:
        """Sync execution not supported."""
        raise NotImplementedError("Use async version")

    async def _arun(
        self,
        update_title: str,
        update_services: list[str],
        key_features: Optional[list[str]] = None,
    ) -> str:
        """Search update-related documentation asynchronously."""
        try:
            all_results = []

            # Filter out invalid service names
            invalid_names = {"unknown", "n/a", "none", "null", ""}
            # Also filter out meta-categories that are not actual Azure services
            meta_categories = {
                "retirements",
                "features",
                "in preview",
                "generally available",
                "public preview",
                "private preview",
                "retirement",
            }
            valid_services = [
                s
                for s in update_services
                if s.strip().lower() not in invalid_names
                and s.strip().lower() not in meta_categories
            ]

            # 1. Search for the update title
            title_results = await self._service.search_azure_docs(
                query=update_title,
                top=3,
            )
            all_results.extend(title_results.get("results", []))

            # 2. Search for each valid service (skip invalid ones)
            # Deduplicate service names (case-insensitive, normalize whitespace)
            seen_service_keys = set()
            deduped_services = []
            for service in valid_services:
                key = service.strip().lower()
                if key not in seen_service_keys:
                    seen_service_keys.add(key)
                    deduped_services.append(service)

            for service in deduped_services[:3]:  # Limit to 3 services
                # Use search_docs directly with Azure filter to avoid query duplication
                service_results = await self._service.search_docs(
                    query=f"Azure {service}",
                    top=2,
                    filter_products=["azure"],
                )
                all_results.extend(service_results.get("results", []))

            # 3. Search for key features
            if key_features:
                valid_features = [f for f in key_features if f.strip().lower() not in invalid_names]
                for feature in valid_features[:3]:  # Limit to 3 features
                    base_service = valid_services[0] if valid_services else ""
                    feature_query = f"{base_service} {feature}".strip() if base_service else feature
                    feature_results = await self._service.search_azure_docs(
                        query=feature_query,
                        top=2,
                    )
                    all_results.extend(feature_results.get("results", []))

            # Deduplicate by URL
            seen_urls = set()
            unique_results = []
            for r in all_results:
                url = r.get("url", "")
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    unique_results.append(r)

            if not unique_results:
                return "No related documents found."

            # Format results
            output_lines = [
                f"## Azure Update related document search results\n",
                f"**Update**: {update_title}\n",
                f"**Related services**: {', '.join(update_services)}\n",
            ]

            if key_features:
                output_lines.append(f"**Key features**: {', '.join(key_features)}\n")

            output_lines.append(f"\n### Related documents ({len(unique_results)})\n")

            for i, doc in enumerate(unique_results[:10], 1):
                output_lines.append(f"#### {i}. {doc.get('title', 'No title')}")
                output_lines.append(f"- URL: {doc.get('url', '')}")
                output_lines.append(f"- Description: {doc.get('description', 'No description')}")
                output_lines.append("")

            return "\n".join(output_lines)
        except Exception as e:
            logger.error("Update-related docs search failed", error=str(e))
            return f"Related document search error: {str(e)}"


# ============================================================================
# Schema Exploration Tool
# ============================================================================


class ExploreResourceSchemaInput(BaseModel):
    """Input for exploring resource type schema."""

    resource_type: str = Field(
        description=(
            "Full Azure resource type to explore "
            "(e.g., 'Microsoft.Storage/storageAccounts', 'Microsoft.Compute/virtualMachines')"
        )
    )
    focus_area: str = Field(
        default="",
        description=(
            "Optional focus hint — what aspect of the resource you want to discover "
            "(e.g., 'TLS settings', 'network configuration', 'replication'). "
            "Leave empty to discover all top-level property keys."
        ),
    )


class ExploreResourceSchemaTool(BaseTool):
    """Tool to discover available properties in a Resource Graph resource type.

    Runs a sampling query that extracts the top-level keys from the `properties`
    bag of one resource, then records the discovery into the KQL knowledge base
    so future queries can reference it.
    """

    name: str = "explore_resource_schema"
    description: str = """Explores the property schema of a specific resource type in Azure Resource Graph.

    Use this to discover undocumented fields within properties.
    Step 1: Samples one resource and displays all top-level keys within properties.
    Step 2 (when focus_area is specified): Displays actual values for matching keys.

    Discovered property paths are recorded in the internal knowledge base for reference in future queries.

    Usage examples:
    - resource_type: "Microsoft.Storage/storageAccounts"  ->  List properties keys
    - resource_type: "Microsoft.Sql/servers", focus_area: "TLS"  ->  Extract TLS-related properties only
    """
    args_schema: Type[BaseModel] = ExploreResourceSchemaInput

    _service: Optional[ResourceGraphService] = None

    def __init__(self, service: Optional[ResourceGraphService] = None, **kwargs):
        super().__init__(**kwargs)
        self._service = service or ResourceGraphService()

    def _run(self, resource_type: str, focus_area: str = "") -> str:
        raise NotImplementedError("Use async version")

    async def _arun(self, resource_type: str, focus_area: str = "") -> str:
        """Explore the properties schema of a resource type."""
        # Check knowledge base first
        known = get_known_schema(resource_type)
        if known and not focus_area:
            lines = [
                f"## Known schema for {resource_type} (from knowledge base)\n",
                f"Property paths ({len(known)}):",
            ]
            for p in known:
                lines.append(f"  - {p}")
            lines.append(
                "\nThese paths were confirmed in previous queries. "
                "You can use them directly in your KQL `extend` statements."
            )
            return "\n".join(lines)

        # --- Phase 1: sample one resource to get top-level property keys ---
        sample_query = (
            f"Resources\n"
            f"| where type =~ '{resource_type}'\n"
            f"| take 1\n"
            f"| project properties, sku, kind, identity"
        )
        try:
            result = await self._service.query_resources(sample_query)
        except Exception as e:
            return f"Schema exploration failed: {e}"

        data = result.get("data", [])
        if not data:
            return f"No resources of type '{resource_type}' found. Cannot explore schema."

        sample = data[0]
        props = sample.get("properties", {})
        sku_val = sample.get("sku")
        kind_val = sample.get("kind")

        # Extract property keys recursively (2 levels deep)
        discovered_paths: list[str] = []
        if isinstance(props, dict):
            for k, v in sorted(props.items()):
                discovered_paths.append(f"properties.{k}")
                if isinstance(v, dict):
                    for k2 in sorted(v.keys()):
                        discovered_paths.append(f"properties.{k}.{k2}")

        if sku_val and isinstance(sku_val, dict):
            for k in sorted(sku_val.keys()):
                discovered_paths.append(f"sku.{k}")

        # Record to knowledge base
        record_schema(resource_type, discovered_paths)

        # --- Phase 2 (optional): if focus_area is specified, show values ---
        focus_detail = ""
        if focus_area:
            focus_lower = focus_area.lower()
            matching = [p for p in discovered_paths if focus_lower in p.lower()]
            if matching:
                # Build a query that projects the matching fields
                extends = []
                projections = ["name"]
                for path in matching[:8]:
                    alias = path.replace(".", "_")
                    extends.append(f"extend {alias} = tostring({path})")
                    projections.append(alias)
                detail_query = (
                    f"Resources\n"
                    f"| where type =~ '{resource_type}'\n"
                    f"| {'| '.join(extends)}\n"
                    f"| project {', '.join(projections)}\n"
                    f"| take 5"
                )
                try:
                    detail_result = await self._service.query_resources(detail_query)
                    detail_data = detail_result.get("data", [])
                    if detail_data:
                        focus_detail = f"\n## Focus: '{focus_area}' — sample values\n"
                        for row in detail_data[:3]:
                            focus_detail += f"Resource: {row.get('name', '?')}\n"
                            for k, v in row.items():
                                if k != "name":
                                    focus_detail += f"  {k}: {v}\n"
                except Exception:
                    focus_detail = (
                        f"\n(Focus query for '{focus_area}' failed — use top-level paths above)\n"
                    )

        # Format output
        lines = [
            f"## Discovered schema for {resource_type}\n",
            f"Top-level property paths ({len(discovered_paths)}):",
        ]
        for p in discovered_paths:
            lines.append(f"  - {p}")

        if kind_val:
            lines.append(f"\nkind: {kind_val}")

        if focus_detail:
            lines.append(focus_detail)

        lines.append(
            "\nUse these paths in your KQL queries with "
            "`| extend alias = tostring(properties.X.Y)`. "
            "These paths have been recorded to the knowledge base for future reference."
        )

        return "\n".join(lines)


# ============================================================================
# Azure Advisor & Service Health Tools
# ============================================================================


class GetAdvisorRecommendationsInput(BaseModel):
    """Input for getting Azure Advisor recommendations."""

    category: Optional[str] = Field(
        default=None,
        description="Recommended category: 'Cost', 'Security', 'Reliability', 'OperationalExcellence', 'Performance' (optional)",
    )
    impact: Optional[str] = Field(
        default=None, description="Impact filter: 'High', 'Medium', 'Low' (optional)"
    )


class GetAdvisorRecommendationsTool(BaseTool):
    """Tool to get Azure Advisor recommendations through the read-only REST API."""

    name: str = "get_advisor_recommendations"
    description: str = """Retrieves Azure Advisor recommendations.
    
    Azure Advisor provides recommendations in the following categories:
    - Cost: Cost optimization recommendations
    - Security: Security improvement recommendations
    - Reliability: Reliability/high-availability recommendations
    - OperationalExcellence: Operational excellence recommendations
    - Performance: Performance improvement recommendations
    
        Uses the read-only Microsoft.Advisor 2023-01-01 REST API. Returns remediation actions,
        learn-more links, potential benefits, risk level, and solution text. API failures remain
        explicit and never fall back to another specialist's Resource Graph surface.
    
    When analyzing Azure Updates, check Advisor recommendations for affected resources.
    """
    args_schema: Type[BaseModel] = GetAdvisorRecommendationsInput

    def _run(
        self,
        category: Optional[str] = None,
        impact: Optional[str] = None,
    ) -> str:
        """Sync execution not supported."""
        raise NotImplementedError("Use async version")

    async def _arun(
        self,
        category: Optional[str] = None,
        impact: Optional[str] = None,
    ) -> str:
        """Get detailed Advisor recommendations asynchronously through REST."""
        return await self._fetch_via_rest_api(category, impact)

    async def _fetch_via_rest_api(
        self, category: Optional[str] = None, impact: Optional[str] = None
    ) -> str:
        """Fetch Advisor recommendations via REST API (detailed, with remediation)."""
        from src.services.azure_rest import AzureRestClient

        try:
            client = AzureRestClient()

            # Build $filter for REST API
            filter_parts = []
            if category:
                filter_parts.append(f"Category eq '{category}'")
            if impact:
                filter_parts.append(f"Impact eq '{impact}'")
            filter_expr = " and ".join(filter_parts) if filter_parts else ""

            params: dict[str, str] = {}
            if filter_expr:
                params["$filter"] = filter_expr

            result = await client.call_api(
                path="/subscriptions/{subscriptionId}/providers/Microsoft.Advisor/recommendations",
                api_version="2023-01-01",
                params=params if params else None,
                max_results=100,
            )

            if "error" in result:
                logger.warning(
                    "advisor_rest_api_failed",
                    error=result["error"],
                )
                return f"Advisor REST API error: {result['error']}"

            values = result.get("value", [])
            if not values:
                return "No active Advisor recommendations found."

            return self._format_rest_results(values)

        except Exception as e:
            logger.warning(
                "advisor_rest_api_error",
                error=str(e),
            )
            return f"Advisor REST API error: {str(e)}"

    def _format_rest_results(self, values: list[dict]) -> str:
        """Format REST API results into detailed markdown."""
        by_category: dict[str, list] = {}
        for rec in values:
            props = rec.get("properties", {})
            cat = props.get("category", "Unknown")
            if cat not in by_category:
                by_category[cat] = []
            by_category[cat].append(rec)

        total = len(values)
        output_lines = [f"## Azure Advisor Recommendations — Detailed ({total})\n"]

        for cat, recs in by_category.items():
            output_lines.append(f"### {cat} ({len(recs)})")
            for i, rec in enumerate(recs[:10], 1):
                props = rec.get("properties", {})
                impact = props.get("impact", "")
                impact_emoji = {"High": "🔴", "Medium": "🟡", "Low": "🟢"}.get(impact, "⚪")
                risk = props.get("risk", "")
                risk_tag = f" | Risk: {risk}" if risk else ""

                short_desc = props.get("shortDescription", {})
                problem = short_desc.get("problem", "N/A")
                solution = short_desc.get("solution", "")

                impacted_value = props.get("impactedValue", "N/A")
                impacted_type = props.get("impactedType", "N/A")
                benefits = props.get("potentialBenefits", "")
                learn_more = props.get("learnMoreLink", "")
                description = props.get("description", "")

                # Remediation info
                remediation = props.get("remediation", {})
                remediation_text = ""
                if remediation:
                    rem_details = remediation.get("details", "")
                    if rem_details:
                        remediation_text = f"\n   - Remediation: {rem_details}"

                # Actions
                actions = props.get("actions", [])
                actions_text = ""
                if actions:
                    action_items = []
                    for action in actions[:3]:
                        caption = action.get("caption", "")
                        link = action.get("link", "")
                        if caption and link:
                            action_items.append(f"[{caption}]({link})")
                        elif caption:
                            action_items.append(caption)
                    if action_items:
                        actions_text = "\n   - Actions: " + " | ".join(action_items)

                lines = [
                    f"{i}. {impact_emoji} **{problem}**{risk_tag}",
                    f"   - Affected: {impacted_value} ({impacted_type})",
                ]
                if solution:
                    lines.append(f"   - Solution: {solution}")
                if benefits:
                    lines.append(f"   - Benefits: {benefits}")
                if description and description != problem:
                    # Truncate long descriptions
                    desc_short = (
                        description[:200] + "..." if len(description) > 200 else description
                    )
                    lines.append(f"   - Detail: {desc_short}")
                if learn_more:
                    lines.append(f"   - Learn more: {learn_more}")
                if remediation_text:
                    lines.append(remediation_text)
                if actions_text:
                    lines.append(actions_text)

                output_lines.append("\n".join(lines))
            output_lines.append("")

        return "\n".join(output_lines)


class GetServiceHealthInput(BaseModel):
    """Input for getting Azure Service Health information."""

    event_type: Optional[str] = Field(
        default=None,
        description="Event type: 'ServiceIssue', 'PlannedMaintenance', 'HealthAdvisory', 'SecurityAdvisory' (optional)",
    )


class GetServiceHealthTool(BaseTool):
    """Tool to get Azure Service Health information via Resource Graph."""

    name: str = "get_service_health"
    description: str = """Retrieves Azure Service Health information via Resource Graph.
    
    Service Health provides the following information:
    - ServiceIssue: Active Azure service incidents
    - PlannedMaintenance: Planned maintenance
    - HealthAdvisory: Health advisory
    - SecurityAdvisory: Security advisory
    
    When analyzing Azure Updates, check current service status to determine update application timing.
    """
    args_schema: Type[BaseModel] = GetServiceHealthInput

    _service: Optional[ResourceGraphService] = None

    def __init__(self, service: Optional[ResourceGraphService] = None, **kwargs):
        """Initialize with optional service."""
        super().__init__(**kwargs)
        self._service = service or ResourceGraphService()

    def _run(self, event_type: Optional[str] = None) -> str:
        """Sync execution not supported."""
        raise NotImplementedError("Use async version")

    async def _arun(self, event_type: Optional[str] = None) -> str:
        """Get Service Health information asynchronously with query-fixing retry logic."""
        type_filter = (
            f"| where tostring(properties.eventType) == '{event_type}'" if event_type else ""
        )

        query = f"""
servicehealthresources
| where type =~ 'microsoft.resourcehealth/events'
{type_filter}
| extend 
    eventType = tostring(properties.eventType),
    status = tostring(properties.status),
    title = tostring(properties.title),
    summary = tostring(properties.summary),
    impactStartTime = todatetime(properties.impactStartTime),
    impactMitigationTime = todatetime(properties.impactMitigationTime),
    impactedServices = tostring(properties.impact)
| project 
    name, 
    eventType, 
    status, 
    title, 
    summary,
    impactStartTime,
    impactMitigationTime,
    impactedServices
| order by impactStartTime desc
| limit 50
"""

        try:
            result = await execute_kql_with_retry(self._service, query, enrich_subscriptions=False)
        except RuntimeError as e:
            return f"Service Health query error: {e}"

        data = result.get("data", [])
        if not data:
            return "✅ No active Service Health events found. All services are operating normally."

        output_lines = [f"## Azure Service Health Events ({len(data)})\n"]

        for event in data[:20]:
            event_emoji = {
                "ServiceIssue": "🚨",
                "PlannedMaintenance": "🔧",
                "HealthAdvisory": "ℹ️",
                "SecurityAdvisory": "🔒",
            }.get(event.get("eventType"), "⚪")

            status_emoji = "🟢" if event.get("status") == "Resolved" else "🔴"

            output_lines.append(
                f"### {event_emoji} {event.get('title', 'N/A')}\n"
                f"- Type: {event.get('eventType', 'N/A')}\n"
                f"- Status: {status_emoji} {event.get('status', 'N/A')}\n"
                f"- Start: {event.get('impactStartTime', 'N/A')}\n"
                f"- Summary: {event.get('summary', 'N/A')[:200]}...\n"
            )

        return "\n".join(output_lines)


# ============================================================================
# Resource Health Tool (via REST API)
# ============================================================================


class GetResourceHealthInput(BaseModel):
    """Input for getting Azure Resource Health availability statuses."""

    resource_type: Optional[str] = Field(
        default=None,
        description=(
            "Filter by resource type (e.g., 'Microsoft.Compute/virtualMachines', "
            "'Microsoft.Web/sites'). Optional — omit to get all resources."
        ),
    )


class GetResourceHealthTool(BaseTool):
    """Tool to get Azure Resource Health availability statuses via REST API."""

    name: str = "get_resource_health"
    description: str = """Retrieves availability status of Azure resources via the Resource Health REST API.

    Returns health state (Available, Unavailable, Degraded, Unknown) for resources,
    along with reason and recommended actions. Essential for impact analysis —
    determines whether resources affected by an update are currently healthy.

    Use when analyzing updates that might affect resource availability or when
    you need to assess the current health state of affected resources.
    """
    args_schema: Type[BaseModel] = GetResourceHealthInput

    def _run(self, resource_type: Optional[str] = None) -> str:
        raise NotImplementedError("Use async version")

    async def _arun(self, resource_type: Optional[str] = None) -> str:
        """Get resource health statuses via REST API."""
        from src.services.azure_rest import AzureRestClient

        try:
            client = AzureRestClient()

            params: dict[str, str] = {}
            if resource_type:
                params["$filter"] = f"resourceType eq '{resource_type}'"

            result = await client.call_api(
                path="/subscriptions/{subscriptionId}/providers/Microsoft.ResourceHealth/availabilityStatuses",
                api_version="2023-07-01-preview",
                params=params if params else None,
                max_results=100,
            )

            if "error" in result:
                return f"Resource Health API error: {result['error']}"

            values = result.get("value", [])
            if not values:
                return "No resource health data available."

            return self._format_results(values)

        except Exception as e:
            logger.warning("resource_health_api_error", error=str(e))
            return f"Resource Health API error: {e}"

    def _format_results(self, values: list[dict]) -> str:
        """Format resource health results into markdown."""
        by_status: dict[str, list] = {}
        for item in values:
            props = item.get("properties", {})
            status = props.get("availabilityState", "Unknown")
            if status not in by_status:
                by_status[status] = []
            by_status[status].append(item)

        total = len(values)
        status_counts = {s: len(items) for s, items in by_status.items()}
        summary_parts = []
        for s, c in sorted(status_counts.items()):
            emoji = {"Available": "🟢", "Unavailable": "🔴", "Degraded": "🟡"}.get(s, "⚪")
            summary_parts.append(f"{emoji} {s}: {c}")

        output_lines = [
            f"## Resource Health Status ({total} resources)\n",
            f"Summary: {' | '.join(summary_parts)}\n",
        ]

        # Show non-available resources first (most important for impact analysis)
        for status in ["Unavailable", "Degraded", "Unknown", "Available"]:
            items = by_status.get(status, [])
            if not items:
                continue

            status_emoji = {"Available": "🟢", "Unavailable": "🔴", "Degraded": "🟡"}.get(
                status, "⚪"
            )
            # Show details for non-available; just count for available
            if status == "Available":
                output_lines.append(f"### {status_emoji} Available ({len(items)} resources)")
                # Only show first 5 available resources
                for item in items[:5]:
                    res_id = item.get("id", "")
                    res_name = res_id.split("/")[-3] if "/" in res_id else "N/A"
                    output_lines.append(f"- {res_name}")
                if len(items) > 5:
                    output_lines.append(f"- ... and {len(items) - 5} more")
            else:
                output_lines.append(f"### {status_emoji} {status} ({len(items)} resources)")
                for item in items[:15]:
                    props = item.get("properties", {})
                    res_id = item.get("id", "")
                    # Extract resource name from the long ID
                    parts = res_id.split("/providers/")
                    res_name = parts[0].split("/")[-1] if parts else "N/A"
                    reason = props.get("reasonType", "")
                    summary = props.get("summary", "")
                    res_type = props.get("resourceType", "") if props.get("resourceType") else ""
                    recommended = props.get("recommendedActions", [])

                    lines = [f"- **{res_name}**"]
                    if res_type:
                        lines[0] += f" ({res_type})"
                    if reason:
                        lines.append(f"  - Reason: {reason}")
                    if summary:
                        lines.append(f"  - Summary: {summary[:200]}")
                    if recommended:
                        for action in recommended[:2]:
                            action_text = (
                                action.get("action", "")
                                if isinstance(action, dict)
                                else str(action)
                            )
                            if action_text:
                                lines.append(f"  - Action: {action_text[:150]}")
                    output_lines.append("\n".join(lines))
            output_lines.append("")

        return "\n".join(output_lines)


# ============================================================================
# Policy Compliance Tool (via REST API)
# ============================================================================


class GetPolicyComplianceInput(BaseModel):
    """Input for getting Azure Policy compliance summary."""

    resource_type: Optional[str] = Field(
        default=None,
        description=(
            "Filter by resource type (e.g., 'Microsoft.Compute/virtualMachines'). "
            "Optional — omit for overall compliance summary."
        ),
    )
    policy_category: Optional[str] = Field(
        default=None,
        description="Filter by policy category (e.g., 'Security', 'Monitoring'). Optional.",
    )


class GetPolicyComplianceTool(BaseTool):
    """Tool to get Azure Policy compliance summary via REST API."""

    name: str = "get_policy_compliance"
    description: str = """Retrieves Azure Policy compliance summary via the Policy Insights REST API.

    Returns compliance state (Compliant, NonCompliant) for resources and policies.
    Useful for impact analysis — determines whether an update's changes might affect
    policy compliance, or whether non-compliant resources need the update to become compliant.

    Use when analyzing updates related to security, compliance, governance,
    or configuration changes that might affect policy evaluation.
    """
    args_schema: Type[BaseModel] = GetPolicyComplianceInput

    def _run(
        self,
        resource_type: Optional[str] = None,
        policy_category: Optional[str] = None,
    ) -> str:
        raise NotImplementedError("Use async version")

    async def _arun(
        self,
        resource_type: Optional[str] = None,
        policy_category: Optional[str] = None,
    ) -> str:
        """Get policy compliance summary via REST API."""
        from src.services.azure_rest import AzureRestClient

        try:
            client = AzureRestClient()

            params: dict[str, str] = {}
            filter_parts = []
            if resource_type:
                filter_parts.append(f"resourceType eq '{resource_type}'")
            if policy_category:
                filter_parts.append(f"policyDefinitionCategory eq '{policy_category}'")
            if filter_parts:
                params["$filter"] = " and ".join(filter_parts)

            result = await client.call_api(
                path="/subscriptions/{subscriptionId}/providers/Microsoft.PolicyInsights/policyStates/latest/summarize",
                api_version="2024-10-01",
                method="POST",
                params=params if params else None,
                max_results=50,
            )

            if "error" in result:
                return f"Policy Compliance API error: {result['error']}"

            values = result.get("value", [])
            if not values:
                return "No policy compliance data available."

            return self._format_results(values)

        except Exception as e:
            logger.warning("policy_compliance_api_error", error=str(e))
            return f"Policy Compliance API error: {e}"

    def _format_results(self, values: list[dict]) -> str:
        """Format policy compliance summary into markdown."""
        output_lines = ["## Azure Policy Compliance Summary\n"]

        for summary in values[:1]:  # Usually returns single summary object
            results = summary.get("results", {})
            total = results.get("resourceDetails", [])

            compliant = 0
            non_compliant = 0
            for detail in total:
                compliance = detail.get("complianceState", "")
                count = detail.get("count", 0)
                if compliance == "compliant":
                    compliant = count
                elif compliance == "noncompliant":
                    non_compliant = count

            total_count = compliant + non_compliant
            if total_count > 0:
                compliance_pct = round(compliant / total_count * 100, 1)
                output_lines.append(
                    f"**Overall**: {compliance_pct}% compliant "
                    f"(🟢 {compliant} compliant | 🔴 {non_compliant} non-compliant)\n"
                )

            # Policy assignments summary
            assignments = summary.get("policyAssignments", [])
            if assignments:
                # Sort by non-compliant count descending
                non_compliant_assignments = []
                for assign in assignments:
                    a_results = assign.get("results", {})
                    a_nc = 0
                    for d in a_results.get("resourceDetails", []):
                        if d.get("complianceState") == "noncompliant":
                            a_nc = d.get("count", 0)
                    if a_nc > 0:
                        non_compliant_assignments.append((assign, a_nc))

                non_compliant_assignments.sort(key=lambda x: x[1], reverse=True)

                if non_compliant_assignments:
                    output_lines.append(
                        f"### Non-Compliant Policies ({len(non_compliant_assignments)})\n"
                    )
                    for assign, nc_count in non_compliant_assignments[:10]:
                        policy_id = assign.get("policyAssignmentId", "")
                        policy_name = policy_id.split("/")[-1] if policy_id else "N/A"
                        output_lines.append(
                            f"- 🔴 **{policy_name}**: {nc_count} non-compliant resources"
                        )

                        # Show affected resource types
                        policy_defs = assign.get("policyDefinitions", [])
                        for pd in policy_defs[:3]:
                            pd_results = pd.get("results", {})
                            pd_nc = 0
                            for d in pd_results.get("resourceDetails", []):
                                if d.get("complianceState") == "noncompliant":
                                    pd_nc = d.get("count", 0)
                            if pd_nc > 0:
                                ref = pd.get("policyDefinitionReferenceId", "")
                                output_lines.append(f"  - {ref}: {pd_nc} resources")

                    output_lines.append("")

        return "\n".join(output_lines)


# ============================================================================
# Enhanced Service Health (REST API mode)
# ============================================================================


class GetServiceHealthEventsInput(BaseModel):
    """Input for getting detailed Azure Service Health events via REST API."""

    event_type: Optional[str] = Field(
        default=None,
        description="Event type: 'ServiceIssue', 'PlannedMaintenance', 'HealthAdvisory', 'SecurityAdvisory' (optional)",
    )
    service_name: Optional[str] = Field(
        default=None,
        description="Filter by Azure service name (e.g., 'Virtual Machines', 'App Service'). Optional.",
    )


class GetServiceHealthEventsTool(BaseTool):
    """Tool to get detailed Azure Service Health events via REST API."""

    name: str = "get_service_health_events"
    description: str = """Retrieves detailed Azure Service Health events via the Resource Health REST API.

    More detailed than the KQL-based get_service_health tool. Returns full event descriptions,
    affected services/regions, recommended actions, and FAQ links.

    Use when analyzing updates that might be related to active incidents, planned maintenance,
    or health advisories. Helps determine if an update is a response to known issues.
    """
    args_schema: Type[BaseModel] = GetServiceHealthEventsInput

    def _run(
        self,
        event_type: Optional[str] = None,
        service_name: Optional[str] = None,
    ) -> str:
        raise NotImplementedError("Use async version")

    async def _arun(
        self,
        event_type: Optional[str] = None,
        service_name: Optional[str] = None,
    ) -> str:
        """Get detailed service health events via REST API."""
        from src.services.azure_rest import AzureRestClient

        try:
            client = AzureRestClient()

            params: dict[str, str] = {}
            filter_parts = []
            if event_type:
                filter_parts.append(f"properties/eventType eq '{event_type}'")
            if service_name:
                filter_parts.append(
                    f"properties/impact/any(i: i/impactedService eq '{service_name}')"
                )
            if filter_parts:
                params["$filter"] = " and ".join(filter_parts)

            result = await client.call_api(
                path="/subscriptions/{subscriptionId}/providers/Microsoft.ResourceHealth/events",
                api_version="2024-02-01",
                params=params if params else None,
                max_results=30,
            )

            if "error" in result:
                return f"Service Health Events API error: {result['error']}"

            values = result.get("value", [])
            if not values:
                return (
                    "✅ No active Service Health events found. All services are operating normally."
                )

            return self._format_results(values)

        except Exception as e:
            logger.warning("service_health_events_api_error", error=str(e))
            return f"Service Health Events API error: {e}"

    def _format_results(self, values: list[dict]) -> str:
        """Format service health event results into detailed markdown."""
        output_lines = [f"## Service Health Events — Detailed ({len(values)})\n"]

        for event in values[:15]:
            props = event.get("properties", {})
            event_type = props.get("eventType", "Unknown")
            status = props.get("status", "Unknown")
            title = props.get("title", "N/A")
            summary = props.get("summary", "")
            description = props.get("description", "")
            impact_start = props.get("impactStartTime", "")
            impact_end = props.get("impactMitigationTime", "")

            event_emoji = {
                "ServiceIssue": "🚨",
                "PlannedMaintenance": "🔧",
                "HealthAdvisory": "ℹ️",
                "SecurityAdvisory": "🔒",
            }.get(event_type, "⚪")

            status_emoji = "🟢" if status in ("Resolved", "Complete") else "🔴"

            lines = [
                f"### {event_emoji} {title}",
                f"- Type: {event_type} | Status: {status_emoji} {status}",
                f"- Impact start: {impact_start}",
            ]

            if impact_end:
                lines.append(f"- Impact end: {impact_end}")

            # Affected services and regions
            impacts = props.get("impact", [])
            if impacts:
                affected = []
                for impact in impacts[:5]:
                    svc = impact.get("impactedService", "")
                    regions = [
                        r.get("impactedRegion", "") for r in impact.get("impactedRegions", [])
                    ]
                    region_str = ", ".join(r for r in regions[:5] if r)
                    if svc:
                        affected.append(f"{svc} ({region_str})" if region_str else svc)
                if affected:
                    lines.append(f"- Affected: {' | '.join(affected)}")

            # Summary/description
            text = description or summary
            if text:
                text_clean = text[:400] + "..." if len(text) > 400 else text
                lines.append(f"- Detail: {text_clean}")

            # Recommended actions
            recommended = props.get("recommendedActions", {})
            if recommended:
                message = recommended.get("message", "")
                if message:
                    lines.append(f"- Recommended: {message[:200]}")

                actions = recommended.get("actions", [])
                for action in actions[:2]:
                    action_text = action.get("actionText", "")
                    if action_text:
                        lines.append(f"  - {action_text[:150]}")

            # FAQ links
            faqs = props.get("faqs", [])
            for faq in faqs[:2]:
                q = faq.get("question", "")
                a = faq.get("answer", "")
                if q:
                    lines.append(f"- FAQ: {q}")
                    if a:
                        lines.append(f"  - {a[:200]}")

            output_lines.append("\n".join(lines))
            output_lines.append("")

        return "\n".join(output_lines)


# ============================================================================
# Cost Management Tools
# ============================================================================


class GetCostByResourceTypeInput(BaseModel):
    """Input for getting cost by resource type."""

    days: int = Field(
        default=30, ge=1, le=365, description="Query costs for past N days (1-365, default: 30)"
    )
    top: int = Field(
        default=15, ge=1, le=100, description="Return top N resource types (1-100, default: 15)"
    )


class GetCostByResourceTypeTool(BaseTool):
    """Tool to get cost breakdown by resource type."""

    name: str = "get_cost_by_resource_type"
    description: str = """Retrieves cost by Azure resource type.
    
    Useful when evaluating updates that may affect costs during Azure Update analysis.
    Identify which resource types are currently generating the most costs.
    
    Usage examples:
    - days: 30, top: 10 -> Top 10 resource types by cost for the last 30 days
    """
    args_schema: Type[BaseModel] = GetCostByResourceTypeInput

    _service: Optional[CostManagementService] = None

    def __init__(self, service: Optional[CostManagementService] = None, **kwargs):
        """Initialize with optional service."""
        super().__init__(**kwargs)
        self._service = service or CostManagementService()

    def _run(self, days: int = 30, top: int = 15) -> str:
        """Sync execution not supported."""
        raise NotImplementedError("Use async version")

    async def _arun(self, days: int = 30, top: int = 15) -> str:
        """Get cost by resource type asynchronously."""
        try:
            result = await self._service.get_cost_by_resource_type(days=days, top=top)

            if not result.get("success"):
                return f"Cost query error: {result.get('error', 'Unknown error')}"

            costs = result.get("costs_by_type", [])
            if not costs:
                return "No cost data for this period."

            output_lines = [
                f"## Cost by resource type (last {days} days)\n",
                f"**Total cost**: ${result.get('total_cost', 0):,.2f}\n",
                f"**Period**: {result.get('start_date', 'N/A')} ~ {result.get('end_date', 'N/A')}\n",
                "\n### Top cost resource types\n",
            ]

            for i, cost in enumerate(costs, 1):
                percentage = (
                    (cost["cost"] / result["total_cost"] * 100) if result["total_cost"] > 0 else 0
                )
                bar = "█" * int(percentage / 5) + "░" * (20 - int(percentage / 5))
                output_lines.append(
                    f"{i:2}. {cost['resource_type']}\n"
                    f"    ${cost['cost']:,.2f} ({percentage:.1f}%) [{bar}]"
                )

            return "\n".join(output_lines)

        except Exception as e:
            logger.error("Cost by resource type query failed", error=str(e))
            return f"Cost query error: {str(e)}"


class GetCostByServiceInput(BaseModel):
    """Input for getting cost by Azure service."""

    days: int = Field(default=30, description="Query costs for past N days (default: 30)")


class GetCostByServiceTool(BaseTool):
    """Tool to get cost breakdown by Azure service."""

    name: str = "get_cost_by_service"
    description: str = """Retrieves cost by Azure service.
    
    Useful when evaluating how an Azure Update may affect specific service costs.
    """
    args_schema: Type[BaseModel] = GetCostByServiceInput

    _service: Optional[CostManagementService] = None

    def __init__(self, service: Optional[CostManagementService] = None, **kwargs):
        """Initialize with optional service."""
        super().__init__(**kwargs)
        self._service = service or CostManagementService()

    def _run(self, days: int = 30) -> str:
        """Sync execution not supported."""
        raise NotImplementedError("Use async version")

    async def _arun(self, days: int = 30) -> str:
        """Get cost by service asynchronously."""
        try:
            result = await self._service.get_cost_by_service(days=days)

            if not result.get("success"):
                return f"Service cost query error: {result.get('error', 'Unknown error')}"

            costs = result.get("costs_by_service", [])
            if not costs:
                return "No cost data for this period."

            output_lines = [
                f"## Azure service cost (last {days} days)\n",
                f"**Total cost**: ${result.get('total_cost', 0):,.2f}\n",
            ]

            for i, cost in enumerate(costs[:20], 1):
                output_lines.append(f"{i}. **{cost['service']}**: ${cost['cost']:,.2f}")

            return "\n".join(output_lines)

        except Exception as e:
            logger.error("Cost by service query failed", error=str(e))
            return f"Service cost query error: {str(e)}"


# ============================================================================
# Azure Billing Tools
# ============================================================================


class ListBillingAccountsInput(BaseModel):
    """Input for listing accessible Azure Billing accounts."""

    top: int = Field(
        default=20,
        ge=1,
        le=50,
        description="Maximum billing accounts to return (1-50, default: 20)",
    )


class ListBillingAccountsTool(BaseTool):
    """List Azure Billing accounts visible to the Hosted Agent identity."""

    name: str = "list_billing_accounts"
    description: str = (
        "Lists Azure Billing accounts available to the current identity through the "
        "Microsoft.Billing 2024-04-01 REST API. Use for pricing, agreement, invoice, or "
        "billing-scope updates before requesting a specific billing profile."
    )
    args_schema: Type[BaseModel] = ListBillingAccountsInput

    _service: Optional[BillingService] = None

    def __init__(self, service: Optional[BillingService] = None, **kwargs):
        """Initialize with an optional Billing service."""
        super().__init__(**kwargs)
        self._service = service or BillingService()

    def _run(self, top: int = 20) -> str:
        """Sync execution is not supported."""
        raise NotImplementedError("Use async version")

    async def _arun(self, top: int = 20) -> str:
        """List and format accessible billing accounts."""
        result = await self._service.list_billing_accounts(top=top)
        if not result["success"]:
            return f"Billing API error: {result['error']}"
        accounts = result["accounts"]
        if not accounts:
            return (
                "No billing accounts are visible to this identity. This does not prove that "
                "the tenant has no billing account."
            )

        lines = [
            f"## Azure Billing accounts ({result['api_version']})",
            f"Visible accounts: {result['count']}",
        ]
        for account in accounts:
            lines.extend(
                [
                    f"- {account['display_name'] or account['name']}",
                    f"  Evidence: billing:{account['id']}",
                    f"  Name: {account['name']}",
                    f"  Status: {account['status'] or 'unknown'}",
                    f"  Account type: {account['account_type'] or 'unknown'}",
                    f"  Agreement type: {account['agreement_type'] or 'unknown'}",
                    f"  Read access: {account['has_read_access']}",
                ]
            )
        return "\n".join(lines)


class ListBillingProfilesInput(BaseModel):
    """Input for listing profiles under one Azure Billing account."""

    billing_account_name: str = Field(
        min_length=1,
        description="Billing account name returned by list_billing_accounts",
    )
    top: int = Field(
        default=50,
        ge=1,
        le=50,
        description="Maximum billing profiles to return (1-50, default: 50)",
    )


class ListBillingProfilesTool(BaseTool):
    """List profiles under one accessible Azure Billing account."""

    name: str = "list_billing_profiles"
    description: str = (
        "Lists billing profiles for one account through the Microsoft.Billing 2024-04-01 "
        "REST API. Call list_billing_accounts first and pass its exact account name. This "
        "operation is supported for Microsoft Customer Agreement and Microsoft Partner "
        "Agreement accounts; preserve errors for other account types as evidence gaps."
    )
    args_schema: Type[BaseModel] = ListBillingProfilesInput

    _service: Optional[BillingService] = None

    def __init__(self, service: Optional[BillingService] = None, **kwargs):
        """Initialize with an optional Billing service."""
        super().__init__(**kwargs)
        self._service = service or BillingService()

    def _run(self, billing_account_name: str, top: int = 50) -> str:
        """Sync execution is not supported."""
        raise NotImplementedError("Use async version")

    async def _arun(self, billing_account_name: str, top: int = 50) -> str:
        """List and format billing profiles."""
        result = await self._service.list_billing_profiles(
            billing_account_name=billing_account_name,
            top=top,
        )
        if not result["success"]:
            return f"Billing profile API error: {result['error']}"
        profiles = result["profiles"]
        if not profiles:
            return (
                f"No billing profiles are visible under {billing_account_name}. The account "
                "type may not support profiles or the identity may lack billing-scope access."
            )

        lines = [
            f"## Billing profiles ({result['api_version']})",
            f"Billing account: {billing_account_name}",
            f"Visible profiles: {result['count']}",
        ]
        for profile in profiles:
            lines.extend(
                [
                    f"- {profile['display_name'] or profile['name']}",
                    f"  Evidence: billing:{profile['id']}",
                    f"  Status: {profile['status'] or 'unknown'}",
                    f"  Currency: {profile['currency'] or 'unknown'}",
                    f"  Invoice day: {profile['invoice_day']}",
                    f"  Purchase order: {profile['purchase_order_number'] or '(none)'}",
                ]
            )
        return "\n".join(lines)


# ============================================================================
# Log Analytics Tools
# ============================================================================


class QueryLogAnalyticsInput(BaseModel):
    """Input for querying Log Analytics."""

    query: str = Field(description="KQL query string")
    hours: int = Field(default=24, description="Past N hours range (default: 24)")


class QueryLogAnalyticsTool(BaseTool):
    """Tool to query Azure Log Analytics."""

    name: str = "query_log_analytics"
    description: str = """Executes KQL queries on Azure Log Analytics workspace.
    
    Can analyze operational logs, security events, performance metrics, etc.
    Useful for understanding current operational status during Azure Update analysis.
    
    Key tables:
    - AzureActivity: Azure activity log
    - SigninLogs: Sign-in activity
    - SecurityEvent: Security events
    - Perf: Performance counters
    - ContainerLog: Container logs
    """
    args_schema: Type[BaseModel] = QueryLogAnalyticsInput

    _service: Optional[LogAnalyticsService] = None

    def __init__(self, service: Optional[LogAnalyticsService] = None, **kwargs):
        """Initialize with optional service."""
        super().__init__(**kwargs)
        self._service = service or LogAnalyticsService()

    def _run(self, query: str, hours: int = 24) -> str:
        """Sync execution not supported."""
        raise NotImplementedError("Use async version")

    async def _arun(self, query: str, hours: int = 24) -> str:
        """Query Log Analytics asynchronously."""
        from datetime import timedelta

        try:
            result = await self._service.query_logs(query, timedelta(hours=hours))

            if not result.get("success"):
                return f"Log Analytics query error: {result.get('error', 'Unknown error')}"

            data = result.get("data", [])
            if not data:
                return "No query results."

            output_lines = [f"## Log Analytics query results ({len(data)} rows)\n"]

            # Format as table
            if data:
                columns = list(data[0].keys())
                output_lines.append("| " + " | ".join(columns) + " |")
                output_lines.append("| " + " | ".join(["---"] * len(columns)) + " |")

                for row in data[:30]:
                    values = [str(row.get(col, ""))[:50] for col in columns]
                    output_lines.append("| " + " | ".join(values) + " |")

                if len(data) > 30:
                    output_lines.append(f"\n... and {len(data) - 30} more rows")

            return "\n".join(output_lines)

        except Exception as e:
            logger.error("Log Analytics query failed", error=str(e))
            return f"Log Analytics query error: {str(e)}"


class GetRecentErrorsInput(BaseModel):
    """Input for getting recent errors."""

    hours: int = Field(default=24, description="Past N hours range (default: 24)")


class GetRecentErrorsTool(BaseTool):
    """Tool to get recent errors from Log Analytics."""

    name: str = "get_recent_errors"
    description: str = """Retrieves recent error logs from Azure Log Analytics.
    
    Useful for understanding the current error state of the operational environment before applying Azure Updates.
    Evaluate whether the update can resolve existing issues or poses new risks.
    """
    args_schema: Type[BaseModel] = GetRecentErrorsInput

    _service: Optional[LogAnalyticsService] = None

    def __init__(self, service: Optional[LogAnalyticsService] = None, **kwargs):
        """Initialize with optional service."""
        super().__init__(**kwargs)
        self._service = service or LogAnalyticsService()

    def _run(self, hours: int = 24) -> str:
        """Sync execution not supported."""
        raise NotImplementedError("Use async version")

    async def _arun(self, hours: int = 24) -> str:
        """Get recent errors asynchronously."""
        try:
            result = await self._service.get_recent_errors(hours=hours)

            if not result.get("success"):
                return f"Error log query failed: {result.get('error', 'Unknown error')}"

            errors = result.get("errors", [])
            if not errors:
                return f"✅ No errors found in the last {hours} hours."

            output_lines = [
                f"## Recent error log ({hours} hours)\n",
                f"**Total error types**: {len(errors)}\n",
            ]

            for i, error in enumerate(errors[:20], 1):
                count = error.get("ErrorCount", 0)
                msg = error.get("ErrorMessage", "Unknown")[:100]
                output_lines.append(f"{i}. **[{count} occurrences]** {msg}")

            return "\n".join(output_lines)

        except Exception as e:
            logger.error("Recent errors query failed", error=str(e))
            return f"Error log query failed: {str(e)}"


class GetActivityLogSummaryInput(BaseModel):
    """Input for getting activity log summary."""

    hours: int = Field(default=24, description="Past N hours range (default: 24)")


class GetActivityLogSummaryTool(BaseTool):
    """Tool to get Azure Activity Log summary."""

    name: str = "get_activity_log_summary"
    description: str = """Retrieves Azure Activity Log summary.
    
    Provides a summary of recent Azure operations:
    - Total operations and failure rate
    - Top callers information
    - Activity by resource group
    
    Useful for understanding recent changes before applying Azure Updates.
    """
    args_schema: Type[BaseModel] = GetActivityLogSummaryInput

    _service: Optional[LogAnalyticsService] = None

    def __init__(self, service: Optional[LogAnalyticsService] = None, **kwargs):
        """Initialize with optional service."""
        super().__init__(**kwargs)
        self._service = service or LogAnalyticsService()

    def _run(self, hours: int = 24) -> str:
        """Sync execution not supported."""
        raise NotImplementedError("Use async version")

    async def _arun(self, hours: int = 24) -> str:
        """Get activity log summary asynchronously."""
        try:
            result = await self._service.get_activity_log_summary(hours=hours)

            if not result.get("success"):
                return f"Activity Log query failed: {result.get('error', 'Unknown error')}"

            output_lines = [
                f"## Azure Activity Log Summary ({hours} hours)\n",
                f"- **Total operations**: {result.get('total_operations', 0):,}",
                f"- **Failed operations**: {result.get('total_failures', 0):,}",
                f"- **Failure rate**: {result.get('failure_rate', 0):.1f}%\n",
            ]

            operations = result.get("operations", [])
            if operations:
                output_lines.append("### Top operations")
                for i, op in enumerate(operations[:10], 1):
                    success = op.get("SuccessCount", 0)
                    fail = op.get("FailCount", 0)
                    output_lines.append(
                        f"{i}. {op.get('OperationName', 'N/A')}\n"
                        f"   - Success: {success}, Failed: {fail}"
                    )

            return "\n".join(output_lines)

        except Exception as e:
            logger.error("Activity log summary query failed", error=str(e))
            return f"Activity Log query failed: {str(e)}"


# ---------------------------------------------------------------------------
# Service Region Availability Tool (via ARM providers API)
# ---------------------------------------------------------------------------


class GetServiceRegionAvailabilityInput(BaseModel):
    """Input for checking Azure service availability by region."""

    provider_namespace: str = Field(
        description=(
            "Azure resource provider namespace to check "
            "(e.g., 'Microsoft.Databricks', 'Microsoft.App', 'Microsoft.DBforPostgreSQL'). "
            "This is the authoritative source of truth for the regions a service supports."
        )
    )
    resource_type: str = Field(
        default="",
        description=(
            "Optional specific resource type under the namespace to focus on "
            "(e.g., 'workspaces' for Microsoft.Databricks, 'containerApps' for Microsoft.App). "
            "Omit to report availability for all resource types in the namespace."
        ),
    )
    regions: str = Field(
        default="",
        description=(
            "Optional comma-separated regions to check (e.g., 'koreacentral,koreasouth,eastus'). "
            "Omit to auto-detect the administrator's primary regions from their actual resources."
        ),
    )


class GetServiceRegionAvailabilityTool(BaseTool):
    """Check whether an Azure service is available in specific regions.

    Uses the ARM ``providers/{namespace}`` API, which returns the authoritative
    list of supported locations per resource type — far more accurate than doc
    search for answering "is service X available in region Y?".
    """

    name: str = "get_service_region_availability"
    description: str = """Checks whether an Azure service/feature is available in specific regions — the DEFINITIVE answer.

    Uses the Azure Resource Manager providers API (`/providers/{namespace}`), which returns the
    authoritative list of supported regions per resource type. Prefer this OVER documentation search
    whenever an update announces a new service, feature, SKU, or region expansion and you need to
    confirm availability in the administrator's regions.

    Examples:
    - "Is Azure Databricks available in Korea Central?"
      → provider_namespace="Microsoft.Databricks", regions="koreacentral"
    - "Which regions support Azure Container Apps?"
      → provider_namespace="Microsoft.App", resource_type="containerApps"

    If `regions` is omitted, the tool auto-detects the administrator's primary regions from their
    actual resource footprint, so the verdict is grounded in the real environment. Returns an
    availability matrix (resource type × region) with clear ✅/❌ markers and a concise verdict.
    """
    args_schema: Type[BaseModel] = GetServiceRegionAvailabilityInput

    _service: Optional[ResourceGraphService] = None

    def __init__(self, service: Optional[ResourceGraphService] = None, **kwargs):
        """Initialize with an optional shared Resource Graph service for region auto-detection."""
        super().__init__(**kwargs)
        self._service = service or ResourceGraphService()

    def _run(self, *args, **kwargs) -> str:
        raise NotImplementedError("Use async version")

    @staticmethod
    def _normalize_region(name: str) -> str:
        """Normalize an Azure region name to its canonical short form.

        "Korea Central" -> "koreacentral", "UK West" -> "ukwest".
        """
        return "".join(str(name).lower().split())

    async def _detect_admin_regions(self, limit: int = 5) -> list[str]:
        """Detect the administrator's primary regions from their resource footprint."""
        try:
            result = await self._service.query_resources(
                "Resources | where isnotempty(location) "
                "| summarize count() by location | order by count_ desc | take 20"
            )
            regions = []
            for row in result.get("data", []):
                loc = str(row.get("location", "")).strip()
                if loc and loc.lower() not in ("global", "unknown"):
                    regions.append(loc)
            return regions[:limit]
        except Exception as e:
            logger.warning("admin_region_detection_failed", error=str(e))
            return []

    async def _arun(
        self,
        provider_namespace: str,
        resource_type: str = "",
        regions: str = "",
    ) -> str:
        """Check service region availability via the ARM providers API."""
        from src.services.azure_rest import AzureRestClient

        namespace = provider_namespace.strip()
        if not namespace:
            return "Error: provider_namespace is required (e.g., 'Microsoft.Databricks')."

        target_regions = [self._normalize_region(r) for r in regions.split(",") if r.strip()]
        auto_detected = False
        if not target_regions:
            detected = await self._detect_admin_regions()
            target_regions = [self._normalize_region(r) for r in detected]
            auto_detected = True

        try:
            client = AzureRestClient()
            data = await client.get_resource(
                path=f"/subscriptions/{{subscriptionId}}/providers/{namespace}",
                api_version="2021-04-01",
            )
        except Exception as e:
            logger.warning("region_availability_api_error", error=str(e), namespace=namespace)
            return f"Region availability check failed: {e}"

        if "error" in data:
            return (
                f"Region availability check for '{namespace}' failed: {data['error']}. "
                "Verify the provider namespace (e.g., 'Microsoft.Databricks', 'Microsoft.App')."
            )

        resource_types = data.get("resourceTypes", [])
        if not resource_types:
            return (
                f"No resource types found for provider '{namespace}'. "
                "Verify the namespace is correct."
            )

        rt_filter = resource_type.strip().lower()
        if rt_filter:
            filtered = [
                rt for rt in resource_types if rt.get("resourceType", "").lower() == rt_filter
            ]
            if filtered:
                resource_types = filtered

        return self._format_availability(namespace, resource_types, target_regions, auto_detected)

    @classmethod
    def _format_availability(
        cls,
        namespace: str,
        resource_types: list[dict],
        target_regions: list[str],
        auto_detected: bool,
    ) -> str:
        """Format the provider resourceTypes into a region availability matrix.

        The verdict leads: a provider with many resource types overflows the
        prompt budget, and a verdict placed last is exactly what gets cut.
        """
        detail: list[str] = []
        supported: dict[str, list[str]] = {r: [] for r in target_regions}
        unsupported: dict[str, list[str]] = {r: [] for r in target_regions}
        global_types = 0

        for rt in resource_types:
            rt_name = rt.get("resourceType", "?")
            locations = rt.get("locations", []) or []
            norm_supported = {cls._normalize_region(loc) for loc in locations}
            is_global = len(norm_supported) == 0

            detail.append(f"### {namespace}/{rt_name}")
            if target_regions:
                if is_global:
                    global_types += 1
                    detail.append("- 🌐 Global service (no regional restriction)")
                else:
                    for region in target_regions:
                        if region in norm_supported:
                            detail.append(f"- ✅ {region}")
                            supported[region].append(rt_name)
                        else:
                            detail.append(f"- ❌ {region}")
                            unsupported[region].append(rt_name)
            else:
                display = ", ".join(sorted(locations)) if locations else "Global (no restriction)"
                detail.append(f"- Supported regions: {display}")
            detail.append("")

        lines = [f"## Region Availability: {namespace}\n"]
        if target_regions:
            src = "auto-detected from admin's resources" if auto_detected else "requested"
            lines.append(f"Target regions ({src}): {', '.join(target_regions)}\n")
            lines.append("### Verdict")
            lines.extend(cls._verdict_lines(supported, unsupported, global_types, target_regions))
            lines.append("")
        else:
            lines.append(
                "No target regions specified or detected — listing all supported regions per type.\n"
            )

        lines.append(f"### Detail ({len(resource_types)} resource types)\n")
        lines.extend(detail)
        return "\n".join(lines)

    @staticmethod
    def _verdict_lines(
        supported: dict[str, list[str]],
        unsupported: dict[str, list[str]],
        global_types: int,
        target_regions: list[str],
    ) -> list[str]:
        """Summarise availability per region.

        Rolled up rather than enumerated: a provider like Microsoft.Network has
        hundreds of resource types, and one line per type pushed the verdict past
        the prompt budget. The ❌ list stays explicit because it is the actionable part.
        """
        out: list[str] = []
        for region in target_regions:
            ok = supported[region]
            no = unsupported[region]
            total = len(ok) + len(no)
            if total == 0:
                out.append(f"- 🌐 {region}: global service — no regional restriction.")
                continue
            out.append(f"- {region}: ✅ {len(ok)}/{total} resource types available")
            if no:
                shown = "; ".join(no[:20])
                extra = f" (+{len(no) - 20} more)" if len(no) > 20 else ""
                out.append(f"  - ❌ NOT available: {shown}{extra}")
            elif ok:
                out.append(f"  - ✅ every resource type available (e.g. {', '.join(ok[:5])})")
        if global_types:
            out.append(
                f"- 🌐 {global_types} resource type(s) are global (no regional restriction)."
            )
        return out


# ---------------------------------------------------------------------------
# Azure Management REST API Tool (general-purpose)
# ---------------------------------------------------------------------------


class AzureRestApiInput(BaseModel):
    """Input for calling Azure Management REST API."""

    path: str = Field(
        description=(
            "API path starting with /subscriptions/{subscriptionId}/... "
            "The {subscriptionId} placeholder is auto-replaced. "
            "Examples: "
            "'/subscriptions/{subscriptionId}/providers/Microsoft.Compute/skus' (VM SKUs), "
            "'/subscriptions/{subscriptionId}/providers/Microsoft.Compute/locations/koreacentral/vmSizes' (VM sizes per region), "
            "'/subscriptions/{subscriptionId}/locations' (available regions)"
        )
    )
    api_version: str = Field(
        default="2021-07-01",
        description="API version (e.g., '2021-07-01' for Compute, '2023-01-01' for Storage)",
    )
    filter_expression: str = Field(
        default="", description="OData $filter expression (e.g., \"location eq 'koreacentral'\")"
    )
    max_results: int = Field(default=200, description="Maximum number of items to return")


class AzureRestApiTool(BaseTool):
    """General-purpose tool for calling any Azure Management REST API.

    Use this to check resource availability, SKUs, capabilities, and region support
    for ANY Azure resource type — not just VMs.
    """

    name: str = "call_azure_rest_api"
    description: str = """Calls any Azure Management REST API to check resource availability, SKUs, or capabilities.

    This is a general-purpose tool for querying Azure ARM APIs that are NOT available through Resource Graph.
    Common use cases:

    1. **VM size availability per region**:
       path: "/subscriptions/{subscriptionId}/providers/Microsoft.Compute/skus"
       api_version: "2021-07-01"
       filter_expression: "location eq 'koreacentral'"
       → Returns all Compute SKUs (VMs, disks) with availability and restrictions

    2. **VM sizes in a specific region**:
       path: "/subscriptions/{subscriptionId}/providers/Microsoft.Compute/locations/koreacentral/vmSizes"
       api_version: "2024-07-01"
       → Returns all VM sizes available in that region

    3. **Available regions for subscription**:
       path: "/subscriptions/{subscriptionId}/locations"
       api_version: "2022-12-01"
       → Returns all Azure regions the subscription can access

    4. **Resource provider capabilities**:
       path: "/subscriptions/{subscriptionId}/providers/Microsoft.Storage"
       api_version: "2021-04-01"
       → Returns Storage resource provider metadata and features

    The {subscriptionId} placeholder is automatically replaced with the active subscription ID.
    Results are returned as JSON with a 'value' array and 'count'.
    """
    args_schema: Type[BaseModel] = AzureRestApiInput

    def _run(
        self,
        path: str,
        api_version: str = "2021-07-01",
        filter_expression: str = "",
        max_results: int = 200,
    ) -> str:
        raise NotImplementedError("Use async version")

    async def _arun(
        self,
        path: str,
        api_version: str = "2021-07-01",
        filter_expression: str = "",
        max_results: int = 200,
    ) -> str:
        """Call Azure REST API asynchronously."""
        from src.services.azure_rest import AzureRestClient

        # Security: only allow management.azure.com paths
        if not path.startswith("/"):
            path = "/" + path
        if "management.azure.com" in path:
            return "Error: provide only the path, not the full URL."

        try:
            client = AzureRestClient()
            params = {}
            if filter_expression:
                params["$filter"] = filter_expression

            result = await client.call_api(
                path=path,
                api_version=api_version,
                params=params if params else None,
                max_results=max_results,
            )

            if "error" in result:
                return f"Azure REST API call failed: {result['error']}"

            values = result.get("value", [])
            count = result.get("count", 0)

            if not values:
                return f"API returned 0 results for: {path}"

            import json

            # Smart summarization for Compute SKU responses
            if "/Microsoft.Compute/skus" in path and count > 20:
                return self._summarize_compute_skus(values, count, result.get("path", path))

            # Generic output for other APIs
            if count > 30:
                summary = json.dumps(values[:30], indent=2, default=str)
                return (
                    f"## Azure REST API Results\n"
                    f"Path: {result.get('path', path)}\n"
                    f"Total: {count} items (showing first 30)\n\n"
                    f"```json\n{summary}\n```"
                )
            else:
                summary = json.dumps(values, indent=2, default=str)
                return (
                    f"## Azure REST API Results\n"
                    f"Path: {result.get('path', path)}\n"
                    f"Total: {count} items\n\n"
                    f"```json\n{summary}\n```"
                )

        except Exception as e:
            logger.error("Azure REST API call failed", error=str(e))
            return f"Azure REST API call failed: {str(e)}"

    @staticmethod
    def _summarize_compute_skus(values: list, total: int, path: str) -> str:
        """Summarize Compute SKU API results into a useful format.

        Groups VM SKUs by family and lists all VM size names with availability status,
        so the LLM can easily find specific VM sizes (e.g., "Standard_D2as_v7").
        """
        # Separate by resource type
        vm_skus = []
        disk_skus = []
        other_skus = []

        for sku in values:
            rt = sku.get("resourceType", "")
            if rt == "virtualMachines":
                vm_skus.append(sku)
            elif rt == "disks":
                disk_skus.append(sku)
            else:
                other_skus.append(sku)

        lines = [
            f"## Compute SKU Availability",
            f"Path: {path}",
            f"Total SKUs: {total} (VMs: {len(vm_skus)}, Disks: {len(disk_skus)}, Other: {len(other_skus)})",
            "",
            "### Virtual Machine SKUs",
        ]

        # Group VMs by family
        families: dict[str, list] = {}
        for sku in vm_skus:
            family = sku.get("family", "Unknown")
            name = sku.get("name", "")
            restrictions = sku.get("restrictions", [])
            is_restricted = any(
                r.get("reasonCode") in ("NotAvailableForSubscription", "QuotaId")
                for r in restrictions
            )

            # Extract key capabilities
            caps = {c["name"]: c["value"] for c in sku.get("capabilities", [])}
            vcpus = caps.get("vCPUs", "?")
            memory = caps.get("MemoryGB", "?")

            families.setdefault(family, []).append(
                {
                    "name": name,
                    "available": not is_restricted,
                    "vcpus": vcpus,
                    "memory": memory,
                    "restriction": restrictions[0].get("reasonCode", "") if is_restricted else "",
                }
            )

        # Sort families alphabetically and output
        for family in sorted(families.keys()):
            skus = families[family]
            avail_count = sum(1 for s in skus if s["available"])
            lines.append(f"\n**{family}** ({avail_count}/{len(skus)} available)")
            for s in sorted(skus, key=lambda x: x["name"]):
                status = "✅" if s["available"] else f"❌ ({s['restriction']})"
                lines.append(f"  - {s['name']}: {status} | {s['vcpus']} vCPUs, {s['memory']} GB")

        # Disk summary (compact)
        if disk_skus:
            lines.append(f"\n### Disk SKUs: {len(disk_skus)} types available")

        return "\n".join(lines)


# =========================================================================
# Phase 1.1: Resource Configuration Profiling Tool
# =========================================================================

# Mapping of service keywords → critical configuration properties to check.
# Used by GetResourceConfigurationsTool to build targeted KQL queries.
SERVICE_CONFIG_PROFILES: dict[str, dict[str, Any]] = {
    "aks": {
        "resource_type": "Microsoft.ContainerService/managedClusters",
        "properties": [
            ("kubernetesVersion", "properties.kubernetesVersion"),
            ("currentKubernetesVersion", "properties.currentKubernetesVersion"),
            ("autoUpgradeChannel", "properties.autoUpgradeChannel"),
            ("networkPlugin", "properties.networkProfile.networkPlugin"),
            ("networkPolicy", "properties.networkProfile.networkPolicy"),
            ("networkDataplane", "properties.networkProfile.networkDataplane"),
            ("osSku", "properties.agentPoolProfiles[0].osSKU"),
            ("nodeImageVersion", "properties.agentPoolProfiles[0].nodeImageVersion"),
            ("enableRBAC", "properties.enableRBAC"),
            ("aadEnabled", "properties.aadProfile.managed"),
            ("privateFqdn", "properties.privateFQDN"),
            ("skuTier", "sku.tier"),
        ],
    },
    "storage": {
        "resource_type": "Microsoft.Storage/storageAccounts",
        "properties": [
            ("minimumTlsVersion", "properties.minimumTlsVersion"),
            ("allowBlobPublicAccess", "properties.allowBlobPublicAccess"),
            ("allowSharedKeyAccess", "properties.allowSharedKeyAccess"),
            ("isHnsEnabled", "properties.isHnsEnabled"),
            ("networkDefaultAction", "properties.networkAcls.defaultAction"),
            ("keySource", "properties.encryption.keySource"),
            ("infrastructureEncryption", "properties.encryption.requireInfrastructureEncryption"),
            ("skuName", "sku.name"),
        ],
    },
    "virtual machines": {
        "resource_type": "Microsoft.Compute/virtualMachines",
        "properties": [
            ("vmSize", "properties.hardwareProfile.vmSize"),
            ("osType", "properties.storageProfile.osDisk.osType"),
            ("osSku", "properties.storageProfile.imageReference.sku"),
            ("osVersion", "properties.storageProfile.imageReference.version"),
            ("securityType", "properties.securityProfile.securityType"),
            ("encryptionAtHost", "properties.securityProfile.encryptionAtHost"),
            ("licenseType", "properties.licenseType"),
            ("provisioningState", "properties.provisioningState"),
        ],
    },
    "app service": {
        "resource_type": "Microsoft.Web/sites",
        "properties": [
            ("kind", "kind"),
            ("httpsOnly", "properties.httpsOnly"),
            ("minTlsVersion", "properties.siteConfig.minTlsVersion"),
            ("http20Enabled", "properties.siteConfig.http20Enabled"),
            ("ftpsState", "properties.siteConfig.ftpsState"),
            ("vnetRouteAllEnabled", "properties.siteConfig.vnetRouteAllEnabled"),
            ("alwaysOn", "properties.siteConfig.alwaysOn"),
            ("linuxFxVersion", "properties.siteConfig.linuxFxVersion"),
            ("netFrameworkVersion", "properties.siteConfig.netFrameworkVersion"),
        ],
    },
    "sql database": {
        "resource_type": "Microsoft.Sql/servers",
        "properties": [
            ("minimalTlsVersion", "properties.minimalTlsVersion"),
            ("publicNetworkAccess", "properties.publicNetworkAccess"),
            ("version", "properties.version"),
            ("administratorType", "properties.administrators.administratorType"),
        ],
    },
    "cosmos db": {
        "resource_type": "Microsoft.DocumentDB/databaseAccounts",
        "properties": [
            ("databaseAccountOfferType", "properties.databaseAccountOfferType"),
            ("consistencyLevel", "properties.consistencyPolicy.defaultConsistencyLevel"),
            ("enableAutomaticFailover", "properties.enableAutomaticFailover"),
            ("enableMultipleWriteLocations", "properties.enableMultipleWriteLocations"),
            ("isVirtualNetworkFilterEnabled", "properties.isVirtualNetworkFilterEnabled"),
            ("publicNetworkAccess", "properties.publicNetworkAccess"),
            ("minimalTlsVersion", "properties.minimalTlsVersion"),
        ],
    },
    "key vault": {
        "resource_type": "Microsoft.KeyVault/vaults",
        "properties": [
            ("enableSoftDelete", "properties.enableSoftDelete"),
            ("enablePurgeProtection", "properties.enablePurgeProtection"),
            ("enableRbacAuthorization", "properties.enableRbacAuthorization"),
            ("networkDefaultAction", "properties.networkAcls.defaultAction"),
            ("skuName", "sku.name"),
        ],
    },
    "container apps": {
        "resource_type": "Microsoft.App/containerApps",
        "properties": [
            ("managedEnvironmentId", "properties.managedEnvironmentId"),
            ("revisionMode", "properties.configuration.activeRevisionsMode"),
            ("ingressTransport", "properties.configuration.ingress.transport"),
            ("minReplicas", "properties.template.scale.minReplicas"),
            ("maxReplicas", "properties.template.scale.maxReplicas"),
        ],
    },
    "functions": {
        "resource_type": "Microsoft.Web/sites",
        "kind_filter": "functionapp",
        "properties": [
            ("httpsOnly", "properties.httpsOnly"),
            ("minTlsVersion", "properties.siteConfig.minTlsVersion"),
            ("ftpsState", "properties.siteConfig.ftpsState"),
            ("linuxFxVersion", "properties.siteConfig.linuxFxVersion"),
            ("netFrameworkVersion", "properties.siteConfig.netFrameworkVersion"),
            ("pythonVersion", "properties.siteConfig.pythonVersion"),
            ("nodeVersion", "properties.siteConfig.nodeVersion"),
            ("javaVersion", "properties.siteConfig.javaVersion"),
            ("powerShellVersion", "properties.siteConfig.powerShellVersion"),
            ("vnetRouteAllEnabled", "properties.siteConfig.vnetRouteAllEnabled"),
        ],
    },
}


class GetResourceConfigurationsInput(BaseModel):
    """Input for resource configuration profiling tool."""

    service_name: str = Field(
        description="Azure service name (e.g., 'AKS', 'Storage', 'Virtual Machines', 'App Service')"
    )
    focus_properties: list[str] = Field(
        default=[],
        description="Specific property names to check (optional — if empty, uses service default profile)",
    )


class GetResourceConfigurationsTool(BaseTool):
    """Tool to profile actual resource configurations for precise impact analysis."""

    name: str = "get_resource_configurations"
    description: str = """Profiles the actual configuration values of resources for a specific Azure service.

    Unlike get_service_resource_details (which shows general resource info), this tool focuses on
    **configuration properties relevant to update impact analysis**: versions, security settings,
    feature flags, SKU tiers, and network configuration.

    Use this tool when you need to determine:
    - Which resources are affected by a version change (e.g., K8s version, TLS version)
    - Which resources use a deprecated feature or setting
    - The distribution of configurations (e.g., "3/5 clusters on K8s 1.28, 2/5 on 1.30")

    Returns a configuration profile matrix showing each resource's critical settings.

    Supported services: AKS, Storage, Virtual Machines, App Service, SQL Database,
    Cosmos DB, Key Vault, Container Apps, Functions.
    """
    args_schema: type[BaseModel] = GetResourceConfigurationsInput

    _service: Optional[ResourceGraphService] = None

    def __init__(self, service: Optional[ResourceGraphService] = None, **kwargs):
        super().__init__(**kwargs)
        self._service = service or ResourceGraphService()

    def _run(self, service_name: str, focus_properties: list[str] | None = None) -> str:
        raise NotImplementedError("Use async version")

    async def _arun(self, service_name: str, focus_properties: list[str] | None = None) -> str:
        """Profile resource configurations for impact analysis."""
        focus_properties = focus_properties or []

        # Resolve service profile
        svc_lower = service_name.lower().strip()
        profile = None
        for key, prof in SERVICE_CONFIG_PROFILES.items():
            if key in svc_lower or svc_lower in key:
                profile = prof
                break

        if not profile:
            return (
                f"No configuration profile for '{service_name}'. "
                f"Supported: {', '.join(SERVICE_CONFIG_PROFILES.keys())}. "
                f"Use explore_resource_schema or query_azure_resources for custom queries."
            )

        resource_type = profile["resource_type"]
        props = profile["properties"]

        # Build KQL query
        extend_clauses = []
        project_fields = ["name", "resourceGroup", "subscriptionId", "location"]
        for alias, path in props:
            if path.startswith("sku.") or path == "kind":
                extend_clauses.append(f"extend {alias} = tostring({path})")
            elif "[0]" in path:
                # Array access — use dynamic
                extend_clauses.append(f"extend {alias} = tostring({path})")
            else:
                extend_clauses.append(f"extend {alias} = tostring({path})")
            project_fields.append(alias)

        where_clause = f"type =~ '{resource_type}'"
        if profile.get("kind_filter"):
            where_clause += f" and kind contains '{profile['kind_filter']}'"

        query = (
            f"Resources\n"
            f"| where {where_clause}\n"
            + "\n".join(f"| {e}" for e in extend_clauses)
            + f"\n| project {', '.join(project_fields)}"
            + "\n| order by name asc"
        )

        try:
            result = await execute_kql_with_retry(self._service, query, max_retries=3)
        except RuntimeError as e:
            return f"Configuration profiling error: {e}"

        data = result.get("data", [])
        count = result.get("count", 0)

        if not data:
            return (
                f"No {service_name} resources found. "
                f"No resources of type '{resource_type}' exist in the tenant."
            )

        # Build configuration matrix
        lines = [f"## {service_name} Configuration Profile ({count} resources)\n"]

        # Build distribution summary for key properties
        distributions: dict[str, dict[str, int]] = {}
        for alias, _ in props:
            dist: dict[str, int] = {}
            for resource in data:
                val = str(resource.get(alias, "null"))
                dist[val] = dist.get(val, 0) + 1
            if len(dist) > 1 or (len(dist) == 1 and "null" not in dist):
                distributions[alias] = dist

        if distributions:
            lines.append("### Configuration Distribution Summary")
            for prop_name, dist in distributions.items():
                sorted_dist = sorted(dist.items(), key=lambda x: -x[1])
                dist_parts = [f"{val}: {cnt}" for val, cnt in sorted_dist]
                lines.append(f"- **{prop_name}**: {', '.join(dist_parts)}")
            lines.append("")

        # Per-resource detail (limit to 25)
        lines.append("### Per-Resource Configuration")
        for i, resource in enumerate(data[:25], 1):
            lines.append(
                f"\n**{i}. {resource.get('name', 'Unknown')}** "
                f"({resource.get('resourceGroup', '')} / "
                f"{resource.get('location', '')})"
            )
            for alias, _ in props:
                val = resource.get(alias)
                if val is not None and str(val) != "null":
                    lines.append(f"  - {alias}: {val}")

        if count > 25:
            lines.append(f"\n... and {count - 25} more resources")

        return truncate_tool_result("\n".join(lines))


# =========================================================================
# Phase 1.2: Resource Dependency Mapping Tool
# =========================================================================

# Dependency query templates: resource_type → KQL snippet to find dependencies
_DEPENDENCY_QUERIES: dict[str, list[dict[str, str]]] = {
    "microsoft.web/sites": [
        {
            "label": "VNet Integration",
            "query": (
                "Resources | where type =~ 'microsoft.web/sites' "
                "| extend vnetSubnetId = tostring(properties.virtualNetworkSubnetId) "
                "| where isnotempty(vnetSubnetId) "
                "| project name, type, resourceGroup, subscriptionId, vnetSubnetId"
            ),
        },
        {
            "label": "Private Endpoints",
            "query": (
                "Resources | where type =~ 'microsoft.network/privateendpoints' "
                "| mv-expand conn = properties.privateLinkServiceConnections "
                "| extend targetId = tostring(conn.properties.privateLinkServiceId) "
                "| where targetId contains 'microsoft.web/sites' "
                "| project name, resourceGroup, subscriptionId, targetId"
            ),
        },
    ],
    "microsoft.storage/storageaccounts": [
        {
            "label": "Private Endpoints",
            "query": (
                "Resources | where type =~ 'microsoft.network/privateendpoints' "
                "| mv-expand conn = properties.privateLinkServiceConnections "
                "| extend targetId = tostring(conn.properties.privateLinkServiceId) "
                "| where targetId contains 'microsoft.storage' "
                "| project name, resourceGroup, subscriptionId, targetId"
            ),
        },
        {
            "label": "Linked Services (e.g., Diagnostics)",
            "query": (
                "Resources | where type =~ 'microsoft.insights/diagnosticsettings' "
                "| extend storageAccountId = tostring(properties.storageAccountId) "
                "| where isnotempty(storageAccountId) "
                "| project name, resourceGroup, subscriptionId, storageAccountId"
            ),
        },
    ],
    "microsoft.containerservice/managedclusters": [
        {
            "label": "VNet (Subnet)",
            "query": (
                "Resources | where type =~ 'microsoft.containerservice/managedclusters' "
                "| extend vnetSubnetId = tostring(properties.agentPoolProfiles[0].vnetSubnetID) "
                "| where isnotempty(vnetSubnetId) "
                "| project name, type, resourceGroup, subscriptionId, vnetSubnetId"
            ),
        },
        {
            "label": "Attached ACR",
            "query": (
                "Resources | where type =~ 'microsoft.containerregistry/registries' "
                "| project name, type, resourceGroup, subscriptionId, loginServer = tostring(properties.loginServer)"
            ),
        },
    ],
    "microsoft.keyvault/vaults": [
        {
            "label": "Private Endpoints",
            "query": (
                "Resources | where type =~ 'microsoft.network/privateendpoints' "
                "| mv-expand conn = properties.privateLinkServiceConnections "
                "| extend targetId = tostring(conn.properties.privateLinkServiceId) "
                "| where targetId contains 'microsoft.keyvault' "
                "| project name, resourceGroup, subscriptionId, targetId"
            ),
        },
    ],
    "microsoft.sql/servers": [
        {
            "label": "Private Endpoints",
            "query": (
                "Resources | where type =~ 'microsoft.network/privateendpoints' "
                "| mv-expand conn = properties.privateLinkServiceConnections "
                "| extend targetId = tostring(conn.properties.privateLinkServiceId) "
                "| where targetId contains 'microsoft.sql' "
                "| project name, resourceGroup, subscriptionId, targetId"
            ),
        },
        {
            "label": "SQL Databases",
            "query": (
                "Resources | where type =~ 'microsoft.sql/servers/databases' "
                "| extend skuName = tostring(sku.name) "
                "| extend skuTier = tostring(sku.tier) "
                "| extend maxSizeBytes = tostring(properties.maxSizeBytes) "
                "| project name, resourceGroup, subscriptionId, skuName, skuTier, maxSizeBytes"
            ),
        },
    ],
}


class GetResourceDependenciesInput(BaseModel):
    """Input for resource dependency mapping tool."""

    resource_type: str = Field(
        description="Azure resource type (e.g., 'Microsoft.Web/sites', 'Microsoft.Storage/storageAccounts')"
    )


class GetResourceDependenciesTool(BaseTool):
    """Tool to map dependencies between resources for blast radius analysis."""

    name: str = "get_resource_dependencies"
    description: str = """Maps dependencies between Azure resources to determine the blast radius of an update.

    Given a resource type, discovers:
    - VNet integrations and subnet connections
    - Private Endpoint connections
    - Cross-service references (e.g., App Service → Storage, AKS → ACR)
    - Diagnostic settings links

    Use this tool when analyzing updates that affect core infrastructure services
    (Storage, VNet, Key Vault, SQL) to understand the cascading impact.

    Returns a dependency map showing which resources depend on the target service.
    """
    args_schema: type[BaseModel] = GetResourceDependenciesInput

    _service: Optional[ResourceGraphService] = None

    def __init__(self, service: Optional[ResourceGraphService] = None, **kwargs):
        super().__init__(**kwargs)
        self._service = service or ResourceGraphService()

    def _run(self, resource_type: str) -> str:
        raise NotImplementedError("Use async version")

    async def _arun(self, resource_type: str) -> str:
        """Map resource dependencies for blast radius analysis."""
        rt_lower = resource_type.lower().strip()

        # Find matching dependency queries
        dep_queries = None
        for key, queries in _DEPENDENCY_QUERIES.items():
            if key in rt_lower or rt_lower in key:
                dep_queries = queries
                break

        lines = [f"## Resource Dependency Map for {resource_type}\n"]

        if not dep_queries:
            # Generic fallback: search for Private Endpoints targeting this type
            generic_query = (
                "Resources | where type =~ 'microsoft.network/privateendpoints' "
                "| mv-expand conn = properties.privateLinkServiceConnections "
                f"| extend targetId = tostring(conn.properties.privateLinkServiceId) "
                f"| where targetId contains '{rt_lower.split('/')[-1]}' "
                "| project name, resourceGroup, subscriptionId, targetId"
            )
            try:
                result = await execute_kql_with_retry(self._service, generic_query, max_retries=2)
                data = result.get("data", [])
                if data:
                    lines.append(f"### Private Endpoints ({len(data)} found)")
                    for ep in data[:15]:
                        lines.append(f"- {ep.get('name', '?')} → {ep.get('targetId', '?')}")
                else:
                    lines.append("No Private Endpoint dependencies found.")
            except RuntimeError:
                lines.append("Could not query Private Endpoints.")
            return truncate_tool_result("\n".join(lines))

        # Execute each dependency query
        total_deps = 0
        for dep in dep_queries:
            try:
                result = await execute_kql_with_retry(self._service, dep["query"], max_retries=2)
                data = result.get("data", [])
                count = result.get("count", 0)
                total_deps += count

                lines.append(f"### {dep['label']} ({count} found)")
                if data:
                    for item in data[:15]:
                        item_parts = [f"**{item.get('name', '?')}**"]
                        for k, v in item.items():
                            if k not in ("name",) and v is not None:
                                item_parts.append(f"{k}: {v}")
                        lines.append(f"- {', '.join(item_parts)}")
                    if count > 15:
                        lines.append(f"  ... and {count - 15} more")
                else:
                    lines.append("  (none found)")
                lines.append("")
            except RuntimeError as e:
                lines.append(f"### {dep['label']}")
                lines.append(f"  Query error: {e}\n")

        lines.insert(1, f"**Total dependencies found: {total_deps}**\n")

        return truncate_tool_result("\n".join(lines))


# ---------------------------------------------------------------------------
# Stored tool result sub-query (context recursion)
# ---------------------------------------------------------------------------

# Module-level, not class attributes: BaseTool is a Pydantic model and a
# leading-underscore class attribute resolves to a ModelPrivateAttr, not its value.
QTR_MAX_PATTERN_CHARS = 200
QTR_MAX_LINE_CHARS = 400
QTR_MATCH_WINDOW = 200


def _clip_line(text: str) -> str:
    if len(text) <= QTR_MAX_LINE_CHARS:
        return text
    return text[:QTR_MAX_LINE_CHARS] + " …"


def _match_offsets(line: str, needle: str, rx) -> list[int]:
    """Offsets of every match within a single line."""
    if rx is not None:
        return [m.start() for m in rx.finditer(line)]
    low = line.lower()
    out, pos = [], low.find(needle)
    while pos >= 0:
        out.append(pos)
        pos = low.find(needle, pos + max(1, len(needle)))
    return out


class QueryToolResultInput(BaseModel):
    """Input for searching a previously truncated tool result."""

    ref: str = Field(
        pattern=r"^R[1-9][0-9]*$",
        description="Ref shown as [ref=R7] at the end of a truncated tool result preview.",
    )
    pattern: str = Field(
        default="",
        description=(
            "Text to search for on each line, case-insensitive "
            "(e.g. a resource name, a region, 'TLS1_0'). "
            "Leave empty with mode='stats' to sample the result instead."
        ),
    )
    mode: str = Field(
        default="search",
        description="'search' (default), 'head', 'tail', or 'stats' (evenly spaced sample).",
    )
    regex: bool = Field(
        default=False,
        description="Treat pattern as a regular expression instead of literal text.",
    )
    max_matches: int = Field(default=60, description="Maximum lines to return (capped at 200).")


class QueryToolResultTool(BaseTool):
    """Search the full text of a tool result that was too large to inline."""

    name: str = "query_tool_result"
    description: str = """Searches the FULL text of an earlier tool result that was truncated.

    When a tool result is too large to show completely, you see a preview ending with
    `[ref=R7] ... TRUNCATED PREVIEW`. The rest is NOT lost — it is retained and searchable
    through this tool.

    USE THIS whenever you are about to say a resource is absent, a property is unverified,
    or a check needs manual review, AND the relevant tool result was truncated. A preview
    proves nothing about the rows it did not show.

    Examples:
    - query_tool_result(ref="R7", pattern="koreacentral") -> every line mentioning that region
    - query_tool_result(ref="R7", pattern="mystorageacct") -> confirm one resource exists
    - query_tool_result(ref="R7", mode="stats") -> sample lines spread across the whole result
    - query_tool_result(ref="R7", mode="tail") -> the end of the result

    A 'no match' answer here IS a confirmed absence, because the entire result was searched.
    """
    args_schema: Type[BaseModel] = QueryToolResultInput

    def _run(self, *args, **kwargs) -> str:
        raise NotImplementedError("Use async version")

    async def _arun(
        self,
        ref: str,
        pattern: str = "",
        mode: str = "search",
        regex: bool = False,
        max_matches: int = 60,
    ) -> str:
        """Search or sample a stored tool result."""
        from src.agent.context_store import get_result_store

        stored = get_result_store().get(ref)
        if stored is None:
            return (
                f"No stored result for ref '{ref}'. Refs appear as [ref=R7] at the end of a "
                "truncated tool result and are dropped once memory is reclaimed. "
                "Re-run the original tool with a narrower query instead."
            )

        content = stored.content
        lines = content.splitlines()
        # Serialized dicts arrive as one enormous line; address those by character offset.
        is_blob = len(lines) <= 2 and stored.char_count > QTR_MAX_LINE_CHARS
        scope = (
            f"first {stored.char_count:,} of {stored.original_chars:,} chars — entry was capped"
            if stored.is_partial
            else f"{stored.char_count:,} chars (searched in full)"
        )
        header = f"Result {stored.ref} from {stored.tool} — {scope}, {len(lines):,} lines."
        mode = (mode or "search").strip().lower()
        limit = max(1, min(int(max_matches or 60), 200))

        if mode in ("head", "tail"):
            if is_blob:
                span = min(len(content), limit * 100)
                chunk = content[:span] if mode == "head" else content[-span:]
                return truncate_tool_result(f"{header}\n[{mode} {len(chunk):,} chars]\n{chunk}")
            picked = lines[:limit] if mode == "head" else lines[-limit:]
            offset = 1 if mode == "head" else max(1, len(lines) - len(picked) + 1)
            body = "\n".join(f"{offset + i}: {_clip_line(ln)}" for i, ln in enumerate(picked))
            return truncate_tool_result(f"{header}\n[{mode} {len(picked)} lines]\n{body}")

        if mode == "stats" or not pattern:
            if is_blob:
                step = max(1, len(content) // 12)
                windows = [(i, content[i : i + 160]) for i in range(0, len(content), step)][:12]
                body = "\n".join(f"@{off}: {txt}" for off, txt in windows)
                return truncate_tool_result(
                    f"{header}\n[evenly spaced sample of {len(windows)} character windows — "
                    f'pass pattern="..." to search]\n{body}'
                )
            step = max(1, len(lines) // 12)
            sampled = [(i + 1, lines[i]) for i in range(0, len(lines), step)][:12]
            body = "\n".join(f"{no}: {_clip_line(ln)}" for no, ln in sampled)
            return truncate_tool_result(
                f"{header}\n[evenly spaced sample of {len(sampled)} lines — "
                f'pass pattern="..." to search]\n{body}'
            )

        rx = None
        if regex:
            if len(pattern) > QTR_MAX_PATTERN_CHARS:
                return f"{header}\nPattern exceeds {QTR_MAX_PATTERN_CHARS} chars; shorten it."
            try:
                rx = re.compile(pattern, re.IGNORECASE)
            except re.error as e:
                return f"{header}\nInvalid regex {pattern!r}: {e}. Retry with regex=false."
        needle = pattern.lower()

        hits: list[str] = []
        for i, line in enumerate(lines):
            if len(line) <= QTR_MAX_LINE_CHARS:
                matched = rx.search(line) is not None if rx else needle in line.lower()
                if matched:
                    hits.append(f"{i + 1}: {line}")
            else:
                for off in _match_offsets(line, needle, rx):
                    start = max(0, off - QTR_MATCH_WINDOW // 2)
                    window = line[start : start + QTR_MATCH_WINDOW]
                    hits.append(f"{i + 1}@{off}: …{window}…")
                    if len(hits) >= limit:
                        break
            if len(hits) >= limit:
                break

        if not hits:
            if stored.is_partial:
                return (
                    f"{header}\nNo match for {pattern!r} in the stored portion. This entry was "
                    "capped, so absence is NOT confirmed — re-run the original tool with a "
                    "narrower query to check the remainder."
                )
            return (
                f"{header}\nNo line matches {pattern!r}. The entire result was searched, "
                "so this is a confirmed absence — not a truncation artifact."
            )

        more = ""
        if len(hits) >= limit:
            more = f"\n... (stopped at {limit} matches; narrow the pattern)"
        return truncate_tool_result(
            f"{header}\n[{len(hits)} matching lines for {pattern!r}]\n" + "\n".join(hits) + more
        )


def get_all_tools() -> list[BaseTool]:
    """Get all available tools for the agent.

    Shares service instances across tools to avoid duplicate connections,
    credential discovery, and subscription enumeration.
    """
    # Shared service instances — one per service type
    rg_service = ResourceGraphService()
    learn_service = MicrosoftLearnService()
    cost_service = CostManagementService()
    billing_service = BillingService()
    log_service = LogAnalyticsService()

    return [
        # Basic Resource Graph tools
        ResourceGraphQueryTool(service=rg_service),
        GetResourceTypeSummaryTool(service=rg_service),
        FindRelatedResourcesTool(service=rg_service),
        # Advanced Resource Graph tools
        GetServiceResourceDetailsTool(service=rg_service),
        GetSecurityPostureTool(service=rg_service),
        SearchResourceGraphDocsTool(service=learn_service),
        ExploreResourceSchemaTool(service=rg_service),
        # Resource Configuration & Dependency tools (Deep Environment Intelligence)
        GetResourceConfigurationsTool(service=rg_service),
        GetResourceDependenciesTool(service=rg_service),
        # Azure Advisor & Service Health (via Resource Graph)
        GetAdvisorRecommendationsTool(),
        GetServiceHealthTool(service=rg_service),
        # Impact analysis tools (via REST API)
        GetResourceHealthTool(),
        GetPolicyComplianceTool(),
        GetServiceHealthEventsTool(),
        GetServiceRegionAvailabilityTool(service=rg_service),
        # Cost Management tools
        GetCostByResourceTypeTool(service=cost_service),
        GetCostByServiceTool(service=cost_service),
        # Azure Billing tools
        ListBillingAccountsTool(service=billing_service),
        ListBillingProfilesTool(service=billing_service),
        # Log Analytics tools
        QueryLogAnalyticsTool(service=log_service),
        GetRecentErrorsTool(service=log_service),
        GetActivityLogSummaryTool(service=log_service),
        # Microsoft Learn documentation tools
        SearchAzureDocsTool(service=learn_service),
        GetServiceDocumentationTool(service=learn_service),
        SearchUpdateRelatedDocsTool(service=learn_service),
        # Azure Management REST API (general-purpose)
        AzureRestApiTool(),
        # Context recursion: reach past a truncated result's preview
        QueryToolResultTool(),
    ]
