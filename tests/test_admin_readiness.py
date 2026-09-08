"""Tests for the Admin operational readiness checklist."""

import base64
import json
from copy import deepcopy

import pytest

from src.admin.readiness import AdminReadinessCollector
from src.config import Settings


def _settings(**overrides) -> Settings:
    values = {
        "azure_tenant_id": "tenant",
        "azure_subscription_id": "subscription",
        "azure_client_id": "identity",
        "foundry_project_endpoint": "https://account.services.ai.azure.com/api/projects/project",
        "foundry_hosted_agent_name": "hosted",
        "admin_readiness_resource_group": "rg-azbrief",
        "admin_readiness_foundry_account": "account",
        "admin_readiness_foundry_project": "project",
        "admin_readiness_foundry_model_deployment": "model",
        "admin_readiness_container_environments": json.dumps(["app-env", "mcp-env"]),
        "admin_readiness_container_apps": json.dumps(["app", "mcp"]),
        "admin_readiness_container_jobs": json.dumps(["job"]),
        "admin_readiness_prompt_agents": json.dumps(
            {
                "coordinator": "coordinator",
                "resource_graph": "resource-graph",
                "azure_mcp": "azure-mcp",
                "azure_api": "azure-api",
                "report_writer": "report-writer",
                "quality_reviewer": "quality-reviewer",
            }
        ),
        "admin_readiness_support_resources": json.dumps(
            [
                {
                    "id": "key_vault",
                    "label": "Key Vault",
                    "name": "vault",
                    "resource_type": "Microsoft.KeyVault/vaults",
                    "api_version": "2023-07-01",
                }
            ]
        ),
        "checkpoint_blob_url": "https://storage.test/state/checkpoint.json",
        "archive_file_path": "archive",
        "archive_ui_enabled": True,
        "communication_services_endpoint": "https://acs.communication.azure.com",
        "email_sender_address": "sender@example.com",
        "api_key": "secret",
        "otel_enabled": True,
        "applicationinsights_connection_string": "InstrumentationKey=test",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def _resource(state="Succeeded", **properties):
    return {"properties": {"provisioningState": state, **properties}}


def test_readiness_settings_accept_base64_json():
    apps = ["app", "mcp"]
    agents = {"coordinator": "coord"}
    resources = [
        {
            "id": "vault",
            "name": "kv",
            "resource_type": "Microsoft.KeyVault/vaults",
            "api_version": "2023-07-01",
        }
    ]

    def encoded(value):
        return base64.b64encode(json.dumps(value).encode("utf-8")).decode("ascii")

    settings = _settings(
        admin_readiness_container_apps=encoded(apps),
        admin_readiness_prompt_agents=encoded(agents),
        admin_readiness_support_resources=encoded(resources),
    )

    assert settings.get_admin_readiness_container_apps() == apps
    assert settings.get_admin_readiness_prompt_agents() == agents
    assert settings.get_admin_readiness_support_resources() == resources


class Inventory:
    def __init__(self):
        self.closed = False
        self.arm = {
            "foundry_account": {"success": True, "data": _resource(), "error": ""},
            "foundry_project": {"success": True, "data": _resource(), "error": ""},
            "foundry_model_deployment": {
                "success": True,
                "data": {
                    "properties": {
                        "provisioningState": "Succeeded",
                        "model": {"name": "gpt-4o", "version": "2024-11-20"},
                    },
                    "sku": {"name": "GlobalStandard", "capacity": 200},
                },
                "error": "",
            },
            "environment:app-env": {"success": True, "data": _resource(), "error": ""},
            "environment:mcp-env": {"success": True, "data": _resource(), "error": ""},
            "app:app": {
                "success": True,
                "data": _resource(
                    runningStatus="Running",
                    latestRevisionName="app--1",
                    latestReadyRevisionName="app--1",
                    template={"containers": [{"image": "registry/app:v1"}]},
                ),
                "error": "",
            },
            "app:mcp": {
                "success": True,
                "data": _resource(
                    runningStatus="Running",
                    latestRevisionName="mcp--1",
                    latestReadyRevisionName="mcp--1",
                    template={"containers": [{"image": "registry/mcp:v1"}]},
                ),
                "error": "",
            },
            "job:job": {
                "success": True,
                "data": _resource(
                    configuration={
                        "triggerType": "Schedule",
                        "scheduleTriggerConfig": {"cronExpression": "0 2 * * *"},
                    },
                    template={"containers": [{"image": "registry/app:v1"}]},
                ),
                "error": "",
            },
            "support:key_vault": {
                "success": True,
                "data": _resource(),
                "error": "",
            },
        }
        self.agents = {
            name: {
                "version": "1",
                "kind": ("HostedAgentDefinition" if name == "hosted" else "PromptAgentDefinition"),
                "status": "AgentVersionStatus.ACTIVE",
            }
            for name in (
                "hosted",
                "coordinator",
                "resource-graph",
                "azure-mcp",
                "azure-api",
                "report-writer",
                "quality-reviewer",
            )
        }

    async def get_arm_resources(self, _requests):
        return deepcopy(self.arm)

    async def list_foundry_agents(self, _endpoint):
        return {"success": True, "data": deepcopy(self.agents), "error": ""}

    def close(self):
        self.closed = True


class Configuration:
    configured = True

    async def get_subscribers(self):
        return [object()]


@pytest.mark.asyncio
async def test_readiness_report_is_green_when_every_dependency_is_ready():
    inventory = Inventory()
    report = await AdminReadinessCollector(
        settings=_settings(),
        inventory=inventory,
        configuration=Configuration(),
        runtime_ready=lambda: True,
    ).collect()

    assert report.ok is True
    assert report.ready == report.total
    assert [section.id for section in report.sections] == [
        "foundry",
        "container_apps",
        "azure_services",
        "runtime",
    ]
    assert inventory.closed is True


@pytest.mark.asyncio
async def test_readiness_report_keeps_partial_failures_actionable():
    inventory = Inventory()
    inventory.agents.pop("quality-reviewer")
    inventory.arm["app:app"]["data"]["properties"]["runningStatus"] = "Stopped"

    report = await AdminReadinessCollector(
        settings=_settings(),
        inventory=inventory,
        configuration=Configuration(),
        runtime_ready=lambda: True,
    ).collect()
    checks = {check.id: check for section in report.sections for check in section.checks}

    assert report.ok is False
    assert checks["prompt_agent_quality_reviewer"].ok is False
    assert "quality_reviewer" in checks["prompt_agent_quality_reviewer"].action
    assert checks["container_app_app"].ok is False
    assert "Stopped" in checks["container_app_app"].detail
    assert all(check.action for check in checks.values() if not check.ok)


@pytest.mark.asyncio
async def test_readiness_rejects_wrong_agent_kind_and_inactive_version():
    inventory = Inventory()
    inventory.agents["hosted"]["kind"] = "PromptAgentDefinition"
    inventory.agents["coordinator"]["status"] = "AgentVersionStatus.DRAFT"

    report = await AdminReadinessCollector(
        settings=_settings(),
        inventory=inventory,
        configuration=Configuration(),
        runtime_ready=lambda: True,
    ).collect()
    checks = {check.id: check for section in report.sections for check in section.checks}

    assert checks["hosted_agent"].ok is False
    assert checks["prompt_agent_coordinator"].ok is False
    assert "PromptAgentDefinition" in checks["hosted_agent"].detail
    assert "AgentVersionStatus.DRAFT" in checks["prompt_agent_coordinator"].detail


@pytest.mark.asyncio
async def test_inventory_errors_do_not_abort_the_report():
    class FailingInventory(Inventory):
        async def get_arm_resources(self, _requests):
            raise RuntimeError("offline")

        async def list_foundry_agents(self, _endpoint):
            raise RuntimeError("offline")

    report = await AdminReadinessCollector(
        settings=_settings(),
        inventory=FailingInventory(),
        configuration=Configuration(),
        runtime_ready=lambda: True,
    ).collect()

    assert report.ok is False
    assert report.total > 0
    assert any(
        "RuntimeError" in check.detail for section in report.sections for check in section.checks
    )
