"""Tests for the Container Apps to Hosted Agent client boundary."""

import httpx
import pytest

from src.agent import hosted_client
from src.agent.analyzer import RelevanceStatus
from src.agent.hosted_contract import (
    HostedAgentResponse,
    HostedAnalysisRequest,
    HostedCustomizationRequest,
    HostedEvaluationResult,
    HostedRunDiagnostics,
    HostedSubscriber,
    HostedUpdate,
)
from src.config import Settings, Subscriber
from src.rss.parser import AzureUpdate

_ENDPOINT = "https://demo.services.ai.azure.com/api/projects/azbrief"


def _settings(**overrides) -> Settings:
    values = {
        "azure_tenant_id": "00000000-0000-0000-0000-000000000000",
        "foundry_project_endpoint": _ENDPOINT,
        "foundry_hosted_agent_name": "azbrief-analysis-hosted",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def _update() -> AzureUpdate:
    return AzureUpdate(
        id="update-1",
        title="Azure Update",
        description="Description",
        link="https://azure.microsoft.com/updates/update-1",
        published_date=None,
        categories=[],
        azure_services=[],
        update_type=None,
        status=None,
    )


def _result_payload(summary: str = "Summary") -> dict:
    return {
        "update_id": "update-1",
        "update_title": "Azure Update",
        "relevance": RelevanceStatus.RELEVANT.value,
        "one_line_summary": summary,
        "relevance_reason": "Relevant",
        "affected_resources": [],
        "impact_summary": "Impact",
        "recommendations": [],
        "reference_docs": [],
        "should_notify": True,
    }


def test_hosted_agent_endpoint_escapes_agent_name():
    endpoint = hosted_client.hosted_agent_responses_endpoint(_ENDPOINT, "agent/name")

    assert endpoint == (
        f"{_ENDPOINT}/agents/agent%2Fname/endpoint/protocols/openai/responses?api-version=v1"
    )


def test_extract_response_text_uses_message_content():
    payload = {
        "output": [
            {
                "type": "message",
                "content": [
                    {"type": "output_text", "text": "first"},
                    {"type": "output_text", "text": " second"},
                ],
            }
        ]
    }

    assert hosted_client.extract_response_text(payload) == "first second"


@pytest.mark.asyncio
async def test_invoke_uses_entra_and_responses_contract(monkeypatch):
    captured = {}

    class Credential:
        closed = False

        def get_token(self, scope):
            captured["scope"] = scope
            return type("Token", (), {"token": "entra-token"})()

        def close(self):
            self.closed = True

    credential = Credential()

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            wire_response = HostedAgentResponse(
                operation="analyze_update",
                status="completed",
                result=_result_payload(),
                trace_id="trace-1",
            )
            return {"output_text": wire_response.model_dump_json()}

    class Client:
        def __init__(self, timeout):
            captured["timeout"] = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, endpoint, headers, json):
            captured.update(endpoint=endpoint, headers=headers, json=json)
            return Response()

    monkeypatch.setattr(hosted_client, "get_azure_credential", lambda: credential)
    monkeypatch.setattr(hosted_client.httpx, "AsyncClient", Client)
    request = HostedAnalysisRequest(
        update=HostedUpdate.model_validate(_update().to_dict()),
        trace_id="trace-1",
    )

    response = await hosted_client.invoke_hosted_agent(_settings(), request)

    assert response.result["update_id"] == "update-1"
    assert captured["scope"] == "https://ai.azure.com/.default"
    assert captured["endpoint"].endswith(
        "/agents/azbrief-analysis-hosted/endpoint/protocols/openai/responses?api-version=v1"
    )
    assert captured["headers"] == {"Authorization": "Bearer entra-token"}
    assert captured["json"] == {
        "model": "azbrief-analysis-hosted",
        "input": request.model_dump_json(),
        "store": False,
        "stream": False,
    }
    assert credential.closed is True


@pytest.mark.asyncio
async def test_analysis_retries_transient_http_status(monkeypatch):
    attempts = []

    class Credential:
        def get_token(self, _scope):
            return type("Token", (), {"token": "entra-token"})()

        def close(self):
            return None

    class Response:
        def __init__(self, status_code):
            self.status_code = status_code
            self.request = httpx.Request("POST", _ENDPOINT)

        def raise_for_status(self):
            if self.status_code >= 400:
                raise httpx.HTTPStatusError(
                    "transient",
                    request=self.request,
                    response=httpx.Response(self.status_code, request=self.request),
                )

        def json(self):
            result = HostedAgentResponse(
                operation="analyze_update",
                status="completed",
                result=_result_payload(),
                trace_id="trace-1",
            )
            return {"output_text": result.model_dump_json()}

    class Client:
        def __init__(self, timeout):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, *_args, **_kwargs):
            attempts.append(True)
            return Response(503 if len(attempts) == 1 else 200)

    async def no_sleep(_delay):
        return None

    monkeypatch.setattr(hosted_client, "get_azure_credential", lambda: Credential())
    monkeypatch.setattr(hosted_client.httpx, "AsyncClient", Client)
    monkeypatch.setattr(hosted_client.asyncio, "sleep", no_sleep)
    request = HostedAnalysisRequest(
        update=HostedUpdate.model_validate(_update().to_dict()),
        trace_id="trace-1",
    )

    response = await hosted_client.invoke_hosted_agent(_settings(), request)

    assert response.status == "completed"
    assert len(attempts) == 2


