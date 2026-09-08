"""Async-safe resource scope for one Azure Update analysis."""

import re
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SCOPED_ANALYSIS_TOOL_NAMES = frozenset(
    {
        "query_azure_resources",
        "get_resource_type_summary",
        "find_related_resources",
        "get_service_resource_details",
        "get_security_posture",
        "get_service_health",
        "get_resource_configurations",
        "get_resource_dependencies",
        "explore_resource_schema",
        "search_resource_graph_docs",
        "search_azure_docs",
        "get_service_documentation",
        "search_update_related_docs",
        "query_tool_result",
    }
)

_RESOURCE_GROUP_ID_RE = re.compile(
    r"^/?subscriptions/(?P<subscription>[^/]+)/resourcegroups/(?P<group>[^/]+)(?:/.*)?$",
    re.IGNORECASE,
)


def _normalize_subscription_id(value: Any) -> str:
    text = str(value).strip().strip("/")
    if text.casefold().startswith("subscriptions/"):
        text = text.split("/", 2)[1]
    try:
        return str(UUID(text))
    except ValueError as exc:
        raise ValueError(f"Invalid Azure Subscription ID: {text!r}") from exc


def _resource_group_id_parts(value: Any) -> tuple[str, str] | None:
    match = _RESOURCE_GROUP_ID_RE.fullmatch(str(value).strip())
    if match is None:
        return None
    return _normalize_subscription_id(match.group("subscription")), match.group("group")


