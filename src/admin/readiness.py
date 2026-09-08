"""Operational readiness report for the Admin console."""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone
from typing import Any, Callable, Optional
from urllib.parse import quote

from pydantic import BaseModel, Field

from src.admin.configuration import AdminConfigurationManager, get_admin_configuration
from src.config import SPECIALIST_AGENT_ROLES, Settings, get_settings
from src.orchestrator import services_ready
from src.services.runtime_inventory import RuntimeInventoryService

_COGNITIVE_API_VERSION = "2025-06-01"
_MODEL_DEPLOYMENT_API_VERSION = "2024-10-01"
_CONTAINER_APPS_API_VERSION = "2025-01-01"
_RESOURCE_TYPE_RE = re.compile(r"^[A-Za-z0-9.]+/[A-Za-z0-9.]+$")
_ROLE_LABELS = {
    "coordinator": "Coordinator Prompt Agent",
    "resource_graph": "Resource Graph Prompt Agent",
    "azure_mcp": "Azure MCP Prompt Agent",
    "azure_api": "Azure API Prompt Agent",
    "report_writer": "Report Writer Prompt Agent",
    "quality_reviewer": "Quality Reviewer Prompt Agent",
}


class ReadinessCheck(BaseModel):
    """One actionable readiness assertion."""

    id: str
    name: str
    ok: bool
    detail: str
    action: str = ""


class ReadinessSection(BaseModel):
    """A group of related readiness assertions."""

    id: str
    title: str
    ok: bool
    checks: list[ReadinessCheck] = Field(default_factory=list)


class ReadinessReport(BaseModel):
    """Complete readiness checklist returned to the Admin console."""

    ok: bool
    ready: int
    total: int
    checked_at: datetime
    sections: list[ReadinessSection] = Field(default_factory=list)


def _check(
    check_id: str,
    name: str,
    ok: bool,
    detail: str,
    action: str = "",
) -> ReadinessCheck:
    return ReadinessCheck(
        id=check_id,
        name=name,
        ok=ok,
        detail=detail,
        action="" if ok else action,
    )


def _section(section_id: str, title: str, checks: list[ReadinessCheck]) -> ReadinessSection:
    return ReadinessSection(
        id=section_id,
        title=title,
        ok=bool(checks) and all(check.ok for check in checks),
        checks=checks,
    )


def _agent_is_active(agent: dict[str, str], expected_kind: str) -> bool:
    status = agent.get("status", "").rsplit(".", 1)[-1].casefold()
    return bool(agent.get("version") and agent.get("kind") == expected_kind and status == "active")


def _agent_detail(agent_name: str, agent: dict[str, str]) -> str:
    return (
        f"{agent_name or 'Not configured'} · v{agent.get('version') or 'none'} · "
        f"{agent.get('kind') or 'no kind'} · {agent.get('status') or 'no status'}"
    )


