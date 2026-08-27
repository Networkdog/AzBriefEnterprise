"""Tests for the Foundry agent provisioning script.

The script talks to a live project, so these cover the parts that must be right
before any network call: instructions derived from the runtime prompts, roster
resolution against Foundry settings, and per-agent fault isolation.
"""

from unittest.mock import MagicMock

import pytest

from scripts.provision_foundry_agents import (
    agent_instructions,
    provision,
    resolve_roster,
    resolve_runtime_roster,
    stage_instructions,
    validate_roster,
)
from src.config import FOUNDRY_AGENT_STAGES, LLM_ROLES, get_settings

_TENANT = "00000000-0000-0000-0000-000000000000"
_ENDPOINT = "https://r.services.ai.azure.com/api/projects/p"


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    for key in (
        "FOUNDRY_ENRICHMENT_AGENTS",
        "FOUNDRY_PROJECT_ENDPOINT",
        "FOUNDRY_MODEL_DEPLOYMENT",
        "FOUNDRY_PRIMARY_AGENT_NAME",
        "FOUNDRY_PLANNER_AGENT_NAME",
        "FOUNDRY_EVALUATOR_AGENT_NAME",
        "FOUNDRY_REPORTER_AGENT_NAME",
        "FOUNDRY_CODEX_AGENT_NAME",
        "FOUNDRY_FAST_AGENT_NAME",
    ):
        monkeypatch.setenv(key, "")
    monkeypatch.setenv("AZURE_TENANT_ID", _TENANT)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class TestStageInstructions:
    def test_every_stage_has_instructions(self):
        for stage in FOUNDRY_AGENT_STAGES:
            assert len(stage_instructions(stage)) > 100

    def test_runtime_placeholders_are_stripped(self):
        """A standing instruction must not carry the per-run template holes."""
        for stage in FOUNDRY_AGENT_STAGES:
            text = stage_instructions(stage)
            assert "{update_context}" not in text
            assert "{prior_findings}" not in text
            assert "Azure Update under analysis" not in text

    def test_instructions_are_a_prefix_of_the_runtime_prompt(self):
        """Derived, not duplicated — the two can never drift."""
        from src.agent.foundry_backend import STAGE_PROMPTS

        for stage in FOUNDRY_AGENT_STAGES:
            assert STAGE_PROMPTS[stage].startswith(stage_instructions(stage))


class TestRosterResolution:
    def test_defaults_to_all_four_stages(self):
        roster = resolve_roster(None)
        assert [stage for _, stage in roster] == list(FOUNDRY_AGENT_STAGES)
        assert [name for name, _ in roster] == [f"azbrief-{s}" for s in FOUNDRY_AGENT_STAGES]

    def test_configured_names_win(self, monkeypatch):
        """Provisioned names must match what the running app looks up."""
        monkeypatch.setenv(
            "FOUNDRY_ENRICHMENT_AGENTS",
            '[{"name":"acme-research","stage":"research"}]',
        )
        get_settings.cache_clear()
        names = dict((stage, name) for name, stage in resolve_roster(None))
        assert names["research"] == "acme-research"
        assert names["impact"] == "azbrief-impact"  # unconfigured stage keeps the default

    def test_subset_of_stages(self):
        assert [s for _, s in resolve_roster(["impact"])] == ["impact"]

    def test_primary_runtime_agent_is_required_by_configuration(self, monkeypatch):
        monkeypatch.setenv("FOUNDRY_PRIMARY_AGENT_NAME", "azbrief-primary")
        get_settings.cache_clear()
        assert resolve_runtime_roster(None) == [("azbrief-primary", "primary")]

    def test_distinct_runtime_agents_are_provisioned(self, monkeypatch):
        monkeypatch.setenv("FOUNDRY_PRIMARY_AGENT_NAME", "azbrief-primary")
        monkeypatch.setenv("FOUNDRY_PLANNER_AGENT_NAME", "azbrief-planner")
        monkeypatch.setenv("FOUNDRY_EVALUATOR_AGENT_NAME", "azbrief-evaluator")
        monkeypatch.setenv("FOUNDRY_REPORTER_AGENT_NAME", "azbrief-reporter")
        monkeypatch.setenv("FOUNDRY_CODEX_AGENT_NAME", "azbrief-codex")
        monkeypatch.setenv("FOUNDRY_FAST_AGENT_NAME", "azbrief-fast")
        get_settings.cache_clear()
        assert resolve_runtime_roster(None) == [
            ("azbrief-primary", "primary"),
            ("azbrief-planner", "planner"),
            ("azbrief-evaluator", "evaluator"),
            ("azbrief-reporter", "reporter"),
            ("azbrief-codex", "codex"),
            ("azbrief-fast", "fast"),
        ]

    def test_every_runtime_role_has_standing_instructions(self):
        for role in LLM_ROLES:
            assert len(agent_instructions(role)) > 100


