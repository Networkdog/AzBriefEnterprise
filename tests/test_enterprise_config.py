"""Tests for the enterprise configuration surface.

Covers the settings that drive the Foundry multi-agent pipeline, the durable
checkpoint, and the admin console.
"""

import json
import os

import pytest

from src.config import FOUNDRY_AGENT_STAGES, FoundryAgentSpec, Settings

_TENANT = "00000000-0000-0000-0000-000000000000"

# Settings reads os.environ even with _env_file=None, so a developer's real
# .env-derived environment would otherwise leak into these assertions.
_ISOLATED_PREFIXES = (
    "COMMUNICATION_SERVICES_",
    "EMAIL_",
    "SUBSCRIBERS",
    "ADMIN_",
    "FOUNDRY_",
    "ORCHESTRATOR_",
    "LLM_BACKEND",
)


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    for key in list(os.environ):
        if key.upper().startswith(_ISOLATED_PREFIXES):
            monkeypatch.delenv(key, raising=False)


def _settings(**overrides) -> Settings:
    """Build Settings without reading the repo's .env."""
    base = {"azure_tenant_id": _TENANT}
    base.update(overrides)
    return Settings(_env_file=None, **base)


class TestBackendSelection:
    """llm_backend validation and the Foundry readiness flag."""

    def test_defaults_to_foundry(self):
        assert _settings().llm_backend == "foundry"

    def test_foundry_requires_an_endpoint(self):
        # Asking for Foundry without a project must degrade, not crash the run.
        assert _settings().use_foundry is False

    def test_foundry_is_active_once_configured(self):
        settings = _settings(
            foundry_project_endpoint="https://r.services.ai.azure.com/api/projects/p"
        )
        assert settings.use_foundry is True

    def test_openai_backend_never_activates_foundry(self):
        settings = _settings(
            llm_backend="openai",
            foundry_project_endpoint="https://r.services.ai.azure.com/api/projects/p",
        )
        assert settings.use_foundry is False

    def test_unknown_backend_is_rejected(self):
        # A typo must fail loudly rather than silently running the wrong backend.
        with pytest.raises(ValueError, match="llm_backend"):
            _settings(llm_backend="bedrock")


class TestFoundryAgentRoster:
    """FOUNDRY_AGENTS parsing for the hosted multi-agent pipeline."""

    def test_empty_by_default(self):
        assert _settings().get_foundry_agents() == []

    def test_parses_roster(self):
        roster = json.dumps(
            [
                {"name": "azbrief-research", "stage": "research"},
                {"name": "azbrief-impact", "stage": "impact"},
                {"name": "azbrief-action", "stage": "action"},
            ]
        )
        agents = _settings(foundry_agents=roster).get_foundry_agents()
        assert {a.stage for a in agents} == {"research", "impact", "action"}
        assert all(a.version == "latest" for a in agents)

    def test_last_entry_wins_per_stage(self):
        roster = json.dumps(
            [
                {"name": "old", "stage": "research"},
                {"name": "new", "stage": "research"},
            ]
        )
        agents = _settings(foundry_agents=roster).get_foundry_agents()
        assert [a.name for a in agents] == ["new"]

    def test_unknown_stage_entry_is_skipped(self):
        # A bad entry must not take the whole roster down with it.
        roster = json.dumps(
            [
                {"name": "bad", "stage": "summarize"},
                {"name": "good", "stage": "impact"},
            ]
        )
        agents = _settings(foundry_agents=roster).get_foundry_agents()
        assert [a.name for a in agents] == ["good"]

    @pytest.mark.parametrize("raw", ["not json", "{}", '"a string"', "[1, 2, 3]"])
    def test_malformed_roster_degrades_to_empty(self, raw: str):
        assert _settings(foundry_agents=raw).get_foundry_agents() == []

    def test_stage_names_match_pipeline_order(self):
        assert FOUNDRY_AGENT_STAGES == ("research", "impact", "action", "review")

    def test_spec_rejects_unknown_stage(self):
        with pytest.raises(ValueError, match="stage"):
            FoundryAgentSpec(name="x", stage="nope")


class TestAdminAllowList:
    """ADMIN_ALLOWED_PRINCIPALS parsing — must fail closed."""

    def test_unset_denies_everyone(self):
        assert _settings().get_admin_allowed_principals() == set()

    def test_blank_string_denies_everyone(self):
        assert _settings(admin_allowed_principals="  , ,").get_admin_allowed_principals() == set()

    def test_entries_are_trimmed_and_lowercased(self):
        settings = _settings(admin_allowed_principals=" Admin@Co.COM , 1234-abcd ")
        assert settings.get_admin_allowed_principals() == {"admin@co.com", "1234-abcd"}

    def test_admin_ui_is_off_by_default(self):
        settings = _settings()
        assert settings.admin_ui_enabled is False
        assert settings.admin_require_auth is True


class TestEmailTransport:
    """use_email must accept managed-identity delivery, not only a connection string."""

    def test_connection_string_transport(self):
        settings = _settings(
            communication_services_connection_string="endpoint=https://x;accesskey=y",
            email_sender_address="DoNotReply@example.com",
            email_recipient_address="admin@example.com",
        )
        assert settings.use_email is True

    def test_endpoint_only_transport_is_enough(self):
        # The enterprise profile can run without any stored email secret.
        settings = _settings(
            communication_services_endpoint="https://acs.communication.azure.com",
            email_sender_address="DoNotReply@example.com",
            email_recipient_address="admin@example.com",
        )
        assert settings.use_email is True

    def test_no_transport_falls_back_to_console(self):
        settings = _settings(
            email_sender_address="DoNotReply@example.com",
            email_recipient_address="admin@example.com",
        )
        assert settings.use_email is False

    def test_missing_recipient_disables_email(self):
        settings = _settings(
            communication_services_endpoint="https://acs.communication.azure.com",
            email_sender_address="DoNotReply@example.com",
        )
        assert settings.use_email is False

    def test_empty_recipient_disables_email(self):
        """The template always defines these, so "" has to read as unset.

        Otherwise delivery is attempted against an empty address, the transport
        rejects it, and the console fallback is skipped — losing the digest.
        """
        settings = _settings(
            communication_services_connection_string="endpoint=https://x;accesskey=y",
            email_sender_address="DoNotReply@example.com",
            email_recipient_address="",
            subscribers="",
        )
        assert settings.use_email is False

    def test_empty_transport_disables_email(self):
        settings = _settings(
            communication_services_connection_string="",
            communication_services_endpoint="",
            email_sender_address="DoNotReply@example.com",
            email_recipient_address="admin@example.com",
        )
        assert settings.use_email is False
