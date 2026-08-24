"""Tests for the Foundry agent provisioning script.

The script talks to a live project, so these cover the parts that must be right
before any network call: instructions derived from the runtime prompts, roster
resolution against FOUNDRY_AGENTS, and per-agent fault isolation.
"""

from unittest.mock import MagicMock

import pytest

from scripts.provision_foundry_agents import provision, resolve_roster, stage_instructions
from src.config import FOUNDRY_AGENT_STAGES, get_settings

_TENANT = "00000000-0000-0000-0000-000000000000"
_ENDPOINT = "https://r.services.ai.azure.com/api/projects/p"


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    for key in ("FOUNDRY_AGENTS", "FOUNDRY_PROJECT_ENDPOINT", "FOUNDRY_MODEL_DEPLOYMENT"):
        monkeypatch.delenv(key, raising=False)
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
        monkeypatch.setenv("FOUNDRY_AGENTS", '[{"name":"acme-research","stage":"research"}]')
        get_settings.cache_clear()
        names = dict((stage, name) for name, stage in resolve_roster(None))
        assert names["research"] == "acme-research"
        assert names["impact"] == "azbrief-impact"  # unconfigured stage keeps the default

    def test_subset_of_stages(self):
        assert [s for _, s in resolve_roster(["impact"])] == ["impact"]


class TestProvision:
    def test_without_endpoint_it_refuses(self, capsys):
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
