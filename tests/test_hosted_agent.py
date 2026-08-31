"""Tests for the Foundry Hosted Agent entry point."""

import pytest

from src import hosted_agent
from src.agent.analyzer import AnalysisResult, RelevanceStatus
from src.agent.hosted_contract import (
    HostedAnalysisRequest,
    HostedCustomizationRequest,
    HostedEvaluationRequest,
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
    aliases = {
        "AZBRIEF_PROMPT_COORDINATOR_AGENT_NAME": "azbrief-coordinator",
        "AZBRIEF_PROMPT_RESOURCE_GRAPH_AGENT_NAME": "azbrief-resource-graph",
        "AZBRIEF_PROMPT_AZURE_MCP_AGENT_NAME": "azbrief-azure-mcp",
        "AZBRIEF_PROMPT_AZURE_API_AGENT_NAME": "azbrief-azure-api",
        "AZBRIEF_PROMPT_REPORT_WRITER_AGENT_NAME": "azbrief-report-writer",
        "AZBRIEF_PROMPT_QUALITY_REVIEWER_AGENT_NAME": "azbrief-quality-reviewer",
    }
    for name, value in aliases.items():
        monkeypatch.setenv(name, value)

    resolved = get_hosted_settings()

    assert {spec.role: spec.name for spec in resolved.get_foundry_specialist_agents()} == {
        "coordinator": "azbrief-coordinator",
        "resource_graph": "azbrief-resource-graph",
        "azure_mcp": "azbrief-azure-mcp",
        "azure_api": "azbrief-azure-api",
        "report_writer": "azbrief-report-writer",
        "quality_reviewer": "azbrief-quality-reviewer",
    }
    assert resolved.has_complete_specialist_roster is True
    assert resolved.foundry_hosted_agent_name is None


def test_hosted_runtime_rejects_an_incomplete_specialist_roster(monkeypatch):
    settings = Settings(
        _env_file=None,
        azure_tenant_id="00000000-0000-0000-0000-000000000000",
        foundry_project_endpoint="https://demo.services.ai.azure.com/api/projects/azbrief",
        foundry_coordinator_agent_name="azbrief-coordinator",
    )
    monkeypatch.setattr(hosted_agent, "_analyzer", None)
    monkeypatch.setattr(hosted_agent, "get_hosted_settings", lambda: settings)

    with pytest.raises(RuntimeError, match="requires distinct coordinator"):
        hosted_agent.get_analysis_runtime()


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
async def test_execute_request_returns_bounded_evaluation_diagnostics():
    class FakeAnalyzer:
        async def analyze_update(self, update, trace_id=None):
            assert trace_id == "trace-eval"
            return _result()

        def get_last_run_diagnostics(self):
            return {
                "report_quality": {"weighted_score": 4.25, "critical_flaws": []},
                "trajectory": {"score": 94.0, "passed": True},
                "action_verification": {"blocked": 0, "passed": True},
            }

    request = HostedEvaluationRequest(update=_update(), trace_id="trace-eval")

    response = await execute_request(request.model_dump_json(), FakeAnalyzer())

    assert response.status == "completed"
    assert response.operation == "evaluate_update"
    assert response.result["analysis"]["one_line_summary"] == "Summary"
    assert response.result["diagnostics"]["report_quality"]["weighted_score"] == 4.25
    assert response.trace_id == "trace-eval"


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
