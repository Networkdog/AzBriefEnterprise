"""Prepare or deploy the KT private infrastructure without changing an existing VNet."""

from __future__ import annotations

import argparse
import base64
import json
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from ipaddress import IPv4Address, IPv4Network, ip_network
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID, uuid5

import structlog

from src.config import Settings
from src.error_logging import configure_redaction, redact_fields, redact_text

logger = structlog.get_logger(__name__)
ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "infra" / "kt" / "azuredeploy.json"
MANAGEMENT = "https://management.azure.com"
PROFILE = "kt-private-foundation"
NETWORK_API = "2024-05-01"
FOUNDRY_API = "2025-06-01"
HOST_API = "2025-04-01-preview"
CONTAINER_ENV_API = "2026-01-01"
COMMUNICATION_API = "2023-04-01"
ARM_GUID_NAMESPACE = UUID("11fb06fb-712d-4ddd-98c7-e71bbd588830")
LEGACY_BOOTSTRAP_IMAGE = "mcr.microsoft.com/azuredocs/containerapps-helloworld:latest"
FOUNDATION_ENV_NAMES = frozenset(
    {
        "AZURE_TENANT_ID",
        "AZURE_SUBSCRIPTION_ID",
        "AZURE_CLIENT_ID",
        "FOUNDRY_PROJECT_ENDPOINT",
        "FOUNDRY_HOSTED_AGENT_NAME",
        "API_KEY",
        "ARCHIVE_BLOB_CONTAINER_URL",
        "CHECKPOINT_BLOB_URL",
        "ADMIN_UI_ENABLED",
        "ADMIN_REQUIRE_AUTH",
        "ARCHIVE_UI_ENABLED",
        "ARCHIVE_REQUIRE_AUTH",
        "FEEDBACK_UI_ENABLED",
        "MAX_CONCURRENT_ANALYSES",
    }
)
WEB_ENV_NAMES = frozenset(
    {
        "ADMIN_ALLOWED_PRINCIPALS",
        "ARCHIVE_ALLOWED_PRINCIPALS",
        "ADMIN_CONFIG_BLOB_URL",
        "ARCHIVE_BASE_URL",
        "ADMIN_READINESS_RESOURCE_GROUP",
        "ADMIN_READINESS_FOUNDRY_ACCOUNT",
        "ADMIN_READINESS_FOUNDRY_PROJECT",
    }
)
READINESS_ENV_NAMES = frozenset(
    {
        "ADMIN_READINESS_CONTAINER_ENVIRONMENTS",
        "ADMIN_READINESS_CONTAINER_APPS",
        "ADMIN_READINESS_SUPPORT_RESOURCES",
    }
)
RUNTIME_SETUP_ENV_NAMES = frozenset(
    {
        "ADMIN_READINESS_FOUNDRY_MODEL_DEPLOYMENT",
        "ADMIN_READINESS_FOUNDRY_SIMPLE_MODEL_DEPLOYMENT",
        "ADMIN_READINESS_PROMPT_AGENTS",
        "ADMIN_READINESS_CONTAINER_JOBS",
        "COMMUNICATION_SERVICES_ENDPOINT",
        "EMAIL_SENDER_ADDRESS",
        "EMAIL_RECIPIENT_ADDRESS",
        "OTEL_ENABLED",
        "APPLICATIONINSIGHTS_CONNECTION_STRING",
    }
)
_ROLE_NAME_SUFFIXES = {
    "coordinator": "coordinator",
    "resource_graph": "resource-graph",
    "azure_mcp": "azure-mcp",
    "azure_api": "azure-api",
    "report_writer": "report-writer",
    "quality_reviewer": "quality-reviewer",
}
EMAIL_SENDER_ACTIONS = frozenset(
    {
        "microsoft.communication/communicationservices/read",
        "microsoft.communication/communicationservices/write",
        "microsoft.communication/emailservices/write",
    }
)
PRIVATE_RANGES = tuple(
    IPv4Network(cidr) for cidr in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)
ACA_RESERVED = tuple(
    IPv4Network(cidr)
    for cidr in (
        "169.254.0.0/16",
        "172.30.0.0/16",
        "172.31.0.0/16",
        "192.0.2.0/24",
        "100.100.0.0/17",
        "100.100.128.0/19",
        "100.100.160.0/19",
        "100.100.192.0/19",
    )
)
SUBNETS = (
    ("private_endpoint", "peSubnetName", "peSubnetAddressPrefix", "createPESubnet", 28),
    ("foundry", "foundrySubnetName", "foundrySubnetAddressPrefix", "createFoundrySubnet", 27),
    (
        "container_apps",
        "containerAppsSubnetName",
        "containerAppsSubnetAddressPrefix",
        "createContainerAppsSubnet",
        27,
    ),
)
INTERNAL_PARAMETERS = {row[3] for row in SUBNETS} | {
    "deployCapabilityHost",
    "linkedPrivateDnsZones",
    "reuseExistingApiKey",
    "existingControlPlaneRoleAssignmentNames",
    "reuseExistingFoundrySetup",
    "reuseExistingTelemetryConnection",
    "reuseCoreModel",
    "reuseSimpleModel",
    "useCoreForSimpleModel",
    "reuseCommunicationService",
    "reuseEmailService",
    "reuseEmailDomain",
    "reuseEmailSenderRole",
    "existingEmailSenderRoleDefinitionId",
    "existingCommunicationTags",
    "existingCommunicationIdentity",
    "existingLinkedEmailDomains",
}


@dataclass(frozen=True)
class SubnetPlan:
    """A read-only decision to reuse a subnet or create a missing child resource."""

    role: str
    name: str
    network: IPv4Network
    create_parameter: str
    create: bool


def _ipv4(cidr: str) -> IPv4Network:
    network = ip_network(cidr, strict=True)
    if not isinstance(network, IPv4Network):
        raise ValueError(f"This profile requires an IPv4 prefix: {cidr}")
    return network


def _subnet_prefix(subnet: dict[str, Any]) -> str:
    props = subnet["properties"]
    prefixes = props.get("addressPrefixes") or [props.get("addressPrefix")]
    prefix = prefixes[0] if isinstance(prefixes, list) and len(prefixes) == 1 else None
    if not isinstance(prefix, str) or not prefix:
        raise ValueError(f"Exactly one IPv4 prefix is required for subnet {subnet['name']}")
    return prefix


def plan_subnets(parameters: dict[str, Any], vnet: dict[str, Any]) -> list[SubnetPlan]:
    """Validate size, scope, delegation and overlap before proposing any subnet writes."""
    if parameters["location"].casefold() != vnet["location"].casefold():
        raise ValueError("Foundry and the existing VNet must have the same Azure region")
    requested_names = [str(parameters[row[1]]).strip() for row in SUBNETS]
    if any(not name or "/" in name for name in requested_names):
        raise ValueError("Each subnet name must be a non-empty child resource name")
    if len({name.casefold() for name in requested_names}) != len(requested_names):
        raise ValueError("Private Endpoint, Foundry, and Container Apps subnets must be distinct")
    spaces = tuple(_ipv4(cidr) for cidr in vnet["properties"]["addressSpace"]["addressPrefixes"])
    existing = {subnet["name"].casefold(): subnet for subnet in vnet["properties"]["subnets"]}
    occupied = [(name, _ipv4(_subnet_prefix(subnet))) for name, subnet in existing.items()]
    plans: list[SubnetPlan] = []
    for role, name_parameter, prefix_parameter, create_parameter, minimum in SUBNETS:
        name = str(parameters[name_parameter]).strip()
        subnet = existing.get(name.casefold())
        supplied = parameters.get(prefix_parameter, "")
        prefix = _subnet_prefix(subnet) if subnet is not None else supplied
        if not prefix:
            raise ValueError(f"Supply {prefix_parameter} to create missing {name}")
        network = _ipv4(prefix)
        if subnet is not None and supplied and _ipv4(supplied) != network:
            raise ValueError(
                f"{name} already exists with a different prefix; it will not be resized"
            )
        if network.prefixlen > minimum:
            raise ValueError(f"{name} must be /{minimum} or larger")
        if not any(network.subnet_of(private) for private in PRIVATE_RANGES):
            raise ValueError(f"{name} must use RFC1918 private IPv4 space")
        if not any(network.subnet_of(space) for space in spaces):
            raise ValueError(f"{name} is outside the existing VNet address space")
        if role == "container_apps" and any(network.overlaps(r) for r in ACA_RESERVED):
            raise ValueError(f"{name} overlaps a Container Apps reserved range")
        if any(
            network.overlaps(other)
            for other_name, other in occupied
            if other_name != name.casefold()
        ):
            raise ValueError(f"{name} overlaps an existing or proposed subnet")
        if subnet is not None:
            delegations = [
                item["properties"]["serviceName"]
                for item in subnet["properties"].get("delegations", [])
            ]
            expected = [] if role == "private_endpoint" else ["Microsoft.App/environments"]
            if sorted(delegations) != expected:
                raise ValueError(
                    f"{name} has incompatible delegation; existing settings are not changed"
                )
        else:
            occupied.append((name.casefold(), network))
        plans.append(SubnetPlan(role, name, network, create_parameter, subnet is None))
    return plans