class AdminReadinessCollector:
    """Combine live inventory and static runtime configuration into one report."""

    def __init__(
        self,
        settings: Optional[Settings] = None,
        inventory: Optional[RuntimeInventoryService] = None,
        configuration: Optional[AdminConfigurationManager] = None,
        runtime_ready: Callable[[], bool] = services_ready,
    ) -> None:
        self.settings = settings or get_settings()
        self.inventory = inventory or RuntimeInventoryService()
        self.configuration = configuration or get_admin_configuration()
        self.runtime_ready = runtime_ready

    def _resource_base(self) -> str:
        subscription = quote(self.settings.azure_subscription_id or "", safe="")
        resource_group = quote(self.settings.admin_readiness_resource_group or "", safe="")
        return f"/subscriptions/{subscription}/resourceGroups/{resource_group}"

    def _arm_requests(self) -> dict[str, tuple[str, str]]:
        if (
            not self.settings.azure_subscription_id
            or not self.settings.admin_readiness_resource_group
        ):
            return {}
        base = self._resource_base()
        requests: dict[str, tuple[str, str]] = {}
        account = self.settings.admin_readiness_foundry_account
        project = self.settings.admin_readiness_foundry_project
        if account:
            account_id = (
                f"{base}/providers/Microsoft.CognitiveServices/accounts/"
                f"{quote(account, safe='')}"
            )
            requests["foundry_account"] = (account_id, _COGNITIVE_API_VERSION)
            if project:
                requests["foundry_project"] = (
                    f"{account_id}/projects/{quote(project, safe='')}",
                    _COGNITIVE_API_VERSION,
                )
            model = self.settings.admin_readiness_foundry_model_deployment
            if model:
                requests["foundry_model_deployment"] = (
                    f"{account_id}/deployments/{quote(model, safe='')}",
                    _MODEL_DEPLOYMENT_API_VERSION,
                )
        for name in self.settings.get_admin_readiness_container_environments():
            requests[f"environment:{name}"] = (
                f"{base}/providers/Microsoft.App/managedEnvironments/{quote(name, safe='')}",
                _CONTAINER_APPS_API_VERSION,
            )
        for name in self.settings.get_admin_readiness_container_apps():
            requests[f"app:{name}"] = (
                f"{base}/providers/Microsoft.App/containerApps/{quote(name, safe='')}",
                _CONTAINER_APPS_API_VERSION,
            )
        for name in self.settings.get_admin_readiness_container_jobs():
            requests[f"job:{name}"] = (
                f"{base}/providers/Microsoft.App/jobs/{quote(name, safe='')}",
                _CONTAINER_APPS_API_VERSION,
            )
        for resource in self.settings.get_admin_readiness_support_resources():
            resource_key = resource.get("id", "")
            resource_name = resource.get("name", "")
            resource_type = resource.get("resource_type", "")
            api_version = resource.get("api_version", "")
            if (
                resource_key
                and resource_name
                and _RESOURCE_TYPE_RE.fullmatch(resource_type)
                and api_version
            ):
                requests[f"support:{resource_key}"] = (
                    f"{base}/providers/{resource_type}/{quote(resource_name, safe='')}",
                    api_version,
                )
        return requests

    async def _load_arm(self, requests: dict[str, tuple[str, str]]) -> dict[str, Any]:
        try:
            return await self.inventory.get_arm_resources(requests)
        except Exception as exc:
            error = type(exc).__name__
            return {key: {"success": False, "data": {}, "error": error} for key in requests}

    async def _load_agents(self) -> dict[str, Any]:
        endpoint = self.settings.foundry_project_endpoint
        if not endpoint:
            return {"success": False, "data": {}, "error": "NotConfigured"}
        try:
            return await self.inventory.list_foundry_agents(endpoint)
        except Exception as exc:
            return {"success": False, "data": {}, "error": type(exc).__name__}

    async def _load_admin_configuration(self) -> tuple[bool, int, str]:
        if not self.configuration.configured:
            return False, 0, "Durable state storage is not configured."
        try:
            subscribers = await self.configuration.get_subscribers()
            return True, len(subscribers), f"Readable · {len(subscribers)} subscribers"
        except Exception as exc:
            return False, 0, f"Read failed ({type(exc).__name__})"

    @staticmethod
    def _provisioned_resource(
        envelope: dict[str, Any],
        check_id: str,
        name: str,
        label: str,
        action: str,
    ) -> ReadinessCheck:
        if not envelope.get("success"):
            error = envelope.get("error", "UnknownError")
            return _check(check_id, label, False, f"{name} · lookup failed ({error})", action)
        data = envelope.get("data") or {}
        state = str(data.get("properties", {}).get("provisioningState", "") or "")
        return _check(
            check_id,
            label,
            state.casefold() == "succeeded",
            f"{name} · {state or 'no status'}",
            action,
        )

    def _foundry_section(
        self,
        arm: dict[str, Any],
        agent_result: dict[str, Any],
    ) -> ReadinessSection:
        settings = self.settings
        account = settings.admin_readiness_foundry_account or "Not configured"
        project = settings.admin_readiness_foundry_project or "Not configured"
        checks = [
            _check(
                "foundry_endpoint",
                "Foundry project endpoint",
                bool(settings.foundry_project_endpoint),
                "Configured" if settings.foundry_project_endpoint else "Not configured",
                "Set FOUNDRY_PROJECT_ENDPOINT.",
            ),
            self._provisioned_resource(
                arm.get("foundry_account", {}),
                "foundry_account",
                account,
                "Foundry account",
                "Check the Foundry account name, RBAC, and provisioning state.",
            ),
            self._provisioned_resource(
                arm.get("foundry_project", {}),
                "foundry_project",
                project,
                "Foundry Project",
                "Check the Foundry project name and provisioning state.",
            ),
        ]
        model_name = settings.admin_readiness_foundry_model_deployment or "Not configured"
        model_envelope = arm.get("foundry_model_deployment", {})
        model_data = model_envelope.get("data") or {}
        model_properties = model_data.get("properties", {})
        model_state = str(model_properties.get("provisioningState", "") or "")
        model = model_properties.get("model", {}) or {}
        sku = model_data.get("sku", {}) or {}
        model_ok = bool(model_envelope.get("success") and model_state.casefold() == "succeeded")
        model_detail = (
            f"{model_name} · {model_state or 'no status'} · "
            f"{model.get('name', 'no model')} {model.get('version', '')} · "
            f"{sku.get('name', 'no SKU')} capacity {sku.get('capacity', 'none')}"
        )
        if not model_envelope.get("success"):
            model_detail = (
                f"{model_name} · lookup failed " f"({model_envelope.get('error', 'UnknownError')})"
            )
        checks.append(
            _check(
                "foundry_model_deployment",
                "Foundry model deployment",
                model_ok,
                model_detail,
                "Check the model deployment name, version, SKU, quota, and provisioning state.",
            )
        )

        agents = agent_result.get("data") or {}
        data_plane_ok = bool(agent_result.get("success"))
        checks.append(
            _check(
                "foundry_data_plane",
                "Foundry Agent data plane",
                data_plane_ok,
                (
                    f"{len(agents)} agents readable"
                    if data_plane_ok
                    else f"Lookup failed ({agent_result.get('error', 'UnknownError')})"
                ),
                "Check the managed identity's Foundry User role and project network access.",
            )
        )

        hosted_name = settings.foundry_hosted_agent_name or ""
        hosted = agents.get(hosted_name, {}) if hosted_name else {}
        hosted_ok = bool(hosted_name and _agent_is_active(hosted, "HostedAgentDefinition"))
        checks.append(
            _check(
                "hosted_agent",
                "Hosted Agent",
                hosted_ok,
                _agent_detail(hosted_name, hosted),
                "Check the Hosted Agent name and the latest version's "
                "HostedAgentDefinition/ACTIVE status.",
            )
        )

        configured = settings.get_admin_readiness_prompt_agents()
        expected_roles = set(SPECIALIST_AGENT_ROLES)
        roster_ok = (
            set(configured) == expected_roles
            and len({name.casefold() for name in configured.values() if name})
            == len(expected_roles)
            and all(configured.values())
        )
        checks.append(
            _check(
                "prompt_roster",
                "Prompt Agent roster",
                roster_ok,
                f"{len(configured)}/{len(expected_roles)} roles · unique names: {roster_ok}",
                "Map all six specialist roles to distinct Prompt Agent names.",
            )
        )
        for role in SPECIALIST_AGENT_ROLES:
            agent_name = configured.get(role, "")
            agent = agents.get(agent_name, {}) if agent_name else {}
            agent_ok = bool(roster_ok and _agent_is_active(agent, "PromptAgentDefinition"))
            checks.append(
                _check(
                    f"prompt_agent_{role}",
                    _ROLE_LABELS[role],
                    agent_ok,
                    _agent_detail(agent_name, agent),
                    f"Check the {role} Agent's PromptAgentDefinition/ACTIVE latest "
                    "version and readiness name.",
                )
            )
        return _section("foundry", "Microsoft Foundry", checks)

    def _container_apps_section(self, arm: dict[str, Any]) -> ReadinessSection:
        checks: list[ReadinessCheck] = []
        environments = self.settings.get_admin_readiness_container_environments()
        apps = self.settings.get_admin_readiness_container_apps()
        jobs = self.settings.get_admin_readiness_container_jobs()
        if not environments:
            checks.append(
                _check(
                    "container_environments_configured",
                    "Container Apps environments",
                    False,
                    "No expected environment is configured.",
                    "Set ADMIN_READINESS_CONTAINER_ENVIRONMENTS.",
                )
            )
        for name in environments:
            checks.append(
                self._provisioned_resource(
                    arm.get(f"environment:{name}", {}),
                    f"container_environment_{name}",
                    name,
                    "Container Apps Environment",
                    "Check the environment provisioning state and network configuration.",
                )
            )
        if not apps:
            checks.append(
                _check(
                    "container_apps_configured",
                    "Container Apps",
                    False,
                    "No expected Container App is configured.",
                    "Set ADMIN_READINESS_CONTAINER_APPS.",
                )
            )
        app_images: dict[str, str] = {}
        for name in apps:
            envelope = arm.get(f"app:{name}", {})
            data = envelope.get("data") or {}
            properties = data.get("properties", {})
            provisioning = str(properties.get("provisioningState", "") or "")
            running = str(properties.get("runningStatus", "") or "")
            latest = str(properties.get("latestRevisionName", "") or "")
            ready = str(properties.get("latestReadyRevisionName", "") or "")
            containers = properties.get("template", {}).get("containers", []) or []
            image = str(containers[0].get("image", "") or "") if containers else ""
            app_images[name] = image
            app_ok = bool(
                envelope.get("success")
                and provisioning.casefold() == "succeeded"
                and running.casefold() == "running"
                and latest
                and latest == ready
                and image
            )
            detail = (
                f"{provisioning or 'no status'} · {running or 'no running status'} · "
                f"{ready or 'no ready revision'} · {image or 'no image'}"
            )
            if not envelope.get("success"):
                detail = f"Lookup failed ({envelope.get('error', 'UnknownError')})"
            checks.append(
                _check(
                    f"container_app_{name}",
                    f"Container App · {name}",
                    app_ok,
                    detail,
                    "Check the App revision, replica, image pull, and health status.",
                )
            )
        if not jobs:
            checks.append(
                _check(
                    "container_jobs_configured",
                    "Container Apps Jobs",
                    False,
                    "No expected Job is configured.",
                    "Set ADMIN_READINESS_CONTAINER_JOBS.",
                )
            )
        job_images: dict[str, str] = {}
        for name in jobs:
            envelope = arm.get(f"job:{name}", {})
            data = envelope.get("data") or {}
            properties = data.get("properties", {})
            provisioning = str(properties.get("provisioningState", "") or "")
            configuration = properties.get("configuration", {})
            trigger = str(configuration.get("triggerType", "") or "")
            schedule = configuration.get("scheduleTriggerConfig", {}) or {}
            cron = str(schedule.get("cronExpression", "") or "")
            containers = properties.get("template", {}).get("containers", []) or []
            image = str(containers[0].get("image", "") or "") if containers else ""
            job_images[name] = image
            job_ok = bool(
                envelope.get("success")
                and provisioning.casefold() == "succeeded"
                and trigger.casefold() == "schedule"
                and cron
                and image
            )
            detail = (
                f"{provisioning or 'no status'} · {trigger or 'no trigger'} · "
                f"{cron or 'no cron'} · {image or 'no image'}"
            )
            if not envelope.get("success"):
                detail = f"Lookup failed ({envelope.get('error', 'UnknownError')})"
            checks.append(
                _check(
                    f"container_job_{name}",
                    f"Container Apps Job · {name}",
                    job_ok,
                    detail,
                    "Check the Job image, schedule, timeout, and provisioning state.",
                )
            )
        if apps and jobs:
            control_image = app_images.get(apps[0], "")
            scheduler_image = job_images.get(jobs[0], "")
            images_match = bool(control_image and control_image == scheduler_image)
            checks.append(
                _check(
                    "control_plane_image_parity",
                    "App / Scheduler image parity",
                    images_match,
                    (
                        control_image
                        if images_match
                        else f"App: {control_image or 'none'} · Job: {scheduler_image or 'none'}"
                    ),
                    "Deploy the Container App and Scheduler Job with the same image digest/tag.",
                )
            )
        return _section("container_apps", "Azure Container Apps", checks)

    def _azure_services_section(self, arm: dict[str, Any]) -> ReadinessSection:
        checks: list[ReadinessCheck] = []
        resources = self.settings.get_admin_readiness_support_resources()
        if not resources:
            checks.append(
                _check(
                    "support_resources_configured",
                    "Azure supporting resources",
                    False,
                    "No expected supporting resources are configured.",
                    "Set ADMIN_READINESS_SUPPORT_RESOURCES.",
                )
            )
        for resource in resources:
            resource_key = resource.get("id", "")
            resource_name = resource.get("name", "")
            resource_type = resource.get("resource_type", "")
            api_version = resource.get("api_version", "")
            label = resource.get("label", "Azure resource")
            valid = bool(
                resource_key
                and resource_name
                and _RESOURCE_TYPE_RE.fullmatch(resource_type)
                and api_version
            )
            if not valid:
                checks.append(
                    _check(
                        f"support_{resource_key or 'invalid'}",
                        label,
                        False,
                        "The readiness resource definition is invalid.",
                        "Check resource id, name, resource_type, and api_version.",
                    )
                )
                continue
            envelope = arm.get(f"support:{resource_key}", {})
            data = envelope.get("data") or {}
            state = str(data.get("properties", {}).get("provisioningState", "") or "")
            ok = bool(envelope.get("success") and (not state or state.casefold() == "succeeded"))
            detail = f"{resource_name} · {state or 'resource readable'}"
            if not envelope.get("success"):
                detail = (
                    f"{resource_name} · lookup failed " f"({envelope.get('error', 'UnknownError')})"
                )
            checks.append(
                _check(
                    f"support_{resource_key}",
                    label,
                    ok,
                    detail,
                    f"Check the {label} resource, RBAC, and provisioning state.",
                )
            )
        return _section("azure_services", "Azure services", checks)

    def _runtime_section(
        self,
        admin_config_ok: bool,
        subscriber_count: int,
        admin_config_detail: str,
    ) -> ReadinessSection:
        settings = self.settings
        identity_ok = bool(
            settings.azure_tenant_id
            and settings.azure_subscription_id
            and settings.azure_client_id
            and settings.admin_readiness_resource_group
        )
        transport = bool(
            settings.communication_services_connection_string
            or settings.communication_services_endpoint
        )
        recipient = bool(settings.email_recipient_address or subscriber_count)
        email_ok = bool(transport and settings.email_sender_address and recipient)
        telemetry_ok = bool(
            settings.otel_enabled and settings.applicationinsights_connection_string
        )
        checkpoint_ok = bool(settings.checkpoint_blob_url or settings.checkpoint_file_path)
        archive_ok = settings.archive_enabled and settings.archive_ui_enabled
        identity_missing = [
            name
            for name, value in (
                ("tenant", settings.azure_tenant_id),
                ("subscription", settings.azure_subscription_id),
                ("managed identity", settings.azure_client_id),
                ("resource group", settings.admin_readiness_resource_group),
            )
            if not value
        ]
        return _section(
            "runtime",
            "AzBrief runtime",
            [
                _check(
                    "azure_identity",
                    "Azure identity and scope",
                    identity_ok,
                    (
                        "tenant / subscription / managed identity / resource group configured"
                        if identity_ok
                        else f"Missing: {', '.join(identity_missing)}"
                    ),
                    "Check AZURE_TENANT_ID, AZURE_SUBSCRIPTION_ID, AZURE_CLIENT_ID, "
                    "and the readiness resource group.",
                ),
                _check(
                    "orchestrator_runtime",
                    "Orchestrator runtime",
                    self.runtime_ready(),
                    (
                        "Analysis services initialized"
                        if self.runtime_ready()
                        else "Services not initialized"
                    ),
                    "Check Container App startup logs and Hosted Agent configuration.",
                ),
                _check(
                    "checkpoint_store",
                    "Digest checkpoint",
                    checkpoint_ok,
                    (
                        "Durable backend configured"
                        if checkpoint_ok
                        else "Durable backend not configured"
                    ),
                    "Set CHECKPOINT_BLOB_URL and check Storage Blob permissions.",
                ),
                _check(
                    "admin_configuration",
                    "Admin configuration store",
                    admin_config_ok,
                    admin_config_detail,
                    "Check ADMIN_CONFIG_BLOB_URL and Storage Blob permissions.",
                ),
                _check(
                    "archive_store",
                    "Analysis Archive",
                    archive_ok,
                    (
                        "Store and UI configured"
                        if archive_ok
                        else "Archive storage or UI is not configured."
                    ),
                    "Check the Archive Blob URL, UI flag, and reader allow-list.",
                ),
                _check(
                    "email_delivery",
                    "Email delivery",
                    email_ok,
                    (
                        f"transport / sender / recipient ready · {subscriber_count} subscribers"
                        if email_ok
                        else "Transport, sender, or recipient is missing."
                    ),
                    "Check Communication Services, the sender, and recipients or subscribers.",
                ),
                _check(
                    "api_key",
                    "API / MCP key",
                    bool(settings.api_key),
                    "Configured" if settings.api_key else "Not configured",
                    "Check the API_KEY Key Vault reference.",
                ),
                _check(
                    "telemetry",
                    "Application Insights telemetry",
                    telemetry_ok,
                    (
                        "OTEL and connection string configured"
                        if telemetry_ok
                        else "Telemetry not configured"
                    ),
                    "Check OTEL_ENABLED and the Application Insights connection string.",
                ),
            ],
        )

    async def collect(self) -> ReadinessReport:
        """Collect all checks without allowing one dependency failure to abort the report."""
        requests = self._arm_requests()
        try:
            arm, agents, admin_configuration = await asyncio.gather(
                self._load_arm(requests),
                self._load_agents(),
                self._load_admin_configuration(),
            )
        finally:
            self.inventory.close()

        admin_config_ok, subscriber_count, admin_config_detail = admin_configuration
        sections = [
            self._foundry_section(arm, agents),
            self._container_apps_section(arm),
            self._azure_services_section(arm),
            self._runtime_section(
                admin_config_ok,
                subscriber_count,
                admin_config_detail,
            ),
        ]
        checks = [check for section in sections for check in section.checks]
        ready = sum(check.ok for check in checks)
        return ReadinessReport(
            ok=bool(checks) and ready == len(checks),
            ready=ready,
            total=len(checks),
            checked_at=datetime.now(timezone.utc),
            sections=sections,
        )


async def collect_admin_readiness() -> ReadinessReport:
    """Collect a fresh report for one authenticated Admin request."""
    return await AdminReadinessCollector().collect()
