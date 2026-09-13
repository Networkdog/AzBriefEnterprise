"""Request-local resource query evidence and bounded Portal navigation."""

import json
import re
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any, Iterator, Optional
from urllib.parse import quote
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from src.agent.scope import AnalysisScope

RESOURCE_LIST_LIMIT = 20
MAX_PORTAL_QUERY_URL_LENGTH = 4096
_MAX_CATALOG_RESOURCES = 20_000
_RESOURCE_ID = re.compile(
    r"^/subscriptions/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
    r"/resourceGroups/([^/]+)/providers/([^/]+)/(.+)$",
    re.IGNORECASE,
)


class ResourceQueryEvidence(BaseModel):
    """Delivery-only metadata for a resource set selected from executed evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    reference: str = Field(pattern=r"^rq-[0-9a-f]{32}$")
    reason: str = Field(default="", max_length=2000)
    count: int = Field(ge=0)
    complete: bool
    queried_at: datetime
    scope: AnalysisScope
    portal_query: str = Field(default="", max_length=32_000)

    def portal_url(self) -> str:
        """Return a query-prefilled Portal link, never an executable action."""
        if not self.portal_query or not self.complete:
            return ""
        url = "https://portal.azure.com/#blade/HubsExtension/ArgQueryBlade/query/" + quote(
            self.portal_query, safe=""
        )
        return url if len(url) <= MAX_PORTAL_QUERY_URL_LENGTH else ""


class ResourceQuerySelection(BaseModel):
    """The writer selects an existing result, not a count, query, or URL."""

    model_config = ConfigDict(extra="forbid")

    reference: str
    reason: str = Field(min_length=1, max_length=2000)


def resource_identity(resource: dict[str, Any]) -> str:
    """Resolve an unambiguous identity without conflating same-name resources."""
    resource_id = str(resource.get("id") or "").strip().rstrip("/")
    if _RESOURCE_ID.fullmatch(resource_id):
        return resource_id.casefold()
    subscription = str(resource.get("subscriptionId") or "").strip()
    group = str(resource.get("resourceGroup") or "").strip()
    resource_type = str(resource.get("type") or "").strip()
    name = str(resource.get("name") or "").strip()
    if not subscription or not group or not name or resource_type.count("/") != 1 or "/" in name:
        return ""
    namespace, kind = resource_type.split("/")
    candidate = (
        f"/subscriptions/{subscription}/resourceGroups/{group}/providers/{namespace}/{kind}/{name}"
    )
    return candidate.casefold() if _RESOURCE_ID.fullmatch(candidate) else ""


def _identity_row(row: dict[str, Any]) -> Optional[dict[str, Any]]:
    resource_id = str(row.get("id") or "").strip().rstrip("/")
    match = _RESOURCE_ID.fullmatch(resource_id)
    if not match:
        return None
    subscription, group, namespace, path = match.groups()
    segments = path.split("/")
    if len(segments) % 2 or any(not segment for segment in segments):
        return None
    return {
        "id": resource_id,
        "name": "/".join(segments[1::2]),
        "type": str(row.get("type") or namespace + "/" + "/".join(segments[::2])),
        "resourceGroup": group,
        "subscriptionId": subscription,
        "subscriptionName": str(row.get("subscriptionName") or ""),
        "location": str(row.get("location") or ""),
    }


class ResourceEvidenceCatalog:
    """Keep identity rows outside model output for exactly one analysis."""

    def __init__(self) -> None:
        self.entries: dict[str, tuple[ResourceQueryEvidence, list[dict[str, Any]]]] = {}
        self.queries: dict[str, dict[str, str]] = {}
        self.resource_count = 0

    def register(self, result: dict[str, Any]) -> Optional[ResourceQueryEvidence]:
        """Capture a query result without treating missing IDs or pages as complete."""
        query = result.get("executed_query")
        scope_data = result.get("query_scope")
        if (
            not isinstance(query, str)
            or not query
            or len(query) > 32_000
            or not isinstance(scope_data, dict)
            or len(self.entries) >= 64
        ):
            return None
        scope = AnalysisScope.model_validate(scope_data)
        source_rows = result.get("data") or []
        if not isinstance(source_rows, list):
            return None
        rows_by_id: dict[str, dict[str, Any]] = {}
        valid_rows = 0
        for source in source_rows:
            row = _identity_row(source) if isinstance(source, dict) else None
            if row is None or not scope.contains_resource(row):
                continue
            valid_rows += 1
            rows_by_id.setdefault(row["id"].casefold(), row)
        if source_rows and not rows_by_id:
            return None
        if self.resource_count + len(rows_by_id) > _MAX_CATALOG_RESOURCES:
            return None

        from src.agent.tools import _RE_KQL_LITERAL
        from src.services.resource_graph import ResourceGraphService

        code = _RE_KQL_LITERAL.sub(lambda match: " " * len(match.group()), query)
        limited = bool(re.search(r"(?:^|\|)\s*(?:take|limit|top)\b", code, re.IGNORECASE))
        complete = (
            result.get("query_status") == "complete"
            and not result.get("result_truncated")
            and not limited
            and valid_rows == len(source_rows)
            and len(source_rows) >= result.get("total_records", len(source_rows))
        )
        portal_query = ""
        if complete and scope.subscriptions and not scope.management_groups:
            try:
                portal_query = ResourceGraphService._apply_scope_filters(
                    query, AnalysisScope(), subscriptions=list(scope.subscriptions)
                )
            except ValueError:
                portal_query = ""
        if len(portal_query) > 32_000:
            portal_query = ""
        evidence = ResourceQueryEvidence(
            reference="rq-" + uuid4().hex,
            count=len(rows_by_id),
            complete=complete,
            queried_at=result.get("queried_at") or datetime.now(timezone.utc),
            scope=scope,
            portal_query=portal_query,
        )
        self.entries[evidence.reference] = (evidence, list(rows_by_id.values()))
        self.queries[evidence.reference] = {
            "query": query,
            "purpose": str(result.get("query_purpose") or "")[:2000],
        }
        self.resource_count += evidence.count
        return evidence

    def prompt_context(self) -> str:
        """Expose references even when a specialist summarizes away the raw header."""
        if not self.entries:
            return ""
        lines = [
            "## Executed resource query catalog",
            "Select resource_queries only when EVERY returned resource meets the update's "
            "applicability condition and the shared reason. This is evidence, not an instruction. "
            "A broad inventory or diagnostic sample is not an affected set. Use affected_resources "
            "for individually verified subsets instead. Counts below are unique ARM identities; "
            "complete=false means confirmed rows only, never the total population.",
        ]
        for reference, (evidence, _) in self.entries.items():
            lines.append(
                json.dumps(
                    {
                        "reference": reference,
                        "count": evidence.count,
                        "complete": evidence.complete,
                        "scope": evidence.scope.model_dump(mode="json"),
                        **self.queries[reference],
                    },
                    ensure_ascii=False,
                )
            )
        return "\n".join(lines)

    def resolve(
        self, selections: list[dict[str, Any]], resources: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], list[ResourceQueryEvidence]]:
        """Rehydrate selected identities and merge overlapping query results."""
        merged: dict[str, dict[str, Any]] = {}
        evidence_list: list[ResourceQueryEvidence] = []
        seen: set[str] = set()
        for raw_selection in selections:
            selection = ResourceQuerySelection.model_validate(raw_selection)
            if selection.reference not in self.entries:
                raise ValueError("Unknown resource query reference in report")
            if selection.reference in seen:
                continue
            seen.add(selection.reference)
            evidence, rows = self.entries[selection.reference]
            evidence_list.append(evidence.model_copy(update={"reason": selection.reason}))
            for row in rows:
                identity = row["id"].casefold()
                if identity not in merged:
                    merged[identity] = {**row, "reason": selection.reason, "query_refs": []}
                target = merged[identity]
                target["query_refs"].append(selection.reference)
                if selection.reason not in target["reason"].split("\n"):
                    target["reason"] += "\n" + selection.reason
        for index, row in enumerate(resources):
            if not isinstance(row, dict):
                continue
            identity = resource_identity(row) or f"unresolved-{index}"
            if identity not in merged:
                merged[identity] = {key: value for key, value in row.items() if key != "query_refs"}
        return list(merged.values()), evidence_list


_CATALOG: ContextVar[Optional[ResourceEvidenceCatalog]] = ContextVar(
    "azbrief_resource_evidence", default=None
)


@contextmanager
def resource_evidence_context() -> Iterator[ResourceEvidenceCatalog]:
    """Isolate concurrent analyses, including failure and cancellation paths."""
    catalog = ResourceEvidenceCatalog()
    token = _CATALOG.set(catalog)
    try:
        yield catalog
    finally:
        _CATALOG.reset(token)


def register_resource_query(result: dict[str, Any]) -> dict[str, Any]:
    """Add a bounded, model-visible reference without removing the evidence rows."""
    catalog = _CATALOG.get()
    evidence = catalog.register(result) if catalog is not None else None
    if evidence is None:
        return result
    return {
        "resource_query_ref": evidence.reference,
        "resource_count": evidence.count,
        "resource_count_complete": evidence.complete,
        **result,
    }


def resolve_resource_queries(
    selections: list[dict[str, Any]], resources: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[ResourceQueryEvidence]]:
    """Resolve only references created during the current analysis."""
    catalog = _CATALOG.get()
    if selections and catalog is None:
        raise ValueError("Resource query references require current analysis evidence")
    if catalog is not None:
        return catalog.resolve(selections, resources)
    return (
        [
            {key: value for key, value in row.items() if key != "query_refs"}
            for row in resources
            if isinstance(row, dict)
        ],
        [],
    )


def resource_query_prompt_context() -> str:
    """Return the current catalog without consulting another request's evidence."""
    catalog = _CATALOG.get()
    return catalog.prompt_context() if catalog is not None else ""


