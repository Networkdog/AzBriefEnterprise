"""Allow-listed, redacted failure-history contracts for authenticated administrators."""

from datetime import datetime, timezone
from typing import Annotated, Any

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationInfo, field_validator

from src.error_logging import redact_text


class AdminErrorEvent(BaseModel):
    """One persisted event without raw payloads, evidence or exception stacks."""

    model_config = ConfigDict(extra="ignore")

    occurred_at: Annotated[datetime, AwareDatetime] = Field(validation_alias="TimeGenerated")
    level: str = Field(default="", validation_alias="Level")
    runtime: str = Field(default="", validation_alias="Runtime")
    event: str = Field(default="", validation_alias="Event")
    status: str = Field(default="", validation_alias="Status")
    error_type: str = Field(default="", validation_alias="ErrorType")
    message: str = Field(default="", validation_alias="ErrorMessage")
    run_id: str = Field(default="", validation_alias="RunId")
    trace_id: str = Field(default="", validation_alias="TraceId")
    update_id: str = Field(default="", validation_alias="UpdateId")
    task_id: str = Field(default="", validation_alias="TaskId")
    phase: str = Field(default="", validation_alias="Phase")
    operation: str = Field(default="", validation_alias="Operation")
    agent_role: str = Field(default="", validation_alias="AgentRole")
    failed: int = Field(default=0, ge=0, validation_alias="FailedCount")
    archive_failed: int = Field(default=0, ge=0, validation_alias="ArchiveFailedCount")
    pending: int = Field(default=0, ge=0, validation_alias="PendingCount")
    deferred: int = Field(default=0, ge=0, validation_alias="DeferredCount")

    @field_validator("occurred_at")
    @classmethod
    def normalize_time(cls, value: datetime) -> datetime:
        return value.astimezone(timezone.utc)

    @field_validator("*", mode="before")
    @classmethod
    def redact_strings(cls, value: Any, info: ValidationInfo) -> Any:
        if not isinstance(value, str):
            return value
        redacted = redact_text(value)
        limit = 2000 if info.field_name == "message" else 256
        if len(redacted) > limit:
            return redacted[: limit - 14] + "...[TRUNCATED]"
        return redacted


class AdminErrorHistory(BaseModel):
    """A bounded page, not an assertion that all historical errors were collected."""

    events: list[AdminErrorEvent]
    period_hours: int
    has_more: bool
