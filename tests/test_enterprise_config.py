"""Tests for the enterprise configuration surface.

Covers the settings that drive the Foundry multi-agent pipeline, the durable
checkpoint, and the admin console.
"""

import json
import os
from pathlib import Path

import pytest

from src.config import (
    EVIDENCE_SPECIALIST_ROLES,
    SPECIALIST_AGENT_ROLES,
    FoundryAgentSpec,
    Settings,
)

_TENANT = "00000000-0000-0000-0000-000000000000"

# Settings reads os.environ even with _env_file=None, so a developer's real
# .env-derived environment would otherwise leak into these assertions.
_ISOLATED_PREFIXES = (
    "ARCHIVE_",
    "COMMUNICATION_SERVICES_",
    "EMAIL_",
    "SUBSCRIBERS",
    "ADMIN_",
    "FOUNDRY_",
    "ORCHESTRATOR_",
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


class TestFoundryReadiness:
    """Generic Foundry calls and Hosted specialist readiness are separate gates."""

    def test_foundry_requires_an_endpoint(self):
        assert _settings().use_foundry is False

    def test_foundry_requires_the_specialist_roster(self):
        settings = _settings(
            foundry_project_endpoint="https://r.services.ai.azure.com/api/projects/p"
        )
        assert settings.use_foundry is False

    def test_complete_specialist_roster_requires_six_distinct_agents(self):
        settings = _settings(
            foundry_project_endpoint="https://r.services.ai.azure.com/api/projects/p",
            foundry_coordinator_agent_name="coordinator",
            foundry_resource_graph_agent_name="resource-graph",
            foundry_azure_mcp_agent_name="azure-mcp",
            foundry_azure_api_agent_name="azure-api",
            foundry_report_writer_agent_name="report-writer",
            foundry_quality_reviewer_agent_name="quality-reviewer",
        )

        assert settings.has_complete_specialist_roster is True
        assert settings.use_foundry is True

    def test_duplicate_specialist_agent_names_are_not_complete(self):
        settings = _settings(
            foundry_project_endpoint="https://r.services.ai.azure.com/api/projects/p",
            foundry_coordinator_agent_name="shared",
            foundry_resource_graph_agent_name="shared",
            foundry_azure_mcp_agent_name="azure-mcp",
            foundry_azure_api_agent_name="azure-api",
            foundry_report_writer_agent_name="report-writer",
            foundry_quality_reviewer_agent_name="quality-reviewer",
        )

        assert settings.has_complete_specialist_roster is False

    def test_agent_names_are_trimmed_and_case_only_duplicates_are_rejected(self):
        settings = _settings(
            foundry_project_endpoint="https://r.services.ai.azure.com/api/projects/p",
            foundry_coordinator_agent_name=" Shared ",
            foundry_resource_graph_agent_name="shared",
            foundry_azure_mcp_agent_name="azure-mcp",
            foundry_azure_api_agent_name="azure-api",
            foundry_report_writer_agent_name="report-writer",
            foundry_quality_reviewer_agent_name="quality-reviewer",
        )

        assert settings.foundry_coordinator_agent_name == "Shared"
        assert settings.has_complete_specialist_roster is False

    def test_blank_specialist_agent_name_is_unconfigured(self):
        settings = _settings(foundry_coordinator_agent_name="   ")

        assert settings.foundry_coordinator_agent_name is None


class TestFoundryAgentRoster:
    """Explicit specialist fields produce one canonical roster."""

    def test_empty_by_default(self):
        assert _settings().get_foundry_specialist_agents() == []

    def test_roster_uses_canonical_role_order(self):
        settings = _settings(
            foundry_coordinator_agent_name="coordinator",
            foundry_resource_graph_agent_name="resource-graph",
            foundry_azure_mcp_agent_name="azure-mcp",
            foundry_azure_api_agent_name="azure-api",
            foundry_report_writer_agent_name="report-writer",
            foundry_quality_reviewer_agent_name="quality-reviewer",
        )
        agents = settings.get_foundry_specialist_agents()

        assert [agent.role for agent in agents] == list(SPECIALIST_AGENT_ROLES)
        assert [agent.name for agent in agents] == [
            "coordinator",
            "resource-graph",
            "azure-mcp",
            "azure-api",
            "report-writer",
            "quality-reviewer",
        ]

    def test_stage_names_match_pipeline_order(self):
        assert EVIDENCE_SPECIALIST_ROLES == ("resource_graph", "azure_mcp", "azure_api")

    def test_spec_rejects_unknown_role(self):
        with pytest.raises(ValueError, match="role"):
            FoundryAgentSpec(name="x", role="nope")


class TestSpecialistDeploymentContract:
    def test_hosted_manifest_carries_all_six_specialist_aliases(self):
        manifest = Path("azure.yaml").read_text(encoding="utf-8")
        aliases = (
            "AZBRIEF_PROMPT_COORDINATOR_AGENT_NAME",
            "AZBRIEF_PROMPT_RESOURCE_GRAPH_AGENT_NAME",
            "AZBRIEF_PROMPT_AZURE_MCP_AGENT_NAME",
            "AZBRIEF_PROMPT_AZURE_API_AGENT_NAME",
            "AZBRIEF_PROMPT_REPORT_WRITER_AGENT_NAME",
            "AZBRIEF_PROMPT_QUALITY_REVIEWER_AGENT_NAME",
        )

        assert all(manifest.count(f"- name: {name}") == 1 for name in aliases)
        assert "AZBRIEF_PROMPT_PRIMARY_AGENT_NAME" not in manifest
        assert "AZBRIEF_ENRICHMENT_AGENT_ROSTER" not in manifest

    def test_compiled_template_outputs_specialist_names_and_config_command(self):
        template = json.loads(
            Path("infra/azbrief-enterprise-deploy.json").read_text(encoding="utf-8")
        )

        outputs = template["outputs"]
        assert "foundrySpecialistAgentNames" in outputs
        assert "configureHostedAgentCommand" in outputs
        assert "foundryPrimaryAgentName" not in outputs
        assert "foundryEnrichmentAgentRoster" not in outputs
        command = outputs["configureHostedAgentCommand"]["value"]
        for role in (
            "COORDINATOR",
            "RESOURCE_GRAPH",
            "AZURE_MCP",
            "AZURE_API",
            "REPORT_WRITER",
            "QUALITY_REVIEWER",
        ):
            assert f"AZBRIEF_PROMPT_{role}_AGENT_NAME" in command

    def test_compiled_template_wires_the_private_archive(self):
        template = json.loads(
            Path("infra/azbrief-enterprise-deploy.json").read_text(encoding="utf-8")
        )
        serialized = json.dumps(template)
        assert "azbrief-archive" in serialized
        for name in (
            "ARCHIVE_BLOB_CONTAINER_URL",
            "ARCHIVE_BASE_URL",
            "ARCHIVE_UI_ENABLED",
            "ARCHIVE_ALLOWED_PRINCIPALS",
        ):
            assert serialized.count(f'"name": "{name}"') == 2
        assert "archivePageUrl" in template["outputs"]
        assert "archiveBlobContainerUrl" in template["outputs"]


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


class TestArchiveConfiguration:
    """Archive storage and reader access remain explicit and fail closed."""

    def test_archive_is_disabled_by_default(self):
        settings = _settings()
        assert settings.archive_enabled is False
        assert settings.archive_ui_enabled is False
        assert settings.archive_require_auth is True

    def test_blob_or_file_configures_the_archive(self):
        assert _settings(archive_file_path="data/archive").archive_enabled is True
        settings = _settings(
            archive_blob_container_url=("https://acct.blob.core.windows.net/azbrief-archive/")
        )
        assert settings.archive_enabled is True
        assert settings.archive_blob_container_url.endswith("azbrief-archive")

    @pytest.mark.parametrize(
        "url",
        (
            "http://acct.blob.core.windows.net/archive",
            "https://attacker.example/archive",
            "https://user@acct.blob.core.windows.net/archive",
            "https://acct.blob.core.windows.net/archive/nested",
            "https://acct.blob.core.windows.net/archive?sig=secret",
            "https://acct.blob.core.windows.net/archive#fragment",
        ),
    )
    def test_archive_urls_require_plain_https(self, url):
        with pytest.raises(ValueError, match="Azure Storage HTTPS container"):
            _settings(archive_blob_container_url=url)

    def test_archive_readers_include_admins_and_groups(self):
        settings = _settings(
            admin_allowed_principals="admin@co.com",
            archive_allowed_principals=" Group-OID , reader@co.com ",
        )
        assert settings.get_archive_allowed_principals() == {
            "admin@co.com",
            "group-oid",
            "reader@co.com",
        }


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