def load_parameters(path: Path) -> dict[str, Any]:
    """Load only the explicit ARM parameter file; never read .env or azd developer state."""
    template = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    supplied = json.loads(path.read_text(encoding="utf-8-sig"))["parameters"]
    schema = template["parameters"]
    if {"agentStorageAccountName", "stateStorageAccountName"}.intersection(supplied):
        raise ValueError(
            "Use storageAccountName with the single-account template. Legacy two-account "
            "deployments require a separately approved data/RBAC migration; no account is "
            "selected or removed automatically."
        )
    unknown = set(supplied) - set(schema)
    if unknown or INTERNAL_PARAMETERS.intersection(supplied):
        raise ValueError(
            f"Unknown or reserved deployment parameters: {sorted(unknown | (INTERNAL_PARAMETERS & set(supplied)))}"
        )
    values: dict[str, Any] = {}
    for name, definition in schema.items():
        if name in supplied:
            entry = supplied[name]
            if set(entry) != {"value"}:
                raise ValueError(f"{name} requires a literal value in the protected parameter file")
            value = entry["value"]
        elif name == "apiKey" or (name in INTERNAL_PARAMETERS and "defaultValue" not in definition):
            continue
        elif "defaultValue" in definition:
            value = definition["defaultValue"]
        else:
            raise ValueError(f"Missing required parameter: {name}")
        if isinstance(value, str) and ("<" in value or ">" in value):
            raise ValueError(f"Replace the example placeholder for {name}")
        if definition["type"].casefold() in {"string", "securestring"} and not isinstance(
            value, str
        ):
            raise ValueError(f"{name} must be a string")
        if isinstance(value, str):
            if len(value) < definition.get("minLength", 0):
                raise ValueError(f"{name} is shorter than its required minimum length")
            if "maxLength" in definition and len(value) > definition["maxLength"]:
                raise ValueError(f"{name} exceeds its maximum length")
        if definition["type"] == "bool" and not isinstance(value, bool):
            raise ValueError(f"{name} must be a boolean")
        if "defaultValue" not in definition and not value:
            raise ValueError(f"Required parameter cannot be empty: {name}")
        if "allowedValues" in definition and value not in definition["allowedValues"]:
            raise ValueError(f"Unsupported value for {name}")
        values[name] = value
    if "promptAgentNames" not in supplied:
        values["promptAgentNames"] = {
            role: f"{values['projectName'][:47]}-{suffix}"
            for role, suffix in _ROLE_NAME_SUFFIXES.items()
        }
    if "schedulerJobName" not in supplied:
        values["schedulerJobName"] = f"job-{values['containerAppName']}"
    if "communicationServicesName" not in supplied:
        values["communicationServicesName"] = f"acs-{values['containerAppName']}"
    if "emailServiceName" not in supplied:
        values["emailServiceName"] = f"email-{values['containerAppName']}"
    if not isinstance(values["existingPrivateDnsZoneIds"], dict):
        raise ValueError("existingPrivateDnsZoneIds must be an object")
    if not isinstance(values["tags"], dict) or values["tags"].get("deploymentProfile") != PROFILE:
        raise ValueError(f"Preserve the deploymentProfile={PROFILE} ownership tag")
    api_key = values.get("apiKey")
    if api_key is not None and (
        not api_key.strip() or any(character.isspace() for character in api_key)
    ):
        raise ValueError("apiKey must be a separate random key without whitespace")
    if values["containerRegistryAuthMode"] == "Credentials":
        if (
            not values["containerRegistryUsername"].strip()
            or len(values["containerRegistryPassword"].strip()) < 8
        ):
            raise ValueError("Private GHCR requires a username and a read:packages token")
        if api_key == values["containerRegistryPassword"]:
            raise ValueError("apiKey and the GHCR pull token must be different")
    elif values["containerRegistryPassword"]:
        raise ValueError("Anonymous registry mode must not carry an unused pull token")
    web_fields = (
        "adminEntraClientId",
        "adminEntraClientSecret",
        "adminAllowedPrincipals",
        "archiveAllowedPrincipals",
    )
    if values["enablePrivateAnonymousWebUi"] and any(values[name].strip() for name in web_fields):
        raise ValueError("Private anonymous web access must not carry unused Entra inputs")
    if any(values[name].strip() for name in web_fields):
        for name in ("adminAllowedPrincipals", "archiveAllowedPrincipals"):
            principals = [part.strip() for part in values[name].split(",") if part.strip()]
            if any(any(character.isspace() for character in part) for part in principals):
                raise ValueError(f"{name} must contain comma-separated Object IDs or UPNs")
            values[name] = ",".join(principals)
        if (
            not values["adminEntraClientId"].strip()
            or not values["adminEntraClientSecret"].strip()
            or not (values["adminAllowedPrincipals"] or values["archiveAllowedPrincipals"])
        ):
            raise ValueError(
                "Authenticated web setup requires adminEntraClientId, adminEntraClientSecret "
                "and at least one administrator or Archive reader"
            )
        try:
            values["adminEntraClientId"] = str(UUID(values["adminEntraClientId"].strip()))
        except ValueError:
            raise ValueError("adminEntraClientId must be an application client ID GUID") from None
        secret = values["adminEntraClientSecret"]
        if any(character.isspace() for character in secret):
            raise ValueError("adminEntraClientSecret must not contain whitespace")
        if secret in (api_key, values["containerRegistryPassword"]):
            raise ValueError("Entra client secret, API key and GHCR token must be different")
    validate_runtime_setup(values)
    return values


def validate_runtime_setup(values: dict[str, Any]) -> None:
    """Reject partial or incompatible runtime bindings before any Azure write."""
    agents = values["promptAgentNames"]
    if not isinstance(agents, dict) or set(agents) != set(_ROLE_NAME_SUFFIXES):
        raise ValueError("promptAgentNames must declare exactly the six specialist roles")
    if (
        any(
            not isinstance(name, str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,62}[A-Za-z0-9]", name)
            for name in agents.values()
        )
        or len({name.casefold() for name in agents.values()}) != 6
        or values["foundryHostedAgentName"].casefold()
        in {name.casefold() for name in agents.values()}
    ):
        raise ValueError("promptAgentNames must contain six distinct valid 2-64 character names")
    for name in ("modelDeploymentName", "simpleModelDeploymentName"):
        if values[name] and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", values[name]):
            raise ValueError(f"{name} must be a model deployment name, not an endpoint")
    if values["simpleModelDeploymentName"] and (
        not values["modelDeploymentName"]
        or values["simpleModelDeploymentName"].casefold()
        == values["modelDeploymentName"].casefold()
    ):
        raise ValueError("A simple model needs a separate, configured core model deployment")
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,58}[a-z0-9]", values["schedulerJobName"]):
        raise ValueError("schedulerJobName must be a valid 2-60 character Container Apps Job name")
    email_fields = ("communicationServicesEndpoint", "emailSenderAddress", "emailRecipientAddress")
    if values["provisionModels"]:
        if not values["modelDeploymentName"] or not values["simpleModelDeploymentName"]:
            raise ValueError("Automatic models need two distinct deployment aliases")
        for name in ("coreModelName", "simpleModelName"):
            if not re.fullmatch(r"gpt-5[A-Za-z0-9_.-]*", values[name]):
                raise ValueError(f"{name} must identify a supported GPT-5 model")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", values["modelVersion"]):
            raise ValueError("modelVersion must be an explicitly pinned YYYY-MM-DD version")
        for name in ("modelCapacity", "simpleModelCapacity"):
            if type(values[name]) is not int or not 1 <= values[name] <= 1000:
                raise ValueError(f"{name} must be between 1 and 1000 capacity units")
    if values["provisionEmail"]:
        for name in ("communicationServicesName", "emailServiceName"):
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,62}[A-Za-z0-9]", values[name]):
                raise ValueError(f"{name} must be a valid service name")
        if values["emailRecipientAddress"]:
            from src.feedback.models import FeedbackRequest

            FeedbackRequest.validate_contact_email(values["emailRecipientAddress"])
    elif any(values[name] for name in email_fields):
        if not all(values[name] for name in email_fields):
            raise ValueError(
                "Email setup needs an ACS endpoint, verified sender and approved recipient"
            )
        try:
            endpoint = urlsplit(values["communicationServicesEndpoint"])
            port = endpoint.port
        except ValueError:
            raise ValueError(
                "communicationServicesEndpoint must be an Azure ACS HTTPS origin"
            ) from None
        if (
            endpoint.scheme != "https"
            or not (endpoint.hostname or "").endswith(".communication.azure.com")
            or endpoint.username
            or endpoint.password
            or endpoint.query
            or endpoint.fragment
            or port is not None
            or endpoint.path not in ("", "/")
        ):
            raise ValueError("communicationServicesEndpoint must be an Azure ACS HTTPS origin")
        from src.feedback.models import FeedbackRequest

        for name in ("emailSenderAddress", "emailRecipientAddress"):
            try:
                FeedbackRequest.validate_contact_email(values[name])
                if len(values[name]) > 254:
                    raise ValueError("email length")
            except ValueError:
                raise ValueError(f"{name} must be a valid email address") from None
        if values["assignControlPlaneRoles"] and (
            not values["communicationServicesName"] or not values["emailSenderRoleDefinitionId"]
        ):
            raise ValueError(
                "Automatic email RBAC needs the existing ACS name and approved sender role GUID"
            )
    elif values["emailSenderRoleDefinitionId"]:
        raise ValueError("An email role cannot be assigned without complete email configuration")
    if values["communicationServicesName"] and not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9-]{0,62}[A-Za-z0-9]", values["communicationServicesName"]
    ):
        raise ValueError("communicationServicesName must identify the existing ACS resource")
    if values["emailSenderRoleDefinitionId"]:
        try:
            UUID(values["emailSenderRoleDefinitionId"])
        except ValueError:
            raise ValueError("emailSenderRoleDefinitionId must be an existing role GUID") from None
    if bool(values["azureMcpServerUrl"]) != bool(values["azureMcpProjectConnectionName"]):
        raise ValueError("Azure MCP publication needs both server URL and project connection name")
    if values["azureMcpServerUrl"]:
        try:
            endpoint = urlsplit(values["azureMcpServerUrl"])
            port = endpoint.port
        except ValueError:
            raise ValueError("azureMcpServerUrl must be a valid HTTPS server URL") from None
        if (
            endpoint.scheme != "https"
            or not endpoint.hostname
            or endpoint.username
            or endpoint.password
            or endpoint.query
            or endpoint.fragment
            or (port is not None and not 1 <= port <= 65535)
        ):
            raise ValueError("azureMcpServerUrl must be a valid HTTPS server URL")
        if not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", values["azureMcpProjectConnectionName"]
        ):
            raise ValueError("azureMcpProjectConnectionName must be a project connection name")
    if values["applicationInsightsName"] and not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9_.()-]{0,254}", values["applicationInsightsName"]
    ):
        raise ValueError("applicationInsightsName must identify an existing component in this RG")


def runtime_setup_env(values: dict[str, Any]) -> dict[str, str]:
    """Resolve non-secret stage-two runtime inputs with the same gates as Bicep."""
    stage_two = values["deployCapabilityHost"] is True

    def encoded(data: object) -> str:
        return base64.b64encode(json.dumps(data, separators=(",", ":")).encode("utf-8")).decode(
            "ascii"
        )

    return {
        "ADMIN_READINESS_FOUNDRY_MODEL_DEPLOYMENT": (
            values["modelDeploymentName"] if stage_two else ""
        ),
        "ADMIN_READINESS_FOUNDRY_SIMPLE_MODEL_DEPLOYMENT": (
            values["simpleModelDeploymentName"] if stage_two else ""
        ),
        "ADMIN_READINESS_PROMPT_AGENTS": encoded(values["promptAgentNames"]),
        "ADMIN_READINESS_CONTAINER_JOBS": encoded(
            [values["schedulerJobName"]] if stage_two and values["deploySchedulerJob"] else []
        ),
        "COMMUNICATION_SERVICES_ENDPOINT": (
            values["communicationServicesEndpoint"] if stage_two else ""
        ),
        "EMAIL_SENDER_ADDRESS": values["emailSenderAddress"] if stage_two else "",
        "EMAIL_RECIPIENT_ADDRESS": values["emailRecipientAddress"] if stage_two else "",
        "OTEL_ENABLED": str(stage_two and bool(values["applicationInsightsName"])).lower(),
    }


