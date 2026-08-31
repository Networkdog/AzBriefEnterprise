"""Versioned contracts for the canonical analysis archive."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.agent.hosted_contract import HOSTED_ANALYSIS_CONTRACT_VERSION

ARCHIVE_SCHEMA_VERSION = "1"
ARCHIVE_ID_PATTERN = r"^[0-9]{13}-[0-9a-f]{32}$"


class ArchiveSource(str, Enum):
    """Control-plane entry point that produced an archived analysis."""

    SCHEDULED_DIGEST = "scheduled_digest"
    ADMIN_RUN = "admin_run"
    API_ORCHESTRATE = "api_orchestrate"
    API_ANALYZE = "api_analyze"
    API_BATCH = "api_batch"
    MCP = "mcp"


class ArchiveUpdateLinkV1(BaseModel):
    """One link extracted from an Azure Update description."""

    model_config = ConfigDict(extra="forbid")

    text: str
    url: str


class ArchiveUpdateV1(BaseModel):
    """Frozen v1 projection of the Azure Update input."""

    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    description: str = ""
    link: str = ""
    published_date: Optional[datetime] = None
    categories: list[str] = Field(default_factory=list)
    azure_services: list[str] = Field(default_factory=list)
    update_type: Optional[str] = None
    status: Optional[str] = None
    learn_more_links: list[ArchiveUpdateLinkV1] = Field(default_factory=list)


class ArchiveActionItemV1(BaseModel):
    """Frozen v1 action item delivered with a canonical analysis."""

    model_config = ConfigDict(extra="forbid")

    step: int = 1
    priority: int = 1
    urgency: str = "medium"
    task: str
    why: str = ""
    target_resources: list[str] = Field(default_factory=list)
    procedure: str = ""
    cli_command: str = ""
    estimated_time: str = ""
    deadline: str = ""
    risk_if_not_done: str = ""
    precaution: str = ""
    rollback: str = ""
    reference_url: str = ""
    verification_status: str = ""
    verification_notes: list[str] = Field(default_factory=list)


class ArchiveImpactSummaryV1(BaseModel):
    """Frozen v1 impact dimensions."""

    model_config = ConfigDict(extra="forbid")

    cost_impact: str = ""
    security_impact: str = ""
    performance_impact: str = ""
    operational_impact: str = ""


class ArchiveAffectedResourceV1(BaseModel):
    """Frozen v1 resource projection used by report and email renderers."""

    model_config = ConfigDict(extra="forbid")

    name: str
    type: str = ""
    resourceGroup: str = ""
    subscription: str = ""
    subscriptionId: str = ""
    subscriptionName: str = ""
    location: str = ""
    reason: str = ""
    action_required: Optional[bool] = None


class ArchiveReferenceDocumentV1(BaseModel):
    """Frozen v1 technical reference projection."""

    model_config = ConfigDict(extra="forbid")

    title: str
    url: str = ""
    related_content: str = ""
    description: str = ""


class ArchiveAnalysisResultV1(BaseModel):
    """Frozen v1 projection of a canonical AnalysisResult."""

    model_config = ConfigDict(extra="forbid")

    update_id: str
    update_title: str
    update_category: Literal[
        "retirement",
        "feature_change",
        "new_feature",
        "new_service",
        "region_expansion",
        "preview",
        "sdk_tooling",
        "pricing",
    ] = "new_feature"
    urgency: Literal["critical", "high", "medium", "low"] = "medium"
    importance: Literal["", "high", "medium", "low"] = ""
    impact_level: Literal["", "high", "medium", "low"] = ""
    blast_radius_score: int = 0
    blast_radius_detail: str = ""
    relevance: Literal["relevant", "not_relevant", "opportunity", "unknown"]
    one_line_summary: str = ""
    relevance_evidence: str = ""
    relevance_reason: str
    affected_resources: list[ArchiveAffectedResourceV1] = Field(default_factory=list)
    impact_summary: str
    impact_details: Optional[ArchiveImpactSummaryV1] = None
    action_items: list[ArchiveActionItemV1] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    reference_docs: list[ArchiveReferenceDocumentV1] = Field(default_factory=list)
    additional_checks: list[str] = Field(default_factory=list)
    should_notify: bool


class ArchiveDocumentV1(BaseModel):
    """Immutable canonical analysis document stored as one archive object."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1"] = ARCHIVE_SCHEMA_VERSION
    archive_id: str = Field(pattern=ARCHIVE_ID_PATTERN)
    analyzed_at: datetime
    source: ArchiveSource
    run_id: str = Field(default="", max_length=128)
    hosted_contract_version: Literal["2"] = HOSTED_ANALYSIS_CONTRACT_VERSION
    hosted_agent_name: str = Field(default="", max_length=256)
    trace_id: str = Field(default="", max_length=128)
    report_language: str = Field(default="ko", min_length=2, max_length=35)
    update: ArchiveUpdateV1
    result: ArchiveAnalysisResultV1

    @field_validator("analyzed_at")
    @classmethod
    def normalize_analyzed_at(cls, value: datetime) -> datetime:
        """Store every archive timestamp as timezone-aware UTC."""
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def validate_update_identity(self) -> "ArchiveDocumentV1":
        """Reject a document that pairs a report with a different update."""
        if self.result.update_id != self.update.id:
            raise ValueError("result.update_id must match update.id")
        return self


