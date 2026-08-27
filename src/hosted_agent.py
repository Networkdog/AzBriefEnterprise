"""Foundry Hosted Agent entry point for the complete AzBrief analysis runtime."""

import asyncio
import os
from pathlib import Path
from typing import Optional, Protocol

os.environ.setdefault("AZBRIEF_VERBOSE", "false")
os.environ.setdefault("AZBRIEF_DATA_DIR", str(Path.home() / ".azbrief"))

from azure.ai.agentserver.responses import (
    CreateResponse,
    ResponseContext,
    ResponsesAgentServerHost,
    ResponsesServerOptions,
    TextResponse,
)
from pydantic import ValidationError
from structlog import get_logger

from src.agent.analyzer import AnalysisResult, AzureUpdateAnalyzer
from src.agent.hosted_contract import (
    HOSTED_AGENT_REQUEST_ADAPTER,
    HostedAgentResponse,
    HostedAnalysisRequest,
    HostedCustomizationRequest,
    HostedUpdate,
)
from src.config import Subscriber, get_settings
from src.logging_config import setup_logging
from src.rss.parser import AzureUpdate

setup_logging(file_enabled=False)
logger = get_logger()


class AnalysisRuntime(Protocol):
    """Runtime operations exposed by the Hosted Agent protocol."""

    async def analyze_update(
        self, update: AzureUpdate, trace_id: Optional[str] = None
    ) -> AnalysisResult: ...

    async def customize_for_subscriber(
        self,
        result: AnalysisResult,
        subscriber: Subscriber,
        update: AzureUpdate,
    ) -> AnalysisResult: ...


_analyzer: Optional[AzureUpdateAnalyzer] = None


def get_hosted_settings():
    """Resolve Hosted aliases and prevent recursive Hosted Agent invocation."""
    settings = get_settings()
    updates = {"foundry_hosted_agent_name": None}
    role_aliases = {
        "foundry_primary_agent_name": "AZBRIEF_PROMPT_PRIMARY_AGENT_NAME",
        "foundry_planner_agent_name": "AZBRIEF_PROMPT_PLANNER_AGENT_NAME",
        "foundry_evaluator_agent_name": "AZBRIEF_PROMPT_EVALUATOR_AGENT_NAME",
        "foundry_reporter_agent_name": "AZBRIEF_PROMPT_REPORTER_AGENT_NAME",
        "foundry_codex_agent_name": "AZBRIEF_PROMPT_CODEX_AGENT_NAME",
        "foundry_fast_agent_name": "AZBRIEF_PROMPT_FAST_AGENT_NAME",
    }
    for field_name, environment_name in role_aliases.items():
        value = os.environ.get(environment_name)
        if value:
            updates[field_name] = value
    roster = os.environ.get("AZBRIEF_ENRICHMENT_AGENT_ROSTER")
    if roster:
        updates["foundry_enrichment_agents"] = roster
    return settings.model_copy(update=updates)


def get_analysis_runtime() -> AzureUpdateAnalyzer:
    """Create the complete in-container analyzer on first use."""
    global _analyzer
    if _analyzer is None:
        _analyzer = AzureUpdateAnalyzer(settings=get_hosted_settings())
    return _analyzer


def _to_azure_update(payload: HostedUpdate) -> AzureUpdate:
    """Convert the strict wire model into the application domain type."""
    return AzureUpdate(
        id=payload.id,
        title=payload.title,
        description=payload.description,
        link=payload.link,
        published_date=payload.published_date,
        categories=list(payload.categories),
        azure_services=list(payload.azure_services),
        update_type=payload.update_type,
        status=payload.status,
        learn_more_links=[dict(link) for link in payload.learn_more_links],
    )


async def execute_request(raw_request: str, analyzer: AnalysisRuntime) -> HostedAgentResponse:
    """Validate and execute one full-analysis runtime request."""
    try:
        request = HOSTED_AGENT_REQUEST_ADAPTER.validate_json(raw_request)
    except ValidationError:
        return HostedAgentResponse(
            operation="analyze_update",
            status="failed",
            trace_id="invalid-request",
            error="Invalid Hosted Agent request",
        )

    try:
        update = _to_azure_update(request.update)
        if isinstance(request, HostedAnalysisRequest):
            result = await analyzer.analyze_update(update, trace_id=request.trace_id)
        elif isinstance(request, HostedCustomizationRequest):
            result = await analyzer.customize_for_subscriber(
                AnalysisResult.model_validate(request.result),
                Subscriber.model_validate(request.subscriber.model_dump()),
                update,
            )
        else:  # pragma: no cover - the discriminated contract is exhaustive
            raise TypeError(f"Unsupported request type: {type(request).__name__}")
    except Exception:
        logger.exception(
            "hosted_analysis_failed",
            operation=request.operation,
            trace_id=request.trace_id,
        )
        return HostedAgentResponse(
            operation=request.operation,
            status="failed",
            trace_id=request.trace_id,
            error="Hosted analysis failed",
        )

    return HostedAgentResponse(
        operation=request.operation,
        status="completed",
        result=result.model_dump(mode="json"),
        trace_id=request.trace_id,
    )


app = ResponsesAgentServerHost(options=ResponsesServerOptions(default_fetch_history_count=1))


@app.response_handler
async def handle_create(
    request: CreateResponse,
    context: ResponseContext,
    cancellation_signal: asyncio.Event,
):
    """Run one full analysis operation through the Responses protocol."""

    async def run():
        if cancellation_signal.is_set():
            return
        raw_request = await context.get_input_text() or ""
        response = await execute_request(raw_request, get_analysis_runtime())
        yield response.model_dump_json()

    return TextResponse(context, request, text=run())


if __name__ == "__main__":
    app.run()