def _web_ui_flags(values: dict[str, Any]) -> tuple[bool, bool]:
    """Resolve the same stage and credential gates as the KT template."""
    if _private_anonymous_web_ui(values):
        return True, True
    authenticated = (
        values["deployCapabilityHost"]
        and bool(values["adminEntraClientId"].strip())
        and bool(values["adminEntraClientSecret"].strip())
    )
    admin = authenticated and any(
        part.strip() for part in values["adminAllowedPrincipals"].split(",")
    )
    archive = authenticated and (
        admin or any(part.strip() for part in values["archiveAllowedPrincipals"].split(","))
    )
    return admin, archive


def _private_anonymous_web_ui(values: dict[str, Any]) -> bool:
    """Require an explicit opt-in and stage two for the KT private exception."""
    return values["deployCapabilityHost"] is True and values["enablePrivateAnonymousWebUi"] is True


def _matches_bool_setting(value: object, expected: bool) -> bool:
    """Compare declared boolean strings without changing their authentication meaning."""
    return isinstance(value, str) and value.casefold() == str(expected).lower()


class AzureCli:
    """A bounded ARM-only adapter; CLI errors always stop the deployment."""

    def __init__(self, subscription: str, tenant: str, resource_group: str):
        self.subscription = subscription
        self.tenant = tenant
        self.group = resource_group
        self.group_id = f"/subscriptions/{subscription}/resourceGroups/{resource_group}"

    def json(self, *args: str) -> Any:
        executable = shutil.which("az")
        if executable is None:
            raise RuntimeError("Azure CLI is required; install it before running this script")
        result = subprocess.run(
            [executable, *args, "--only-show-errors", "--output", "json"],
            check=False,
            capture_output=True,
            encoding="utf-8",
            stdin=subprocess.DEVNULL,
            timeout=7200 if args[:3] == ("deployment", "group", "create") else 180,
        )
        if result.returncode:
            raise RuntimeError(f"Azure CLI {args[0]} failed: {redact_text(result.stderr.strip())}")
        return json.loads(result.stdout)

    def get(self, resource_id: str, api_version: str) -> dict[str, Any]:
        if not resource_id.startswith("/subscriptions/") or any(c in resource_id for c in "?#"):
            raise ValueError("Expected an ARM resource ID, not a URL")
        response = self.json(
            "rest",
            "--method",
            "get",
            "--url",
            f"{MANAGEMENT}{resource_id}?api-version={api_version}",
        )
        if not isinstance(response, dict):
            raise ValueError("ARM GET did not return a JSON object")
        return response

    def collection(self, resource_id: str, api_version: str) -> list[dict[str, Any]]:
        page = self.get(resource_id, api_version)
        if not isinstance(page["value"], list):
            raise ValueError("ARM inventory value must be an array")
        rows = list(page["value"])
        for _ in range(20):
            next_link = page.get("nextLink")
            if not next_link:
                return rows
            if not next_link.startswith(f"{MANAGEMENT}/subscriptions/"):
                raise ValueError("Unexpected ARM continuation URL")
            page = self.json("rest", "--method", "get", "--url", next_link)
            if not isinstance(page, dict) or not isinstance(page["value"], list):
                raise ValueError("ARM inventory continuation value must be an array")
            rows.extend(page["value"])
        raise RuntimeError("ARM inventory exceeded its page limit; no deployment was attempted")

    def assert_context(self) -> None:
        account = self.json("account", "show")
        if (
            account["id"].casefold() != self.subscription.casefold()
            or account["tenantId"].casefold() != self.tenant.casefold()
        ):
            raise ValueError(
                "Default Azure CLI subscription/tenant differs from the explicit KT target"
            )
        if self.json("cloud", "show")["name"] != "AzureCloud":
            raise ValueError("This KT template currently supports Azure public cloud only")
        self.json("group", "show", "--name", self.group, "--subscription", self.subscription)

    def deploy(self, values: dict[str, Any], mode: str, name: str) -> dict[str, Any]:
        if mode not in {"validate", "what-if", "create"}:
            raise ValueError(f"Unsupported ARM deployment operation: {mode}")
        configure_redaction(values)
        with tempfile.TemporaryDirectory(prefix="azbrief-kt-") as temporary:
            path = Path(temporary) / "parameters.json"
            payload = {"parameters": {key: {"value": value} for key, value in values.items()}}
            path.write_text(json.dumps(payload), encoding="utf-8")
            extra = ["--no-pretty-print"] if mode == "what-if" else []
            response = self.json(
                "deployment",
                "group",
                mode,
                "--name",
                name,
                "--resource-group",
                self.group,
                "--subscription",
                self.subscription,
                "--mode",
                "Incremental",
                "--template-file",
                str(TEMPLATE),
                "--parameters",
                f"@{path}",
                *extra,
            )
            if not isinstance(response, dict):
                raise ValueError("ARM deployment did not return a JSON object")
            return response


def _same_id(left: str, right: str) -> bool:
    return left.rstrip("/").casefold() == right.rstrip("/").casefold()


def control_plane_role_targets(values: dict[str, Any], group_id: str) -> dict[str, tuple[str, str]]:
    """Declare only the configured UAMI permissions, never Hosted evidence grants."""
    if not (values["deployCapabilityHost"] and values["assignControlPlaneRoles"]):
        return {}
    storage = (
        f"{group_id}/providers/Microsoft.Storage/storageAccounts/{values['storageAccountName']}"
    )
    targets = {
        "reader": (group_id, "acdd72a7-3385-48ef-bd42-f606fba81ae7"),
        "foundryUser": (
            f"{group_id}/providers/Microsoft.CognitiveServices/accounts/"
            f"{values['foundryAccountName']}/projects/{values['projectName']}",
            "53ca6127-db72-4b80-b1b0-d745d6d5456d",
        ),
        "stateContributor": (
            f"{storage}/blobServices/default/containers/azbrief-state",
            "ba92f5b4-2d11-453d-a403-e96b0029c9fe",
        ),
        "archiveContributor": (
            f"{storage}/blobServices/default/containers/azbrief-archive",
            "ba92f5b4-2d11-453d-a403-e96b0029c9fe",
        ),
    }
    if values["applicationInsightsName"]:
        targets["telemetryPublisher"] = (
            f"{group_id}/providers/Microsoft.Insights/components/{values['applicationInsightsName']}",
            "3913510d-42f4-4e42-8a64-420c390055eb",
        )
    if values["communicationServicesName"] and values["emailSenderRoleDefinitionId"]:
        targets["emailSender"] = (
            f"{group_id}/providers/Microsoft.Communication/communicationServices/"
            f"{values['communicationServicesName']}",
            values["emailSenderRoleDefinitionId"],
        )
    return targets