class ArchiveSummary(BaseModel):
    """Bounded list-view projection of an archive document."""

    model_config = ConfigDict(extra="forbid")

    archive_id: str = Field(pattern=ARCHIVE_ID_PATTERN)
    analyzed_at: datetime
    source: ArchiveSource
    run_id: str = ""
    update_id: str
    title: str
    published_date: Optional[datetime] = None
    azure_services: list[str] = Field(default_factory=list)
    update_category: str = ""
    urgency: str = ""
    importance: str = ""
    impact_level: str = ""
    relevance: str = ""
    one_line_summary: str = ""
    affected_resource_count: int = 0
    action_item_count: int = 0

    @classmethod
    def from_document(cls, document: ArchiveDocumentV1) -> "ArchiveSummary":
        """Build the stable list projection from a validated document."""
        result = document.result
        urgency = result.urgency.value if hasattr(result.urgency, "value") else str(result.urgency)
        relevance = (
            result.relevance.value if hasattr(result.relevance, "value") else str(result.relevance)
        )
        return cls(
            archive_id=document.archive_id,
            analyzed_at=document.analyzed_at,
            source=document.source,
            run_id=document.run_id,
            update_id=document.update.id,
            title=document.update.title,
            published_date=document.update.published_date,
            azure_services=document.update.azure_services,
            update_category=result.update_category,
            urgency=urgency,
            importance=result.importance,
            impact_level=result.impact_level,
            relevance=relevance,
            one_line_summary=result.one_line_summary,
            affected_resource_count=len(result.affected_resources),
            action_item_count=len(result.action_items),
        )


class ArchiveReceipt(BaseModel):
    """Result of committing one immutable archive document."""

    model_config = ConfigDict(extra="forbid")

    archived: bool
    archive_id: str = ""
    object_name: str = ""


class ArchiveQuery(BaseModel):
    """Validated filters for a bounded archive listing."""

    model_config = ConfigDict(extra="forbid")

    q: str = Field(default="", max_length=200)
    service: str = Field(default="", max_length=200)
    category: str = Field(default="", max_length=100)
    relevance: str = Field(default="", max_length=50)
    importance: str = Field(default="", max_length=50)
    impact_level: str = Field(default="", max_length=50)
    source: Optional[ArchiveSource] = None
    update_id: str = Field(default="", max_length=2_000)
    analyzed_after: Optional[datetime] = None
    analyzed_before: Optional[datetime] = None
    published_after: Optional[datetime] = None
    published_before: Optional[datetime] = None
    limit: int = Field(default=25, ge=1, le=50)
    cursor: str = Field(default="", max_length=512)

    @field_validator(
        "analyzed_after",
        "analyzed_before",
        "published_after",
        "published_before",
    )
    @classmethod
    def normalize_query_datetime(cls, value: Optional[datetime]) -> Optional[datetime]:
        """Normalize every filter boundary to timezone-aware UTC."""
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def validate_ranges(self) -> "ArchiveQuery":
        """Reject inverted date ranges before they reach a storage scan."""
        ranges = (
            (self.analyzed_after, self.analyzed_before, "analyzed"),
            (self.published_after, self.published_before, "published"),
        )
        for lower, upper, name in ranges:
            if lower and upper and lower > upper:
                raise ValueError(f"{name}_after must not be later than {name}_before")
        return self


class ArchivePage(BaseModel):
    """Cursor-paginated archive summaries."""

    model_config = ConfigDict(extra="forbid")

    items: list[ArchiveSummary] = Field(default_factory=list)
    next_cursor: str = ""
    scanned: int = 0
    has_more: bool = False
