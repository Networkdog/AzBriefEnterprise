"""Tests for the Foundry Hosted Agent entry point."""

import pytest

from src.agent.analyzer import AnalysisResult, RelevanceStatus
from src.agent.hosted_contract import (
    HostedAnalysisRequest,
    HostedCustomizationRequest,
    HostedSubscriber,
    HostedUpdate,
)
from src.config import Settings
from src.hosted_agent import execute_request, get_hosted_settings


def _update() -> HostedUpdate:
    return HostedUpdate(
        id="update-1",
        title="Azure Update",
        description="Description",
        link="https://azure.microsoft.com/updates/update-1",
        categories=[],
        azure_services=[],
    )


def _result(summary: str = "Summary") -> AnalysisResult:
    return AnalysisResult(
        update_id="update-1",
        update_title="Azure Update",
        relevance=RelevanceStatus.RELEVANT,
        one_line_summary=summary,
        relevance_reason="Relevant",
        affected_resources=[],
        impact_summary="Impact",
        recommendations=[],
        reference_docs=[],
        should_notify=True,
    )


def test_hosted_settings_map_non_reserved_agent_aliases(monkeypatch):
    settings = Settings(
        _env_file=None,
        azure_tenant_id="00000000-0000-0000-0000-000000000000",
        foundry_project_endpoint="https://demo.services.ai.azure.com/api/projects/azbrief",
        foundry_hosted_agent_name="must-not-recurse",
    )
    monkeypatch.setattr("src.hosted_agent.get_settings", lambda: settings)
    monkeypatch.setenv("AZBRIEF_PROMPT_PRIMARY_AGENT_NAME", "azbrief-primary")
    monkeypatch.setenv(
        "AZBRIEF_ENRICHMENT_AGENT_ROSTER",
        '[{"name":"azbrief-research","stage":"research"}]',
    )

    resolved = get_hosted_settings()

    assert resolved.foundry_primary_agent_name == "azbrief-primary"
    assert [spec.name for spec in resolved.get_foundry_enrichment_agents()] == ["azbrief-research"]
    assert resolved.foundry_hosted_agent_name is None


@pytest.mark.asyncio
async def test_execute_request_returns_complete_analysis_contract():
    class FakeAnalyzer:
        async def analyze_update(self, update, trace_id=None):
            assert update.id == "update-1"
            assert trace_id == "trace-1"
            return _result()

    request = HostedAnalysisRequest(update=_update(), trace_id="trace-1")

    response = await execute_request(request.model_dump_json(), FakeAnalyzer())

    assert response.status == "completed"
    assert response.operation == "analyze_update"
    assert response.result["one_line_summary"] == "Summary"
    assert response.trace_id == "trace-1"


@pytest.mark.asyncio
async def test_execute_request_customizes_inside_hosted_runtime():
    class FakeAnalyzer:
        async def customize_for_subscriber(self, result, subscriber, update):
            assert result.update_id == "update-1"
            assert subscriber.email == "admin@example.com"
            assert update.title == "Azure Update"
            return result.model_copy(update={"one_line_summary": "Customized"})

    request = HostedCustomizationRequest(
        update=_update(),
        result=_result().model_dump(mode="json"),
        subscriber=HostedSubscriber(email="admin@example.com", name="Admin"),
        trace_id="trace-2",
    )

    response = await execute_request(request.model_dump_json(), FakeAnalyzer())

    assert response.status == "completed"
    assert response.operation == "customize_for_subscriber"
    assert response.result["one_line_summary"] == "Customized"
    assert response.trace_id == "trace-2"


@pytest.mark.asyncio
async def test_execute_request_rejects_invalid_request():
    class FakeAnalyzer:
        async def analyze_update(self, update, trace_id=None):
            raise AssertionError("invalid request must not run the analyzer")

    response = await execute_request("not-json", FakeAnalyzer())

    assert response.status == "failed"
    assert response.error == "Invalid Hosted Agent request"


@pytest.mark.asyncio
async def test_execute_request_withholds_internal_error():
    class FakeAnalyzer:
        async def analyze_update(self, update, trace_id=None):
            raise RuntimeError("secret internal detail")

    request = HostedAnalysisRequest(update=_update(), trace_id="trace-1")

    response = await execute_request(request.model_dump_json(), FakeAnalyzer())

    assert response.status == "failed"
    assert response.error == "Hosted analysis failed"
    assert "secret" not in response.error