@pytest.mark.asyncio
async def test_customization_fails_fast_and_preserves_status(monkeypatch):
    attempts = []

    class Credential:
        def get_token(self, _scope):
            return type("Token", (), {"token": "entra-token"})()

        def close(self):
            return None

    class Client:
        def __init__(self, timeout):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, *_args, **_kwargs):
            attempts.append(True)
            request = httpx.Request("POST", _ENDPOINT)
            return httpx.Response(529, request=request)

    monkeypatch.setattr(hosted_client, "get_azure_credential", lambda: Credential())
    monkeypatch.setattr(hosted_client.httpx, "AsyncClient", Client)
    request = HostedCustomizationRequest(
        update=HostedUpdate.model_validate(_update().to_dict()),
        result=_result_payload(),
        subscriber=HostedSubscriber(email="admin@example.com", name="Admin"),
        trace_id="trace-2",
    )

    with pytest.raises(hosted_client.HostedAgentError) as exc:
        await hosted_client.invoke_hosted_agent(_settings(), request)

    assert exc.value.status_code == 529
    assert "HTTP 529" in str(exc.value)
    assert len(attempts) == 1


def test_proxy_fails_closed_without_a_hosted_agent_name():
    with pytest.raises(hosted_client.HostedAgentError):
        hosted_client.HostedAgentAnalyzer(_settings(foundry_hosted_agent_name=None))


@pytest.mark.asyncio
async def test_proxy_returns_complete_hosted_analysis(monkeypatch):
    async def fake_invoke(settings, request):
        assert request.update.id == "update-1"
        return HostedAgentResponse(
            operation="analyze_update",
            status="completed",
            result=_result_payload(),
            trace_id=request.trace_id,
        )

    monkeypatch.setattr(hosted_client, "invoke_hosted_agent", fake_invoke)
    analyzer = hosted_client.HostedAgentAnalyzer(_settings())

    result = await analyzer.analyze_update(_update())

    assert result.update_id == "update-1"
    assert result.one_line_summary == "Summary"
    assert result._hosted_trace_id
    assert "hosted_trace_id" not in result.model_dump()


@pytest.mark.asyncio
async def test_proxy_returns_hosted_evaluation_diagnostics(monkeypatch):
    async def fake_invoke(settings, request):
        payload = HostedEvaluationResult(
            trace_id=request.trace_id,
            analysis=_result_payload(),
            diagnostics=HostedRunDiagnostics(
                report_quality={"weighted_score": 4.25},
                trajectory={"score": 94.0},
                action_verification={"blocked": 0},
            ),
        )
        return HostedAgentResponse(
            operation="evaluate_update",
            status="completed",
            result=payload.model_dump(mode="json"),
            trace_id=request.trace_id,
        )

    monkeypatch.setattr(hosted_client, "invoke_hosted_agent", fake_invoke)
    analyzer = hosted_client.HostedAgentAnalyzer(_settings())

    evaluated = await analyzer.evaluate_update(_update(), trace_id="campaign-trace")

    assert evaluated.trace_id == "campaign-trace"
    assert evaluated.analysis["update_id"] == "update-1"
    assert evaluated.diagnostics.report_quality["weighted_score"] == 4.25
    assert evaluated.diagnostics.trajectory["score"] == 94.0


@pytest.mark.asyncio
async def test_proxy_customizes_inside_hosted_agent(monkeypatch):
    async def fake_invoke(settings, request):
        assert request.subscriber.email == "admin@example.com"
        return HostedAgentResponse(
            operation="customize_for_subscriber",
            status="completed",
            result=_result_payload("Customized"),
            trace_id=request.trace_id,
        )

    monkeypatch.setattr(hosted_client, "invoke_hosted_agent", fake_invoke)
    analyzer = hosted_client.HostedAgentAnalyzer(_settings())
    from src.agent.analyzer import AnalysisResult

    customized = await analyzer.customize_for_subscriber(
        AnalysisResult.model_validate(_result_payload()),
        Subscriber(email="admin@example.com", name="Admin"),
        _update(),
    )

    assert customized.one_line_summary == "Customized"