def automatic_service_plan(
    cli: AzureCli, values: dict[str, Any], resources: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """Plan creation/reuse from complete live inventory without changing existing services."""
    plan: dict[str, Any] = {
        "reuseCoreModel": False,
        "reuseSimpleModel": False,
        "useCoreForSimpleModel": False,
        "reuseCommunicationService": False,
        "reuseEmailService": False,
        "reuseEmailDomain": False,
        "reuseEmailSenderRole": False,
        "existingEmailSenderRoleDefinitionId": "",
        "existingCommunicationTags": {},
        "existingCommunicationIdentity": {"type": "None"},
        "existingLinkedEmailDomains": [],
    }
    if not values["deployCapabilityHost"]:
        return plan
    if values["provisionModels"]:
        account_id = (
            f"{cli.group_id}/providers/Microsoft.CognitiveServices/accounts/"
            f"{values['foundryAccountName']}"
        )
        deployments = cli.collection(f"{account_id}/deployments", FOUNDRY_API)
        existing = {row["name"].split("/")[-1].casefold(): row for row in deployments}
        if len(existing) != len(deployments):
            raise ValueError("Model inventory contains ambiguous deployment aliases")
        missing: list[str] = []
        for role, name in (
            ("Core", values["modelDeploymentName"]),
            ("Simple", values["simpleModelDeploymentName"]),
        ):
            deployment = existing.get(name.casefold())
            if deployment is None:
                missing.append(role)
                continue
            props = deployment["properties"]
            model = props["model"]
            if (
                props.get("provisioningState") != "Succeeded"
                or model.get("format") != "OpenAI"
                or not model.get("name", "").startswith("gpt-5")
            ):
                raise ValueError(
                    f"Existing {role.lower()} model is not a compatible ready deployment"
                )
            plan[f"reuse{role}Model"] = True
        if missing:
            catalog = cli.collection(f"{account_id}/models", FOUNDRY_API)
            usage = cli.collection(
                f"/subscriptions/{cli.subscription}/providers/Microsoft.CognitiveServices/"
                f"locations/{values['location']}/usages",
                FOUNDRY_API,
            )

            def capacity(model_name: str) -> int:
                models = [
                    model
                    for model in catalog
                    if model.get("name") == model_name
                    and model.get("version") == values["modelVersion"]
                    and model.get("capabilities", {}).get("responses") == "true"
                    and model.get("capabilities", {}).get("agentsV2") == "true"
                ]
                skus = [
                    sku
                    for model in models
                    for sku in model.get("skus", [])
                    if sku["name"] == "GlobalStandard"
                ]
                if len(skus) != 1:
                    raise ValueError(
                        f"No verified Responses/Agents GlobalStandard model: {model_name}"
                    )
                rows = [row for row in usage if row["name"]["value"] == skus[0]["usageName"]]
                if len(rows) != 1 or any(
                    type(rows[0][key]) not in (int, float) for key in ("limit", "currentValue")
                ):
                    raise ValueError(f"Model quota is unreadable or ambiguous: {model_name}")
                return int(rows[0]["limit"] - rows[0]["currentValue"])

            core_required = values["modelCapacity"] if "Core" in missing else 0
            core_available = capacity(values["coreModelName"]) if "Core" in missing else None
            if core_available is not None and core_available < core_required:
                raise ValueError("Insufficient core-model quota; no allocation was changed")
            if (
                "Simple" in missing
                and values["coreModelName"] == values["simpleModelName"]
                and (
                    core_available
                    if core_available is not None
                    else capacity(values["coreModelName"])
                )
                < core_required + values["simpleModelCapacity"]
            ):
                raise ValueError("Insufficient combined quota for the two model deployments")
            if (
                "Simple" in missing
                and capacity(values["simpleModelName"]) < values["simpleModelCapacity"]
            ):
                if core_available is None:
                    core_available = capacity(values["coreModelName"])
                if core_available < core_required + values["simpleModelCapacity"]:
                    raise ValueError("Neither simple-model nor core fallback quota is available")
                plan["useCoreForSimpleModel"] = True
                logger.warning(
                    "kt_simple_model_quota_fallback",
                    requested_model=values["simpleModelName"],
                    resolved_model=values["coreModelName"],
                    deployment=values["simpleModelDeploymentName"],
                    existing_models_unchanged=True,
                )
    if values["provisionEmail"]:
        acs_id = (
            f"{cli.group_id}/providers/Microsoft.Communication/communicationServices/"
            f"{values['communicationServicesName']}"
        )
        email_id = (
            f"{cli.group_id}/providers/Microsoft.Communication/emailServices/"
            f"{values['emailServiceName']}"
        )
        domain_id = f"{email_id}/domains/AzureManagedDomain"
        if email_id.casefold() in resources:
            email = cli.get(email_id, COMMUNICATION_API)["properties"]
            if email.get("provisioningState") != "Succeeded":
                raise ValueError("Existing Email Communication Services is not ready")
            plan["emailDataLocation"] = email["dataLocation"]
            plan["reuseEmailService"] = True
            domains = cli.collection(f"{email_id}/domains", COMMUNICATION_API)
            matching = [
                row for row in domains if row["name"].split("/")[-1] == "AzureManagedDomain"
            ]
            if len(matching) > 1:
                raise ValueError("Azure-managed email domain is ambiguous")
            if matching:
                domain = matching[0]["properties"]
                if (
                    domain.get("domainManagement") != "AzureManaged"
                    or domain.get("provisioningState") != "Succeeded"
                    or not re.fullmatch(
                        r"[A-Za-z0-9-]+\.azurecomm\.net", domain.get("fromSenderDomain", "")
                    )
                ):
                    raise ValueError("Existing managed email domain is not ready")
                plan["reuseEmailDomain"] = True
                plan["emailSenderAddress"] = f"DoNotReply@{domain['fromSenderDomain']}"
        if acs_id.casefold() in resources:
            communication = cli.get(acs_id, COMMUNICATION_API)
            props = communication["properties"]
            if props.get("provisioningState") != "Succeeded" or not re.fullmatch(
                r"(?:[A-Za-z0-9-]+\.)+communication\.azure\.com", props.get("hostName", "")
            ):
                raise ValueError("Existing Communication Services is not ready")
            if props["dataLocation"] != plan.get("emailDataLocation", values["emailDataLocation"]):
                if plan["reuseEmailService"]:
                    raise ValueError("ACS and Email Services have incompatible data locations")
                plan["emailDataLocation"] = props["dataLocation"]
            plan["communicationServicesEndpoint"] = f"https://{props['hostName']}"
            links = props.get("linkedDomains", [])
            if not isinstance(links, list):
                raise ValueError("Existing ACS domain links are invalid")
            plan["existingLinkedEmailDomains"] = links
            plan["existingCommunicationTags"] = communication.get("tags") or {}
            identity = communication.get("identity") or {"type": "None"}
            plan["existingCommunicationIdentity"] = {
                "type": identity["type"],
                **(
                    {
                        "userAssignedIdentities": {
                            key: {} for key in identity["userAssignedIdentities"]
                        }
                    }
                    if identity.get("userAssignedIdentities")
                    else {}
                ),
            }
            plan["reuseCommunicationService"] = any(_same_id(link, domain_id) for link in links)
        role_id = str(
            uuid5(
                ARM_GUID_NAMESPACE,
                "-".join(
                    (
                        cli.group_id.casefold(),
                        values["communicationServicesName"].casefold(),
                        "azbrief-email-sender",
                    )
                ),
            )
        )
        definitions = cli.json(
            "role",
            "definition",
            "list",
            "--custom-role-only",
            "true",
            "--scope",
            cli.group_id,
            "--subscription",
            cli.subscription,
        )
        if not isinstance(definitions, list) or any(
            not isinstance(row, dict) for row in definitions
        ):
            raise ValueError("Custom sender-role inventory must be a complete array")
        role_name = f"AzBrief Email Sender {cli.group_id.casefold()}/{values['communicationServicesName'].casefold()}"
        matching_roles = [
            row
            for row in definitions
            if row["name"].casefold() == role_id or row.get("roleName") == role_name
        ]
        if len(matching_roles) > 1:
            raise ValueError("Automatic email sender role is ambiguous")
        if matching_roles:
            permissions = matching_roles[0]["permissions"]
            if (
                len(permissions) != 1
                or {action.casefold() for action in permissions[0]["actions"]}
                != EMAIL_SENDER_ACTIONS
                or any(
                    permissions[0].get(key)
                    for key in ("notActions", "dataActions", "notDataActions")
                )
            ):
                raise ValueError("Existing automatic sender role has unexpected permissions")
            plan["reuseEmailSenderRole"] = True
            role_id = matching_roles[0]["name"]
            UUID(role_id)
            plan["existingEmailSenderRoleDefinitionId"] = role_id
        plan["emailSenderRoleDefinitionId"] = role_id
        for name in ("communicationServicesEndpoint", "emailSenderAddress"):
            if values[name] and values[name] != plan.get(name):
                raise ValueError(
                    f"Automatic email {name} does not match the existing managed service; "
                    "use provisionEmail=false for legacy bindings"
                )
    return plan


def existing_control_plane_roles(
    cli: AzureCli, values: dict[str, Any], principal_id: str
) -> dict[str, str]:
    """Reuse only exact unconditioned principal/role/scope assignments from live inventory."""
    targets = control_plane_role_targets(values, cli.group_id)
    if not targets:
        return {}
    UUID(principal_id)
    rows = cli.json(
        "role",
        "assignment",
        "list",
        "--assignee",
        principal_id,
        "--all",
        "--fill-principal-name",
        "false",
        "--subscription",
        cli.subscription,
    )
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError("Role assignment inventory did not return a complete array")
    names: dict[str, str] = {}
    for key, (scope, role) in targets.items():
        matches = [
            row
            for row in rows
            if row.get("principalId", "").casefold() == principal_id.casefold()
            and _same_id(row.get("scope", ""), scope)
            and row.get("roleDefinitionId", "").split("/")[-1].casefold() == role.casefold()
        ]
        if not matches:
            continue
        if len(matches) != 1 or matches[0].get("condition"):
            raise ValueError(f"Existing UAMI assignment is ambiguous or conditional: {key}")
        name = matches[0]["name"]
        UUID(name)
        names[key] = name
    return names


def _matches_container_resources(
    resources: object,
    cpu: float,
    memory: str,
    ephemeral_storage: str,
    *,
    allow_empty_ephemeral: bool = False,
) -> bool:
    """Accept only known service representations of automatic ephemeral storage."""
    return (
        isinstance(resources, dict)
        and set(resources) <= {"cpu", "memory", "ephemeralStorage"}
        and resources.get("cpu") == cpu
        and resources.get("memory") == memory
        and (
            resources.get("ephemeralStorage", ephemeral_storage) == ephemeral_storage
            or (allow_empty_ephemeral and resources.get("ephemeralStorage") == "")
        )
    )


def validate_scheduler_projection(
    document: dict[str, Any],
    values: dict[str, Any],
    group_id: str,
    client_id: str,
    telemetry_connection: str,
) -> None:
    """Reject changes to an owned Job's identity, credentials or bounded Manual runtime."""
    props = document["properties"]
    identity_id = (
        f"{group_id}/providers/Microsoft.ManagedIdentity/userAssignedIdentities/"
        f"id-{values['containerAppName']}"
    )
    identity = document.get("identity", {})
    identities = identity.get("userAssignedIdentities", {})
    config = props.get("configuration", {})
    template = props.get("template", {})
    containers = template.get("containers", [])
    if (
        identity.get("type") != "UserAssigned"
        or not isinstance(identities, dict)
        or {key.casefold() for key in identities} != {identity_id.casefold()}
        or config.get("triggerType") != "Manual"
        or config.get("replicaTimeout") != 43200
        or config.get("replicaRetryLimit") != 0
        or config.get("manualTriggerConfig") != {"parallelism": 1, "replicaCompletionCount": 1}
        or props.get("workloadProfileName") != "Consumption"
        or not _same_id(
            props.get("environmentId", ""), endpoint_specs(values, group_id)[4]["target"]
        )
        or template.get("volumes")
        or not isinstance(containers, list)
        or len(containers) != 1
    ):
        raise ValueError("Refusing to overwrite an operational or foreign scheduler Job")
    container = containers[0]
    if (
        container.get("name") != "azbrief-scheduler"
        or container.get("image") != values["bootstrapImage"]
        or container.get("command") != ["python", "-m", "src.scheduler"]
        or container.get("args")
        or container.get("volumeMounts")
        or not _matches_container_resources(
            container.get("resources"), 1, "2Gi", "4Gi", allow_empty_ephemeral=True
        )
    ):
        raise ValueError("Refusing to overwrite customized scheduler execution settings")
    admin_ui, _ = _web_ui_flags(values)
    blob = f"https://{values['storageAccountName']}.blob.core.windows.net"
    expected_env = {
        **runtime_setup_env(values),
        "APPLICATIONINSIGHTS_CONNECTION_STRING": telemetry_connection,
        "AZURE_TENANT_ID": values["tenantId"],
        "AZURE_SUBSCRIPTION_ID": group_id.split("/")[2],
        "AZURE_CLIENT_ID": client_id,
        "FOUNDRY_PROJECT_ENDPOINT": (
            f"https://{values['foundryAccountName']}.services.ai.azure.com/"
            f"api/projects/{values['projectName']}"
        ),
        "FOUNDRY_HOSTED_AGENT_NAME": values["foundryHostedAgentName"],
        "ARCHIVE_BLOB_CONTAINER_URL": f"{blob}/azbrief-archive",
        "CHECKPOINT_BLOB_URL": f"{blob}/azbrief-state/checkpoint.json",
        "ADMIN_CONFIG_BLOB_URL": f"{blob}/azbrief-state/admin-config.json" if admin_ui else "",
        "MAX_CONCURRENT_ANALYSES": "1",
        "SCHEDULE_DISPATCH_ENABLED": "true",
        "RUN_TIME_BUDGET_S": "39600",
    }
    entries = container.get("env", [])
    if not isinstance(entries, list) or any(not isinstance(entry, dict) for entry in entries):
        raise ValueError("Scheduler environment must be an array of named settings")
    env = {entry["name"]: entry for entry in entries}
    if len(env) != len(entries) or set(env) != set(expected_env) | {"API_KEY"}:
        raise ValueError("Refusing to overwrite customized scheduler environment")
    if env["API_KEY"] != {"name": "API_KEY", "secretRef": "orchestrator-api-key"} or any(
        env[name] != {"name": name, "value": expected} for name, expected in expected_env.items()
    ):
        raise ValueError("Scheduler runtime bindings do not match the approved App settings")
    credential_registry = values["containerRegistryAuthMode"] == "Credentials"
    expected_registry = (
        [
            {
                "server": "ghcr.io",
                "username": values["containerRegistryUsername"],
                "passwordSecretRef": "ghcr-pull-token",
            }
        ]
        if credential_registry
        else []
    )
    secrets = config.get("secrets") or []
    if (
        (config.get("registries") or []) != expected_registry
        or {secret["name"] for secret in secrets}
        != (
            {"orchestrator-api-key", "ghcr-pull-token"}
            if credential_registry
            else {"orchestrator-api-key"}
        )
        or any(secret.get("keyVaultUrl") for secret in secrets)
    ):
        raise ValueError("Scheduler secret references or registry bindings are customized")


def endpoint_specs(values: dict[str, Any], group_id: str) -> list[dict[str, Any]]:
    """Describe all five targets and which private endpoints the template manages."""
    return [
        {
            "name": f"pe-{values[key]}",
            "target": f"{group_id}/providers/{resource_type}/{values[key]}",
            "group": group,
            "ips": ips,
            "deploy": group != "managedEnvironments"
            or values["deployContainerAppsPrivateEndpoint"],
        }
        for key, resource_type, group, ips in (
            ("foundryAccountName", "Microsoft.CognitiveServices/accounts", "account", 3),
            ("storageAccountName", "Microsoft.Storage/storageAccounts", "blob", 1),
            ("cosmosAccountName", "Microsoft.DocumentDB/databaseAccounts", "Sql", 2),
            ("searchServiceName", "Microsoft.Search/searchServices", "searchService", 1),
            (
                "containerAppsEnvironmentName",
                "Microsoft.App/managedEnvironments",
                "managedEnvironments",
                1,
            ),
        )
    ]


def validate_existing_host(
    cli: AzureCli, account_id: str, values: dict[str, Any]
) -> tuple[bool, bool]:
    """Reject changes to immutable host bindings instead of implicitly replacing customer data."""
    projects = cli.collection(f"{account_id}/projects", FOUNDRY_API)
    if not any(
        project["name"].split("/")[-1].casefold() == values["projectName"].casefold()
        for project in projects
    ):
        if projects:
            raise ValueError(
                "Another Foundry project already exists in this account. Use the exact "
                "original project name; never create a near-name project to reuse its connections."
            )
        return False, False
    project_id = f"{account_id}/projects/{values['projectName']}"
    hosts = cli.collection(f"{project_id}/capabilityHosts", HOST_API)
    if not hosts:
        raise ValueError(
            "The project already exists without a verifiable BYO Capability Host. "
            "Stop before changing its connections; inspect initialization and use an "
            "explicitly approved recovery. The deployment never deletes or resets hosts."
        )
    if len(hosts) != 1 or hosts[0]["name"].split("/")[-1].casefold() != "agents":
        raise ValueError(
            "An incompatible project Capability Host already exists. A legacy automatic "
            "host cannot be converted by rerunning stage two; review data retention and "
            "approve a separate recovery. The deployment never deletes or resets hosts."
        )
    if hosts[0]["properties"].get("provisioningState") != "Succeeded":
        raise ValueError(
            "Existing project Capability Host is not ready; no connections were changed"
        )
    managed_agent_data = values.get("useMicrosoftManagedAgentData", False) is True
    expected = {
        "storageConnections": ["agent-storage"],
        "threadStorageConnections": ["agent-cosmos"],
        "vectorStoreConnections": ["agent-search"],
    }
    host_properties = hosts[0]["properties"]
    invalid_bindings = (
        any(host_properties.get(key) not in (None, []) for key in expected)
        if managed_agent_data
        else any(host_properties.get(key) != value for key, value in expected.items())
    )
    if invalid_bindings:
        raise ValueError(
            "Capability Host bindings are immutable; a migration needs separate approval"
        )
    connection_rows = cli.collection(f"{project_id}/connections", FOUNDRY_API)
    connections = {item["name"].split("/")[-1]: item["properties"] for item in connection_rows}
    if len(connections) != len(connection_rows):
        raise ValueError("Foundry connection inventory contains duplicate names")
    if managed_agent_data and {"agent-storage", "agent-cosmos", "agent-search"}.intersection(
        connections
    ):
        raise ValueError("Microsoft-managed agent data cannot adopt existing BYO connections")
    targets = endpoint_specs(values, cli.group_id)
    required_connections = (
        ()
        if managed_agent_data
        else (
            (
                "agent-storage",
                targets[1]["target"],
                "AzureStorageAccount",
                {f"https://{values['storageAccountName']}.blob.core.windows.net"},
            ),
            (
                "agent-cosmos",
                targets[2]["target"],
                "CosmosDb",
                {
                    f"https://{values['cosmosAccountName']}.documents.azure.com",
                    f"https://{values['cosmosAccountName']}.documents.azure.com:443",
                },
            ),
            (
                "agent-search",
                targets[3]["target"],
                "CognitiveSearch",
                {f"https://{values['searchServiceName']}.search.windows.net"},
            ),
        )
    )
    for name, target, category, endpoints in required_connections:
        connection = connections.get(name, {})
        if (
            connection.get("authType") != "AAD"
            or connection.get("category") != category
            or connection.get("target", "").rstrip("/").casefold()
            not in {endpoint.casefold() for endpoint in endpoints}
            or not _same_id(connection.get("metadata", {}).get("ResourceId", ""), target)
        ):
            raise ValueError(f"Refusing to redirect an existing Capability Host connection: {name}")
    if values["applicationInsightsName"] and "azbrief-app-insights" in connections:
        connection = connections["azbrief-app-insights"]
        target = (
            f"{cli.group_id}/providers/Microsoft.Insights/components/"
            f"{values['applicationInsightsName']}"
        )
        if (
            connection.get("category") != "AppInsights"
            or connection.get("authType") != "ProjectManagedIdentity"
            or not _same_id(connection.get("target", ""), target)
            or not _same_id(connection.get("metadata", {}).get("ResourceId", ""), target)
        ):
            raise ValueError("Refusing to redirect an existing Foundry telemetry connection")
    return True, bool(values["applicationInsightsName"] and "azbrief-app-insights" in connections)


def prepare(cli: AzureCli, values: dict[str, Any]) -> dict[str, Any]:
    """Inventory live resources and derive create flags without modifying existing subnets."""
    cli.assert_context()
    validate_runtime_setup(values)
    vnet_id = (
        f"/subscriptions/{cli.subscription}/resourceGroups/{values['virtualNetworkResourceGroupName']}"
        f"/providers/Microsoft.Network/virtualNetworks/{values['virtualNetworkName']}"
    )
    vnet = cli.get(vnet_id, NETWORK_API)
    plans = plan_subnets(values, vnet)
    resources = cli.json(
        "resource", "list", "--resource-group", cli.group, "--subscription", cli.subscription
    )
    if not isinstance(resources, list) or any(not isinstance(row, dict) for row in resources):
        raise ValueError("Resource inventory must be a complete array before deployment")
    by_id = {resource["id"].casefold(): resource for resource in resources}
    values = {**values, **automatic_service_plan(cli, values, by_id)}
    specs = endpoint_specs(values, cli.group_id)
    for resource in resources:
        if (
            "/providers/microsoft.storage/storageaccounts/" in resource["id"].casefold()
            and (resource.get("tags") or {}).get("deploymentProfile") == PROFILE
            and not _same_id(resource["id"], specs[1]["target"])
        ):
            raise ValueError(
                "Another KT-profile Storage Account exists. Complete the separately approved "
                "single-account migration before deploying; existing accounts are not deleted."
            )
    if len({spec["name"].casefold() for spec in specs}) != len(specs):
        raise ValueError("Resource names must give each private endpoint a distinct name")
    planned_ids = [spec["target"] for spec in specs] + [
        f"{cli.group_id}/providers/Microsoft.App/containerApps/{values['containerAppName']}",
        f"{cli.group_id}/providers/Microsoft.ManagedIdentity/userAssignedIdentities/id-{values['containerAppName']}",
    ]
    job_id = f"{cli.group_id}/providers/Microsoft.App/jobs/{values['schedulerJobName']}"
    telemetry_connection = ""
    if values["deployCapabilityHost"] and values["applicationInsightsName"]:
        insights_id = f"{cli.group_id}/providers/Microsoft.Insights/components/{values['applicationInsightsName']}"
        props = cli.get(insights_id, "2020-02-02")["properties"]
        if props.get("DisableLocalAuth") is not True or not props.get("ConnectionString"):
            raise ValueError(
                "Existing Application Insights must be Entra-only with a connection string"
            )
        telemetry_connection = props["ConnectionString"]
    if (
        values["deployCapabilityHost"]
        and values["deploySchedulerJob"]
        and job_id.casefold() in by_id
    ):
        if (by_id[job_id.casefold()].get("tags") or {}).get("deploymentProfile") != PROFILE:
            raise ValueError("Refusing to overwrite an operational or foreign scheduler Job")
        job_identity = cli.get(planned_ids[-1], "2023-01-31")["properties"]
        validate_scheduler_projection(
            cli.get(job_id, CONTAINER_ENV_API),
            {**values, "tenantId": cli.tenant},
            cli.group_id,
            job_identity["clientId"],
            telemetry_connection,
        )
    for resource_id in planned_ids:
        existing = by_id.get(resource_id.casefold())
        if (
            existing is not None
            and (existing.get("tags") or {}).get("deploymentProfile") != PROFILE
        ):
            raise ValueError(
                f"Refusing to modify a resource not owned by the KT profile: {resource_id}"
            )
    if values["deployCapabilityHost"] and any(
        resource_id.casefold() not in by_id for resource_id in planned_ids
    ):
        raise ValueError(
            "Stage two requires a complete existing KT foundation; run stage one first"
        )
    role_names: dict[str, str] = {}
    if control_plane_role_targets(values, cli.group_id):
        identity = cli.get(planned_ids[-1], "2023-01-31")["properties"]
        role_names = existing_control_plane_roles(cli, values, identity["principalId"])
        if not values["provisionEmail"] and values["communicationServicesEndpoint"]:
            acs_id = (
                f"{cli.group_id}/providers/Microsoft.Communication/communicationServices/"
                f"{values['communicationServicesName']}"
            )
            acs = cli.get(acs_id, "2023-03-31")["properties"]
            if (
                acs.get("provisioningState") != "Succeeded"
                or (urlsplit(values["communicationServicesEndpoint"]).hostname or "").casefold()
                != str(acs.get("hostName", "")).casefold()
            ):
                raise ValueError("Existing ACS must be ready and match the configured endpoint")
            definition = cli.get(
                f"/subscriptions/{cli.subscription}/providers/Microsoft.Authorization/"
                f"roleDefinitions/{values['emailSenderRoleDefinitionId']}",
                "2022-04-01",
            )["properties"]
            permissions = definition.get("permissions", [])
            actions = {
                action.casefold()
                for permission in permissions
                for action in permission.get("actions", [])
            }
            allowed = EMAIL_SENDER_ACTIONS | {
                "microsoft.communication/emailservices/read",
                "microsoft.communication/operations/read",
            }
            if (
                definition.get("type") != "CustomRole"
                or not EMAIL_SENDER_ACTIONS <= actions <= allowed
                or any(
                    permission.get(key)
                    for permission in permissions
                    for key in ("notActions", "dataActions", "notDataActions")
                )
                or not any(
                    _same_id(scope, f"/subscriptions/{cli.subscription}")
                    or _same_id(scope, cli.group_id)
                    or _same_id(scope, acs_id)
                    for scope in definition.get("assignableScopes", [])
                )
            ):
                raise ValueError(
                    "Email sender role must be an approved minimal ACS custom role; "
                    "wildcards, deletion and key management are not granted automatically"
                )
    reuse_foundry = False
    reuse_telemetry = False
    if specs[0]["target"].casefold() in by_id:
        reuse_foundry, reuse_telemetry = validate_existing_host(cli, specs[0]["target"], values)
    app_id = planned_ids[-2]
    reuse_existing_api_key = False
    if app_id.casefold() in by_id:
        app = cli.get(app_id, CONTAINER_ENV_API)["properties"]
        containers = app.get("template", {}).get("containers", [])
        if len(containers) != 1 or containers[0].get("image") not in {
            values["bootstrapImage"],
            LEGACY_BOOTSTRAP_IMAGE,
        }:
            raise ValueError(
                "Container App is already promoted; foundation cannot downgrade it to bootstrap"
            )
        container = containers[0]
        configuration = app.get("configuration", {})
        if container.get("command") or container.get("args"):
            raise ValueError("Foundation cannot overwrite a customized Container App entry point")
        if container["image"] == LEGACY_BOOTSTRAP_IMAGE:
            if (
                container.get("name") != "bootstrap"
                or container.get("env")
                or configuration.get("secrets")
                or configuration.get("registries")
            ):
                raise ValueError("Foundation can replace only an uncustomized legacy bootstrap")
        else:
            entries = container.get("env", [])
            env = {entry["name"]: entry for entry in entries}
            if (
                values["deployCapabilityHost"]
                and not reuse_foundry
                and not _same_id(
                    env.get("FOUNDRY_PROJECT_ENDPOINT", {}).get("value", ""),
                    f"https://{values['foundryAccountName']}.services.ai.azure.com/"
                    f"api/projects/{values['projectName']}",
                )
            ):
                raise ValueError(
                    "Initial stage two must use the exact project name bound by stage one"
                )
            admin_ui, archive_ui = _web_ui_flags(values)
            matches_web_request = (admin_ui or archive_ui) and all(
                _matches_bool_setting(env.get(name, {}).get("value"), enabled)
                for name, enabled in (
                    ("ADMIN_UI_ENABLED", admin_ui),
                    ("ARCHIVE_UI_ENABLED", archive_ui),
                )
            )
            current_private_anonymous = matches_web_request and all(
                _matches_bool_setting(env.get(name, {}).get("value"), False)
                for name in ("ADMIN_REQUIRE_AUTH", "ARCHIVE_REQUIRE_AUTH")
            )
            if (
                _private_anonymous_web_ui(values)
                and any(
                    _matches_bool_setting(env.get(name, {}).get("value"), True)
                    for name in ("ADMIN_UI_ENABLED", "ARCHIVE_UI_ENABLED")
                )
                and not current_private_anonymous
            ):
                raise ValueError(
                    "Existing web access requires authentication; refusing automatic anonymous downgrade"
                )
            flags = {
                "ADMIN_UI_ENABLED": str(admin_ui and matches_web_request).lower(),
                "ADMIN_REQUIRE_AUTH": str(not current_private_anonymous).lower(),
                "ARCHIVE_UI_ENABLED": str(archive_ui and matches_web_request).lower(),
                "ARCHIVE_REQUIRE_AUTH": str(not current_private_anonymous).lower(),
                "FEEDBACK_UI_ENABLED": "false",
                "MAX_CONCURRENT_ANALYSES": "1",
            }
            ingress = configuration.get("ingress", {})
            fqdn = ingress.get("fqdn")
            if matches_web_request and (not isinstance(fqdn, str) or not fqdn):
                raise ValueError("Authenticated web setup requires the Container App HTTPS FQDN")
            web_env = {
                "ADMIN_ALLOWED_PRINCIPALS": (
                    values["adminAllowedPrincipals"]
                    if admin_ui and matches_web_request and not current_private_anonymous
                    else ""
                ),
                "ARCHIVE_ALLOWED_PRINCIPALS": (
                    values["archiveAllowedPrincipals"]
                    if archive_ui and matches_web_request and not current_private_anonymous
                    else ""
                ),
                "ADMIN_CONFIG_BLOB_URL": (
                    f"https://{values['storageAccountName']}.blob.core.windows.net/"
                    "azbrief-state/admin-config.json"
                    if admin_ui and matches_web_request
                    else ""
                ),
                "ARCHIVE_BASE_URL": (
                    f"https://{fqdn}" if archive_ui and matches_web_request else ""
                ),
                "ADMIN_READINESS_RESOURCE_GROUP": cli.group,
                "ADMIN_READINESS_FOUNDRY_ACCOUNT": values["foundryAccountName"],
                "ADMIN_READINESS_FOUNDRY_PROJECT": values["projectName"],
            }
            web_env_matches = True
            for name, expected in web_env.items():
                if name not in env:
                    continue
                actual = env[name].get("value")
                if not isinstance(actual, str):
                    matches = False
                elif name.endswith("_ALLOWED_PRINCIPALS"):
                    matches = {
                        part.strip().casefold() for part in actual.split(",") if part.strip()
                    } == {part.strip().casefold() for part in expected.split(",") if part.strip()}
                elif name.startswith("ADMIN_READINESS_"):
                    matches = actual.casefold() == expected.casefold()
                else:
                    matches = actual == expected
                web_env_matches = web_env_matches and matches
            readiness_targets: dict[str, list[Any]] = {
                "ADMIN_READINESS_CONTAINER_ENVIRONMENTS": [values["containerAppsEnvironmentName"]],
                "ADMIN_READINESS_CONTAINER_APPS": [values["containerAppName"]],
                "ADMIN_READINESS_SUPPORT_RESOURCES": [
                    {
                        "id": key,
                        "label": label,
                        "name": name,
                        "resource_type": resource_type,
                        "api_version": api,
                    }
                    for key, label, name, resource_type, api in (
                        (
                            "managed_identity",
                            "User-assigned managed identity",
                            f"id-{values['containerAppName']}",
                            "Microsoft.ManagedIdentity/userAssignedIdentities",
                            "2023-01-31",
                        ),
                        (
                            "state_storage",
                            "Shared Storage (State, Archive, Foundry)",
                            values["storageAccountName"],
                            "Microsoft.Storage/storageAccounts",
                            "2023-05-01",
                        ),
                        (
                            "cosmos",
                            "Cosmos DB",
                            values["cosmosAccountName"],
                            "Microsoft.DocumentDB/databaseAccounts",
                            "2024-11-15",
                        ),
                        (
                            "search",
                            "Azure AI Search",
                            values["searchServiceName"],
                            "Microsoft.Search/searchServices",
                            "2023-11-01",
                        ),
                    )
                ],
            }
            for name, expected_rows in readiness_targets.items():
                if name not in env:
                    continue
                raw = env[name].get("value")
                if not isinstance(raw, str):
                    raise ValueError(f"{name} requires a JSON or Base64 JSON value")
                actual_rows = Settings._decode_readiness_json(raw)
                if actual_rows is None:
                    raise ValueError(f"{name} contains invalid readiness metadata")
                if not isinstance(actual_rows, list):
                    raise ValueError(f"{name} readiness metadata must be an array")
                if name == "ADMIN_READINESS_SUPPORT_RESOURCES":
                    expected_keys = set(expected_rows[0])
                    if any(
                        not isinstance(row, dict)
                        or set(row) != expected_keys
                        or any(not isinstance(value, str) for value in row.values())
                        for row in actual_rows
                    ):
                        raise ValueError(
                            f"{name} readiness resource descriptors have invalid fields"
                        )
                elif any(not isinstance(row, str) for row in actual_rows):
                    raise ValueError(f"{name} readiness metadata must contain only resource names")
                if sorted(
                    json.dumps(row, sort_keys=True).casefold() for row in actual_rows
                ) != sorted(json.dumps(row, sort_keys=True).casefold() for row in expected_rows):
                    raise ValueError(
                        f"Foundation cannot overwrite customized readiness metadata: {name}"
                    )
            expected_setup = {
                **runtime_setup_env(values),
                "APPLICATIONINSIGHTS_CONNECTION_STRING": telemetry_connection,
            }
            for name, expected_value in expected_setup.items():
                if name not in env:
                    continue
                actual_value = env[name].get("value")
                if not isinstance(actual_value, str):
                    raise ValueError(f"{name} requires a string")
                if values["deployCapabilityHost"] and actual_value == "":
                    continue
                if (
                    name == "OTEL_ENABLED"
                    and values["deployCapabilityHost"]
                    and _matches_bool_setting(actual_value, False)
                ):
                    continue
                if name in {"ADMIN_READINESS_PROMPT_AGENTS", "ADMIN_READINESS_CONTAINER_JOBS"}:
                    if actual_value == expected_value:
                        continue
                    actual_data = Settings._decode_readiness_json(actual_value)
                    expected_data = Settings._decode_readiness_json(expected_value)
                    if values["deployCapabilityHost"] and actual_data in ({}, []):
                        continue
                    if actual_data != expected_data:
                        raise ValueError(
                            f"Foundation cannot overwrite configured runtime targets: {name}"
                        )
                elif (
                    not _matches_bool_setting(actual_value, expected_value == "true")
                    if name == "OTEL_ENABLED"
                    else actual_value != expected_value
                ):
                    raise ValueError(
                        f"Foundation cannot overwrite configured runtime settings: {name}"
                    )
            legacy_web_env_names = FOUNDATION_ENV_NAMES | WEB_ENV_NAMES
            legacy_readiness_env_names = legacy_web_env_names | READINESS_ENV_NAMES
            full_env_names = legacy_readiness_env_names | RUNTIME_SETUP_ENV_NAMES
            permitted_env_names = (
                (legacy_web_env_names, legacy_readiness_env_names, full_env_names)
                if matches_web_request
                else (
                    FOUNDATION_ENV_NAMES,
                    legacy_web_env_names,
                    legacy_readiness_env_names,
                    full_env_names,
                )
            )
            permitted_secrets = {"orchestrator-api-key", "ghcr-pull-token"}
            if matches_web_request and not current_private_anonymous:
                permitted_secrets.add("microsoft-provider-authentication-secret")
            if (
                container.get("name") != "azbrief"
                or set(env) not in permitted_env_names
                or len(entries) != len(env)
                or any(
                    (
                        not _matches_bool_setting(env[name].get("value"), value == "true")
                        if value in {"true", "false"}
                        else env[name].get("value") != value
                    )
                    for name, value in flags.items()
                )
                or not web_env_matches
                or env["API_KEY"].get("secretRef") != "orchestrator-api-key"
                or env["API_KEY"].get("value")
                or app.get("template", {}).get("scale", {}).get("minReplicas")
                != (1 if admin_ui and matches_web_request else 0)
                or app.get("template", {}).get("scale", {}).get("maxReplicas") != 1
                or not _matches_container_resources(
                    container.get("resources"), 0.25, "0.5Gi", "1Gi"
                )
                or configuration.get("activeRevisionsMode") != "Single"
                or configuration.get("ingress", {}).get("targetPort") != 8000
                or any(
                    registry.get("server") != "ghcr.io"
                    or registry.get("passwordSecretRef") != "ghcr-pull-token"
                    or registry.get("identity")
                    for registry in configuration.get("registries", [])
                )
                or {secret["name"] for secret in configuration.get("secrets", [])}
                - permitted_secrets
                or (
                    matches_web_request
                    and not current_private_anonymous
                    and sum(
                        secret.get("name") == "microsoft-provider-authentication-secret"
                        for secret in configuration.get("secrets", [])
                    )
                    != 1
                )
            ):
                raise ValueError("Foundation cannot overwrite a promoted or customized AzBrief app")
            api_secrets = [
                secret
                for secret in configuration.get("secrets", [])
                if secret.get("name") == "orchestrator-api-key"
            ]
            if len(api_secrets) != 1:
                raise ValueError(
                    "Existing AzBrief API key is missing or ambiguous; refusing automatic rotation"
                )
            reuse_existing_api_key = True
    existing_subnets = {
        subnet["name"].casefold(): subnet for subnet in vnet["properties"]["subnets"]
    }
    for plan, spec, api in (
        (plans[1], specs[0], FOUNDRY_API),
        (plans[2], specs[4], CONTAINER_ENV_API),
    ):
        subnet = existing_subnets.get(plan.name.casefold())
        owner = None
        if spec["target"].casefold() in by_id:
            owner = cli.get(spec["target"], api)["properties"]
            expected = f"{vnet_id}/subnets/{plan.name}"
            if plan.role == "foundry":
                bindings = owner.get("networkInjections", [])
                matches = any(
                    binding.get("scenario") == "agent"
                    and _same_id(binding.get("subnetArmId", ""), expected)
                    for binding in bindings
                )
            else:
                vnet_configuration = owner.get("vnetConfiguration", {})
                matches = _same_id(vnet_configuration.get("infrastructureSubnetId", ""), expected)
                if vnet_configuration.get("internal") is not True:
                    raise ValueError(
                        "Existing Container Apps Environment is external. KT Policy requires "
                        "vnetConfiguration.internal=true at creation; recreate the foundation "
                        "instead of attempting an in-place conversion."
                    )
                if (
                    _private_anonymous_web_ui(values)
                    and owner.get("publicNetworkAccess") != "Disabled"
                ):
                    raise ValueError(
                        "Private anonymous web access requires public network access Disabled "
                        "before deployment; missing or unknown values are not private isolation."
                    )
            if not matches:
                raise ValueError(
                    f"Existing {spec['name']} is not injected into the requested subnet"
                )
        if subnet is not None and owner is None:
            props = subnet["properties"]
            if any(
                props.get(key)
                for key in (
                    "ipConfigurations",
                    "privateEndpoints",
                    "serviceAssociationLinks",
                    "resourceNavigationLinks",
                )
            ):
                raise ValueError(
                    f"{plan.name} is occupied; never share another service's delegated subnet"
                )

    required_ips = 0
    for spec in specs:
        if not spec["deploy"]:
            continue
        endpoint_id = f"{cli.group_id}/providers/Microsoft.Network/privateEndpoints/{spec['name']}"
        if endpoint_id.casefold() not in by_id:
            required_ips += spec["ips"]
            continue
        props = cli.get(endpoint_id, NETWORK_API)["properties"]
        connections = props.get("privateLinkServiceConnections", [])
        if (
            not _same_id(props["subnet"]["id"], f"{vnet_id}/subnets/{plans[0].name}")
            or len(connections) != 1
            or not _same_id(connections[0]["properties"]["privateLinkServiceId"], spec["target"])
            or connections[0]["properties"]["groupIds"] != [spec["group"]]
            or connections[0]["properties"]["privateLinkServiceConnectionState"]["status"]
            != "Approved"
        ):
            raise ValueError(
                f"Existing private endpoint has incompatible binding or approval: {spec['name']}"
            )
    pe = plans[0]
    if not pe.create and required_ips:
        available = 0
        # 기존 IP 구성 수만 세면 Cosmos/Foundry의 여러 NIC IP를 놓칠 수 있다.
        for address in range(
            int(pe.network.network_address) + 4,
            min(int(pe.network.broadcast_address), int(pe.network.network_address) + 260),
        ):
            result = cli.json(
                "network",
                "vnet",
                "check-ip-address",
                "--subscription",
                cli.subscription,
                "--resource-group",
                values["virtualNetworkResourceGroupName"],
                "--name",
                values["virtualNetworkName"],
                "--ip-address",
                str(IPv4Address(address)),
            )
            if not isinstance(result.get("available"), bool):
                raise ValueError("Azure did not return a definitive IP availability result")
            available += int(result["available"])
            if available >= required_ips:
                break
        if available < required_ips:
            raise ValueError(
                f"Could not verify {required_ips} free IPs in {pe.name} within the bounded check"
            )

    zones = [
        "privatelink.cognitiveservices.azure.com",
        "privatelink.openai.azure.com",
        "privatelink.services.ai.azure.com",
        "privatelink.blob.core.windows.net",
        "privatelink.documents.azure.com",
        "privatelink.search.windows.net",
        f"privatelink.{values['location']}.azurecontainerapps.io",
    ]
    reuse = dict(values["existingPrivateDnsZoneIds"])
    if set(reuse) - set(zones):
        raise ValueError("Unexpected private DNS zone name")
    linked_zone_ids: dict[str, str] = {}
    subscription_zones = cli.collection(
        f"/subscriptions/{cli.subscription}/providers/Microsoft.Network/privateDnsZones",
        "2024-06-01",
    )
    for candidate in subscription_zones:
        zone = str(candidate.get("name", "")).casefold()
        zone_id = str(candidate.get("id", ""))
        if zone not in zones or not zone_id:
            continue
        links = cli.collection(f"{zone_id}/virtualNetworkLinks", "2024-06-01")
        matching = [
            link for link in links if _same_id(link["properties"]["virtualNetwork"]["id"], vnet_id)
        ]
        if not matching:
            continue
        if len(matching) != 1:
            raise ValueError(f"Unexpected multiple VNet links in Private DNS zone: {zone}")
        if matching[0]["properties"].get("registrationEnabled") is not False:
            raise ValueError(f"Private Endpoint DNS zone must use a non-registration link: {zone}")
        previous = linked_zone_ids.get(zone)
        if previous and not _same_id(previous, zone_id):
            raise ValueError(f"VNet is linked to multiple Private DNS zones for namespace: {zone}")
        linked_zone_ids[zone] = zone_id
    for zone, zone_id in linked_zone_ids.items():
        explicit = reuse.get(zone)
        if explicit and not _same_id(explicit, zone_id):
            raise ValueError(f"Explicit DNS zone conflicts with the VNet-linked zone: {zone}")
        reuse[zone] = zone_id
    for zone in zones:
        local_id = f"{cli.group_id}/providers/Microsoft.Network/privateDnsZones/{zone}"
        if zone not in reuse and local_id.casefold() in by_id:
            reuse[zone] = local_id
        if zone not in reuse:
            continue
        zone_id = reuse[zone]
        if not isinstance(zone_id, str) or not zone_id.casefold().endswith(
            f"/microsoft.network/privatednszones/{zone}"
        ):
            raise ValueError(f"Invalid private DNS zone resource ID for {zone}")
        links = cli.collection(f"{zone_id}/virtualNetworkLinks", "2024-06-01")
        if not any(
            _same_id(link["properties"]["virtualNetwork"]["id"], vnet_id)
            and link["properties"].get("registrationEnabled") is False
            for link in links
        ):
            raise ValueError(f"Existing DNS zone needs a non-registration link to the VNet: {zone}")
    prepared = dict(
        values,
        existingPrivateDnsZoneIds=reuse,
        reuseExistingApiKey=reuse_existing_api_key,
        existingControlPlaneRoleAssignmentNames=role_names,
        reuseExistingFoundrySetup=values["deployCapabilityHost"] and reuse_foundry,
        reuseExistingTelemetryConnection=values["deployCapabilityHost"] and reuse_telemetry,
    )
    for plan in plans:
        prepared[plan.create_parameter] = plan.create
        logger.info(
            "kt_subnet_plan",
            subnet_role=plan.role,
            subnet=plan.name,
            cidr=str(plan.network),
            action="create" if plan.create else "reuse_unchanged",
        )
    logger.info(
        "kt_private_endpoint_plan",
        new_ip_budget=required_ips,
        reused_dns_zones=len(reuse),
        container_apps_private_endpoint_managed=values["deployContainerAppsPrivateEndpoint"],
    )
    return prepared


def wait_for_host(cli: AzureCli, scope: str, expected_name: str | None = None) -> None:
    """Wait for the injected account host or requested project host without creating duplicates."""
    for attempt in range(31):
        hosts = cli.collection(f"{scope}/capabilityHosts", HOST_API)
        matching = [
            h
            for h in hosts
            if expected_name is None
            or h["name"].split("/")[-1].casefold() == expected_name.casefold()
        ]
        if len(matching) == 1:
            state = matching[0]["properties"].get("provisioningState")
            if state == "Succeeded":
                return
            if state in {"Failed", "Canceled"}:
                raise RuntimeError(f"Capability Host provisioning failed at {scope}: {state}")
        elif len(matching) > 1:
            raise RuntimeError(f"Unexpected multiple Capability Hosts at {scope}")
        if attempt < 30:
            time.sleep(10)
    raise RuntimeError(
        f"Capability Host did not become ready at {scope}; inspect the deployment, do not recreate or purge the account"
    )


def verify_foundation(cli: AzureCli, values: dict[str, Any]) -> None:
    """Read back public-network controls and all approved endpoint/DNS bindings."""
    prepared = prepare(cli, values)
    values = prepared
    missing_roles = (
        control_plane_role_targets(values, cli.group_id).keys()
        - prepared["existingControlPlaneRoleAssignmentNames"].keys()
    )
    if missing_roles:
        raise RuntimeError(f"Required UAMI role assignments are missing: {sorted(missing_roles)}")
    if any(prepared[row[3]] for row in SUBNETS):
        raise RuntimeError("A required subnet is absent after deployment")
    versions = (
        FOUNDRY_API,
        "2023-05-01",
        "2024-11-15",
        "2023-11-01",
        CONTAINER_ENV_API,
    )
    for spec, api in zip(endpoint_specs(values, cli.group_id), versions):
        props = cli.get(spec["target"], api)["properties"]
        if str(props.get("publicNetworkAccess", "")).casefold() != "disabled":
            raise RuntimeError(f"Public network access is not disabled: {spec['target']}")
    resources = cli.json(
        "resource", "list", "--resource-group", cli.group, "--subscription", cli.subscription
    )
    ids = {resource["id"].casefold() for resource in resources}
    for spec in endpoint_specs(values, cli.group_id):
        if not spec["deploy"]:
            continue
        endpoint_id = f"{cli.group_id}/providers/Microsoft.Network/privateEndpoints/{spec['name']}"
        if endpoint_id.casefold() not in ids:
            raise RuntimeError(f"Required private endpoint is absent: {spec['name']}")
        endpoint_state = cli.get(endpoint_id, NETWORK_API)["properties"].get("provisioningState")
        if endpoint_state != "Succeeded":
            raise RuntimeError(
                f"Private endpoint is not ready: {spec['name']} "
                f"(provisioningState={endpoint_state!r}); Approved alone is not readiness. "
                "Inspect deployment operations before retrying or deleting resources."
            )
    admin_ui, archive_ui = _web_ui_flags(values)
    if values["deployCapabilityHost"] and values["deploySchedulerJob"]:
        job = cli.get(
            f"{cli.group_id}/providers/Microsoft.App/jobs/{values['schedulerJobName']}",
            CONTAINER_ENV_API,
        )
        if job["properties"].get("provisioningState") != "Succeeded":
            raise RuntimeError("Scheduler Job provisioning did not succeed")
        job_identity = cli.get(
            f"{cli.group_id}/providers/Microsoft.ManagedIdentity/userAssignedIdentities/"
            f"id-{values['containerAppName']}",
            "2023-01-31",
        )["properties"]
        telemetry_connection = ""
        if values["applicationInsightsName"]:
            telemetry_connection = cli.get(
                f"{cli.group_id}/providers/Microsoft.Insights/components/{values['applicationInsightsName']}",
                "2020-02-02",
            )["properties"]["ConnectionString"]
        validate_scheduler_projection(
            job,
            {**values, "tenantId": cli.tenant},
            cli.group_id,
            job_identity["clientId"],
            telemetry_connection,
        )
    if values["deployCapabilityHost"]:
        app_id = (
            f"{cli.group_id}/providers/Microsoft.App/containerApps/{values['containerAppName']}"
        )
        app = cli.get(app_id, CONTAINER_ENV_API)["properties"]
        env = {entry["name"]: entry for entry in app["template"]["containers"][0]["env"]}
        for name, expected in runtime_setup_env(values).items():
            if env.get(name, {}).get("value") != expected:
                raise RuntimeError(f"Stage-two runtime setting was not applied: {name}")
    if admin_ui or archive_ui:
        anonymous = _private_anonymous_web_ui(values)
        expected_flags = {
            "ADMIN_UI_ENABLED": str(admin_ui).lower(),
            "ARCHIVE_UI_ENABLED": str(archive_ui).lower(),
            "ADMIN_REQUIRE_AUTH": str(not anonymous).lower(),
            "ARCHIVE_REQUIRE_AUTH": str(not anonymous).lower(),
        }
        if any(env[name].get("value") != expected for name, expected in expected_flags.items()):
            raise RuntimeError("Stage-two Admin/Archive activation was not applied")
        auth = cli.get(f"{app_id}/authConfigs/current", CONTAINER_ENV_API)["properties"]
        provider = auth.get("identityProviders", {}).get("azureActiveDirectory", {})
        registration = provider.get("registration", {})
        audiences = provider.get("validation", {}).get("allowedAudiences", [])
        client_id = values["adminEntraClientId"]
        origin = f"https://{app['configuration']['ingress']['fqdn']}"
        if (
            auth.get("platform", {}).get("enabled") is not (not anonymous)
            or auth.get("globalValidation", {}).get("unauthenticatedClientAction")
            != "AllowAnonymous"
            or auth.get("httpSettings", {}).get("requireHttps") is not True
            or provider.get("enabled") is not (not anonymous)
            or (
                not anonymous
                and (
                    registration.get("clientId") != client_id
                    or registration.get("clientSecretSettingName")
                    != "microsoft-provider-authentication-secret"
                    or registration.get("openIdIssuer")
                    != f"https://login.microsoftonline.com/{cli.tenant}/v2.0"
                    or not isinstance(audiences, list)
                    or not all(isinstance(audience, str) for audience in audiences)
                    or set(audiences) != {client_id, f"api://{client_id}"}
                )
            )
            or auth.get("login", {}).get("allowedExternalRedirectUrls") != [origin]
            or auth.get("login", {}).get("preserveUrlFragmentsForLogins") is not False
        ):
            raise RuntimeError("Stage-two EasyAuth does not match the approved tenant and settings")


def run(cli: AzureCli, values: dict[str, Any], mode: str, stage: str = "both") -> None:
    """Require fresh preflight for every operation, including the second deployment stage."""
    configure_redaction(values)
    if mode not in {"preflight", "validate", "what-if", "deploy"}:
        raise ValueError(f"Unsupported KT operation: {mode}")
    if stage not in {"both", "foundation", "complete"}:
        raise ValueError(f"Unsupported KT stage: {stage}")
    prepared = prepare(cli, {**values, "deployCapabilityHost": stage == "complete"})
    account_id = endpoint_specs(values, cli.group_id)[0]["target"]
    if stage == "complete":
        wait_for_host(cli, account_id)
    if mode == "preflight":
        logger.info(
            "kt_preflight_complete", stage=stage, azure_mutations=False, application_ready=False
        )
        return
    if mode in {"validate", "what-if"}:
        result = cli.deploy(prepared, mode, "kt-private-validation")
        logger.info("kt_validation_result", mode=mode, stage=stage, result=redact_fields(result))
        return
    if stage != "complete":
        result = cli.deploy(prepared, "create", "kt-private-foundation")
        if result["properties"]["provisioningState"] != "Succeeded":
            raise RuntimeError("KT foundation deployment did not succeed")
        wait_for_host(cli, account_id)
    if stage != "foundation":
        prepared = prepare(cli, {**values, "deployCapabilityHost": True})
        result = cli.deploy(prepared, "create", "kt-private-capability-host")
        if result["properties"]["provisioningState"] != "Succeeded":
            raise RuntimeError("KT Capability Host deployment did not succeed")
        wait_for_host(cli, f"{account_id}/projects/{values['projectName']}", "agents")
    verify_foundation(cli, prepared)
    logger.info(
        "kt_foundation_deployed",
        stage=stage,
        application_ready=False,
        container_apps_private_endpoint_managed=values["deployContainerAppsPrivateEndpoint"],
        outputs=result["properties"]["outputs"]["ktFoundation"]["value"],
        next_step="Verify private DNS/connectivity and complete the KT application handoff in infra/kt/README.md",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--subscription", type=UUID, required=True)
    parser.add_argument("--tenant", type=UUID, required=True)
    parser.add_argument("--resource-group", required=True)
    parser.add_argument("--parameters", type=Path, required=True)
    parser.add_argument(
        "--mode", choices=("preflight", "validate", "what-if", "deploy"), default="preflight"
    )
    parser.add_argument(
        "--stage",
        choices=("both", "foundation", "complete"),
        default="both",
        help="Select complete to apply stage two without replaying the Foundry account",
    )
    args = parser.parse_args()
    try:
        values = load_parameters(args.parameters)
        run(
            AzureCli(str(args.subscription), str(args.tenant), args.resource_group),
            values,
            args.mode,
            args.stage,
        )
    except (
        ValueError,
        KeyError,
        TypeError,
        OSError,
        RuntimeError,
        subprocess.SubprocessError,
    ) as exc:
        logger.error(
            "kt_deployment_failed", error_type=type(exc).__name__, error=redact_text(str(exc))
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
