"""Foundry Hosted Agent entry point for the complete AzBrief analysis runtime."""

import asyncio
import os
import time
from pathlib import Path
from typing import Optional, Protocol

os.environ.setdefault("AZBRIEF_VERBOSE", "false")
os.environ.setdefault("AZBRIEF_DATA_DIR", str(Path.home() / ".azbrief"))

from azure.ai.agentserver.responses import (  # noqa: E402
    CreateResponse,
    ResponseContext,
    ResponsesAgentServerHost,
    ResponsesServerOptions,
    TextResponse,
)
from pydantic import ValidationError  # noqa: E402
from structlog import get_logger  # noqa: E402

from src.agent.analyzer import AnalysisResult, AzureUpdateAnalyzer  # noqa: E402
from src.agent.foundry_backend import foundry_invocation_context  # noqa: E402
from src.agent.hosted_contract import (  # noqa: E402
    HOSTED_AGENT_REQUEST_ADAPTER,
    HostedAgentResponse,
    HostedAnalysisRequest,
    HostedCustomizationRequest,
    HostedEvaluationRequest,
    HostedEvaluationResult,
    HostedRunDiagnostics,
    HostedUpdate,
)
from src.config import Subscriber, get_settings  # noqa: E402
from src.logging_config import setup_logging  # noqa: E402
from src.rss.parser import AzureUpdate  # noqa: E402

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

    def get_last_run_diagnostics(self) -> dict: ...


_analyzer: Optional[AzureUpdateAnalyzer] = None


def get_hosted_settings():
    """Resolve specialist aliases and prevent recursive Hosted Agent invocation."""
    settings = get_settings()
    updates = {"foundry_hosted_agent_name": None}
    specialist_aliases = {
        "foundry_coordinator_agent_name": "AZBRIEF_PROMPT_COORDINATOR_AGENT_NAME",
        "foundry_resource_graph_agent_name": "AZBRIEF_PROMPT_RESOURCE_GRAPH_AGENT_NAME",
        "foundry_azure_mcp_agent_name": "AZBRIEF_PROMPT_AZURE_MCP_AGENT_NAME",
        "foundry_azure_api_agent_name": "AZBRIEF_PROMPT_AZURE_API_AGENT_NAME",
        "foundry_report_writer_agent_name": "AZBRIEF_PROMPT_REPORT_WRITER_AGENT_NAME",
        "foundry_quality_reviewer_agent_name": "AZBRIEF_PROMPT_QUALITY_REVIEWER_AGENT_NAME",
    }
    for field_name, environment_name in specialist_aliases.items():
        value = os.environ.get(environment_name)
        if value:
            updates[field_name] = value

    return settings.model_copy(update=updates)


def get_analysis_runtime() -> AzureUpdateAnalyzer:
    """Create the complete in-container analyzer on first use."""
    global _analyzer
    if _analyzer is None:
        settings = get_hosted_settings()
        if not settings.has_complete_specialist_roster:
            raise RuntimeError(
                "Hosted Agent requires distinct coordinator, resource_graph, azure_mcp, "
                "azure_api, report_writer, and quality_reviewer Prompt Agents"
            )
        _analyzer = AzureUpdateAnalyzer(settings=settings)
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

    started = time.monotonic()
    logger.info(
        "hosted_request_started",
        operation=request.operation,
        trace_id=request.trace_id,
        update_id=request.update.id,
    )
    try:
        update = _to_azure_update(request.update)
        with foundry_invocation_context(request.trace_id, f"hosted:{request.operation}"):
            if isinstance(request, HostedAnalysisRequest):
                result = await analyzer.analyze_update(update, trace_id=request.trace_id)
                result_payload = result.model_dump(mode="json")
            elif isinstance(request, HostedEvaluationRequest):
                result = await analyzer.analyze_update(update, trace_id=request.trace_id)
                result_payload = HostedEvaluationResult(
                    trace_id=request.trace_id,
                    analysis=result.model_dump(mode="json"),
                    diagnostics=HostedRunDiagnostics.model_validate(
                        analyzer.get_last_run_diagnostics()
                    ),
                ).model_dump(mode="json")
            elif isinstance(request, HostedCustomizationRequest):
                result = await analyzer.customize_for_subscriber(
                    AnalysisResult.model_validate(request.result),
                    Subscriber.model_validate(request.subscriber.model_dump()),
                    update,
                )
                result_payload = result.model_dump(mode="json")
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

    logger.info(
        "hosted_request_completed",
        operation=request.operation,
        trace_id=request.trace_id,
        update_id=request.update.id,
        elapsed_s=round(time.monotonic() - started, 2),
    )
    return HostedAgentResponse(
        operation=request.operation,
        status="completed",
        result=result_payload,
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
