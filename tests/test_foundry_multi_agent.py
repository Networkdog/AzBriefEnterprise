"""Tests for the Microsoft Foundry hosted multi-agent pipeline."""

import json

import pytest

from src.agent import foundry_backend
from src.config import Settings

_TENANT = "00000000-0000-0000-0000-000000000000"
_ENDPOINT = "https://demo.services.ai.azure.com/api/projects/azbrief"

_ROSTER = json.dumps(
    [
        {"name": "azbrief-research", "stage": "research"},
        {"name": "azbrief-impact", "stage": "impact"},
        {"name": "azbrief-action", "stage": "action"},
    ]
)


def _settings(**overrides) -> Settings:
    base = {
        "azure_tenant_id": _TENANT,
        "llm_backend": "foundry",
        "foundry_project_endpoint": _ENDPOINT,
        "foundry_agents": _ROSTER,
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)


@pytest.fixture
def sdk_present(monkeypatch):
    """Pretend the optional 'foundry' extra is installed."""
    monkeypatch.setattr(foundry_backend, "foundry_available", lambda: True)


@pytest.fixture
def recorded_calls(monkeypatch):
    """Capture hosted-agent invocations and return canned answers."""
    calls: list[dict] = []
    answers: dict[str, str] = {
        "azbrief-research": "- GA on 2026-09-01",
        "azbrief-impact": "- 3 storage accounts affected",
        "azbrief-action": "- Verify minimumTlsVersion",
    }

    async def fake_invoke(project_endpoint, agent_name, version, prompt, timeout_s):
        calls.append(
            {
                "endpoint": project_endpoint,
                "agent": agent_name,
                "version": version,
                "prompt": prompt,
                "timeout": timeout_s,
            }
        )
        return answers.get(agent_name, "")

    monkeypatch.setattr(foundry_backend, "_invoke_hosted_agent", fake_invoke)
    return calls, answers


class TestNodeConstruction:
    def test_disabled_for_the_openai_backend(self, sdk_present):
        assert foundry_backend.build_multi_agent_node(_settings(llm_backend="openai")) is None

    def test_disabled_without_a_project_endpoint(self, sdk_present):
        assert (
            foundry_backend.build_multi_agent_node(_settings(foundry_project_endpoint=None)) is None
        )

    def test_disabled_with_an_empty_roster(self, sdk_present):
        assert foundry_backend.build_multi_agent_node(_settings(foundry_agents=None)) is None

    def test_disabled_when_the_sdk_is_missing(self, monkeypatch):
        # Missing optional extra must degrade, never raise.
        monkeypatch.setattr(foundry_backend, "foundry_available", lambda: False)
        assert foundry_backend.build_multi_agent_node(_settings()) is None

    def test_enabled_with_a_valid_roster(self, sdk_present):
        assert foundry_backend.build_multi_agent_node(_settings()) is not None


class TestPipelineExecution:
    @pytest.mark.asyncio
    async def test_every_stage_contributes_a_labelled_section(self, sdk_present, recorded_calls):
        calls, _ = recorded_calls
        node = foundry_backend.build_multi_agent_node(_settings())
        result = await node({"update_context": "Azure Update: TLS 1.0 retirement"})

        merged = result["update_context"]
        assert merged.startswith("Azure Update: TLS 1.0 retirement")
        assert foundry_backend.MULTI_AGENT_HEADER in merged
        assert "Research findings" in merged
        assert "Tenant impact assessment" in merged
        assert "Proposed actions" in merged
        assert [c["agent"] for c in calls] == [
            "azbrief-research",
            "azbrief-impact",
            "azbrief-action",
        ]

    @pytest.mark.asyncio
    async def test_dependent_stage_sees_the_earlier_findings(self, sdk_present, recorded_calls):
        calls, answers = recorded_calls
        node = foundry_backend.build_multi_agent_node(_settings())
        await node({"update_context": "ctx"})

        action_prompt = calls[-1]["prompt"]
        assert answers["azbrief-research"] in action_prompt
        assert answers["azbrief-impact"] in action_prompt

    @pytest.mark.asyncio
    async def test_a_failing_stage_is_isolated(self, sdk_present, monkeypatch):
        async def fake_invoke(endpoint, agent, version, prompt, timeout_s):
            if agent == "azbrief-impact":
                raise RuntimeError("agent unavailable")
            return f"- output from {agent}"

        monkeypatch.setattr(foundry_backend, "_invoke_hosted_agent", fake_invoke)
        node = foundry_backend.build_multi_agent_node(_settings())
        merged = (await node({"update_context": "ctx"}))["update_context"]

        assert "Research findings" in merged
        assert "Tenant impact assessment" not in merged
        assert "Proposed actions" in merged

    @pytest.mark.asyncio
    async def test_all_stages_empty_leaves_state_untouched(self, sdk_present, monkeypatch):
        async def fake_invoke(*args, **kwargs):
            return ""

        monkeypatch.setattr(foundry_backend, "_invoke_hosted_agent", fake_invoke)
        node = foundry_backend.build_multi_agent_node(_settings())
        assert await node({"update_context": "ctx"}) == {}

    @pytest.mark.asyncio
    async def test_clean_review_adds_no_noise(self, sdk_present, monkeypatch):
        roster = json.dumps(
            [
                {"name": "azbrief-research", "stage": "research"},
                {"name": "azbrief-review", "stage": "review"},
            ]
        )

        async def fake_invoke(endpoint, agent, version, prompt, timeout_s):
            return "NO ISSUES" if agent == "azbrief-review" else "- a fact"

        monkeypatch.setattr(foundry_backend, "_invoke_hosted_agent", fake_invoke)
        node = foundry_backend.build_multi_agent_node(_settings(foundry_agents=roster))
        merged = (await node({"update_context": "ctx"}))["update_context"]

        assert "Research findings" in merged
        assert "Review notes" not in merged

    @pytest.mark.asyncio
    async def test_extra_instructions_are_appended(self, sdk_present, recorded_calls):
        calls, _ = recorded_calls
        roster = json.dumps(
            [{"name": "azbrief-research", "stage": "research", "instructions": "Focus on Korea."}]
        )
        node = foundry_backend.build_multi_agent_node(_settings(foundry_agents=roster))
        await node({"update_context": "ctx"})
        assert calls[0]["prompt"].endswith("Focus on Korea.")

    @pytest.mark.asyncio
    async def test_configured_timeout_reaches_the_agent_call(self, sdk_present, recorded_calls):
        calls, _ = recorded_calls
        node = foundry_backend.build_multi_agent_node(_settings(foundry_agent_timeout_s=42))
        await node({"update_context": "ctx"})
        assert all(call["timeout"] == 42 for call in calls)
