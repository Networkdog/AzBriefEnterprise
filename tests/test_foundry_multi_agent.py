"""Tests for the Microsoft Foundry Prompt Agent enrichment pipeline."""

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


def _stage_result(
    stage: str,
    text: str,
    *,
    evidence: list[str] | None = None,
    claim_id: str | None = None,
) -> str:
    if evidence is None:
        evidence = {
            "research": ["https://learn.microsoft.com/example"],
            "impact": ["resource:/subscriptions/sub/resourceGroups/rg/providers/type/name"],
            "action": ["research-1"],
        }[stage]
    return json.dumps(
        {
            "status": "ok",
            "claims": [
                {
                    "id": claim_id or f"{stage}-1",
                    "text": text,
                    "evidence": evidence,
                    "confidence": "high",
                }
            ],
            "gaps": [],
        }
    )


def _review_result(*, rejected: list[str] | None = None, missing: list[str] | None = None) -> str:
    rejected = rejected or []
    missing = missing or []
    return json.dumps(
        {
            "verdict": "revise" if rejected or missing else "pass",
            "rejected_claim_ids": rejected,
            "missing_facts": missing,
        }
    )


def _settings(**overrides) -> Settings:
    base = {
        "azure_tenant_id": _TENANT,
        "foundry_project_endpoint": _ENDPOINT,
        "foundry_primary_agent_name": "azbrief-primary",
        "foundry_enrichment_agents": _ROSTER,
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
        "azbrief-research": _stage_result(
            "research", "GA on 2026-09-01", evidence=["https://learn.microsoft.com/example"]
        ),
        "azbrief-impact": _stage_result(
            "impact",
            "3 storage accounts affected",
            evidence=[
                "resource:/subscriptions/sub/resourceGroups/rg/providers/Microsoft.Storage/storageAccounts/a"
            ],
        ),
        "azbrief-action": _stage_result(
            "action", "Verify minimumTlsVersion", evidence=["impact-1"]
        ),
    }

    async def fake_invoke(project_endpoint, agent_name, prompt, timeout_s):
        calls.append(
            {
                "endpoint": project_endpoint,
                "agent": agent_name,
                "prompt": prompt,
                "timeout": timeout_s,
            }
        )
        return answers.get(agent_name, "")

    monkeypatch.setattr(foundry_backend, "_invoke_foundry_agent", fake_invoke)
    return calls, answers


