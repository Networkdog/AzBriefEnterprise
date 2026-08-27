"""Container Apps proxy for the AzBrief Foundry Hosted Agent runtime."""

import asyncio
import uuid
from typing import Any, Optional, Union
from urllib.parse import quote

import httpx
from structlog import get_logger

from src.agent.analyzer import AnalysisResult
from src.agent.hosted_contract import (
    HostedAgentResponse,
    HostedAnalysisRequest,
    HostedCustomizationRequest,
    HostedSubscriber,
    HostedUpdate,
)
from src.agent.resilience import calculate_backoff
from src.config import Settings, Subscriber, get_azure_credential, get_settings
from src.rss.parser import AzureUpdate

logger = get_logger()

_FOUNDRY_TOKEN_SCOPE = "https://ai.azure.com/.default"
_TRANSIENT_STATUS_CODES = frozenset({408, 424, 429, 500, 502, 503, 504, 529})
_MAX_ANALYSIS_ATTEMPTS = 3


class HostedAgentError(RuntimeError):
    """Raised when the configured Hosted Agent cannot complete an operation."""

    def __init__(self, message: str, *, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code


def hosted_agent_responses_endpoint(project_endpoint: str, agent_name: str) -> str:
    """Build the dedicated OpenAI Responses endpoint for one Hosted Agent."""
    return (
        f"{project_endpoint.rstrip('/')}/agents/{quote(agent_name, safe='')}"
        "/endpoint/protocols/openai/responses?api-version=v1"
    )


def extract_response_text(payload: dict[str, Any]) -> str:
    """Extract assistant text from an OpenAI Responses payload."""
    direct = payload.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()

    text_parts: list[str] = []
    for item in payload.get("output", []):
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if not isinstance(content, dict) or content.get("type") != "output_text":
                continue
            text = content.get("text")
            if isinstance(text, str):
                text_parts.append(text)
    return "".join(text_parts).strip()


async def invoke_hosted_agent(
    settings: Settings,
    request: Union[HostedAnalysisRequest, HostedCustomizationRequest],
) -> HostedAgentResponse:
    """Invoke the configured Hosted Agent and validate its wire response."""
    agent_name = settings.foundry_hosted_agent_name
    project_endpoint = settings.foundry_project_endpoint
    if not project_endpoint or not agent_name:
        raise HostedAgentError("Hosted Agent endpoint configuration is incomplete")

    credential = get_azure_credential()
    try:
        try:
            token = await asyncio.to_thread(credential.get_token, _FOUNDRY_TOKEN_SCOPE)
        except Exception as exc:
            raise HostedAgentError(
                f"Hosted Agent authentication failed: {type(exc).__name__}"
            ) from exc
        endpoint = hosted_agent_responses_endpoint(project_endpoint, agent_name)
        max_attempts = (
            1 if isinstance(request, HostedCustomizationRequest) else _MAX_ANALYSIS_ATTEMPTS
        )
        async with httpx.AsyncClient(timeout=settings.foundry_hosted_agent_timeout_s) as client:
            for attempt in range(max_attempts):
                try:
                    response = await client.post(
                        endpoint,
                        headers={"Authorization": f"Bearer {token.token}"},
                        json={
                            "model": agent_name,
                            "input": request.model_dump_json(),
                            "store": False,
                            "stream": False,
                        },
                    )
                    response.raise_for_status()
                    payload = response.json()
                    break
                except httpx.HTTPStatusError as exc:
                    status_code = exc.response.status_code
                    if status_code in _TRANSIENT_STATUS_CODES and attempt + 1 < max_attempts:
                        delay = calculate_backoff(attempt)
                        logger.warning(
                            "hosted_agent_transient_retry",
                            operation=request.operation,
                            trace_id=request.trace_id,
                            status_code=status_code,
                            attempt=attempt + 1,
                            delay_s=round(delay, 2),
                        )
                        await asyncio.sleep(delay)
                        continue
                    raise HostedAgentError(
                        f"Hosted Agent request failed: HTTP {status_code}",
                        status_code=status_code,
                    ) from exc
                except httpx.RequestError as exc:
                    if attempt + 1 < max_attempts:
                        delay = calculate_backoff(attempt)
                        logger.warning(
                            "hosted_agent_network_retry",
                            operation=request.operation,
                            trace_id=request.trace_id,
                            error=type(exc).__name__,
                            attempt=attempt + 1,
                            delay_s=round(delay, 2),
                        )
                        await asyncio.sleep(delay)
                        continue
                    raise HostedAgentError(
                        f"Hosted Agent request failed: {type(exc).__name__}"
                    ) from exc
                except ValueError as exc:
                    raise HostedAgentError("Hosted Agent returned invalid JSON") from exc
    finally:
        credential.close()

    text = extract_response_text(payload)
    if not text:
        raise HostedAgentError("Hosted Agent returned no response text")
    try:
        result = HostedAgentResponse.model_validate_json(text)
    except ValueError as exc:
        raise HostedAgentError("Hosted Agent returned an invalid analysis payload") from exc
    if result.trace_id != request.trace_id:
        raise HostedAgentError("Hosted Agent response trace_id does not match the request")
    if result.operation != request.operation:
        raise HostedAgentError("Hosted Agent response operation does not match the request")
    if result.status == "failed":
        raise HostedAgentError(result.error)
    return result


class HostedAgentAnalyzer:
    """Analyzer interface implemented by remote Hosted Agent operations."""

    def __init__(self, settings: Optional[Settings] = None):
        self.settings = settings or get_settings()
        if not self.settings.use_hosted_agent:
            raise HostedAgentError(
                "FOUNDRY_PROJECT_ENDPOINT and FOUNDRY_HOSTED_AGENT_NAME are required"
            )

    async def analyze_update(self, update: AzureUpdate) -> AnalysisResult:
        """Run the complete analysis graph in the Hosted Agent."""
        request = HostedAnalysisRequest(
            update=HostedUpdate.model_validate(update.to_dict()),
            trace_id=uuid.uuid4().hex[:12],
        )
        response = await invoke_hosted_agent(self.settings, request)
        logger.info(
            "foundry_hosted_analysis_done",
            trace_id=request.trace_id,
            update_id=update.id,
        )
        return AnalysisResult.model_validate(response.result)

    async def customize_for_subscriber(
        self,
        result: AnalysisResult,
        subscriber: Subscriber,
        update: AzureUpdate,
    ) -> AnalysisResult:
        """Run role and language customization in the Hosted Agent."""
        request = HostedCustomizationRequest(
            update=HostedUpdate.model_validate(update.to_dict()),
            result=result.model_dump(mode="json"),
            subscriber=HostedSubscriber.model_validate(subscriber.model_dump()),
            trace_id=uuid.uuid4().hex[:12],
        )
        response = await invoke_hosted_agent(self.settings, request)
        logger.info(
            "foundry_hosted_customization_done",
            trace_id=request.trace_id,
            update_id=update.id,
            subscriber=subscriber.email,
        )
        return AnalysisResult.model_validate(response.result)

    async def close(self) -> None:
        """Keep parity with local analyzer cleanup; requests own their clients."""