class AnalysisScope(BaseModel):
    """Hard resource boundary applied to tenant evidence queries."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    management_groups: tuple[str, ...] = Field(default_factory=tuple, max_length=100)
    subscriptions: tuple[str, ...] = Field(default_factory=tuple, max_length=100)
    resource_groups: tuple[str, ...] = Field(default_factory=tuple, max_length=100)

    @model_validator(mode="before")
    @classmethod
    def preserve_resource_group_parent_subscriptions(cls, value: Any) -> Any:
        """Derive and validate parent subscriptions from full Resource Group IDs."""
        if not isinstance(value, dict):
            return value
        data = dict(value)
        raw_groups = data.get("resource_groups")
        if raw_groups is None or isinstance(raw_groups, (str, bytes)):
            return data

        parents = {
            parts[0] for item in raw_groups if (parts := _resource_group_id_parts(item)) is not None
        }
        if not parents:
            return data

        raw_subscriptions = data.get("subscriptions")
        if not raw_subscriptions:
            data["subscriptions"] = sorted(parents)
            return data
        if isinstance(raw_subscriptions, (str, bytes)):
            return data

        explicit = {_normalize_subscription_id(item) for item in raw_subscriptions}
        missing = parents - explicit
        if missing:
            raise ValueError(
                "Resource Group ARM ID parent subscriptions are outside the explicit "
                f"subscription scope: {', '.join(sorted(missing))}"
            )
        return data

    @field_validator("management_groups", "subscriptions", "resource_groups", mode="before")
    @classmethod
    def normalize_values(cls, value: Any, info) -> tuple[str, ...]:
        """Normalize full ARM IDs and de-duplicate scope values case-insensitively."""
        field_name = info.field_name
        if isinstance(value, (str, bytes)):
            raise ValueError(f"{field_name} must be an array, not a scalar string")
        normalized: list[str] = []
        seen: set[str] = set()
        for item in value or ():
            raw_text = str(item).strip()
            text = raw_text.strip("/")
            lowered = text.casefold()
            if field_name == "management_groups":
                marker = "providers/microsoft.management/managementgroups/"
                if marker in lowered:
                    text = text[lowered.index(marker) + len(marker) :].split("/", 1)[0]
            elif field_name == "subscriptions":
                text = _normalize_subscription_id(raw_text)
            elif field_name == "resource_groups" and "/" in text:
                parts = _resource_group_id_parts(raw_text)
                if parts is None:
                    raise ValueError(f"Invalid Azure Resource Group ID: {raw_text!r}")
                parent_subscription, group_name = parts
                text = f"/subscriptions/{parent_subscription}/resourceGroups/{group_name}"
            if not text:
                raise ValueError(f"{field_name} cannot contain an empty value")
            if field_name == "management_groups":
                valid = (
                    len(text) <= 90
                    and text[0].isalnum()
                    and not text.endswith(".")
                    and all(
                        character.isascii() and (character.isalnum() or character in "-_.()")
                        for character in text
                    )
                )
                if not valid:
                    raise ValueError(f"Invalid Azure Management Group ID: {text!r}")
            elif field_name == "resource_groups":
                group_name = (
                    _resource_group_id_parts(text)[1]
                    if _resource_group_id_parts(text) is not None
                    else text
                )
                valid = (
                    len(group_name) <= 90
                    and not group_name.endswith(".")
                    and all(character.isalnum() or character in "-_.()" for character in group_name)
                )
                if not valid:
                    raise ValueError(f"Invalid Azure Resource Group name: {group_name!r}")
            key = text.casefold()
            if text and key not in seen:
                seen.add(key)
                normalized.append(text)
        return tuple(normalized)

    @classmethod
    def from_subscriber(cls, subscriber: Any) -> "AnalysisScope":
        """Build a scope from a Subscriber-compatible object."""
        return cls(
            management_groups=getattr(subscriber, "management_groups", ()),
            subscriptions=getattr(subscriber, "subscriptions", ()),
            resource_groups=getattr(subscriber, "resource_groups", ()),
        )

    @property
    def is_bounded(self) -> bool:
        """Return whether at least one resource boundary is configured."""
        return bool(self.management_groups or self.subscriptions or self.resource_groups)

    def prompt_text(self) -> str:
        """Render the hard boundary for planning and evidence interpretation."""
        if not self.is_bounded:
            return "Resource scope: all resources accessible to the analysis identity."
        lines = [
            "Resource scope: HARD BOUNDARY. Investigate and report resources only inside it.",
            "Never use an unscoped result as fallback and never infer facts outside this scope.",
        ]
        if self.management_groups:
            lines.append(f"- Management Groups: {', '.join(self.management_groups)}")
        if self.subscriptions:
            lines.append(f"- Subscriptions: {', '.join(self.subscriptions)}")
        if self.resource_groups:
            lines.append(f"- Resource Groups: {', '.join(self.resource_groups)}")
        return "\n".join(lines)

    def contains_resource(self, resource: dict[str, Any]) -> bool:
        """Check fields that can be validated directly on a report resource row."""
        if self.subscriptions:
            subscription_id = str(resource.get("subscriptionId", "")).casefold()
            allowed_subscriptions = {item.casefold() for item in self.subscriptions}
            if not subscription_id or subscription_id not in allowed_subscriptions:
                return False
        if self.resource_groups:
            exact_groups = {
                parts
                for value in self.resource_groups
                if (parts := _resource_group_id_parts(value)) is not None
            }
            named_groups = {
                value.casefold()
                for value in self.resource_groups
                if _resource_group_id_parts(value) is None
            }
            resource_group = str(resource.get("resourceGroup", "")).casefold()
            subscription_id = str(resource.get("subscriptionId", "")).casefold()
            exact_match = any(
                subscription_id == parent.casefold() and resource_group == group.casefold()
                for parent, group in exact_groups
            )
            if not resource_group or (resource_group not in named_groups and not exact_match):
                return False
        return True


_CURRENT_ANALYSIS_SCOPE: ContextVar[AnalysisScope] = ContextVar(
    "azbrief_analysis_scope",
    default=AnalysisScope(),
)


@contextmanager
def analysis_scope_context(scope: AnalysisScope | None) -> Iterator[AnalysisScope]:
    """Bind one resource scope without leaking it across concurrent analyses."""
    effective = scope or AnalysisScope()
    token = _CURRENT_ANALYSIS_SCOPE.set(effective)
    try:
        yield effective
    finally:
        _CURRENT_ANALYSIS_SCOPE.reset(token)


def current_analysis_scope() -> AnalysisScope:
    """Return the resource scope bound to the current async context."""
    return _CURRENT_ANALYSIS_SCOPE.get()
