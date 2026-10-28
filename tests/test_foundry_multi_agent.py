"""Tests for specialist Prompt Agent collaboration inside the Hosted Agent."""

import json

import pytest
from structlog.testing import capture_logs

from src.agent import foundry_backend
from src.config import EVIDENCE_SPECIALIST_ROLES, Settings

_TENANT = "00000000-0000-0000-0000-000000000000"
_SUBSCRIPTION = "11111111-1111-1111-1111-111111111111"
_ENDPOINT = "https://demo.services.ai.azure.com/api/projects/azbrief"


def _specialist_result(
    role: str,
    text: str,
    *,
    evidence: list[str] | None = None,
    gaps: list[str] | None = None,
) -> str:
    if evidence is None:
        evidence = {
            "resource_graph": [
                "query:Resources | where type =~ 'microsoft.storage/storageaccounts'"
            ],
            "azure_mcp": [
                "resource:/subscriptions/sub/resourceGroups/rg/providers/"
                "Microsoft.Storage/storageAccounts/a"
            ],
            "azure_api": ["cost:subscription/sub/2026-08"],
        }[role]
    return json.dumps(
        {
            "status": "ok",
            "claims": [
                {
                    "id": f"{role}-1",
                    "text": text,
                    "evidence": evidence,
                    "confidence": "high",
                }
            ],
            "gaps": gaps or [],
        }
    )


def _settings(**overrides) -> Settings:
    base = {
        "azure_tenant_id": _TENANT,
        "foundry_project_endpoint": _ENDPOINT,
        "foundry_coordinator_agent_name": "azbrief-coordinator",
        "foundry_resource_graph_agent_name": "azbrief-resource-graph",
        "foundry_azure_mcp_agent_name": "azbrief-azure-mcp",
        "foundry_azure_api_agent_name": "azbrief-azure-api",
        "foundry_report_writer_agent_name": "azbrief-report-writer",
        "foundry_quality_reviewer_agent_name": "azbrief-quality-reviewer",
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)


@pytest.fixture
def sdk_present(monkeypatch):
    monkeypatch.setattr(foundry_backend, "foundry_available", lambda: True)


@pytest.fixture
def recorded_calls(monkeypatch):
    calls: list[dict] = []
    answers = {
        "azbrief-resource-graph": _specialist_result(
            "resource_graph", "3 storage accounts match the retirement condition"
        ),
        "azbrief-azure-mcp": _specialist_result(
            "azure_mcp", "All matching storage accounts are available"
        ),
        "azbrief-azure-api": _specialist_result(
            "azure_api", "The matching service cost was measured over 30 days"
        ),
    }

    async def fake_invoke(project_endpoint, agent_name, prompt, timeout_s, **kwargs):
        calls.append(
            {
                "endpoint": project_endpoint,
                "agent": agent_name,
                "prompt": prompt,
                "timeout": timeout_s,
                **kwargs,
            }
        )
        return answers[agent_name]

    monkeypatch.setattr(foundry_backend, "_invoke_foundry_agent", fake_invoke)
    return calls


class TestNodeConstruction:
    def test_disabled_without_a_coordinator(self, sdk_present):
        settings = _settings(
            foundry_coordinator_agent_name=None,
        )
        assert foundry_backend.build_specialist_collaboration_node(settings) is None

    def test_disabled_without_a_project_endpoint(self, sdk_present):
        assert (
            foundry_backend.build_specialist_collaboration_node(
                _settings(foundry_project_endpoint=None)
            )
            is None
        )

    def test_disabled_when_one_evidence_specialist_is_missing(self, sdk_present):
        assert (
            foundry_backend.build_specialist_collaboration_node(
                _settings(foundry_azure_api_agent_name=None)
            )
            is None
        )

    def test_disabled_when_the_sdk_is_missing(self, monkeypatch):
        monkeypatch.setattr(foundry_backend, "foundry_available", lambda: False)
        assert foundry_backend.build_specialist_collaboration_node(_settings()) is None

    def test_enabled_with_complete_specialist_roster(self, sdk_present):
        assert foundry_backend.build_specialist_collaboration_node(_settings()) is not None