class TestProvision:
    def test_without_endpoint_it_refuses(self, monkeypatch, capsys):
        monkeypatch.setattr(
            "scripts.provision_foundry_agents._client",
            lambda _endpoint: (_ for _ in ()).throw(
                AssertionError("missing endpoint must not create a live client")
            ),
        )
        assert provision([("a", "research")], "gpt-4o", dry_run=False, delete=False) == 1
        assert "FOUNDRY_PROJECT_ENDPOINT" in capsys.readouterr().out

    def test_dry_run_works_without_an_endpoint(self, capsys):
        """Reviewing the instructions must not require a provisioned project."""
        assert provision([("a", "research")], "gpt-4o", dry_run=True, delete=False) == 0
        assert "Dry run" in capsys.readouterr().out

    def test_dry_run_makes_no_client(self, monkeypatch, capsys):
        monkeypatch.setenv("FOUNDRY_PROJECT_ENDPOINT", _ENDPOINT)
        get_settings.cache_clear()

        def _boom(_endpoint):
            raise AssertionError("dry run must not build a client")

        monkeypatch.setattr("scripts.provision_foundry_agents._client", _boom)
        assert provision([("a", "research")], "gpt-4o", dry_run=True, delete=False) == 0
        assert "Dry run" in capsys.readouterr().out

    def test_creates_missing_and_updates_existing(self, monkeypatch):
        monkeypatch.setenv("FOUNDRY_PROJECT_ENDPOINT", _ENDPOINT)
        get_settings.cache_clear()

        # MagicMock(name=...) sets the mock's own name, not the attribute.
        existing = MagicMock(id="ag-1")
        existing.name = "azbrief-impact"
        client = MagicMock()
        client.list_agents.return_value = [existing]
        client.create_agent.return_value = MagicMock(id="ag-new")
        monkeypatch.setattr("scripts.provision_foundry_agents._client", lambda _e: client)

        code = provision(
            [("azbrief-research", "research"), ("azbrief-impact", "impact")],
            "gpt-4o",
            dry_run=False,
            delete=False,
        )
        assert code == 0
        assert client.create_agent.call_count == 1
        assert client.update_agent.call_count == 1
        assert client.create_agent.call_args.kwargs["name"] == "azbrief-research"
        assert client.update_agent.call_args.args[0] == "ag-1"

    def test_one_failure_does_not_abort_the_rest(self, monkeypatch, capsys):
        monkeypatch.setenv("FOUNDRY_PROJECT_ENDPOINT", _ENDPOINT)
        get_settings.cache_clear()

        client = MagicMock()
        client.list_agents.return_value = []
        client.create_agent.side_effect = [RuntimeError("quota"), MagicMock(id="ag-2")]
        monkeypatch.setattr("scripts.provision_foundry_agents._client", lambda _e: client)

        code = provision(
            [("a", "research"), ("b", "impact")], "gpt-4o", dry_run=False, delete=False
        )
        assert code == 1  # reported
        assert client.create_agent.call_count == 2  # but the second still ran
        assert "FAILED" in capsys.readouterr().out

    def test_delete_skips_absent_agents(self, monkeypatch):
        monkeypatch.setenv("FOUNDRY_PROJECT_ENDPOINT", _ENDPOINT)
        get_settings.cache_clear()

        client = MagicMock()
        present = MagicMock(id="ag-1")
        present.name = "a"
        client.list_agents.return_value = [present]
        monkeypatch.setattr("scripts.provision_foundry_agents._client", lambda _e: client)

        assert provision([("a", "research"), ("b", "impact")], "gpt-4o", False, True) == 0
        client.delete_agent.assert_called_once_with("ag-1")

    def test_rejects_one_agent_name_assigned_to_multiple_roles(self, monkeypatch, capsys):
        monkeypatch.setattr(
            "scripts.provision_foundry_agents._client",
            lambda _endpoint: (_ for _ in ()).throw(
                AssertionError("conflicting roster must not create a live client")
            ),
        )

        code = provision(
            [("shared", "primary"), ("shared", "research")],
            "gpt-4o",
            dry_run=False,
            delete=False,
        )

        assert code == 1
        assert "CONFLICT shared" in capsys.readouterr().out


class TestValidateRoster:
    def test_fails_for_missing_agent_and_required_tools(self, monkeypatch, capsys):
        monkeypatch.setenv("FOUNDRY_PROJECT_ENDPOINT", _ENDPOINT)
        get_settings.cache_clear()
        research = MagicMock()
        research.name = "azbrief-research"
        research.instructions = agent_instructions("research")
        research.tools = []
        client = MagicMock()
        client.list_agents.return_value = [research]
        monkeypatch.setattr("scripts.provision_foundry_agents._client", lambda _e: client)

        code = validate_roster([("azbrief-primary", "primary"), ("azbrief-research", "research")])

        assert code == 1
        output = capsys.readouterr().out
        assert "MISSING azbrief-primary" in output
        assert "NO-TOOL azbrief-research" in output

    def test_passes_with_current_instructions_and_required_tools(self, monkeypatch, capsys):
        monkeypatch.setenv("FOUNDRY_PROJECT_ENDPOINT", _ENDPOINT)
        get_settings.cache_clear()
        primary = MagicMock()
        primary.name = "azbrief-primary"
        primary.instructions = agent_instructions("primary")
        primary.tools = []
        research = MagicMock()
        research.name = "azbrief-research"
        research.instructions = agent_instructions("research")
        research.tools = [{"type": "web_search"}]
        client = MagicMock()
        client.list_agents.return_value = [primary, research]
        monkeypatch.setattr("scripts.provision_foundry_agents._client", lambda _e: client)

        assert (
            validate_roster([("azbrief-primary", "primary"), ("azbrief-research", "research")]) == 0
        )
        assert "roster check passed" in capsys.readouterr().out

    def test_fails_when_deployed_instructions_are_stale(self, monkeypatch, capsys):
        monkeypatch.setenv("FOUNDRY_PROJECT_ENDPOINT", _ENDPOINT)
        get_settings.cache_clear()
        agent = MagicMock()
        agent.name = "azbrief-primary"
        agent.instructions = "old instructions"
        agent.tools = []
        client = MagicMock()
        client.list_agents.return_value = [agent]
        monkeypatch.setattr("scripts.provision_foundry_agents._client", lambda _e: client)

        assert validate_roster([("azbrief-primary", "primary")]) == 1
        assert "STALE" in capsys.readouterr().out