class TestNodeConstruction:
    def test_disabled_without_a_primary_agent(self, sdk_present):
        assert (
            foundry_backend.build_multi_agent_node(_settings(foundry_primary_agent_name=None))
            is None
        )

    def test_disabled_without_a_project_endpoint(self, sdk_present):
        assert (
            foundry_backend.build_multi_agent_node(_settings(foundry_project_endpoint=None)) is None
        )

    def test_disabled_with_an_empty_roster(self, sdk_present):
        assert (
            foundry_backend.build_multi_agent_node(_settings(foundry_enrichment_agents=None))
            is None
        )

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
        assert "GA on 2026-09-01" in action_prompt
        assert "3 storage accounts affected" in action_prompt
        assert "research-1" in action_prompt
        assert "impact-1" in action_prompt

    @pytest.mark.asyncio
    async def test_a_failing_stage_is_isolated(self, sdk_present, monkeypatch):
        async def fake_invoke(endpoint, agent, prompt, timeout_s):
            if agent == "azbrief-impact":
                raise RuntimeError("agent unavailable")
            stage = agent.rsplit("-", 1)[-1]
            evidence = ["research-1"] if stage == "action" else None
            return _stage_result(stage, f"output from {agent}", evidence=evidence)

        monkeypatch.setattr(foundry_backend, "_invoke_foundry_agent", fake_invoke)
        node = foundry_backend.build_multi_agent_node(_settings())
        merged = (await node({"update_context": "ctx"}))["update_context"]

        assert "Research findings" in merged
        assert "Tenant impact assessment" not in merged
        assert "Proposed actions" in merged

    @pytest.mark.asyncio
    async def test_all_stages_empty_leaves_state_untouched(self, sdk_present, monkeypatch):
        async def fake_invoke(*args, **kwargs):
            return ""

        monkeypatch.setattr(foundry_backend, "_invoke_foundry_agent", fake_invoke)
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

        async def fake_invoke(endpoint, agent, prompt, timeout_s):
            return (
                _review_result()
                if agent == "azbrief-review"
                else _stage_result("research", "a fact")
            )

        monkeypatch.setattr(foundry_backend, "_invoke_foundry_agent", fake_invoke)
        node = foundry_backend.build_multi_agent_node(_settings(foundry_enrichment_agents=roster))
        merged = (await node({"update_context": "ctx"}))["update_context"]

        assert "Research findings" in merged
        assert "Review notes" not in merged

    @pytest.mark.asyncio
    async def test_review_removes_rejected_claim_and_dependent_action(
        self, sdk_present, monkeypatch
    ):
        roster = json.dumps(
            [
                {"name": "azbrief-research", "stage": "research"},
                {"name": "azbrief-impact", "stage": "impact"},
                {"name": "azbrief-action", "stage": "action"},
                {"name": "azbrief-review", "stage": "review"},
            ]
        )

        async def fake_invoke(endpoint, agent, prompt, timeout_s):
            if agent == "azbrief-research":
                return json.dumps(
                    {
                        "status": "ok",
                        "claims": [
                            {
                                "id": "research-1",
                                "text": "supported fact",
                                "evidence": ["https://learn.microsoft.com/supported"],
                                "confidence": "high",
                            },
                            {
                                "id": "research-2",
                                "text": "unsupported fact",
                                "evidence": ["https://learn.microsoft.com/unsupported"],
                                "confidence": "low",
                            },
                        ],
                        "gaps": [],
                    }
                )
            if agent == "azbrief-impact":
                return _stage_result("impact", "tenant evidence")
            if agent == "azbrief-action":
                return _stage_result(
                    "action", "action based on unsupported fact", evidence=["research-2"]
                )
            return _review_result(rejected=["research-2"], missing=["Confirm the effective date"])

        monkeypatch.setattr(foundry_backend, "_invoke_foundry_agent", fake_invoke)
        node = foundry_backend.build_multi_agent_node(_settings(foundry_enrichment_agents=roster))
        merged = (await node({"update_context": "ctx"}))["update_context"]

        assert "supported fact" in merged
        assert "unsupported fact" not in merged
        assert "action based on unsupported fact" not in merged
        assert "Confirm the effective date" in merged

    @pytest.mark.asyncio
    async def test_malformed_stage_output_is_excluded(self, sdk_present, monkeypatch):
        async def fake_invoke(*args, **kwargs):
            return "- legacy free text"

        monkeypatch.setattr(foundry_backend, "_invoke_foundry_agent", fake_invoke)
        node = foundry_backend.build_multi_agent_node(_settings())

        assert await node({"update_context": "ctx"}) == {}

    @pytest.mark.asyncio
    async def test_claim_without_evidence_is_excluded(self, sdk_present, monkeypatch):
        async def fake_invoke(*args, **kwargs):
            return json.dumps(
                {
                    "status": "ok",
                    "claims": [
                        {
                            "id": "research-1",
                            "text": "unsupported",
                            "evidence": [],
                            "confidence": "high",
                        }
                    ],
                    "gaps": [],
                }
            )

        monkeypatch.setattr(foundry_backend, "_invoke_foundry_agent", fake_invoke)
        roster = json.dumps([{"name": "azbrief-research", "stage": "research"}])
        node = foundry_backend.build_multi_agent_node(_settings(foundry_enrichment_agents=roster))

        assert await node({"update_context": "ctx"}) == {}

    @pytest.mark.asyncio
    async def test_action_with_unknown_claim_dependency_is_excluded(self, sdk_present, monkeypatch):
        async def fake_invoke(endpoint, agent, prompt, timeout_s):
            if agent == "azbrief-research":
                return _stage_result("research", "supported")
            return _stage_result("action", "orphan action", evidence=["impact-999"])

        monkeypatch.setattr(foundry_backend, "_invoke_foundry_agent", fake_invoke)
        roster = json.dumps(
            [
                {"name": "azbrief-research", "stage": "research"},
                {"name": "azbrief-action", "stage": "action"},
            ]
        )
        node = foundry_backend.build_multi_agent_node(_settings(foundry_enrichment_agents=roster))
        merged = (await node({"update_context": "ctx"}))["update_context"]

        assert "supported" in merged
        assert "orphan action" not in merged

    @pytest.mark.asyncio
    async def test_extra_instructions_are_appended(self, sdk_present, recorded_calls):
        calls, _ = recorded_calls
        roster = json.dumps(
            [{"name": "azbrief-research", "stage": "research", "instructions": "Focus on Korea."}]
        )
        node = foundry_backend.build_multi_agent_node(_settings(foundry_enrichment_agents=roster))
        await node({"update_context": "ctx"})
        assert calls[0]["prompt"].endswith("Focus on Korea.")

    @pytest.mark.asyncio
    async def test_configured_timeout_reaches_the_agent_call(self, sdk_present, recorded_calls):
        calls, _ = recorded_calls
        node = foundry_backend.build_multi_agent_node(_settings(foundry_agent_timeout_s=42))
        await node({"update_context": "ctx"})
        assert all(call["timeout"] == 42 for call in calls)