class TestSpecialistCollaboration:
    @pytest.mark.asyncio
    async def test_each_specialist_contributes_labelled_evidence(self, sdk_present, recorded_calls):
        node = foundry_backend.build_specialist_collaboration_node(_settings())
        with capture_logs() as logs:
            result = await node(
                {"update_context": "Azure Update: TLS retirement", "trace_id": "trace-1"}
            )

        merged = result["update_context"]
        assert merged.startswith("Azure Update: TLS retirement")
        assert foundry_backend.SPECIALIST_CONTEXT_HEADER in merged
        assert "Resource Graph KQL and result analysis" in merged
        assert "Azure MCP tenant analysis" in merged
        assert "ARM, Cost Management, and Billing analysis" in merged
        assert {call["agent"] for call in recorded_calls} == {
            "azbrief-resource-graph",
            "azbrief-azure-mcp",
            "azbrief-azure-api",
        }
        completed = [entry for entry in logs if entry["event"] == "foundry_specialist_completed"]
        assert {entry["role"] for entry in completed} == set(EVIDENCE_SPECIALIST_ROLES)
        assert all(entry["trace_id"] == "trace-1" for entry in completed)
        assert all(entry["status"] == "ok" for entry in completed)
        assert all(entry["claim_count"] == 1 for entry in completed)
        assert all(entry["gap_count"] == 0 for entry in completed)

    @pytest.mark.asyncio
    async def test_specialists_receive_only_their_tool_surfaces(self, sdk_present, monkeypatch):
        calls: dict[str, dict] = {}

        async def fake_invoke(endpoint, agent, prompt, timeout_s, **kwargs):
            role = agent.removeprefix("azbrief-").replace("-", "_")
            calls[role] = kwargs
            return _specialist_result(role, "grounded")

        tools = [
            type("Tool", (), {"name": "query_azure_resources"})(),
            type("Tool", (), {"name": "get_cost_by_service"})(),
            type("Tool", (), {"name": "dangerous_write"})(),
        ]
        monkeypatch.setattr(foundry_backend, "_invoke_foundry_agent", fake_invoke)
        node = foundry_backend.build_specialist_collaboration_node(_settings(), tools)

        await node({"update_context": "ctx", "trace_id": "trace-1"})

        assert set(calls["resource_graph"]["local_tools"]) == {"query_azure_resources"}
        assert set(calls["azure_api"]["local_tools"]) == {"get_cost_by_service"}
        assert "local_tools" not in calls["azure_mcp"]
        assert calls["resource_graph"]["task_id"] == "specialist:resource_graph"
        assert calls["azure_mcp"]["task_id"] == "specialist:azure_mcp"
        assert calls["azure_api"]["task_id"] == "specialist:azure_api"
        assert all(call["trace_id"] == "trace-1" for call in calls.values())

    @pytest.mark.asyncio
    async def test_azure_specialists_receive_exact_scope(self, sdk_present, recorded_calls):
        node = foundry_backend.build_specialist_collaboration_node(
            _settings(azure_subscription_id=_SUBSCRIPTION)
        )

        await node({"update_context": "ctx"})

        prompts = {call["agent"]: call["prompt"] for call in recorded_calls}
        for agent in ("azbrief-azure-mcp", "azbrief-azure-api"):
            assert f"Azure tenant ID: {_TENANT}" in prompts[agent]
            assert f"Azure subscription ID: {_SUBSCRIPTION}" in prompts[agent]
        assert "never pass the literal `default`" in prompts["azbrief-azure-mcp"]
        assert "Azure tenant ID:" not in prompts["azbrief-resource-graph"]

    @pytest.mark.asyncio
    async def test_scope_does_not_invent_a_subscription(self, sdk_present, recorded_calls):
        node = foundry_backend.build_specialist_collaboration_node(
            _settings(azure_subscription_id=None)
        )
        await node({"update_context": "ctx"})

        prompts = {call["agent"]: call["prompt"] for call in recorded_calls}
        assert "Azure subscription ID:" not in prompts["azbrief-azure-mcp"]
        assert "Azure subscription ID:" not in prompts["azbrief-azure-api"]

    @pytest.mark.asyncio
    async def test_failed_specialist_becomes_an_explicit_gap(self, sdk_present, monkeypatch):
        async def fake_invoke(endpoint, agent, prompt, timeout_s, **kwargs):
            role = agent.removeprefix("azbrief-").replace("-", "_")
            if role == "azure_mcp":
                raise RuntimeError("unavailable")
            return _specialist_result(role, "grounded")

        monkeypatch.setattr(foundry_backend, "_invoke_foundry_agent", fake_invoke)
        node = foundry_backend.build_specialist_collaboration_node(_settings())
        merged = (await node({"update_context": "ctx"}))["update_context"]

        assert "azure_mcp specialist failed: RuntimeError" in merged
        assert "Azure MCP tenant analysis [partial]" in merged
        assert "Resource Graph KQL and result analysis [ok]" in merged

    @pytest.mark.asyncio
    async def test_invalid_output_becomes_an_explicit_gap(self, sdk_present, monkeypatch):
        async def fake_invoke(endpoint, agent, prompt, timeout_s, **kwargs):
            role = agent.removeprefix("azbrief-").replace("-", "_")
            if role == "azure_api":
                return "- free text"
            return _specialist_result(role, "grounded")

        monkeypatch.setattr(foundry_backend, "_invoke_foundry_agent", fake_invoke)
        node = foundry_backend.build_specialist_collaboration_node(_settings())
        merged = (await node({"update_context": "ctx"}))["update_context"]

        assert "azure_api specialist returned invalid output" in merged
        assert "ARM, Cost Management, and Billing analysis [partial]" in merged

    @pytest.mark.asyncio
    async def test_ok_with_gaps_is_normalized_to_partial(self, sdk_present, monkeypatch):
        async def fake_invoke(endpoint, agent, prompt, timeout_s, **kwargs):
            role = agent.removeprefix("azbrief-").replace("-", "_")
            gaps = ["Cost scope could not be confirmed"] if role == "azure_api" else []
            return _specialist_result(role, "grounded", gaps=gaps)

        monkeypatch.setattr(foundry_backend, "_invoke_foundry_agent", fake_invoke)
        node = foundry_backend.build_specialist_collaboration_node(_settings())
        with capture_logs() as logs:
            merged = (await node({"update_context": "ctx"}))["update_context"]

        assert "ARM, Cost Management, and Billing analysis [partial]" in merged
        normalized = next(
            entry for entry in logs if entry["event"] == "foundry_specialist_output_normalized"
        )
        assert normalized["role"] == "azure_api"

    @pytest.mark.asyncio
    async def test_wrong_evidence_prefix_is_rejected(self, sdk_present, monkeypatch):
        async def fake_invoke(endpoint, agent, prompt, timeout_s, **kwargs):
            role = agent.removeprefix("azbrief-").replace("-", "_")
            evidence = ["https://example.com/not-tenant-evidence"] if role == "azure_mcp" else None
            return _specialist_result(role, "grounded", evidence=evidence)

        monkeypatch.setattr(foundry_backend, "_invoke_foundry_agent", fake_invoke)
        node = foundry_backend.build_specialist_collaboration_node(_settings())
        merged = (await node({"update_context": "ctx"}))["update_context"]

        assert "azure_mcp specialist returned invalid output" in merged

    @pytest.mark.asyncio
    async def test_leading_zero_claim_id_is_rejected(self, sdk_present, monkeypatch):
        async def fake_invoke(endpoint, agent, prompt, timeout_s, **kwargs):
            role = agent.removeprefix("azbrief-").replace("-", "_")
            payload = json.loads(_specialist_result(role, "grounded"))
            if role == "resource_graph":
                payload["claims"][0]["id"] = "resource_graph-01"
            return json.dumps(payload)

        monkeypatch.setattr(foundry_backend, "_invoke_foundry_agent", fake_invoke)
        node = foundry_backend.build_specialist_collaboration_node(_settings())
        merged = (await node({"update_context": "ctx"}))["update_context"]

        assert "resource_graph specialist returned invalid output" in merged

    @pytest.mark.asyncio
    async def test_configured_timeout_reaches_every_specialist(self, sdk_present, recorded_calls):
        node = foundry_backend.build_specialist_collaboration_node(
            _settings(foundry_agent_timeout_s=42)
        )
        await node({"update_context": "ctx"})

        assert len(recorded_calls) == len(EVIDENCE_SPECIALIST_ROLES)
        assert all(call["timeout"] == 42 for call in recorded_calls)
