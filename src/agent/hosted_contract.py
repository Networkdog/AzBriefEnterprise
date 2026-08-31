"""Versioned contract between the Container Apps control plane and Hosted Agent."""

from datetime import datetime
from typing import Annotated, Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

HOSTED_ANALYSIS_CONTRACT_VERSION = "2"


class HostedUpdate(BaseModel):
    """Serializable Azure Update payload accepted by the Hosted Agent."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=2_000)
    title: str = Field(min_length=1, max_length=10_000)
    description: str = Field(default="", max_length=4_000_000)
    link: str = Field(default="", max_length=20_000)
    published_date: Optional[datetime] = None
    categories: list[str] = Field(default_factory=list)
    azure_services: list[str] = Field(default_factory=list)
    update_type: Optional[str] = None
    status: Optional[str] = None
    learn_more_links: list[dict[str, Any]] = Field(default_factory=list)


class HostedSubscriber(BaseModel):
    """Subscriber fields required for role-specific report customization."""

    model_config = ConfigDict(extra="forbid")

    email: str
    name: str
    role: str = ""
    language: str = "ko"
    subscriptions: list[str] = Field(default_factory=list)
    resource_groups: list[str] = Field(default_factory=list)
    focus_services: list[str] = Field(default_factory=list)
    alert_level: str = "all"


class HostedAnalysisRequest(BaseModel):
    """Request a complete Plan-Execute-Evaluate-Report analysis."""

    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["2"] = HOSTED_ANALYSIS_CONTRACT_VERSION
    operation: Literal["analyze_update"] = "analyze_update"
    update: HostedUpdate
    trace_id: str = Field(min_length=1, max_length=128)


class HostedEvaluationRequest(BaseModel):
    """Request analysis plus bounded pre-release quality diagnostics."""

    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["2"] = HOSTED_ANALYSIS_CONTRACT_VERSION
    operation: Literal["evaluate_update"] = "evaluate_update"
    update: HostedUpdate
    trace_id: str = Field(min_length=1, max_length=128)


class HostedCustomizationRequest(BaseModel):
    """Request subscriber-specific rewriting inside the Hosted Agent runtime."""

    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["2"] = HOSTED_ANALYSIS_CONTRACT_VERSION
    operation: Literal["customize_for_subscriber"] = "customize_for_subscriber"
    update: HostedUpdate
    result: dict[str, Any]
    subscriber: HostedSubscriber
    trace_id: str = Field(min_length=1, max_length=128)


HostedAgentRequest = Annotated[
    Union[HostedAnalysisRequest, HostedEvaluationRequest, HostedCustomizationRequest],
    Field(discriminator="operation"),
]
HOSTED_AGENT_REQUEST_ADAPTER = TypeAdapter(HostedAgentRequest)


class HostedRunDiagnostics(BaseModel):
    """Bounded quality summaries without raw evidence or private reasoning."""

    model_config = ConfigDict(extra="forbid")

    report_quality: Optional[dict[str, Any]] = None
    trajectory: Optional[dict[str, Any]] = None
    action_verification: Optional[dict[str, Any]] = None


class HostedEvaluationResult(BaseModel):
    """Analysis and diagnostics returned only for pre-release evaluation."""

    model_config = ConfigDict(extra="forbid")

    trace_id: str = Field(min_length=1, max_length=128)
    analysis: dict[str, Any]
    diagnostics: HostedRunDiagnostics


class HostedAgentResponse(BaseModel):
    """Validated full-analysis response returned by the Hosted Agent."""

    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["2"] = HOSTED_ANALYSIS_CONTRACT_VERSION
    operation: Literal["analyze_update", "evaluate_update", "customize_for_subscriber"]
    status: Literal["completed", "failed"]
    result: Optional[dict[str, Any]] = None
    trace_id: str = Field(min_length=1, max_length=128)
    error: str = Field(default="", max_length=2_000)

    @model_validator(mode="after")
    def validate_status_payload(self) -> "HostedAgentResponse":
        """Keep success and failure payloads unambiguous."""
        if self.status == "completed" and self.result is None:
            raise ValueError("completed response requires result")
        if self.status == "failed" and not self.error:
            raise ValueError("failed response requires error")
        if self.status == "failed" and self.result is not None:
            raise ValueError("failed response cannot include result")
        if self.status != "failed" and self.error:
            raise ValueError("only failed responses may include error")
        return self
