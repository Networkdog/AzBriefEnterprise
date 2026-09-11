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


def test_analysis_concurrency_must_be_positive():
    with pytest.raises(ValueError):
        _settings(max_concurrent_analyses=0)


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
    def test_bootstrap_image_uses_its_actual_port_and_health_path(self):
        template = json.loads(
            Path("infra/azbrief-enterprise-deploy.json").read_text(encoding="utf-8")
        )
        app = next(
            resource
            for resource in template["resources"]
            if resource["type"].lower() == "microsoft.app/containerapps"
        )

        assert template["variables"]["containerPort"] == (
            "[if(variables('isBootstrapImage'), 80, 8000)]"
        )
        assert app["properties"]["configuration"]["ingress"]["targetPort"] == (
            "[variables('containerPort')]"
        )
        probes = app["properties"]["template"]["containers"][0]["probes"]
        assert len(probes) == 2
        for probe in probes:
            assert probe["httpGet"]["port"] == "[variables('containerPort')]"
            assert probe["httpGet"]["path"] == (
                "[if(variables('isBootstrapImage'), '/', '/health')]"
            )

    def test_scheduling_is_opt_in_and_cannot_run_the_bootstrap_image(self):
        template = json.loads(
            Path("infra/azbrief-enterprise-deploy.json").read_text(encoding="utf-8")
        )
        job = next(
            resource
            for resource in template["resources"]
            if resource["type"].lower() == "microsoft.app/jobs"
        )
        configuration = job["properties"]["configuration"]

        assert template["parameters"]["enableScheduledRuns"]["defaultValue"] is False
        assert template["variables"]["scheduledRunsEnabled"] == (
            "[and(parameters('enableScheduledRuns'), not(variables('isBootstrapImage')))]"
        )
        assert configuration["triggerType"] == (
            "[if(variables('scheduledRunsEnabled'), 'Schedule', 'Manual')]"
        )
        assert "variables('scheduledRunsEnabled')" in configuration["manualTriggerConfig"]
        assert "variables('scheduledRunsEnabled')" in configuration["scheduleTriggerConfig"]
        assert configuration["replicaRetryLimit"] == 0

    def test_initial_concurrency_and_short_job_budget_are_bounded(self):
        template = json.loads(
            Path("infra/azbrief-enterprise-deploy.json").read_text(encoding="utf-8")
        )

        assert template["parameters"]["maxConcurrentAnalyses"]["defaultValue"] == 1
        assert template["variables"]["runTimeBudgetSeconds"] == (
            "[max(60, sub(parameters('jobReplicaTimeoutSeconds'), 3600))]"
        )
        for resource in template["resources"]:
            if resource["type"].lower() not in {
                "microsoft.app/containerapps",
                "microsoft.app/jobs",
            }:
                continue
            environment = resource["properties"]["template"]["containers"][0]["env"]
            setting = next(
                item for item in environment if item["name"] == "MAX_CONCURRENT_ANALYSES"
            )
            assert setting["value"] == "[string(parameters('maxConcurrentAnalyses'))]"

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
        assert "endpoint: ${AZURE_AI_PROJECT_ENDPOINT}" in manifest
        assert "name: ${FOUNDRY_HOSTED_AGENT_NAME}" in manifest
        assert ".services.ai.azure.com" not in manifest

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
        assert "setup_customer.ps1" in command
        assert "-Stage Configure" in command
        assert "azd env set AZURE_SUBSCRIPTION_ID=" not in command
        setup = outputs["customerSetup"]["value"]
        assert setup["schemaVersion"] == 1
        assert set(setup["specialistAgentNames"]) == {
            "coordinator",
            "resourceGraph",
            "azureMcp",
            "azureApi",
            "reportWriter",
            "qualityReviewer",
        }
        assert "foundryProjectId" in setup
        assert "tenantId" in setup
        assert "hosted-agent-principal-id" in outputs["grantReaderCommand"]["value"]
        assert "managedIdentity" not in outputs["grantReaderCommand"]["value"]
        for forbidden in ("secret", "password", "connectionstring", "subscribers", "apikey"):
            assert forbidden not in json.dumps(setup).lower()

    def test_compiled_template_selects_the_acr_pull_role_for_its_permission_mode(self):
        template = json.loads(
            Path("infra/azbrief-enterprise-deploy.json").read_text(encoding="utf-8")
        )

        mode = template["parameters"]["containerRegistryRoleAssignmentMode"]
        assert mode["defaultValue"] == "AbacRepositoryPermissions"
        assert set(mode["allowedValues"]) == {
            "AbacRepositoryPermissions",
            "LegacyRegistryPermissions",
        }
        role = template["variables"]["containerRegistryPullRoleName"]
        serialized_role = json.dumps(role)
        assert "Container Registry Repository Reader" in serialized_role
        assert "AcrPull" in serialized_role
        command = json.dumps(template["outputs"]["grantAcrPullCommand"]["value"])
        assert "containerRegistryPullRoleName" in command

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

    def test_compiled_template_wires_mutable_admin_configuration(self):
        template = json.loads(
            Path("infra/azbrief-enterprise-deploy.json").read_text(encoding="utf-8")
        )
        serialized = json.dumps(template)

        assert serialized.count('"name": "ADMIN_CONFIG_BLOB_URL"') == 2
        assert "admin-config.json" in serialized
        assert "adminConfigBlobUrl" in template["outputs"]

    def test_compiled_template_wires_public_feedback_collection(self):
        template = json.loads(
            Path("infra/azbrief-enterprise-deploy.json").read_text(encoding="utf-8")
        )
        serialized = json.dumps(template)

        assert template["parameters"]["feedbackRecipientAddress"]["defaultValue"] == ""
        for name in (
            "FEEDBACK_UI_ENABLED",
            "FEEDBACK_BASE_URL",
            "FEEDBACK_RECIPIENT_ADDRESS",
        ):
            assert serialized.count(f'"name": "{name}"') == 2
        assert "feedbackPageUrl" in template["outputs"]

    def test_compiled_template_allows_same_origin_admin_posts(self):
        template = json.loads(
            Path("infra/azbrief-enterprise-deploy.json").read_text(encoding="utf-8")
        )
        auth = next(
            resource
            for resource in template["resources"]
            if resource["type"].lower() == "microsoft.app/containerapps/authconfigs"
        )

        allowed_redirects = auth["properties"]["login"]["allowedExternalRedirectUrls"]
        assert len(allowed_redirects) == 1
        assert "https://" in allowed_redirects[0]
        assert "Microsoft.App/containerApps" in allowed_redirects[0]
        assert ".configuration.ingress.fqdn" in allowed_redirects[0]
        assert auth["properties"]["globalValidation"]["unauthenticatedClientAction"] == (
            "AllowAnonymous"
        )

    def test_compiled_template_wires_admin_readiness_inventory(self):
        template = json.loads(
            Path("infra/azbrief-enterprise-deploy.json").read_text(encoding="utf-8")
        )
        serialized = json.dumps(template)
        names = (
            "ADMIN_READINESS_RESOURCE_GROUP",
            "ADMIN_READINESS_FOUNDRY_ACCOUNT",
            "ADMIN_READINESS_FOUNDRY_PROJECT",
            "ADMIN_READINESS_FOUNDRY_MODEL_DEPLOYMENT",
            "ADMIN_READINESS_CONTAINER_ENVIRONMENTS",
            "ADMIN_READINESS_CONTAINER_APPS",
            "ADMIN_READINESS_CONTAINER_JOBS",
            "ADMIN_READINESS_PROMPT_AGENTS",
            "ADMIN_READINESS_SUPPORT_RESOURCES",
        )

        assert all(serialized.count(f'"name": "{name}"') == 2 for name in names)
        variables = template["variables"]
        assert "azureMcpContainerAppName" in variables["azureMcpContainerEnvName"]
        assert set(variables["adminReadinessPromptAgents"]) == set(SPECIALIST_AGENT_ROLES)
        support_ids = {resource["id"] for resource in variables["adminReadinessSupportResources"]}
        assert support_ids == {
            "managed_identity",
            "key_vault",
            "state_storage",
            "evaluation_storage",
            "log_analytics",
            "application_insights",
            "communication_services",
            "email_service",
        }
        readiness_env_values = [
            item["value"]
            for resource in template["resources"]
            if resource["type"].lower()
            in {
                "microsoft.app/containerapps",
                "microsoft.app/jobs",
            }
            for container in resource["properties"]["template"]["containers"]
            for item in container["env"]
            if item["name"]
            in {
                "ADMIN_READINESS_CONTAINER_ENVIRONMENTS",
                "ADMIN_READINESS_CONTAINER_APPS",
                "ADMIN_READINESS_CONTAINER_JOBS",
                "ADMIN_READINESS_PROMPT_AGENTS",
                "ADMIN_READINESS_SUPPORT_RESOURCES",
            }
        ]
        assert len(readiness_env_values) == 10
        assert all("base64(string(" in value for value in readiness_env_values)


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

    def test_readiness_inventory_parses_json_environment_values(self, monkeypatch):
        monkeypatch.setenv("ADMIN_READINESS_CONTAINER_APPS", '["app","mcp"]')
        monkeypatch.setenv(
            "ADMIN_READINESS_PROMPT_AGENTS",
            '{"coordinator":"coord","quality_reviewer":"review"}',
        )
        monkeypatch.setenv(
            "ADMIN_READINESS_SUPPORT_RESOURCES",
            '[{"id":"vault","name":"kv","resource_type":"Microsoft.KeyVault/vaults","api_version":"2023-07-01"}]',
        )

        settings = _settings()

        assert settings.get_admin_readiness_container_apps() == ["app", "mcp"]
        assert settings.get_admin_readiness_prompt_agents() == {
            "coordinator": "coord",
            "quality_reviewer": "review",
        }
        assert settings.get_admin_readiness_support_resources()[0]["id"] == "vault"


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