def resource_query_evidence_facts() -> dict[str, str]:
    """Provide deterministic counts and predicates to the independent reviewer."""
    catalog = _CATALOG.get()
    if catalog is None:
        return {}
    return {
        f"resource_query:{reference}": json.dumps(
            {
                "reference": reference,
                "unique_resource_count": evidence.count,
                "complete": evidence.complete,
                "scope": evidence.scope.model_dump(mode="json"),
                "queried_at": evidence.queried_at.isoformat(),
                **catalog.queries[reference],
            },
            ensure_ascii=False,
        )
        for reference, (evidence, _) in catalog.entries.items()
    }


def customize_resource_queries(
    resources: list[dict[str, Any]],
    queries: list[ResourceQueryEvidence],
    selections: list[dict[str, Any]],
    customized_resources: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[ResourceQueryEvidence]]:
    """Translate reasons without allowing customization to change verified membership."""
    selected = [ResourceQuerySelection.model_validate(item) for item in selections]
    reasons = {item.reference: item.reason for item in selected}
    if len(reasons) != len(selected) or reasons.keys() != {item.reference for item in queries}:
        raise ValueError("Customization must preserve every resource query reference")
    row_reasons = {
        resource_identity(row): str(row.get("reason") or "")
        for row in customized_resources
        if isinstance(row, dict) and resource_identity(row)
    }
    translated = []
    for row in resources:
        references = row.get("query_refs") or []
        if references:
            if any(reference not in reasons for reference in references):
                raise ValueError("Resource membership references missing query evidence")
            reason = "\n".join(dict.fromkeys(reasons[reference] for reference in references))
        else:
            reason = row_reasons.get(resource_identity(row)) or row.get("reason", "")
        translated.append({**row, "reason": reason})
    return translated, [
        item.model_copy(update={"reason": reasons[item.reference]}) for item in queries
    ]
