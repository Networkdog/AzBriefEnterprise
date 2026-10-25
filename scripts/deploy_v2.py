"""Deploy AzBrief v2 infrastructure once and install or update applications separately."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from email.headerregistry import Address
from ipaddress import IPv4Network, ip_network
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit
from uuid import NAMESPACE_URL, UUID, uuid5

import structlog

from scripts.build_v2_ui import derive_resource_names
from scripts.deploy_kt import (
    CONTAINER_ENV_API,
    FOUNDRY_API,
    MANAGEMENT,
    NETWORK_API,
    AzureCli,
    automatic_service_plan,
    normalize_region,
    plan_subnets,
    validate_existing_host,
    wait_for_host,
)
from src.error_logging import configure_redaction

logger = structlog.get_logger(__name__)
ROOT = Path(__file__).resolve().parents[1]
V2 = ROOT / "infra" / "v2"
GRAPH = "https://graph.microsoft.com/v1.0/"
WEB_SECRET_NAME = "azbrief-web-client-secret"
PROFILE = "kt-private-v2"
PREFIX = "azbrief.v2."
TAG_FIELDS = {
    "deploymentName": "deployment",
    "location": "location",
    "containerAppsEnvironmentName": "environment",
    "containerAppName": "app",
    "schedulerJobName": "job",
    "controlPlaneIdentityName": "identity",
    "storageAccountName": "storage",
    "cosmosAccountName": "cosmos",
    "searchServiceName": "search",
    "logAnalyticsWorkspaceName": "workspace",
    "applicationInsightsName": "insights",
    "communicationServicesName": "acs",
    "emailServiceName": "email",
    "foundryAccountName": "foundry",
    "projectName": "project",
    "foundryHostedAgentName": "hosted",
    "modelDeploymentName": "coreModel",
    "simpleModelDeploymentName": "simpleModel",
    "virtualNetworkResourceGroupName": "vnetRg",
    "virtualNetworkName": "vnet",
    "peSubnetName": "peSubnet",
    "foundrySubnetName": "foundrySubnet",
    "containerAppsSubnetName": "appSubnet",
    "azureMcpServerUrl": "mcpUrl",
    "azureMcpProjectConnectionName": "mcpConnection",
    "azureMcpContainerAppName": "mcpApp",
    "azureMcpIdentityName": "mcpIdentity",
}
OPTION_TAG_KEYS = tuple(PREFIX + f"options{index}" for index in range(8))
RESOURCE_TAG_KEYS = tuple(PREFIX + f"resources{index}" for index in range(8))
SHARED_RESOURCES = {
    "foundryAccountMode": (
        "foundryAccountName",
        "existingFoundryAccountResourceId",
        "foundryAccountResourceId",
        "Microsoft.CognitiveServices/accounts",
        FOUNDRY_API,
    ),
    "storageAccountMode": (
        "storageAccountName",
        "existingStorageAccountResourceId",
        "storageAccountResourceId",
        "Microsoft.Storage/storageAccounts",
        "2023-05-01",
    ),
    "containerAppsEnvironmentMode": (
        "containerAppsEnvironmentName",
        "existingContainerAppsEnvironmentResourceId",
        "containerAppsEnvironmentResourceId",
        "Microsoft.App/managedEnvironments",
        CONTAINER_ENV_API,
    ),
    "communicationServicesMode": (
        "communicationServicesName",
        "existingCommunicationServicesResourceId",
        "communicationServicesResourceId",
        "Microsoft.Communication/communicationServices",
        "2023-04-01",
    ),
}
RESOURCE_BINDING_TYPES = {
    **{entry[2]: entry[3] for entry in SHARED_RESOURCES.values()},
    "logAnalyticsWorkspaceResourceId": "Microsoft.OperationalInsights/workspaces",
    "emailDomainResourceId": "Microsoft.Communication/emailServices/domains",
}
CONTAINER_NAME_FIELDS = {"stateContainerName", "archiveContainerName"}
MCP_AUTOMATION_TYPES = {
    "identityResourceId": "Microsoft.ManagedIdentity/userAssignedIdentities",
    "subnetResourceId": "Microsoft.Network/virtualNetworks/subnets",
    "storageAccountResourceId": "Microsoft.Storage/storageAccounts",
}
OPTION_FIELDS = {
    "emailDataLocation": str,
    "searchSku": str,
    "coreModelName": str,
    "simpleModelName": str,
    "modelVersion": str,
    "modelCapacity": int,
    "simpleModelCapacity": int,
    "existingPrivateDnsZoneIds": dict,
    "tags": dict,
}
TAG_KEYS = frozenset(PREFIX + suffix for suffix in TAG_FIELDS.values()) | {
    PREFIX + "schema",
    PREFIX + "profile",
    PREFIX + "state",
    PREFIX + "acaPeEnabled",
}
APPLICATION_TAG_KEYS = frozenset(
    {
        PREFIX + "applicationState",
        PREFIX + "lastApplicationDeploymentAt",
    }
)
APPLICATION_TAG_DEFAULTS = {
    PREFIX + "applicationState": "not_deployed",
    PREFIX + "lastApplicationDeploymentAt": "",
}
NETWORK_OPTION_FIELDS = frozenset(
    {
        "virtualNetworkMode",
        "virtualNetworkAddressPrefix",
        "peSubnetAddressPrefix",
        "foundrySubnetAddressPrefix",
        "containerAppsSubnetAddressPrefix",
    }
)
MODEL_SELECTION_FIELDS = frozenset(
    {
        "coreModelName",
        "simpleModelName",
        "modelVersion",
        "simpleModelVersion",
        "modelCapacity",
        "simpleModelCapacity",
    }
)
IMAGE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9.-]*/[a-z0-9][a-z0-9._/-]*@sha256:[0-9a-f]{64}$")
NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.()-]{0,254}$")
CONFIG_FIELDS = frozenset(
    {
        "appConfiguration",
        "appTemplate",
        "jobConfiguration",
        "jobTemplate",
        "appEnvironment",
        "jobEnvironment",
        "webAuthConfiguration",
        "existingSecrets",
    }
)
INTERNAL_PARAMETERS = frozenset(
    {
        "infrastructureStage",
        "reuseCoreModel",
        "reuseSimpleModel",
        "useCoreForSimpleModel",
        "reuseExistingFoundrySetup",
        "reuseExistingTelemetryConnection",
        "portalVerificationMode",
        "portalEvidence",
        "portalResourceEvidence",
    }
)


def resource_scope(
    resource_id: str, resource_type: str, subscription: str | None = None
) -> tuple[str, str, str]:
    """Validate an exact typed ARM ID without accepting redirects or cross-subscription targets."""
    if not isinstance(resource_id, str) or any(char in resource_id for char in "?#%\\"):
        raise ValueError("Use an exact typed ARM resource ID")
    parts = resource_id.split("/")
    types = resource_type.split("/")
    if (
        len(parts) != 7 + 2 * (len(types) - 1)
        or parts[0] != ""
        or parts[1].casefold() != "subscriptions"
        or parts[3].casefold() != "resourcegroups"
        or parts[5].casefold() != "providers"
        or parts[6].casefold() != types[0].casefold()
        or any(
            parts[7 + index * 2].casefold() != name.casefold()
            for index, name in enumerate(types[1:])
        )
        or not re.fullmatch(r"[A-Za-z0-9_.()-]{1,90}", parts[4])
        or any(not NAME_PATTERN.fullmatch(parts[index]) for index in range(8, len(parts), 2))
    ):
        raise ValueError(f"Expected a {resource_type} ARM resource ID")
    UUID(parts[2])
    if subscription is not None and parts[2].casefold() != subscription.casefold():
        raise ValueError("Shared resources must be in the explicit target subscription")
    return parts[2], parts[4], parts[-1]


def decode_resource_bindings(tags: dict[str, str]) -> dict[str, str]:
    """Read complete bounded ID metadata while preserving older same-RG contracts."""
    present = [key for key in RESOURCE_TAG_KEYS if key in tags]
    if not present:
        return {}
    if present != list(RESOURCE_TAG_KEYS[: len(present)]) or any(not tags[key] for key in present):
        raise ValueError("Incomplete resource-ID tag chunks")
    bindings = json.loads("".join(tags[key] for key in present))
    if (
        not isinstance(bindings, dict)
        or set(bindings) != set(RESOURCE_BINDING_TYPES) | CONTAINER_NAME_FIELDS
    ):
        raise ValueError("Incomplete or unknown shared resource bindings")
    for field, resource_type in RESOURCE_BINDING_TYPES.items():
        resource_scope(bindings[field], resource_type)
    for field in CONTAINER_NAME_FIELDS:
        if not isinstance(bindings[field], str) or not re.fullmatch(
            r"[a-z0-9](?:[a-z0-9]|-(?!-)){1,61}[a-z0-9]", bindings[field]
        ):
            raise ValueError("Invalid workload Blob container name")
    return bindings


def validate_mcp_automation(value: object, subscription: str | None = None) -> dict[str, str]:
    """Accept only three explicit non-secret setup resources; no implicit identity grants."""
    if not isinstance(value, dict):
        raise ValueError("MCP automation configuration must be an object")
    if not value:
        return {}
    if set(value) != set(MCP_AUTOMATION_TYPES):
        raise ValueError(
            "MCP automation requires exactly identity, ACI subnet and setup Storage IDs"
        )
    for field, resource_type in MCP_AUTOMATION_TYPES.items():
        resource_scope(value[field], resource_type, subscription)
    if len(json.dumps(value, separators=(",", ":"))) > 1000:
        raise ValueError("MCP automation setup IDs exceed their bounded option budget")
    return {field: value[field] for field in MCP_AUTOMATION_TYPES}


def resource_bindings(cli: AzureCli, values: dict[str, Any]) -> dict[str, str]:
    """Resolve new resources locally and shared resources only from their explicit full IDs."""
    bindings: dict[str, str] = {}
    for mode, (name, existing, field, resource_type, _) in SHARED_RESOURCES.items():
        bindings[field] = (
            values[existing]
            if values.get(mode, "new") == "existing"
            else f"{cli.group_id}/providers/{resource_type}/{values[name]}"
        )
    bindings["logAnalyticsWorkspaceResourceId"] = (
        values["existingLogAnalyticsWorkspaceResourceId"]
        if values.get("containerAppsEnvironmentMode", "new") == "existing"
        else f"{cli.group_id}/providers/Microsoft.OperationalInsights/workspaces/{values['logAnalyticsWorkspaceName']}"
    )
    bindings["emailDomainResourceId"] = (
        values["existingEmailDomainResourceId"]
        if values.get("communicationServicesMode", "new") == "existing"
        else f"{cli.group_id}/providers/Microsoft.Communication/emailServices/{values['emailServiceName']}/domains/AzureManagedDomain"
    )
    for field, prefix in (
        ("stateContainerName", "azbrief-state"),
        ("archiveContainerName", "azbrief-archive"),
    ):
        bindings[field] = values.get(field) or (
            f"{prefix}-{values['containerAppName'].lower()}"
            if values.get("storageAccountMode", "new") == "existing"
            else prefix
        )
    for field, resource_type in RESOURCE_BINDING_TYPES.items():
        resource_scope(bindings[field], resource_type, cli.subscription)
    return bindings


def normalize_foundry_publishing_access(
    value: object, subscription: str | None = None
) -> dict[str, list[str]]:
    """Validate an explicit bounded inbound publishing policy without widening its sources."""
    if not isinstance(value, dict):
        raise ValueError("Foundry publishing access must be an object")
    if not value:
        return {}
    fields = {"publicIpRanges", "subnetResourceIds"}
    if set(value) != fields or any(not isinstance(value[field], list) for field in fields):
        raise ValueError(
            "Foundry publishing access requires publicIpRanges and subnetResourceIds arrays"
        )
    blocked = tuple(
        IPv4Network(cidr)
        for cidr in (
            "0.0.0.0/8",
            "10.0.0.0/8",
            "100.64.0.0/10",
            "127.0.0.0/8",
            "169.254.0.0/16",
            "172.16.0.0/12",
            "192.0.0.0/24",
            "192.0.2.0/24",
            "192.168.0.0/16",
            "198.18.0.0/15",
            "198.51.100.0/24",
            "203.0.113.0/24",
            "224.0.0.0/4",
            "240.0.0.0/4",
        )
    )
    networks: list[IPv4Network] = []
    public: list[str] = []
    for item in value["publicIpRanges"]:
        if not isinstance(item, str) or item != item.strip():
            raise ValueError("Foundry publishing rules require explicit public IPv4 addresses")
        try:
            network = IPv4Network(item, strict=True)
        except ValueError as exc:
            raise ValueError(
                "Foundry publishing rules require public IPv4 or canonical CIDR"
            ) from exc
        if not network.is_global or any(network.overlaps(entry) for entry in blocked):
            raise ValueError("Foundry publishing rules must contain public IPv4 addresses only")
        if any(network.overlaps(previous) for previous in networks):
            raise ValueError("Foundry publishing public IP rules must not overlap")
        networks.append(network)
        if network.prefixlen >= 31:
            public.extend(str(address) for address in network)
        else:
            public.append(str(network))
        if len(public) > 100:
            raise ValueError("Foundry publishing access exceeds 100 public IP rules")
    subnets: list[str] = []
    for item in value["subnetResourceIds"]:
        resource_scope(item, "Microsoft.Network/virtualNetworks/subnets", subscription)
        normalized = item.lower()
        if normalized in subnets:
            raise ValueError("Foundry publishing subnet rules must be unique")
        subnets.append(normalized)
    if len(subnets) > 100 or not (public or subnets):
        raise ValueError("Foundry publishing access requires a bounded non-empty allow-list")
    return {"publicIpRanges": sorted(public), "subnetResourceIds": sorted(subnets)}


def deployer_environment_defaults(user: dict[str, Any]) -> dict[str, str]:
    """Build first-install defaults from a verified signed-in user's directory profile."""
    principal = user.get("id")
    address = user.get("mail")
    if not isinstance(principal, str) or not isinstance(address, str) or not address.strip():
        raise ValueError(
            "Automatic application setup requires a signed-in user with directory mail"
        )
    try:
        object_id = UUID(principal)
        email = address.strip()
        mailbox = Address(addr_spec=email)
        if not mailbox.username or not mailbox.domain or "#ext#" in email.casefold():
            raise ValueError("Directory mail must identify one deliverable mailbox")
    except (TypeError, ValueError) as exc:
        raise ValueError("The deployment user's directory identity or mail is invalid") from exc
    if not object_id.int:
        raise ValueError("The deployment user must have a non-empty directory object ID")
    return {
        "EMAIL_RECIPIENT_ADDRESS": email,
        "FEEDBACK_RECIPIENT_ADDRESS": email,
        "ADMIN_ALLOWED_PRINCIPALS": str(object_id),
        "ARCHIVE_ALLOWED_PRINCIPALS": str(object_id),
        "ADMIN_UI_ENABLED": "true",
        "ARCHIVE_UI_ENABLED": "true",
        "FEEDBACK_UI_ENABLED": "true",
        "ADMIN_REQUIRE_AUTH": "true",
        "ARCHIVE_REQUIRE_AUTH": "true",
        "REPORT_LANGUAGE": "ko",
    }


def _deployment_user(cli: AzureCli) -> dict[str, Any]:
    cli.assert_context()
    account = cli.json("account", "show")
    identity = account.get("user") if isinstance(account, dict) else None
    if (
        not isinstance(account, dict)
        or str(account.get("id", "")).casefold() != cli.subscription.casefold()
        or str(account.get("tenantId", "")).casefold() != cli.tenant.casefold()
        or not isinstance(identity, dict)
        or identity.get("type") != "user"
    ):
        raise ValueError(
            "Automatic first-install setup requires the target tenant's signed-in user"
        )
    user = cli.json("ad", "signed-in-user", "show")
    if not isinstance(user, dict):
        raise ValueError("The signed-in directory profile is incomplete")
    deployer_environment_defaults(user)
    return user


def _graph_request(
    cli: AzureCli, path: str, payload: dict[str, Any] | None = None
) -> dict[str, Any]:
    arguments = ["rest", "--method", "GET" if payload is None else "POST", "--url", GRAPH + path]
    if payload is None:
        response = cli.json(*arguments)
    else:
        with tempfile.TemporaryDirectory(prefix="azbrief-v2-directory-") as temporary:
            body = Path(temporary) / "request.json"
            body.write_text(json.dumps(payload), encoding="utf-8")
            response = cli.json(*arguments, "--body", f"@{body}")
    if not isinstance(response, dict) or response.get("@odata.nextLink"):
        raise ValueError("Directory response is incomplete or paged; automatic setup is blocked")
    return response


def _web_authentication(client_id: str, tenant_id: str, origin: str) -> dict[str, Any]:
    return {
        "platform": {"enabled": True},
        "httpSettings": {"requireHttps": True},
        "globalValidation": {"unauthenticatedClientAction": "AllowAnonymous"},
        "identityProviders": {
            "azureActiveDirectory": {
                "enabled": True,
                "registration": {
                    "clientId": client_id,
                    "clientSecretSettingName": WEB_SECRET_NAME,
                    "openIdIssuer": f"https://login.microsoftonline.com/{tenant_id}/v2.0",
                },
                "validation": {"allowedAudiences": [client_id, f"api://{client_id}"]},
                "login": {"loginParameters": ["scope=openid profile email"]},
            }
        },
        "login": {"allowedExternalRedirectUrls": [origin]},
    }


def _automatic_application_configuration(
    cli: AzureCli,
    values: dict[str, Any],
    configuration: dict[str, Any],
    user: dict[str, Any],
    *,
    create: bool = False,
) -> dict[str, Any]:
    """Plan web sign-in read-only, materializing directory credentials only for deployment."""
    configuration = copy.deepcopy(configuration)
    if configuration.get("webAuthConfiguration"):
        return configuration
    origin = f"https://{values['containerAppName']}.{values['environmentDefaultDomain']}"
    binding = f"AzBrief v2 web sign-in for {cli.group_id.casefold()}"
    unique_name = "azbrief-v2-web-" + hashlib.sha256(binding.encode("utf-8")).hexdigest()[:13]
    query = urlencode(
        {
            "$filter": f"uniqueName eq '{unique_name}'",
            "$select": "id,appId,uniqueName,displayName,notes,signInAudience,requiredResourceAccess,web,passwordCredentials",
        }
    )
    response = _graph_request(cli, "applications?" + query)
    applications = response.get("value")
    if not isinstance(applications, list) or len(applications) > 1:
        raise ValueError("Web directory application inventory is incomplete or ambiguous")
    expected: dict[str, Any] = {
        "uniqueName": unique_name,
        "displayName": f"AzBrief v2 Web UI ({cli.group})",
        "notes": binding,
        "signInAudience": "AzureADMyOrg",
        "requiredResourceAccess": [],
        "web": {
            "redirectUris": [origin + "/.auth/login/aad/callback"],
            "implicitGrantSettings": {
                "enableIdTokenIssuance": True,
                "enableAccessTokenIssuance": False,
            },
        },
    }
    application = applications[0] if applications else None
    if application is not None:
        if (
            not isinstance(application, dict)
            or any(
                application.get(field) != value
                for field, value in expected.items()
                if field != "web"
            )
            or any(
                application.get("web", {}).get(field) != value
                for field, value in expected["web"].items()
            )
        ):
            raise ValueError("Refusing to adopt a redirected or modified web directory application")
        application_id = str(UUID(application["id"]))
        owners = _graph_request(cli, f"applications/{application_id}/owners").get("value")
        if (
            not isinstance(owners, list)
            or any(not isinstance(owner, dict) for owner in owners)
            or user["id"].casefold() not in {str(owner["id"]).casefold() for owner in owners}
        ):
            raise ValueError("The signed-in deployment user must own the prepared web application")
        if application.get("passwordCredentials") != []:
            raise ValueError(
                "Existing web credentials require explicit recovery; never rotate on missing reads"
            )
    if application is None and create:
        application = _graph_request(
            cli,
            "applications",
            {
                **expected,
                "owners@odata.bind": [GRAPH + "directoryObjects/" + user["id"]],
            },
        )
    client_id = (
        str(UUID(application["appId"])) if application else str(uuid5(NAMESPACE_URL, binding))
    )
    secret_value = "azbrief-web-secret-planned-not-for-deployment"
    if create:
        if application is None:
            raise ValueError("Directory application creation did not return its identity")
        application_id = str(UUID(application["id"]))
        principals = _graph_request(
            cli, "servicePrincipals?" + urlencode({"$filter": f"appId eq '{client_id}'"})
        ).get("value")
        if not isinstance(principals, list) or len(principals) > 1:
            raise ValueError("Web service principal inventory is incomplete or ambiguous")
        if not principals:
            principals = [_graph_request(cli, "servicePrincipals", {"appId": client_id})]
        principal = principals[0]
        if (
            principal.get("appId") != client_id
            or principal.get("appOwnerOrganizationId", "").casefold() != cli.tenant.casefold()
            or principal.get("servicePrincipalType") != "Application"
            or principal.get("accountEnabled") is not True
        ):
            raise ValueError("Web service principal must remain enabled in the deployment tenant")
        credential = _graph_request(
            cli,
            f"applications/{application_id}/addPassword",
            {
                "passwordCredential": {
                    "displayName": "AzBrief v2 Web UI",
                    "endDateTime": (datetime.now(timezone.utc) + timedelta(days=365)).isoformat(),
                }
            },
        )
        secret_value = credential.get("secretText", "")
        if not isinstance(secret_value, str) or not secret_value:
            raise ValueError("Web credential creation did not return a secret; no UI is enabled")
        configure_redaction({"v2_web_client_secret": secret_value})
        UUID(credential["keyId"])
    secrets = configuration.setdefault("existingSecrets", {}).setdefault("app", [])
    if any(secret["name"] == WEB_SECRET_NAME for secret in secrets):
        raise ValueError(
            "Reserved web secret requires complete explicit authentication configuration"
        )
    secrets.append({"name": WEB_SECRET_NAME, "value": secret_value})
    configuration["webAuthConfiguration"] = _web_authentication(client_id, cli.tenant, origin)
    return configuration


def planned_virtual_network(values: dict[str, Any]) -> dict[str, Any]:
    """Validate a dedicated new network with the same role/subnet rules as existing networks."""
    network = ip_network(values["virtualNetworkAddressPrefix"], strict=True)
    private_ranges = (
        IPv4Network("10.0.0.0/8"),
        IPv4Network("172.16.0.0/12"),
        IPv4Network("192.168.0.0/16"),
    )
    if not isinstance(network, IPv4Network) or not any(
        network.subnet_of(private) for private in private_ranges
    ):
        raise ValueError("New VNet address space must be one RFC1918 IPv4 network")
    subnets = []
    for role, name, prefix in (
        ("private_endpoint", "peSubnetName", "peSubnetAddressPrefix"),
        ("foundry", "foundrySubnetName", "foundrySubnetAddressPrefix"),
        ("container_apps", "containerAppsSubnetName", "containerAppsSubnetAddressPrefix"),
    ):
        subnets.append(
            {
                "name": values[name],
                "properties": {
                    "addressPrefix": values[prefix],
                    "delegations": (
                        []
                        if role == "private_endpoint"
                        else [{"properties": {"serviceName": "Microsoft.App/environments"}}]
                    ),
                },
            }
        )
    virtual_network = {
        "location": values["location"],
        "properties": {"addressSpace": {"addressPrefixes": [str(network)]}, "subnets": subnets},
    }
    plan_subnets(values, virtual_network)
    return virtual_network


def discover_model_options(
    cli: AzureCli, regions: list[str], model_names: list[str] | None = None
) -> list[dict[str, Any]]:
    """List verified GPT-5 Agent profiles with both quota and provider deployment capacity."""
    cli.assert_context()
    if (
        not regions
        or len(regions) > 8
        or any(not re.fullmatch(r"[a-z][a-z0-9]{1,39}", region) for region in regions)
    ):
        raise ValueError("Choose one to eight explicit canonical Azure regions")
    capacities: dict[tuple[str, str], list[dict[str, Any]]] = {}
    result: list[dict[str, Any]] = []
    for region in dict.fromkeys(regions):
        base = f"/subscriptions/{cli.subscription}/providers/Microsoft.CognitiveServices"
        catalog = cli.collection(f"{base}/locations/{region}/models", FOUNDRY_API)
        usages = cli.collection(f"{base}/locations/{region}/usages", "2024-10-01")
        profiles: dict[tuple[str, str], dict[str, Any]] = {}
        for row in catalog:
            model = row.get("model", {})
            name, version = model.get("name", ""), model.get("version", "")
            capabilities = model.get("capabilities", {})
            if (
                model.get("format") != "OpenAI"
                or not name.startswith("gpt-5")
                or capabilities.get("responses") != "true"
                or capabilities.get("agentsV2") != "true"
                or not version
                or (model_names and name not in model_names)
            ):
                continue
            profiles[(name, version)] = model
        if len(profiles) > 60:
            raise ValueError("Narrow the model candidates before querying deployment capacity")
        for (name, version), model in profiles.items():
            sku_rows = [sku for sku in model.get("skus", []) if sku.get("name") == "GlobalStandard"]
            usage_names = {sku.get("usageName") for sku in sku_rows}
            if len(usage_names) != 1 or not all(
                isinstance(value, str) and value for value in usage_names
            ):
                continue
            rows = [row for row in usages if row.get("name", {}).get("value") in usage_names]
            if len(rows) != 1 or any(
                type(rows[0].get(field)) not in (int, float) for field in ("limit", "currentValue")
            ):
                raise ValueError(f"Quota is unreadable or ambiguous: {region}/{name}/{version}")
            key = (name, version)
            if key not in capacities:
                query = urlencode(
                    {
                        "api-version": "2024-10-01",
                        "modelFormat": "OpenAI",
                        "modelName": name,
                        "modelVersion": version,
                    }
                )
                payload = cli.json(
                    "rest",
                    "--method",
                    "get",
                    "--url",
                    f"{MANAGEMENT}{base}/modelCapacities?{query}",
                )
                if (
                    not isinstance(payload, dict)
                    or not isinstance(payload.get("value"), list)
                    or payload.get("nextLink")
                ):
                    raise ValueError("Model capacity inventory is incomplete or paged")
                capacities[key] = payload["value"]
            available = [
                row["properties"].get("availableCapacity")
                for row in capacities[key]
                if row.get("location", "").casefold() == region.casefold()
                and row.get("properties", {}).get("skuName") == "GlobalStandard"
            ]
            if len(available) != 1 or type(available[0]) not in (int, float):
                continue
            quota = max(0, int(rows[0]["limit"] - rows[0]["currentValue"]))
            provider = max(0, int(available[0]))
            result.append(
                {
                    "region": region,
                    "model": name,
                    "version": version,
                    "sku": "GlobalStandard",
                    "quota_limit": rows[0]["limit"],
                    "quota_used": rows[0]["currentValue"],
                    "quota_available": quota,
                    "provider_available": provider,
                    "available": min(quota, provider),
                }
            )
    return sorted(
        result, key=lambda row: (-row["available"], row["region"], row["model"], row["version"])
    )


def validate_tag_budget(tags: dict[str, Any]) -> None:
    """Reject oversized or non-string metadata before any deployment writes."""
    if len({key.casefold() for key in tags}) != len(tags) or len(tags) > 50:
        raise ValueError("Resource-group tags must have unique names and fit the 50-tag limit")
    for key, value in tags.items():
        if (
            not isinstance(key, str)
            or not key
            or len(key) > 512
            or any(character in key for character in "<>%&\\?/")
            or not isinstance(value, str)
            or len(value) > 256
        ):
            raise ValueError("Tags require valid names and string values of at most 256 characters")


def _private_endpoint_count(values: dict[str, Any]) -> int:
    """Count only endpoints created by this workload, not shared parents' existing endpoints."""
    return (
        int(values.get("foundryAccountMode", "new") == "new")
        + int(values.get("storageAccountMode", "new") == "new")
        + (0 if values.get("useMicrosoftManagedAgentData", False) else 2)
        + int(
            values.get("deployContainerAppsPrivateEndpoint", False)
            and values.get("containerAppsEnvironmentMode", "new") == "new"
        )
    )


def parse_tag_contract(tags: dict[str, Any], require_ready: bool = True) -> dict[str, str]:
    """Read the bounded, non-secret infrastructure contract, never an arbitrary ARM ID."""
    validate_tag_budget(tags)
    contract = {key: value for key, value in tags.items() if key.casefold().startswith(PREFIX)}
    required = TAG_KEYS - {PREFIX + "mcpApp", PREFIX + "mcpIdentity"}
    if not required <= set(contract) or set(contract) - (
        TAG_KEYS | set(OPTION_TAG_KEYS) | set(RESOURCE_TAG_KEYS) | APPLICATION_TAG_KEYS
    ):
        raise ValueError("Missing, unknown or non-canonical AzBrief v2 resource-group tag keys")
    if (
        contract[PREFIX + "schema"] != "2"
        or contract[PREFIX + "profile"] != PROFILE
        or contract[PREFIX + "state"]
        not in ({"ready"} if require_ready else {"foundation", "mcp_pending", "ready"})
        or contract[PREFIX + "acaPeEnabled"] not in {"true", "false"}
    ):
        raise ValueError("Use the completed AzBrief v2 infrastructure contract")
    options = decode_tag_options(contract)
    bindings = decode_resource_bindings(contract)
    if any(options.get(mode, "new") == "existing" for mode in SHARED_RESOURCES) and not bindings:
        raise ValueError("Shared infrastructure requires its full resource-ID tags")
    managed_agent_data = options.get("useMicrosoftManagedAgentData", False)
    unused_pe_subnet = (
        managed_agent_data
        and options.get("foundryAccountMode") == "existing"
        and options.get("storageAccountMode") == "existing"
        and options.get("containerAppsEnvironmentMode") in {"new", "existing"}
        and contract[PREFIX + "acaPeEnabled"] == "false"
    )
    for suffix in TAG_FIELDS.values():
        value = contract.get(PREFIX + suffix, "")
        if suffix == "peSubnet" and not value and unused_pe_subnet:
            continue
        if suffix in {"cosmos", "search"} and managed_agent_data:
            if value:
                raise ValueError(
                    "Microsoft-managed agent data must not bind customer Cosmos or Search"
                )
            continue
        if suffix in {"mcpApp", "mcpIdentity"} and not value:
            continue
        if suffix in {"mcpUrl", "mcpConnection"} and not value:
            continue
        if suffix == "mcpUrl":
            uri = urlsplit(value)
            if (
                uri.scheme != "https"
                or not uri.hostname
                or uri.username
                or uri.password
                or uri.query
                or uri.fragment
            ):
                raise ValueError(
                    "Azure MCP requires an approved HTTPS URL without credentials or query"
                )
        elif not NAME_PATTERN.fullmatch(value):
            raise ValueError(f"Invalid v2 resource-name tag: {suffix}")
    if not re.fullmatch(r"[a-z0-9]{3,24}", contract[PREFIX + "storage"]):
        raise ValueError("Invalid v2 Storage Account name")
    present = APPLICATION_TAG_KEYS.intersection(contract)
    if present:
        if present != APPLICATION_TAG_KEYS:
            raise ValueError("Incomplete application deployment tag pair")
        application_state = contract[PREFIX + "applicationState"]
        timestamp = contract[PREFIX + "lastApplicationDeploymentAt"]
        if application_state == "not_deployed" and not timestamp:
            return contract
        if application_state != "deployed" or contract[PREFIX + "state"] != "ready":
            raise ValueError("Invalid application deployment state or timestamp")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", timestamp):
            raise ValueError("Application deployment timestamp must be an ISO 8601 UTC value")
        datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%SZ")
    return contract


def decode_tag_options(tags: dict[str, str]) -> dict[str, Any]:
    present = set(OPTION_TAG_KEYS).intersection(tags)
    if not present:
        return {}
    if present != set(OPTION_TAG_KEYS):
        raise ValueError("Incomplete infrastructure option tags")
    options = json.loads("".join(tags[key] for key in OPTION_TAG_KEYS))
    optional = (
        {
            "simpleModelVersion",
            "useMicrosoftManagedAgentData",
            "manageAzureMcp",
            "azureMcpClientId",
            "azureMcpServicePrincipalId",
            "azureMcpAppRoleId",
            "namingPrefix",
            "namingSuffix",
            "mcpAutomation",
            "foundryPublishingAccess",
        }
        | NETWORK_OPTION_FIELDS
        | set(SHARED_RESOURCES)
    )
    if not isinstance(options, dict) or set(options) - (set(OPTION_FIELDS) | optional):
        raise ValueError("Unknown or secret-valued infrastructure options")
    if set(OPTION_FIELDS) - set(options):
        raise ValueError("Missing saved infrastructure options")
    if any(type(options[field]) is not kind for field, kind in OPTION_FIELDS.items()):
        raise ValueError("Invalid saved infrastructure option type")
    if "manageAzureMcp" in options and type(options["manageAzureMcp"]) is not bool:
        raise ValueError("Invalid managed MCP setting")
    validate_mcp_automation(options.get("mcpAutomation", {}))
    if "foundryPublishingAccess" in options:
        options["foundryPublishingAccess"] = normalize_foundry_publishing_access(
            options["foundryPublishingAccess"]
        )
    if (
        "useMicrosoftManagedAgentData" in options
        and type(options["useMicrosoftManagedAgentData"]) is not bool
    ):
        raise ValueError("Invalid agent data storage mode")
    if "simpleModelVersion" in options and (
        not isinstance(options["simpleModelVersion"], str)
        or not options["simpleModelVersion"].strip()
    ):
        raise ValueError("Invalid saved Simple model version")
    if options.get("virtualNetworkMode", "existing") not in {"existing", "new"}:
        raise ValueError("Invalid saved VNet deployment mode")
    if any(options.get(field, "new") not in {"new", "existing"} for field in SHARED_RESOURCES):
        raise ValueError("Invalid saved shared resource mode")
    for field in NETWORK_OPTION_FIELDS - {"virtualNetworkMode"}:
        if field in options:
            if not isinstance(options[field], str):
                raise ValueError("Saved network prefixes must be strings")
            if not isinstance(ip_network(options[field], strict=True), IPv4Network):
                raise ValueError("Saved network prefixes must remain IPv4")
    if options.get("virtualNetworkMode") == "new" and not NETWORK_OPTION_FIELDS <= set(options):
        raise ValueError("New VNet mode requires every saved network address choice")
    for field in (
        optional
        - {
            "manageAzureMcp",
            "useMicrosoftManagedAgentData",
            "namingPrefix",
            "namingSuffix",
            "simpleModelVersion",
            "mcpAutomation",
            "foundryPublishingAccess",
        }
        - NETWORK_OPTION_FIELDS
        - set(SHARED_RESOURCES)
    ):
        if options.get(field):
            UUID(options[field])
    for field in ("namingPrefix", "namingSuffix"):
        value = options.get(field, "")
        if (
            not isinstance(value, str)
            or len(value) > 10
            or (value and not re.fullmatch(r"[a-z0-9]+", value))
        ):
            raise ValueError("Invalid saved common resource-name input")
    return options


def infrastructure_values(cli: AzureCli, tags: dict[str, str]) -> dict[str, Any]:
    """Resolve validated full IDs, retaining legacy same-RG name-only handoffs."""
    values: dict[str, Any] = {
        field: tags.get(PREFIX + suffix, "") for field, suffix in TAG_FIELDS.items()
    }
    values.update(decode_tag_options(tags))
    values.setdefault("useMicrosoftManagedAgentData", False)
    values.setdefault("virtualNetworkMode", "existing")
    values.setdefault("simpleModelVersion", values.get("modelVersion", ""))
    values.update(
        tenantId=cli.tenant,
        subscriptionId=cli.subscription,
        resourceGroup=cli.group,
        deployContainerAppsPrivateEndpoint=tags[PREFIX + "acaPeEnabled"] == "true",
    )
    saved_bindings = decode_resource_bindings(tags)
    for mode, (name, existing, field, resource_type, _) in SHARED_RESOURCES.items():
        values.setdefault(mode, "new")
        resource_id = saved_bindings.get(
            field, f"{cli.group_id}/providers/{resource_type}/{values[name]}"
        )
        _, group, leaf = resource_scope(resource_id, resource_type, cli.subscription)
        if leaf.casefold() != values[name].casefold() or (
            values[mode] == "new" and group.casefold() != cli.group.casefold()
        ):
            raise ValueError("Resource-ID tags differ from the saved name or creation scope")
        values[existing] = resource_id if values[mode] == "existing" else ""
    values["existingLogAnalyticsWorkspaceResourceId"] = (
        saved_bindings.get("logAnalyticsWorkspaceResourceId", "")
        if values["containerAppsEnvironmentMode"] == "existing"
        else ""
    )
    values["existingEmailDomainResourceId"] = (
        saved_bindings.get("emailDomainResourceId", "")
        if values["communicationServicesMode"] == "existing"
        else ""
    )
    bindings = resource_bindings(cli, {**values, **saved_bindings})
    for field in ("logAnalyticsWorkspaceResourceId", "emailDomainResourceId"):
        _, group, leaf = resource_scope(
            bindings[field], RESOURCE_BINDING_TYPES[field], cli.subscription
        )
        expected_name = (
            values["logAnalyticsWorkspaceName"]
            if field.startswith("log")
            else values["emailServiceName"]
        )
        actual_name = leaf if field.startswith("log") else bindings[field].split("/")[8]
        if actual_name.casefold() != expected_name.casefold():
            raise ValueError("Shared dependency ID does not match its saved resource name")
        mode = (
            "containerAppsEnvironmentMode"
            if field.startswith("log")
            else "communicationServicesMode"
        )
        if values[mode] == "new" and group.casefold() != cli.group.casefold():
            raise ValueError("New dependency cannot redirect to another resource group")
    values.update(bindings)
    return values


def _owned(resource: dict[str, Any], resource_id: str) -> None:
    if (
        resource.get("id", "").casefold() != resource_id.casefold()
        or (resource.get("tags") or {}).get("deploymentProfile") != PROFILE
    ):
        raise ValueError("Refusing to adopt a foreign or redirected resource")


def _owned_project(
    cli: AzureCli, values: dict[str, Any], account_id: str, *, allow_missing: bool = False
) -> None:
    project_id = f"{account_id}/projects/{values['projectName']}"
    projects = cli.collection(f"{account_id}/projects", FOUNDRY_API)
    matches = [
        row
        for row in projects
        if row["name"].split("/")[-1].casefold() == values["projectName"].casefold()
    ]
    if not matches and allow_missing:
        return
    if len(matches) != 1:
        raise ValueError("The exact workload project is missing or ambiguous")
    project = cli.get(project_id, FOUNDRY_API)
    _owned(project, project_id)
    if (project.get("tags") or {}).get(
        "azbriefWorkloadResourceGroup", ""
    ).casefold() != cli.group_id.casefold():
        raise ValueError("Refusing to adopt another workload's project in shared Foundry")


def read_infrastructure(cli: AzureCli) -> tuple[dict[str, str], dict[str, Any]]:
    """Verify context, private ingress and immutable BYO bindings before application writes."""
    return _read_infrastructure(cli, "ready")


def _read_infrastructure(
    cli: AzureCli, expected_state: str
) -> tuple[dict[str, str], dict[str, Any]]:
    cli.assert_context()
    group = cli.json("group", "show", "--name", cli.group, "--subscription", cli.subscription)
    current_tags = group.get("tags") or {}
    validate_tag_budget({**APPLICATION_TAG_DEFAULTS, **current_tags})
    tags = parse_tag_contract(group.get("tags") or {}, require_ready=expected_state == "ready")
    if tags[PREFIX + "state"] != expected_state:
        raise ValueError(f"Use the {expected_state} v2 infrastructure contract for this operation")
    values = infrastructure_values(cli, tags)
    values = _resolve_shared_resources(cli, values, expected_state)
    environment_id = values["containerAppsEnvironmentResourceId"]
    environment = cli.get(environment_id, CONTAINER_ENV_API)
    if values["containerAppsEnvironmentMode"] == "new":
        _owned(environment, environment_id)
    props = environment["properties"]
    expected_subnet = (
        f"/subscriptions/{cli.subscription}/resourceGroups/{values['virtualNetworkResourceGroupName']}"
        f"/providers/Microsoft.Network/virtualNetworks/{values['virtualNetworkName']}"
        f"/subnets/{values['containerAppsSubnetName']}"
    )
    if (
        props.get("provisioningState") != "Succeeded"
        or props.get("vnetConfiguration", {}).get("internal") is not True
        or str(props.get("publicNetworkAccess", "")).casefold() != "disabled"
        or props.get("vnetConfiguration", {}).get("infrastructureSubnetId", "").casefold()
        != expected_subnet.casefold()
        or not props.get("defaultDomain")
    ):
        raise ValueError(
            "The v2 Environment must be ready, internal, PNA-disabled and on the tagged subnet"
        )
    identity_id = f"{cli.group_id}/providers/Microsoft.ManagedIdentity/userAssignedIdentities/{values['controlPlaneIdentityName']}"
    identity = cli.get(identity_id, "2023-01-31")
    _owned(identity, identity_id)
    account_id = values["foundryAccountResourceId"]
    account = cli.get(account_id, FOUNDRY_API)
    if values["foundryAccountMode"] == "new":
        _owned(account, account_id)
    else:
        _owned_project(cli, values, account_id)
    _verify_foundry_publishing_access(cli, values, account)
    reused, telemetry = validate_existing_host(cli, account_id, values)
    if not reused or not telemetry:
        raise ValueError("Completed v2 project/BYO and Application Insights bindings are required")
    values.update(
        environmentResourceId=environment_id,
        environmentDefaultDomain=props["defaultDomain"],
        controlPlaneIdentityResourceId=identity_id,
        controlPlaneClientId=identity["properties"]["clientId"],
        controlPlanePrincipalId=identity["properties"]["principalId"],
        foundryProjectEndpoint=f"https://{account['properties'].get('customSubDomainName') or values['foundryAccountName']}.services.ai.azure.com/api/projects/{values['projectName']}",
    )
    vnet_id = (
        f"/subscriptions/{cli.subscription}/resourceGroups/"
        f"{values['virtualNetworkResourceGroupName']}/providers/Microsoft.Network/"
        f"virtualNetworks/{values['virtualNetworkName']}"
    )
    values.update(
        virtualNetworkResourceId=vnet_id,
        foundrySubnetResourceId=f"{vnet_id}/subnets/{values['foundrySubnetName']}",
        infrastructureSubnetId=expected_subnet,
        privateEndpointSubnetId=(
            f"{vnet_id}/subnets/{values['peSubnetName']}" if values["peSubnetName"] else ""
        ),
    )
    return tags, values


def _environment(entries: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for entry in entries:
        if (
            not isinstance(entry, dict)
            or set(entry) not in ({"name", "value"}, {"name", "secretRef"})
            or not isinstance(entry["name"], str)
            or not re.fullmatch(r"[A-Z][A-Z0-9_]*", entry["name"])
            or not all(isinstance(value, str) for value in entry.values())
            or entry["name"] in result
        ):
            raise ValueError("Environment entries need unique names and either value or secretRef")
        if "value" in entry and re.search(
            r"(?:^|_)(?:API_KEY|CLIENT_SECRET|PASSWORD|TOKEN|SECRET)$", entry["name"]
        ):
            raise ValueError(
                "Credential environment settings require secretRef, never literal values"
            )
        result[entry["name"]] = dict(entry)
    return result


def _secrets(entries: list[dict[str, Any]]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    names: set[str] = set()
    for entry in entries:
        secret = {key: value for key, value in entry.items() if value is not None}
        if (
            set(secret) - {"name", "value", "keyVaultUrl", "identity"}
            or not isinstance(secret.get("name"), str)
            or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,252}", secret["name"])
            or secret["name"] in names
            or ("value" in secret) == ("keyVaultUrl" in secret)
            or not all(isinstance(value, str) and value for value in secret.values())
        ):
            raise ValueError(
                "Secrets must be readable, unique values or existing Key Vault references"
            )
        names.add(secret["name"])
        result.append(secret)
    return result


def _read_secrets(cli: AzureCli, resource_id: str) -> list[dict[str, str]]:
    response = cli.json(
        "rest",
        "--method",
        "post",
        "--url",
        f"{MANAGEMENT}{resource_id}/listSecrets?api-version={CONTAINER_ENV_API}",
    )
    if not isinstance(response, dict) or not isinstance(response.get("value"), list):
        raise ValueError(
            "Secret inventory is incomplete; API keys are never rotated on read failure"
        )
    return _secrets(response["value"])


def _merge_dicts(existing: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(existing)
    for key, value in patch.items():
        result[key] = (
            _merge_dicts(result[key], value)
            if isinstance(value, dict) and isinstance(result.get(key), dict)
            else copy.deepcopy(value)
        )
    return result


def _surface(
    cli: AzureCli,
    resource_id: str,
    collection: str,
    values: dict[str, Any],
    *,
    recover_initial_install: bool = False,
) -> dict[str, Any] | None:
    rows = cli.collection(f"{cli.group_id}/providers/Microsoft.App/{collection}", CONTAINER_ENV_API)
    matches = [row for row in rows if row["id"].casefold() == resource_id.casefold()]
    if len(matches) > 1:
        raise ValueError("Ambiguous application inventory")
    if not matches:
        return None
    resource: dict[str, Any] = cli.get(resource_id, CONTAINER_ENV_API)
    _owned(resource, resource_id)
    props = resource["properties"]
    identities = resource.get("identity", {})
    if (
        props.get("provisioningState")
        not in ({"Succeeded", "Failed"} if recover_initial_install else {"Succeeded"})
        or props.get("environmentId", "").casefold() != values["environmentResourceId"].casefold()
        or props.get("workloadProfileName") != "Consumption"
        or identities.get("type") != "UserAssigned"
        or {name.casefold() for name in identities.get("userAssignedIdentities", {})}
        != {values["controlPlaneIdentityResourceId"].casefold()}
        or len(props.get("template", {}).get("containers", [])) != 1
    ):
        raise ValueError(
            "Refusing to change application ownership, identity, environment or container topology"
        )
    return resource


def _validate_runtime(environment: dict[str, dict[str, str]], values: dict[str, Any]) -> None:
    storage = values["storageAccountName"]
    state = f"https://{storage}.blob.core.windows.net/{values.get('stateContainerName', 'azbrief-state')}"
    protected = {
        "AZURE_TENANT_ID": values["tenantId"],
        "AZURE_SUBSCRIPTION_ID": values["subscriptionId"],
        "AZURE_CLIENT_ID": values["controlPlaneClientId"],
        "FOUNDRY_PROJECT_ENDPOINT": values.get("foundryProjectEndpoint")
        or f"https://{values['foundryAccountName']}.services.ai.azure.com/api/projects/{values['projectName']}",
        "FOUNDRY_HOSTED_AGENT_NAME": values["foundryHostedAgentName"],
        "CHECKPOINT_BLOB_URL": f"{state}/checkpoint.json",
        "ARCHIVE_BLOB_CONTAINER_URL": f"https://{storage}.blob.core.windows.net/{values.get('archiveContainerName', 'azbrief-archive')}",
        "ADMIN_CONFIG_BLOB_URL": f"{state}/admin-config.json",
    }
    for name, expected in protected.items():
        if name in environment and environment[name].get("value") != expected:
            raise ValueError(
                f"Refusing to redirect the infrastructure-bound environment setting {name}"
            )
    if "API_KEY" in environment and environment["API_KEY"] != {
        "name": "API_KEY",
        "secretRef": "orchestrator-api-key",
    }:
        raise ValueError("API_KEY must retain the application secret reference")
    for name in ("ADMIN_REQUIRE_AUTH", "ARCHIVE_REQUIRE_AUTH"):
        if name in environment:
            value = environment[name].get("value", "").lower()
            if value != "true":
                raise ValueError("v2 never silently disables Admin or Archive authentication")
            environment[name]["value"] = value


def _validate_authentication(parameters: dict[str, Any], values: dict[str, Any]) -> None:
    environment = _environment(parameters["appEnvironment"])
    enabled: dict[str, bool] = {}
    for name in ("ADMIN_UI_ENABLED", "ARCHIVE_UI_ENABLED"):
        value = environment.get(name, {}).get("value", "false").lower()
        if value not in {"true", "false"}:
            raise ValueError("Web UI flags must be true or false")
        enabled[name] = value == "true"
        if name in environment:
            environment[name]["value"] = value
    parameters["appEnvironment"] = list(environment.values())
    auth = parameters.get("webAuthConfiguration", {})
    if not isinstance(auth, dict):
        raise ValueError("Authentication configuration must be an object")
    if any(enabled.values()) and auth.get("platform", {}).get("enabled") is not True:
        raise ValueError("Enable web UI only with complete Entra authentication")
    if auth.get("platform", {}).get("enabled") is True:
        provider = auth.get("identityProviders", {}).get("azureActiveDirectory", {})
        registration = provider.get("registration", {})
        client_id = str(UUID(registration.get("clientId", "")))
        secret_name = registration.get("clientSecretSettingName")
        secrets = {entry["name"] for entry in parameters["existingSecrets"]["app"]}
        audiences = provider.get("validation", {}).get("allowedAudiences", [])
        origin = f"https://{values['containerAppName']}.{values['environmentDefaultDomain']}"
        if (
            provider.get("enabled") is not True
            or registration.get("openIdIssuer")
            != f"https://login.microsoftonline.com/{values['tenantId']}/v2.0"
            or secret_name not in secrets
            or secret_name == "orchestrator-api-key"
            or not isinstance(audiences, list)
            or set(audiences) != {client_id, f"api://{client_id}"}
            or auth.get("httpSettings", {}).get("requireHttps") is not True
            or auth.get("globalValidation", {}).get("unauthenticatedClientAction")
            != "AllowAnonymous"
            or auth.get("login", {}).get("allowedExternalRedirectUrls") != [origin]
        ):
            raise ValueError(
                "Entra authentication must retain the customer issuer, audiences, secret and same-origin HTTPS"
            )
    administrators = environment.get("ADMIN_ALLOWED_PRINCIPALS", {}).get("value", "")
    readers = environment.get("ARCHIVE_ALLOWED_PRINCIPALS", {}).get("value", "")
    if enabled["ADMIN_UI_ENABLED"] and not administrators.replace(",", "").strip():
        raise ValueError("Admin requires a non-empty administrator allow-list")
    if enabled["ARCHIVE_UI_ENABLED"] and not (administrators + readers).replace(",", "").strip():
        raise ValueError("Archive requires an administrator or reader allow-list")
    if any(enabled.values()):
        scale = parameters["appTemplate"].setdefault("scale", {})
        replicas = scale.get("minReplicas", 0)
        if type(replicas) is not int or replicas < 0:
            raise ValueError("Minimum replicas must be a non-negative integer")
        scale["minReplicas"] = max(1, replicas)


def prepare_application(
    cli: AzureCli,
    image: str,
    configuration: dict[str, Any] | None = None,
    *,
    deployment_user: dict[str, Any] | None = None,
    recover_initial_install: bool = False,
) -> dict[str, Any]:
    """Preserve existing App/Job settings and secrets while deriving a fail-closed install/update."""
    if not IMAGE_PATTERN.fullmatch(image):
        raise ValueError("Use an approved immutable container image @sha256 digest")
    configuration = copy.deepcopy(configuration or {})
    if set(configuration) - CONFIG_FIELDS:
        raise ValueError(
            "Application configuration contains unknown or infrastructure-owned fields"
        )
    tags, values = read_infrastructure(cli)
    if recover_initial_install and (
        tags.get(PREFIX + "applicationState") != "not_deployed"
        or tags.get(PREFIX + "lastApplicationDeploymentAt") != ""
    ):
        raise ValueError("Initial-install recovery requires an explicit never-deployed tag pair")
    app_id = f"{cli.group_id}/providers/Microsoft.App/containerApps/{values['containerAppName']}"
    job_id = f"{cli.group_id}/providers/Microsoft.App/jobs/{values['schedulerJobName']}"
    app = _surface(
        cli, app_id, "containerApps", values, recover_initial_install=recover_initial_install
    )
    job = _surface(cli, job_id, "jobs", values, recover_initial_install=recover_initial_install)
    if recover_initial_install and app is None:
        raise ValueError("Initial-install recovery requires the exact existing App and API secret")
    if (app is None) != (job is None) and not (recover_initial_install and app is not None):
        raise ValueError(
            "Partial application installation needs explicit recovery, not a guessed key rotation"
        )
    parameters: dict[str, Any] = {
        "infrastructureTags": tags,
        "containerImage": image,
        "reuseExistingApiKey": app is not None,
        "existingSecrets": {"app": [], "job": []},
    }
    if app is None and deployment_user is not None:
        defaults = deployer_environment_defaults(deployment_user)
        for label in ("app", "job"):
            parameters[f"{label}Environment"] = [
                {"name": name, "value": value}
                for name, value in defaults.items()
                if label == "app"
                or name in {"EMAIL_RECIPIENT_ADDRESS", "REPORT_LANGUAGE", "FEEDBACK_UI_ENABLED"}
            ]
        app_patch = _environment(configuration.get("appEnvironment", []))
        job_defaults = _environment(parameters["jobEnvironment"])
        for name in (
            "EMAIL_RECIPIENT_ADDRESS",
            "SUBSCRIBERS",
            "REPORT_LANGUAGE",
            "FEEDBACK_UI_ENABLED",
        ):
            if name in app_patch and "value" in app_patch[name]:
                job_defaults[name] = copy.deepcopy(app_patch[name])
        parameters["jobEnvironment"] = list(job_defaults.values())
    if app is not None:
        for label, resource in (("app", app), ("job", job)):
            if resource is None:
                continue
            props = resource["properties"]
            parameters[f"{label}Configuration"] = copy.deepcopy(props["configuration"])
            parameters[f"{label}Configuration"].pop("secrets", None)
            parameters[f"{label}Template"] = copy.deepcopy(props["template"])
            parameters[f"{label}Environment"] = props["template"]["containers"][0].get("env") or []
            parameters["existingSecrets"][label] = _read_secrets(cli, resource["id"])
            parameters["tags" if label == "app" else "jobTags"] = resource["tags"]
        parameters["appConfiguration"].get("ingress", {}).pop("fqdn", None)
        try:
            auth = cli.collection(f"{app_id}/authConfigs", CONTAINER_ENV_API)
        except RuntimeError as error:
            try:
                payload = str(error).removeprefix("Azure CLI rest failed: ERROR: ")
                if payload.startswith("Not Found(") and payload.endswith(")"):
                    payload = payload[len("Not Found(") : -1]
                failure = json.loads(payload)
            except (TypeError, ValueError):
                raise error
            if (
                not isinstance(failure, dict)
                or not isinstance(failure.get("error"), dict)
                or failure["error"].get("code") != "AuthConfigNotFound"
            ):
                raise
            auth = []
        if len(auth) > 1 or (auth and auth[0]["name"].split("/")[-1] != "current"):
            raise ValueError(
                "Unexpected authentication configuration; existing authentication is preserved"
            )
        parameters["webAuthConfiguration"] = auth[0]["properties"] if auth else {}
        keys = [
            secret
            for secret in parameters["existingSecrets"]["app"]
            if secret["name"] == "orchestrator-api-key"
        ]
        job_keys = [
            secret
            for secret in parameters["existingSecrets"]["job"]
            if secret["name"] == "orchestrator-api-key"
        ]
        if len(keys) != 1 or (job is not None and job_keys != keys):
            raise ValueError(
                "App and Job must have one matching readable API secret; never rotate on failure"
            )
        if job is None:
            parameters["existingSecrets"]["job"] = copy.deepcopy(keys)
        if recover_initial_install:
            logger.warning(
                "v2_initial_install_recovery_selected",
                app_state=app["properties"]["provisioningState"],
                scheduler_present=job is not None,
                api_key_reused=True,
            )
    for label in ("app", "job"):
        for suffix in ("Configuration", "Template"):
            name = f"{label}{suffix}"
            patch = configuration.get(name, {})
            if not isinstance(patch, dict) or (suffix == "Configuration" and "secrets" in patch):
                raise ValueError("Use secure secret inputs and object-valued application settings")
            parameters[name] = _merge_dicts(parameters.get(name, {}), patch)
        name = f"{label}Environment"
        environment = _environment(parameters.get(name, []))
        environment.update(_environment(configuration.get(name, [])))
        _validate_runtime(environment, values)
        parameters[name] = list(environment.values())
        current_secrets = {
            secret["name"]: secret for secret in parameters["existingSecrets"][label]
        }
        for secret in _secrets(configuration.get("existingSecrets", {}).get(label, [])):
            if secret["name"] == "orchestrator-api-key":
                raise ValueError(
                    "API-key creation/reuse is infrastructure-derived, not an application override"
                )
            current_secrets[secret["name"]] = secret
        parameters["existingSecrets"][label] = list(current_secrets.values())
        secret_names = set(current_secrets) | {"orchestrator-api-key"}
        if any(
            entry.get("secretRef") not in secret_names
            for entry in parameters[name]
            if "secretRef" in entry
        ):
            raise ValueError("Environment secretRef must name an available application secret")
    if "webAuthConfiguration" in configuration:
        previous = parameters.get("webAuthConfiguration", {})
        requested = _merge_dicts(previous, configuration["webAuthConfiguration"])
        if (
            previous.get("platform", {}).get("enabled") is True
            and requested.get("platform", {}).get("enabled") is not True
        ):
            raise ValueError("Refusing an authenticated-to-anonymous application downgrade")
        parameters["webAuthConfiguration"] = requested
    if (
        app is not None
        and parameters["appConfiguration"].get("ingress", {}).get("targetPort") != 8000
    ):
        raise ValueError("AzBrief ingress must retain port 8000")
    if parameters["appConfiguration"].get("ingress", {}).get("allowInsecure") is True:
        raise ValueError("Application updates must retain HTTPS")
    if (
        job is not None
        and parameters["jobConfiguration"].get("triggerType")
        != job["properties"]["configuration"]["triggerType"]
    ):
        raise ValueError("Application upgrades cannot reset or activate the scheduler trigger")
    _validate_authentication(parameters, values)
    configured_secrets = {
        f"v2_{label}_{index}_secret": secret.get("value", "")
        for label, secrets in parameters["existingSecrets"].items()
        for index, secret in enumerate(secrets)
    }
    configure_redaction(configured_secrets)
    return parameters


def deploy_template(
    cli: AzureCli, template: Path, parameters: dict[str, Any], mode: str, name: str
) -> dict[str, Any]:
    """Submit Incremental ARM deployments using a temporary, never-logged parameter file."""
    if mode not in {"validate", "what-if", "create"}:
        raise ValueError("Unsupported v2 deployment operation")
    with tempfile.TemporaryDirectory(prefix="azbrief-v2-") as temporary:
        path = Path(temporary) / "parameters.json"
        path.write_text(
            json.dumps(
                {"parameters": {key: {"value": value} for key, value in parameters.items()}}
            ),
            encoding="utf-8",
        )
        extra = ["--no-pretty-print"] if mode == "what-if" else []
        result = cli.json(
            "deployment",
            "group",
            mode,
            "--name",
            name,
            "--resource-group",
            cli.group,
            "--subscription",
            cli.subscription,
            "--mode",
            "Incremental",
            "--template-file",
            str(template),
            "--parameters",
            f"@{path}",
            *extra,
        )
        if not isinstance(result, dict):
            raise ValueError("ARM deployment did not return a JSON object")
        if (
            mode == "create"
            and result.get("properties", {}).get("provisioningState") != "Succeeded"
        ):
            raise RuntimeError("v2 deployment did not succeed")
        return result


def run_application(
    cli: AzureCli,
    image: str,
    configuration: dict[str, Any],
    mode: str,
    name: str,
    *,
    automatic_setup: bool = True,
    recover_initial_install: bool = False,
) -> None:
    """Require fresh preflight before every validate, preview and application write."""
    operations = ("validate", "what-if", "create") if mode == "deploy" else (mode,)
    for operation in operations:
        baseline = prepare_application(cli, image, recover_initial_install=recover_initial_install)
        if automatic_setup and not baseline["reuseExistingApiKey"]:
            _, values = read_infrastructure(cli)
            user = _deployment_user(cli)
            planned = _automatic_application_configuration(cli, values, configuration, user)
            parameters = prepare_application(cli, image, planned, deployment_user=user)
            if operation == "create":
                configured = _automatic_application_configuration(
                    cli, values, configuration, user, create=True
                )
                parameters = prepare_application(cli, image, configured, deployment_user=user)
                if parameters["reuseExistingApiKey"]:
                    raise ValueError(
                        "Concurrent application installation detected; never replace its defaults"
                    )
                for verification in ("validate", "what-if"):
                    deploy_template(cli, V2 / "application.json", parameters, verification, name)
            if parameters["reuseExistingApiKey"]:
                raise ValueError(
                    "Concurrent application installation detected; never replace its defaults"
                )
        else:
            parameters = prepare_application(
                cli, image, configuration, recover_initial_install=recover_initial_install
            )
        if operation != "preflight":
            deploy_template(cli, V2 / "application.json", parameters, operation, name)
        logger.info(
            "v2_application_operation_complete",
            operation=operation,
            update=parameters["reuseExistingApiKey"],
            application_ready=False,
        )


def prepare_mcp_authentication(cli: AzureCli) -> dict[str, Any]:
    """Validate the same group's completed core before authorized directory preparation."""
    tags, values = _read_infrastructure(cli, "mcp_pending")
    options = decode_tag_options(tags)
    if options.get("manageAzureMcp") is not True:
        raise ValueError("Only managed MCP infrastructure can request directory preparation")
    record = cli.json(
        "deployment",
        "group",
        "show",
        "--name",
        tags[PREFIX + "deployment"],
        "--resource-group",
        cli.group,
        "--subscription",
        cli.subscription,
        "--query",
        "properties.{state:provisioningState,infrastructure:outputs.infrastructure.value}",
    )
    if not isinstance(record, dict) or record.get("state") != "Succeeded":
        raise ValueError("MCP preparation requires the successful exact core deployment")
    infrastructure = record.get("infrastructure")
    if (
        not isinstance(infrastructure, dict)
        or infrastructure.get("schemaVersion") != 2
        or infrastructure.get("profile") != PROFILE
        or infrastructure.get("infrastructureComplete") is not True
        or str(infrastructure.get("tenantId", "")).casefold() != cli.tenant.casefold()
        or str(infrastructure.get("subscriptionId", "")).casefold() != cli.subscription.casefold()
        or str(infrastructure.get("resourceGroup", "")).casefold() != cli.group.casefold()
    ):
        raise ValueError("MCP core output does not match the explicit target")
    vnet_id = (
        f"/subscriptions/{cli.subscription}/resourceGroups/"
        f"{values['virtualNetworkResourceGroupName']}/providers/Microsoft.Network/"
        f"virtualNetworks/{values['virtualNetworkName']}"
    )
    id_backed_bindings = {
        "controlPlaneIdentityName": (
            "controlPlaneIdentityResourceId",
            f"{cli.group_id}/providers/Microsoft.ManagedIdentity/userAssignedIdentities/"
            f"{values['controlPlaneIdentityName']}",
        ),
        "virtualNetworkResourceGroupName": ("virtualNetworkResourceId", vnet_id),
        "virtualNetworkName": ("virtualNetworkResourceId", vnet_id),
        "peSubnetName": (
            "privateEndpointSubnetId",
            f"{vnet_id}/subnets/{values['peSubnetName']}" if values["peSubnetName"] else "",
        ),
        "foundrySubnetName": (
            "foundrySubnetResourceId",
            f"{vnet_id}/subnets/{values['foundrySubnetName']}",
        ),
        "containerAppsSubnetName": (
            "infrastructureSubnetId",
            f"{vnet_id}/subnets/{values['containerAppsSubnetName']}",
        ),
    }
    for field in TAG_FIELDS:
        if field in id_backed_bindings:
            id_field, expected_id = id_backed_bindings[field]
            if (id_field in infrastructure or field not in infrastructure) and str(
                infrastructure.get(id_field, "")
            ).casefold() != expected_id.casefold():
                raise ValueError(f"MCP core output binding changed: {field}")
            if field not in infrastructure:
                infrastructure[field] = values[field]
        if str(infrastructure.get(field, "")).casefold() != str(values[field]).casefold():
            raise ValueError(f"MCP core output binding changed: {field}")
    for field in (*RESOURCE_BINDING_TYPES, *sorted(CONTAINER_NAME_FIELDS)):
        if field in infrastructure or decode_resource_bindings(tags):
            if str(infrastructure.get(field, "")).casefold() != str(values[field]).casefold():
                raise ValueError(f"MCP core output resource binding changed: {field}")
        infrastructure.setdefault(field, values[field])
    if (
        infrastructure.get(
            "foundryProjectResourceId",
            f"{values['foundryAccountResourceId']}/projects/{values['projectName']}",
        ).casefold()
        != f"{values['foundryAccountResourceId']}/projects/{values['projectName']}".casefold()
    ):
        raise ValueError("MCP core output project ID differs from the saved Foundry account")
    expected_url = (
        f"https://{values['azureMcpContainerAppName']}.{values['environmentDefaultDomain']}"
    )
    if infrastructure["azureMcpServerUrl"] != expected_url:
        raise ValueError("MCP target must remain the owned private Environment URL")
    unique_name = infrastructure.get("azureMcpEntraAppUniqueName", "")
    if not isinstance(unique_name, str) or not re.fullmatch(
        r"azbrief-v2-mcp-[a-z0-9]{13}", unique_name
    ):
        raise ValueError("Core output must provide the deterministic MCP directory uniqueName")
    identity_id = f"{cli.group_id}/providers/Microsoft.ManagedIdentity/userAssignedIdentities/{values['azureMcpIdentityName']}"
    mcp_identity = cli.get(identity_id, "2023-01-31")
    _owned(mcp_identity, identity_id)
    automation = _verify_mcp_automation(cli, values)
    apps = (
        []
        if automation
        else cli.json(
            "ad",
            "app",
            "list",
            "--filter",
            f"uniqueName eq '{unique_name}'",
            "--query",
            "[].{id:id,appId:appId,uniqueName:uniqueName,displayName:displayName}",
        )
    )
    if not isinstance(apps, list) or len(apps) > 1:
        raise ValueError("MCP directory inventory is incomplete or ambiguous")
    if apps:
        app = apps[0]
        if (
            app.get("uniqueName") != unique_name
            or app.get("displayName") != "AzBrief v2 Azure MCP API"
            or (options.get("azureMcpClientId") and app.get("appId") != options["azureMcpClientId"])
        ):
            raise ValueError("Refusing to adopt a different MCP directory application")
        UUID(app["id"])
        UUID(app["appId"])
    completed_options = {
        **options,
        "azureMcpClientId": "0" * 36,
        "azureMcpServicePrincipalId": "0" * 36,
        "azureMcpAppRoleId": "0" * 36,
    }
    if len(json.dumps(completed_options, ensure_ascii=False, separators=(",", ":"))) > 2048:
        raise ValueError("MCP preparation exceeds the saved option tag budget")
    if automation:
        return {
            "infrastructure": infrastructure,
            "infrastructureOptions": options,
            "automation": automation,
            "mcpPrincipalId": str(UUID(mcp_identity["properties"]["principalId"])),
        }
    return {"infrastructure": infrastructure, "infrastructureOptions": options}


def run_mcp_authentication(cli: AzureCli, mode: str, name: str) -> None:
    """Prepare Graph through the authorized CLI without replaying core infrastructure."""
    operations = ("validate", "what-if", "create") if mode == "deploy" else (mode,)
    for operation in operations:
        parameters = prepare_mcp_authentication(cli)
        if operation != "preflight":
            deploy_template(
                cli,
                V2
                / (
                    "mcp-authentication-automatic.json"
                    if parameters.get("automation")
                    else "mcp-authentication.json"
                ),
                parameters,
                operation,
                name + "-mcp-auth",
            )
        logger.info(
            "v2_mcp_preparation_operation_complete", operation=operation, application_ready=False
        )
    if mode == "deploy":
        tags, values = read_infrastructure(cli)
        options = decode_tag_options(tags)
        project_id = f"{values['foundryAccountResourceId']}/projects/{values['projectName']}"
        connection = cli.get(
            f"{project_id}/connections/{values['azureMcpProjectConnectionName']}",
            "2025-10-01-preview",
        )["properties"]
        if (
            connection.get("category") != "RemoteTool"
            or connection.get("authType") != "ProjectManagedIdentity"
            or connection.get("target", "").rstrip("/") != values["azureMcpServerUrl"].rstrip("/")
            or connection.get("audience") != f"api://{options['azureMcpClientId']}"
        ):
            raise ValueError("MCP connection/authentication readback does not match the handoff")


def load_infrastructure_parameters(path: Path) -> dict[str, Any]:
    """Resolve literal parameters and safe name defaults without reading developer .env files."""
    supplied = json.loads(path.read_text(encoding="utf-8-sig"))["parameters"]
    for mode, (name, existing, _, resource_type, _) in SHARED_RESOURCES.items():
        entry = supplied.get(mode, {})
        if isinstance(entry, dict) and entry.get("value") == "existing":
            reference = supplied.get(existing, {})
            if not isinstance(reference, dict) or set(reference) != {"value"}:
                raise ValueError("Existing-resource mode requires its literal full ARM ID")
            _, _, leaf = resource_scope(reference["value"], resource_type)
            supplied[name] = {"value": leaf}
    if "namingPrefix" in supplied or "namingSuffix" in supplied:
        if "namingPrefix" not in supplied or "namingSuffix" not in supplied:
            raise ValueError("Supply both namingPrefix and namingSuffix")
        names = derive_resource_names(
            supplied["namingPrefix"]["value"], supplied["namingSuffix"]["value"]
        )
        for field, value in names.items():
            supplied.setdefault(field, {"value": value})
    shared_network = any(
        isinstance(supplied.get(mode), dict) and supplied[mode].get("value") == "existing"
        for mode in ("foundryAccountMode", "containerAppsEnvironmentMode")
    )
    inferred_network_fields = (
        {"virtualNetworkResourceGroupName", "virtualNetworkName", "location"}
        if shared_network
        else set()
    )
    schema = json.loads((V2 / "infrastructure.json").read_text(encoding="utf-8"))["parameters"]
    if set(supplied) - set(schema) or set(supplied) & INTERNAL_PARAMETERS:
        raise ValueError("Unknown or internally derived infrastructure parameters")
    values: dict[str, Any] = {}
    for name, definition in schema.items():
        if name in INTERNAL_PARAMETERS:
            continue
        if name in supplied:
            if not isinstance(supplied[name], dict) or set(supplied[name]) != {"value"}:
                raise ValueError("Infrastructure parameters must contain explicit literal values")
            value = supplied[name]["value"]
        elif name in inferred_network_fields:
            value = ""
        elif "defaultValue" in definition:
            value = copy.deepcopy(definition["defaultValue"])
            if isinstance(value, str) and value.startswith("["):
                continue
        else:
            raise ValueError(f"Missing required infrastructure parameter: {name}")
        kind = definition["type"].lower()
        expected = {"string": str, "int": int, "bool": bool, "object": dict, "array": list}[kind]
        if type(value) is not expected:
            raise ValueError(f"Invalid infrastructure parameter type: {name}")
        if isinstance(value, str) and ("<" in value or ">" in value or value.startswith("[")):
            raise ValueError(f"Replace the example placeholder or ARM expression for {name}")
        if "allowedValues" in definition and value not in definition["allowedValues"]:
            raise ValueError(f"Unsupported infrastructure parameter: {name}")
        if isinstance(value, str) and not definition.get("defaultValue") and not value:
            if "defaultValue" not in definition and name not in inferred_network_fields:
                raise ValueError(f"Empty required infrastructure parameter: {name}")
        for minimum, maximum, measured in (
            ("minLength", "maxLength", len(value) if isinstance(value, str) else None),
            ("minValue", "maxValue", value if type(value) is int else None),
        ):
            if measured is not None and (
                measured < definition.get(minimum, measured)
                or measured > definition.get(maximum, measured)
            ):
                raise ValueError(f"Infrastructure parameter exceeds its bounds: {name}")
        values[name] = value
    for field, prefix in {
        "schedulerJobName": "job",
        "controlPlaneIdentityName": "id",
        "logAnalyticsWorkspaceName": "log",
        "applicationInsightsName": "appi",
        "communicationServicesName": "acs",
        "emailServiceName": "email",
    }.items():
        values.setdefault(field, f"{prefix}-{values['containerAppName']}")
    values["tags"] = {
        **values["tags"],
        "application": "AzBrief",
        "customer": "KT",
        "deploymentProfile": PROFILE,
    }
    validate_tag_budget(values["tags"])
    if values["useMicrosoftManagedAgentData"]:
        values["cosmosAccountName"] = ""
        values["searchServiceName"] = ""
    elif not values["cosmosAccountName"] or not values["searchServiceName"]:
        raise ValueError("Existing BYO mode requires Cosmos DB and Search names")
    for mode, (name, existing, _, resource_type, _) in SHARED_RESOURCES.items():
        if values[mode] == "existing":
            _, _, leaf = resource_scope(values[existing], resource_type)
            values[name] = leaf
        elif values[existing]:
            raise ValueError("New-resource mode cannot also supply an existing resource ID")
    if not values["useMicrosoftManagedAgentData"] and any(
        values[mode] == "existing" for mode in ("foundryAccountMode", "storageAccountMode")
    ):
        raise ValueError(
            "Shared Foundry/Storage requires Microsoft-managed agent data, not legacy BYO"
        )
    for field, prefix in (
        ("stateContainerName", "azbrief-state"),
        ("archiveContainerName", "azbrief-archive"),
    ):
        values[field] = values[field] or (
            f"{prefix}-{values['containerAppName'].lower()}"
            if values["storageAccountMode"] == "existing"
            else prefix
        )
        if values["storageAccountMode"] == "new" and values[field] != prefix:
            raise ValueError("New Storage retains the existing v2 workload container names")
    values["simpleModelVersion"] = values.get("simpleModelVersion") or values["modelVersion"]
    values["foundryPublishingAccess"] = normalize_foundry_publishing_access(
        values.get("foundryPublishingAccess", {})
    )
    if values["foundryPublishingAccess"] and (
        values["foundryAccountMode"] != "new" or not values["useMicrosoftManagedAgentData"]
    ):
        raise ValueError("Foundry publishing access is only for new managed-data Foundry accounts")
    if len(values["tags"]) > 15:
        raise ValueError("Resource tags must also fit the Private DNS 15-tag limit")
    return values


def make_tag_contract(
    values: dict[str, Any], deployment_name: str, state: str = "ready"
) -> dict[str, str]:
    contract = {PREFIX + suffix: values.get(field, "") for field, suffix in TAG_FIELDS.items()}
    contract.update(
        {
            PREFIX + "deployment": deployment_name,
            PREFIX + "schema": "2",
            PREFIX + "profile": PROFILE,
            PREFIX + "state": state,
            PREFIX + "acaPeEnabled": str(values["deployContainerAppsPrivateEndpoint"]).lower(),
            **APPLICATION_TAG_DEFAULTS,
        }
    )
    options = {field: values[field] for field in OPTION_FIELDS}
    options.update(
        {
            "manageAzureMcp": not bool(values.get("azureMcpServerUrl")),
            "useMicrosoftManagedAgentData": values.get("useMicrosoftManagedAgentData", False),
            "simpleModelVersion": values.get("simpleModelVersion") or values["modelVersion"],
            "azureMcpClientId": values.get("azureMcpClientId", ""),
            "azureMcpServicePrincipalId": values.get("azureMcpServicePrincipalId", ""),
            "azureMcpAppRoleId": values.get("azureMcpAppRoleId", ""),
            "namingPrefix": values.get("namingPrefix", ""),
            "namingSuffix": values.get("namingSuffix", ""),
        }
    )
    options.update({field: values[field] for field in NETWORK_OPTION_FIELDS if field in values})
    options.update({field: values.get(field, "new") for field in SHARED_RESOURCES})
    automation = validate_mcp_automation(
        values.get("mcpAutomation", {}), values.get("subscriptionId")
    )
    if automation:
        options["mcpAutomation"] = automation
    publishing = normalize_foundry_publishing_access(
        values.get("foundryPublishingAccess", {}), values.get("subscriptionId")
    )
    if publishing:
        options["foundryPublishingAccess"] = publishing
    encoded = json.dumps(options, ensure_ascii=False, separators=(",", ":"))
    if len(encoded) + (108 if automation else 0) > 8 * 256:
        raise ValueError("Saved infrastructure options exceed the 2048-character tag budget")
    contract.update(
        {key: encoded[index * 256 : (index + 1) * 256] for index, key in enumerate(OPTION_TAG_KEYS)}
    )
    binding_fields = set(RESOURCE_BINDING_TYPES) | CONTAINER_NAME_FIELDS
    if binding_fields <= set(values):
        encoded_bindings = json.dumps(
            {field: values[field] for field in sorted(binding_fields)},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        if len(encoded_bindings) > 2048:
            raise ValueError("Resource IDs exceed the 2048-character tag budget")
        contract.update(
            {
                RESOURCE_TAG_KEYS[index // 256]: encoded_bindings[index : index + 256]
                for index in range(0, len(encoded_bindings), 256)
            }
        )
    return parse_tag_contract(contract, require_ready=state == "ready")


def _shared_document(
    cli: AzureCli, resource_id: str, resource_type: str, api_version: str
) -> dict[str, Any]:
    resource_scope(resource_id, resource_type, cli.subscription)
    document: dict[str, Any] = cli.get(resource_id, api_version)
    if (
        not isinstance(document, dict)
        or document.get("error")
        or document.get("nextLink")
        or str(document.get("id", "")).casefold() != resource_id.casefold()
        or not isinstance(document.get("properties"), dict)
        or document["properties"].get("provisioningState") != "Succeeded"
    ):
        raise ValueError("Shared resource is not ready or its typed ARM lookup was redirected")
    return document


def _verify_foundry_publishing_access(
    cli: AzureCli, values: dict[str, Any], account: dict[str, Any] | None = None
) -> dict[str, list[str]]:
    """Verify exact allowed clients and retain private-only validation outside the opt-in mode."""
    policy = normalize_foundry_publishing_access(
        values.get("foundryPublishingAccess", {}), cli.subscription
    )
    if not policy:
        if (
            account is not None
            and str(account["properties"].get("publicNetworkAccess", "")).casefold() != "disabled"
        ):
            raise ValueError("The tagged Foundry account must retain disabled public access")
        return {}
    if values.get("foundryAccountMode", "new") != "new" or not values.get(
        "useMicrosoftManagedAgentData", False
    ):
        raise ValueError("Foundry publishing access is only for new managed-data Foundry accounts")
    for subnet_id in policy["subnetResourceIds"]:
        subnet = _shared_document(
            cli, subnet_id, "Microsoft.Network/virtualNetworks/subnets", NETWORK_API
        )
        endpoints = subnet["properties"].get("serviceEndpoints") or []
        if not any(
            isinstance(endpoint, dict)
            and endpoint.get("service") == "Microsoft.CognitiveServices"
            and endpoint.get("provisioningState") == "Succeeded"
            for endpoint in endpoints
        ):
            raise ValueError(
                "Foundry publishing client subnet requires a ready CognitiveServices service endpoint"
            )
    if account is None:
        return policy
    props = account["properties"]
    expected_subnet = (
        f"/subscriptions/{cli.subscription}/resourceGroups/{values['virtualNetworkResourceGroupName']}"
        f"/providers/Microsoft.Network/virtualNetworks/{values['virtualNetworkName']}"
        f"/subnets/{values['foundrySubnetName']}"
    ).casefold()
    injections = props.get("networkInjections") or []
    acls = props.get("networkAcls") or {}
    if (
        props.get("disableLocalAuth") is not True
        or len(injections) != 1
        or str(injections[0].get("subnetArmId", "")).casefold() != expected_subnet
        or injections[0].get("useMicrosoftManagedNetwork") is not False
        or acls.get("defaultAction") != "Deny"
        or acls.get("bypass", "None") != "None"
    ):
        raise ValueError(
            "Foundry publishing requires preserved injection, Entra-only auth and default deny"
        )
    actual = (
        normalize_foundry_publishing_access(
            {
                "publicIpRanges": [entry["value"] for entry in acls.get("ipRules", [])],
                "subnetResourceIds": [entry["id"] for entry in acls.get("virtualNetworkRules", [])],
            },
            cli.subscription,
        )
        if acls.get("ipRules") or acls.get("virtualNetworkRules")
        else {}
    )
    access = str(props.get("publicNetworkAccess", "")).casefold()
    if not (
        (access == "enabled" and actual == policy)
        or (access == "disabled" and actual in ({}, policy))
    ):
        raise ValueError("Foundry publishing access differs from its saved exact allow-list")
    return policy


def _verify_mcp_automation(cli: AzureCli, values: dict[str, Any]) -> dict[str, str]:
    """Read setup prerequisites without granting consent, changing networks or reading keys."""
    automation = validate_mcp_automation(values.get("mcpAutomation", {}), cli.subscription)
    if not automation:
        return {}
    if values.get("azureMcpServerUrl") and values.get("manageAzureMcp") is not True:
        raise ValueError("Automatic MCP setup is only for the managed workload MCP service")
    identity = cli.get(automation["identityResourceId"], "2023-01-31")
    if (
        str(identity.get("id", "")).casefold() != automation["identityResourceId"].casefold()
        or str(identity.get("type", "")).casefold()
        != MCP_AUTOMATION_TYPES["identityResourceId"].casefold()
        or str(identity.get("properties", {}).get("tenantId", "")).casefold()
        != cli.tenant.casefold()
    ):
        raise ValueError("The MCP setup identity is unreadable, redirected or in another tenant")
    UUID(identity["properties"]["principalId"])
    UUID(identity["properties"]["clientId"])
    if automation["identityResourceId"].casefold() in {
        f"{cli.group_id}/providers/Microsoft.ManagedIdentity/userAssignedIdentities/{values[name]}".casefold()
        for name in ("controlPlaneIdentityName", "azureMcpIdentityName")
    }:
        raise ValueError("MCP setup requires a separate deployment-only identity")
    subnet = _shared_document(
        cli, automation["subnetResourceId"], MCP_AUTOMATION_TYPES["subnetResourceId"], NETWORK_API
    )
    props = subnet["properties"]
    delegations = props.get("delegations") or []
    if (
        len(delegations) != 1
        or delegations[0].get("properties", {}).get("serviceName")
        != "Microsoft.ContainerInstance/containerGroups"
    ):
        raise ValueError("MCP automation needs an existing ACI-only execution subnet")
    vnet_id = "/".join(automation["subnetResourceId"].split("/")[:9])
    vnet = _existing_virtual_network(cli, vnet_id)
    if normalize_region(vnet["location"]) != normalize_region(values["location"]):
        raise ValueError("The MCP automation execution VNet must use the workload region")
    storage = _shared_document(
        cli,
        automation["storageAccountResourceId"],
        MCP_AUTOMATION_TYPES["storageAccountResourceId"],
        "2023-05-01",
    )
    if (
        automation["storageAccountResourceId"].casefold()
        == resource_bindings(cli, values)["storageAccountResourceId"].casefold()
        or storage.get("kind") not in {"StorageV2", "FileStorage"}
        or normalize_region(storage["location"]) != normalize_region(values["location"])
        or (
            storage["properties"].get("publicNetworkAccess") != "Disabled"
            and storage["properties"].get("networkAcls", {}).get("defaultAction") != "Deny"
        )
    ):
        raise ValueError(
            "MCP automation requires separate approved private/restricted File Storage"
        )
    return automation


def _shared_network_bindings(
    cli: AzureCli, values: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Resolve omitted network inputs from exact private owners, rejecting explicit conflicts."""
    values = copy.deepcopy(values)
    documents: dict[str, dict[str, Any]] = {}
    bound_vnet = ""
    for mode, role, subnet_field, label in (
        ("foundryAccountMode", "foundry", "foundrySubnetName", "Foundry"),
        (
            "containerAppsEnvironmentMode",
            "container_apps",
            "containerAppsSubnetName",
            "Environment",
        ),
    ):
        if values[mode] != "existing":
            continue
        _, reference, _, resource_type, api_version = SHARED_RESOURCES[mode]
        document = _shared_document(cli, values[reference], resource_type, api_version)
        props = document["properties"]
        if str(props.get("publicNetworkAccess", "")).casefold() != "disabled":
            raise ValueError(f"Shared {label} requires publicNetworkAccess Disabled before reuse")
        if role == "foundry":
            injections = props.get("networkInjections")
            if not isinstance(injections, list) or any(
                not isinstance(item, dict) for item in injections
            ):
                raise ValueError("Shared Foundry requires one existing agent VNet injection")
            agent_bindings = [item for item in injections if item.get("scenario") == "agent"]
            if (
                len(agent_bindings) != 1
                or agent_bindings[0].get("useMicrosoftManagedNetwork") is not False
            ):
                raise ValueError(
                    "Shared Foundry requires exactly one agent VNet injection with "
                    "useMicrosoftManagedNetwork false"
                )
            subnet_id = agent_bindings[0].get("subnetArmId", "")
        else:
            configuration = props.get("vnetConfiguration")
            if not isinstance(configuration, dict) or configuration.get("internal") is not True:
                raise ValueError("Shared Environment requires an existing internal VNet binding")
            subnet_id = configuration.get("infrastructureSubnetId", "")
        try:
            _, network_group, subnet_name = resource_scope(
                subnet_id, "Microsoft.Network/virtualNetworks/subnets", cli.subscription
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"Shared {label} requires its exact existing same-subscription subnet ARM ID"
            ) from exc
        vnet_id = "/".join(subnet_id.split("/")[:9])
        if bound_vnet and bound_vnet.casefold() != vnet_id.casefold():
            raise ValueError("Shared Foundry and Environment must retain the same existing VNet")
        bound_vnet = vnet_id
        for field, actual in (
            ("virtualNetworkResourceGroupName", network_group),
            ("virtualNetworkName", subnet_id.split("/")[8]),
            (subnet_field, subnet_name),
        ):
            selected = values.get(field, "")
            if not isinstance(selected, str) or (
                selected and selected.casefold() != actual.casefold()
            ):
                raise ValueError(
                    f"Shared {label} {field} conflicts with its existing VNet/subnet binding"
                )
            if not selected:
                values[field] = actual
        region = normalize_region(document.get("location", ""))
        if values.get("location") and normalize_region(values["location"]) != region:
            raise ValueError(f"Shared {label} must retain its exact same-region VNet binding")
        values["location"] = region
        documents[role] = document
    values["location"] = normalize_region(values.get("location", ""))
    if any(
        not isinstance(values.get(field), str) or not values[field]
        for field in ("virtualNetworkResourceGroupName", "virtualNetworkName")
    ):
        raise ValueError("Select the existing VNet or bind a private shared Foundry/Environment")
    for mode, field, label in (
        ("foundryAccountMode", "foundrySubnetName", "Foundry"),
        ("containerAppsEnvironmentMode", "containerAppsSubnetName", "Environment"),
    ):
        if values[mode] == "new" and not values.get(field):
            raise ValueError(f"New {label} requires an explicit {field}")
    if _private_endpoint_count(values) and not values.get("peSubnetName"):
        raise ValueError("New Private Endpoints require an explicit peSubnetName")
    values.setdefault("peSubnetName", "")
    if bound_vnet:
        vnet = _existing_virtual_network(cli, bound_vnet)
        if normalize_region(vnet.get("location", "")) != values["location"]:
            raise ValueError("Shared Foundry/Environment must retain their same-region VNet")
        subnets = vnet["properties"].get("subnets")
        if not isinstance(subnets, list):
            raise ValueError("Shared VNet subnet inventory is incomplete")
        for role, field in (
            ("foundry", "foundrySubnetName"),
            ("container_apps", "containerAppsSubnetName"),
        ):
            if role not in documents:
                continue
            matches = [
                row
                for row in subnets
                if isinstance(row, dict)
                and str(row.get("name", "")).casefold() == values[field].casefold()
            ]
            expected_id = f"{bound_vnet}/subnets/{values[field]}"
            if (
                len(matches) != 1
                or str(matches[0].get("id", expected_id)).casefold() != expected_id.casefold()
            ):
                raise ValueError(
                    "The shared owner's bound subnet is missing, ambiguous or redirected"
                )
    return values, documents


def _existing_virtual_network(cli: AzureCli, vnet_id: str) -> dict[str, Any]:
    resource_scope(vnet_id, "Microsoft.Network/virtualNetworks", cli.subscription)
    vnet = cli.get(vnet_id, NETWORK_API)
    if (
        not isinstance(vnet, dict)
        or str(vnet.get("id", "")).casefold() != vnet_id.casefold()
        or vnet.get("error")
        or vnet.get("nextLink")
        or not isinstance(vnet.get("properties"), dict)
        or vnet["properties"].get("provisioningState") != "Succeeded"
    ):
        raise ValueError("The exact VNet read is incomplete, unready or redirected")
    return vnet


def _shared_private_endpoint(
    cli: AzureCli, document: dict[str, Any], vnet_id: str, group: str
) -> None:
    for connection in document["properties"].get("privateEndpointConnections", []):
        properties = connection["properties"]
        if properties.get("privateLinkServiceConnectionState", {}).get("status") != "Approved":
            continue
        endpoint_id = properties.get("privateEndpoint", {}).get("id", "")
        endpoint = _shared_document(
            cli, endpoint_id, "Microsoft.Network/privateEndpoints", NETWORK_API
        )
        props = endpoint["properties"]
        links = props.get("privateLinkServiceConnections", [])
        if props.get("subnet", {}).get("id", "").casefold().startswith(
            vnet_id.casefold() + "/subnets/"
        ) and any(
            link.get("properties", {}).get("privateLinkServiceId", "").casefold()
            == document["id"].casefold()
            and group in link.get("properties", {}).get("groupIds", [])
            for link in links
        ):
            return
    raise ValueError(
        "Shared Foundry/Storage requires an approved Private Endpoint on the selected VNet"
    )


def _shared_environment_dns(cli: AzureCli, environment: dict[str, Any], vnet_id: str) -> None:
    props = environment["properties"]
    domain, address = props.get("defaultDomain", ""), props.get("staticIp", "")
    if not domain or not address:
        raise ValueError("Shared Environment requires its actual internal domain and VIP")
    zones = cli.json(
        "resource",
        "list",
        "--subscription",
        cli.subscription,
        "--resource-type",
        "Microsoft.Network/privateDnsZones",
    )
    if not isinstance(zones, list):
        raise ValueError("Shared Environment DNS inventory must be complete")
    matching = []
    for zone in zones:
        if zone.get("name", "").casefold() != domain.casefold():
            continue
        links = cli.collection(f"{zone['id']}/virtualNetworkLinks", "2024-06-01")
        if any(
            row["properties"].get("virtualNetwork", {}).get("id", "").casefold()
            == vnet_id.casefold()
            and row["properties"].get("registrationEnabled") is False
            and row["properties"].get("provisioningState") == "Succeeded"
            for row in links
        ):
            matching.append(zone)
    if len(matching) != 1:
        raise ValueError(
            "Shared Environment requires one existing private DNS zone linked to the selected VNet"
        )
    for name in ("*", "@"):
        record = cli.get(f"{matching[0]['id']}/A/{name}", "2024-06-01")
        if record.get("properties", {}).get("aRecords") != [{"ipv4Address": address}]:
            raise ValueError("Shared Environment private DNS must resolve its exact internal VIP")


def _resolve_shared_resources(cli: AzureCli, values: dict[str, Any], stage: str) -> dict[str, Any]:
    """Validate explicit shared targets without adopting or changing their configuration."""
    values = copy.deepcopy(values)
    for mode, (name, existing, _, resource_type, _) in SHARED_RESOURCES.items():
        values.setdefault(mode, "new")
        values.setdefault(existing, "")
        if values[mode] not in {"new", "existing"}:
            raise ValueError("Choose new or existing for each shared resource")
        if values[mode] == "existing":
            _, _, leaf = resource_scope(values[existing], resource_type, cli.subscription)
            if values[name].casefold() != leaf.casefold():
                raise ValueError("Shared resource name must match its selected full ARM ID")
        elif values[existing]:
            raise ValueError("New-resource mode cannot supply an existing resource ID")
    values.setdefault("existingLogAnalyticsWorkspaceResourceId", "")
    values.setdefault("existingEmailDomainResourceId", "")
    if not values.get("useMicrosoftManagedAgentData", False) and any(
        values[mode] == "existing" for mode in ("foundryAccountMode", "storageAccountMode")
    ):
        raise ValueError("Shared Foundry/Storage requires Microsoft-managed agent data")
    if values.get("virtualNetworkMode", "existing") == "new" and any(
        values[mode] == "existing"
        for mode in ("foundryAccountMode", "containerAppsEnvironmentMode")
    ):
        raise ValueError("Existing Foundry/Environment must retain their existing VNet")
    values, shared_network = _shared_network_bindings(cli, values)
    vnet_id = (
        f"/subscriptions/{cli.subscription}/resourceGroups/{values['virtualNetworkResourceGroupName']}"
        f"/providers/Microsoft.Network/virtualNetworks/{values['virtualNetworkName']}"
    )
    if values["foundryAccountMode"] == "existing":
        account = shared_network["foundry"]
        props = account["properties"]
        if (
            account.get("kind") != "AIServices"
            or props.get("allowProjectManagement") is not True
            or props.get("disableLocalAuth") is not True
            or not isinstance(props.get("customSubDomainName"), str)
            or not re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9-]{0,63}", props.get("customSubDomainName", "")
            )
            or str(props.get("publicNetworkAccess", "")).casefold() != "disabled"
        ):
            raise ValueError(
                "Shared Foundry must retain project support, Entra-only private access and the exact same-region agent subnet"
            )
        hosts = cli.collection(f"{account['id']}/capabilityHosts", "2025-04-01-preview")
        if not hosts or any(
            row.get("properties", {}).get("provisioningState") != "Succeeded" for row in hosts
        ):
            raise ValueError("Prepare the shared account Capability Host before referencing it")
        _shared_private_endpoint(cli, account, vnet_id, "account")
    if values["containerAppsEnvironmentMode"] == "existing":
        if values["deployContainerAppsPrivateEndpoint"]:
            raise ValueError(
                "Existing Environment networking is retained; do not request a new Environment Private Endpoint"
            )
        environment = shared_network["container_apps"]
        props = environment["properties"]
        if (
            normalize_region(environment.get("location", "")) != values["location"]
            or str(props.get("publicNetworkAccess", "")).casefold() != "disabled"
            or props.get("vnetConfiguration", {}).get("internal") is not True
            or props.get("vnetConfiguration", {}).get("infrastructureSubnetId", "").casefold()
            != f"{vnet_id}/subnets/{values['containerAppsSubnetName']}".casefold()
            or not any(
                row.get("name") == "Consumption" and row.get("workloadProfileType") == "Consumption"
                for row in props.get("workloadProfiles", [])
            )
        ):
            raise ValueError(
                "Shared Environment must retain internal/PNA-disabled Consumption and the exact same-region subnet"
            )
        logs = props.get("appLogsConfiguration", {})
        customer_id = logs.get("logAnalyticsConfiguration", {}).get("customerId", "")
        if logs.get("destination") != "log-analytics" or not customer_id:
            raise ValueError(
                "Shared Environment must already collect logs in a Log Analytics workspace"
            )
        workspace_id = values["existingLogAnalyticsWorkspaceResourceId"]
        if not workspace_id:
            workspaces = cli.collection(
                f"/subscriptions/{cli.subscription}/providers/Microsoft.OperationalInsights/workspaces",
                "2025-02-01",
            )
            matches = [
                row
                for row in workspaces
                if str(row.get("properties", {}).get("customerId", "")).casefold()
                == customer_id.casefold()
            ]
            if len(matches) != 1:
                raise ValueError("The shared Environment workspace lookup is missing or ambiguous")
            workspace_id = matches[0]["id"]
        workspace = _shared_document(
            cli, workspace_id, "Microsoft.OperationalInsights/workspaces", "2025-02-01"
        )
        if str(workspace["properties"].get("customerId", "")).casefold() != customer_id.casefold():
            raise ValueError("Shared workspace does not match the Environment log destination")
        values["existingLogAnalyticsWorkspaceResourceId"] = workspace_id
        values["logAnalyticsWorkspaceName"] = workspace_id.split("/")[-1]
        _shared_environment_dns(cli, environment, vnet_id)
    elif values["existingLogAnalyticsWorkspaceResourceId"]:
        raise ValueError("A new Environment owns its new workspace")
    if values["storageAccountMode"] == "existing":
        storage = _shared_document(
            cli,
            values["existingStorageAccountResourceId"],
            "Microsoft.Storage/storageAccounts",
            "2023-05-01",
        )
        props = storage["properties"]
        if (
            storage.get("kind") not in {"StorageV2", "BlobStorage", "BlockBlobStorage"}
            or str(props.get("publicNetworkAccess", "")).casefold() != "disabled"
            or props.get("allowSharedKeyAccess") is not False
            or props.get("allowBlobPublicAccess") is not False
            or props.get("supportsHttpsTrafficOnly") is not True
            or props.get("minimumTlsVersion") != "TLS1_2"
        ):
            raise ValueError(
                "Shared Storage must already use private Entra-only HTTPS/TLS1_2 Blob access"
            )
        _shared_private_endpoint(cli, storage, vnet_id, "blob")
        names = {
            row["name"].split("/")[-1]: row
            for row in cli.collection(
                f"{storage['id']}/blobServices/default/containers", "2023-05-01"
            )
        }
        for field, prefix in (
            ("stateContainerName", "azbrief-state"),
            ("archiveContainerName", "azbrief-archive"),
        ):
            name = values.get(field) or f"{prefix}-{values['containerAppName'].lower()}"
            values[field] = name
            if name in {"azbrief-state", "azbrief-archive"}:
                raise ValueError("Shared Storage requires workload-specific containers")
            if stage == "foundation" and name in names:
                raise ValueError("Refusing to adopt existing shared-Storage workload containers")
            if stage != "foundation" and (
                name not in names or names[name].get("properties", {}).get("publicAccess") != "None"
            ):
                raise ValueError(
                    "Shared workload Blob containers must already exist and remain private"
                )
    if values["communicationServicesMode"] == "existing":
        communication = _shared_document(
            cli,
            values["existingCommunicationServicesResourceId"],
            "Microsoft.Communication/communicationServices",
            "2023-04-01",
        )
        linked = communication["properties"].get("linkedDomains", [])
        domain_id = values["existingEmailDomainResourceId"]
        if (
            not isinstance(linked, list)
            or not linked
            or len({item.casefold() for item in linked}) != len(linked)
        ):
            raise ValueError("Shared ACS must already have an unambiguous linked email domain")
        if not domain_id:
            if len(linked) != 1:
                raise ValueError("Select one of the shared ACS linked domain resource IDs")
            domain_id = linked[0]
        if domain_id.casefold() not in {item.casefold() for item in linked}:
            raise ValueError("Selected email domain is not already linked to the shared ACS")
        domain = _shared_document(
            cli, domain_id, "Microsoft.Communication/emailServices/domains", "2023-04-01"
        )
        domain_props = domain["properties"]
        if not communication["properties"].get("hostName") or not domain_props.get(
            "fromSenderDomain"
        ):
            raise ValueError(
                "Shared ACS/domain must provide their actual endpoint and sender domain"
            )
        if domain_props.get("domainManagement") != "AzureManaged":
            if (
                domain_props.get("verificationStates", {}).get("Domain", {}).get("status")
                != "Verified"
            ):
                raise ValueError("Shared custom email domain must already be verified")
            senders = cli.collection(f"{domain_id}/senderUsernames", "2023-04-01")
            if not any(
                row.get("properties", {})
                .get("username", row.get("name", "").split("/")[-1])
                .casefold()
                == "donotreply"
                for row in senders
            ):
                raise ValueError("Shared email domain must permit the default DoNotReply sender")
        values["existingEmailDomainResourceId"] = domain_id
        values["emailServiceName"] = domain_id.split("/")[8]
    elif values["existingEmailDomainResourceId"]:
        raise ValueError("A new ACS owns its new managed email domain")
    shared_namespaces = set()
    if values["foundryAccountMode"] == "existing":
        shared_namespaces.update(
            {
                "privatelink.cognitiveservices.azure.com",
                "privatelink.openai.azure.com",
                "privatelink.services.ai.azure.com",
            }
        )
    if values["storageAccountMode"] == "existing":
        shared_namespaces.add("privatelink.blob.core.windows.net")
    if shared_namespaces:
        resolved = _private_dns_bindings(cli, values, vnet_id)
        if not shared_namespaces <= set(resolved):
            raise ValueError("Shared Foundry/Storage requires existing canonical private DNS links")
    return values


def _private_dns_bindings(cli: AzureCli, values: dict[str, Any], vnet_id: str) -> dict[str, str]:
    required = {
        "privatelink.cognitiveservices.azure.com",
        "privatelink.openai.azure.com",
        "privatelink.services.ai.azure.com",
        "privatelink.blob.core.windows.net",
        "privatelink.documents.azure.com",
        "privatelink.search.windows.net",
        f"privatelink.{values['location']}.azurecontainerapps.io",
    }
    if values.get("useMicrosoftManagedAgentData", False):
        required -= {"privatelink.documents.azure.com", "privatelink.search.windows.net"}
    if values.get("containerAppsEnvironmentMode", "new") == "existing":
        required.discard(f"privatelink.{values['location']}.azurecontainerapps.io")
    explicit = values["existingPrivateDnsZoneIds"]
    if set(explicit) - required:
        raise ValueError("Unexpected private DNS namespace")
    zones = cli.json(
        "resource",
        "list",
        "--subscription",
        cli.subscription,
        "--resource-type",
        "Microsoft.Network/privateDnsZones",
    )
    if not isinstance(zones, list):
        raise ValueError("Private DNS inventory must be complete")
    result: dict[str, str] = {}
    unlinked_target_zones: set[str] = set()
    for zone in zones:
        name = zone["name"].casefold()
        if name not in required:
            continue
        links = cli.collection(f"{zone['id']}/virtualNetworkLinks", "2024-06-01")
        matching = [
            link
            for link in links
            if link["properties"].get("virtualNetwork", {}).get("id", "").casefold()
            == vnet_id.casefold()
        ]
        if matching:
            if (
                name in result
                or len(matching) != 1
                or matching[0]["properties"].get("registrationEnabled") is not False
            ):
                raise ValueError("Private DNS links must be unique and non-registering")
            if matching[0]["properties"].get("provisioningState") != "Succeeded":
                raise ValueError("The linked private DNS zone must be ready")
            result[name] = zone["id"]
        elif (
            zone["id"].casefold()
            == f"{cli.group_id}/providers/Microsoft.Network/privateDnsZones/{name}".casefold()
        ):
            unlinked_target_zones.add(name)
    for name, resource_id in explicit.items():
        if (
            not isinstance(resource_id, str)
            or not resource_id.startswith("/subscriptions/")
            or any(character in resource_id for character in "?#")
        ):
            raise ValueError("Private DNS overrides must be exact ARM resource IDs")
        zone = cli.get(resource_id, "2024-06-01")
        if (
            zone.get("id", "").casefold() != resource_id.casefold()
            or zone.get("name", "").casefold() != name
        ):
            raise ValueError("Private DNS override does not match its namespace")
        if name in result and result[name].casefold() != resource_id.casefold():
            raise ValueError("Private DNS override conflicts with the canonical VNet-linked zone")
        if name not in result:
            links = cli.collection(f"{resource_id}/virtualNetworkLinks", "2024-06-01")
            matching = [
                link
                for link in links
                if link["properties"].get("virtualNetwork", {}).get("id", "").casefold()
                == vnet_id.casefold()
            ]
            if (
                len(matching) != 1
                or matching[0]["properties"].get("registrationEnabled") is not False
                or matching[0]["properties"].get("provisioningState") != "Succeeded"
            ):
                raise ValueError(
                    "An explicit private DNS zone must already be linked to the selected VNet"
                )
        result[name] = resource_id
    if unlinked_target_zones - set(result):
        raise ValueError(
            "An existing target-group DNS zone is not linked to the selected VNet; never overwrite it"
        )
    return result


def prepare_infrastructure(
    cli: AzureCli,
    values: dict[str, Any],
    stage: str,
    name: str,
    *,
    allow_model_reselection: bool = False,
) -> dict[str, Any]:
    """Keep infrastructure one-time, reuse the approved network and derive completion decisions."""
    cli.assert_context()
    publishing = _verify_foundry_publishing_access(cli, values)
    values = {**values, "foundryPublishingAccess": publishing}
    values = _resolve_shared_resources(cli, values, stage)
    _verify_mcp_automation(cli, values)
    bindings = resource_bindings(cli, values)
    network_mode = values.get("virtualNetworkMode", "existing")
    if network_mode not in {"existing", "new"}:
        raise ValueError("Choose new or existing VNet deployment")
    if network_mode == "new":
        if values["virtualNetworkResourceGroupName"].casefold() != cli.group.casefold():
            raise ValueError("A new VNet is created only in the explicit target resource group")
        values["virtualNetworkResourceGroupName"] = cli.group
    group = cli.json("group", "show", "--name", cli.group, "--subscription", cli.subscription)
    group_tags = group.get("tags") or {}
    if (
        values["containerAppName"].casefold() == values["azureMcpContainerAppName"].casefold()
        or values["controlPlaneIdentityName"].casefold()
        == values["azureMcpIdentityName"].casefold()
    ):
        raise ValueError("Core/MCP Apps and identities must have distinct customer resource names")
    saved_options: dict[str, Any] = {}
    if any(key.casefold().startswith(PREFIX) for key in group_tags):
        saved = parse_tag_contract(group_tags, require_ready=False)
        if stage != "complete" or saved[PREFIX + "state"] != "foundation":
            raise ValueError(
                "v2 infrastructure already has a handoff; use application deployment, not an infrastructure rerun"
            )
        saved_options = decode_tag_options(saved)
        if not saved_options:
            raise ValueError("Completion requires saved foundation options")
        if publishing != saved_options.get("foundryPublishingAccess", {}):
            raise ValueError("Saved Foundry publishing access is immutable on completion")
        if allow_model_reselection:
            account_id = bindings["foundryAccountResourceId"]
            deployments = cli.collection(f"{account_id}/deployments", FOUNDRY_API)
            projects = cli.collection(f"{account_id}/projects", FOUNDRY_API)
            if values["foundryAccountMode"] == "existing":
                deployments = [
                    row
                    for row in deployments
                    if row["name"].split("/")[-1].casefold()
                    in {
                        values["modelDeploymentName"].casefold(),
                        values["simpleModelDeploymentName"].casefold(),
                    }
                ]
                projects = [
                    row
                    for row in projects
                    if row["name"].split("/")[-1].casefold() == values["projectName"].casefold()
                ]
            if deployments or projects:
                raise ValueError(
                    "Model reselection requires zero model deployments and zero projects"
                )
        if not allow_model_reselection and (
            values.get("simpleModelVersion") or values["modelVersion"]
        ) != saved_options.get("simpleModelVersion", saved_options["modelVersion"]):
            raise ValueError("Saved Simple model version is immutable on completion")
        if values.get("useMicrosoftManagedAgentData", False) != saved_options.get(
            "useMicrosoftManagedAgentData", False
        ):
            raise ValueError(
                "Agent data storage mode is immutable; never convert an existing Capability Host"
            )
        if network_mode != saved_options.get("virtualNetworkMode", "existing"):
            raise ValueError("Saved VNet deployment mode is immutable")
        if network_mode == "new" and any(
            values.get(field) != saved_options.get(field)
            for field in NETWORK_OPTION_FIELDS - {"virtualNetworkMode"}
        ):
            raise ValueError("Saved new VNet address choices are immutable")
        saved_values = infrastructure_values(cli, saved)
        for mode in SHARED_RESOURCES:
            if values[mode] != saved_values[mode]:
                raise ValueError("Saved shared resource modes are immutable")
        if bindings != {field: saved_values[field] for field in bindings}:
            raise ValueError("Refusing to redirect a saved infrastructure resource ID or container")
        for field, suffix in TAG_FIELDS.items():
            if field in {"deploymentName", "azureMcpServerUrl"}:
                continue
            if str(values.get(field, "")).casefold() != saved.get(PREFIX + suffix, "").casefold():
                raise ValueError(f"Refusing to change saved foundation option: {field}")
        for field in OPTION_FIELDS:
            if allow_model_reselection and field in MODEL_SELECTION_FIELDS:
                continue
            if field != "existingPrivateDnsZoneIds" and values[field] != saved_options[field]:
                raise ValueError(f"Refusing to change saved foundation option: {field}")
    contract = make_tag_contract({**values, **bindings}, name + "-complete")
    if allow_model_reselection and not saved_options:
        raise ValueError("Model reselection is only for a saved unprovisioned foundation")
    if saved_options:
        for field in ("namingPrefix", "namingSuffix"):
            if values.get(field, "") != saved_options.get(field, ""):
                raise ValueError(f"Refusing to change saved foundation option: {field}")
        if {
            field: value.casefold()
            for field, value in validate_mcp_automation(values.get("mcpAutomation", {})).items()
        } != {
            field: value.casefold()
            for field, value in validate_mcp_automation(
                saved_options.get("mcpAutomation", {})
            ).items()
        }:
            raise ValueError("Saved MCP automation resources are immutable on completion")
    validate_tag_budget({**group_tags, **contract})
    vnet_id = f"/subscriptions/{cli.subscription}/resourceGroups/{values['virtualNetworkResourceGroupName']}/providers/Microsoft.Network/virtualNetworks/{values['virtualNetworkName']}"
    if network_mode == "new" and stage == "foundation":
        networks = cli.collection(
            f"{cli.group_id}/providers/Microsoft.Network/virtualNetworks", NETWORK_API
        )
        if any(row.get("id", "").casefold() == vnet_id.casefold() for row in networks):
            raise ValueError("Refusing to overwrite an existing VNet in new-network mode")
        vnet = planned_virtual_network(values)
    else:
        vnet = _existing_virtual_network(cli, vnet_id)
        if network_mode == "new":
            _owned(vnet, vnet_id)
            if vnet["properties"]["addressSpace"]["addressPrefixes"] != [
                values["virtualNetworkAddressPrefix"]
            ]:
                raise ValueError("The saved new VNet address space changed")
    subnet_parameters = (
        values
        if network_mode == "new"
        else {
            **values,
            **{
                field: ""
                for field in NETWORK_OPTION_FIELDS
                if field.endswith("SubnetAddressPrefix")
            },
        }
    )
    needed_endpoints = _private_endpoint_count(values)
    subnet_roles = {"foundry", "container_apps"}
    if needed_endpoints or values.get("peSubnetName"):
        subnet_roles.add("private_endpoint")
    plans = plan_subnets(subnet_parameters, vnet, roles=subnet_roles)
    if any(plan.create for plan in plans):
        raise ValueError(
            "Prepare every required approved existing subnet before v2 infrastructure deployment"
        )
    existing_subnets = {
        subnet["name"].casefold(): subnet for subnet in vnet["properties"]["subnets"]
    }
    if stage == "foundation":
        for plan in plans:
            properties = existing_subnets[plan.name.casefold()]["properties"]
            shared_owner = (
                plan.role == "foundry" and values["foundryAccountMode"] == "existing"
            ) or (
                plan.role == "container_apps"
                and values["containerAppsEnvironmentMode"] == "existing"
            )
            if (
                plan.role != "private_endpoint"
                and not shared_owner
                and any(
                    properties.get(field)
                    for field in ("ipConfigurations", "privateEndpoints", "serviceAssociationLinks")
                )
            ):
                raise ValueError(
                    "Foundry and Container Apps subnets must be unused by another workload"
                )
    if stage == "foundation" and needed_endpoints:
        pe_plan = next(plan for plan in plans if plan.role == "private_endpoint")
        pe_props = existing_subnets[pe_plan.name.casefold()]["properties"]
        used = {row["id"].casefold() for row in pe_props.get("ipConfigurations", [])}
        if pe_props.get("privateEndpoints") and not used:
            raise ValueError(
                "Private Endpoint IP occupancy is unknown; verify free addresses before deployment"
            )
        if pe_plan.network.num_addresses - 5 - len(used) < needed_endpoints:
            raise ValueError(
                "The Private Endpoint subnet does not have the required address budget"
            )
    inventory = cli.json(
        "resource", "list", "--resource-group", cli.group, "--subscription", cli.subscription
    )
    if not isinstance(inventory, list):
        raise ValueError("Infrastructure inventory must be a complete array")
    by_id = {row["id"].casefold(): row for row in inventory}
    if len(by_id) != len(inventory):
        raise ValueError("Infrastructure inventory contains duplicate resource IDs")
    expected = {
        "foundryAccountName": "Microsoft.CognitiveServices/accounts",
        "storageAccountName": "Microsoft.Storage/storageAccounts",
        "cosmosAccountName": "Microsoft.DocumentDB/databaseAccounts",
        "searchServiceName": "Microsoft.Search/searchServices",
        "containerAppsEnvironmentName": "Microsoft.App/managedEnvironments",
        "controlPlaneIdentityName": "Microsoft.ManagedIdentity/userAssignedIdentities",
        "logAnalyticsWorkspaceName": "Microsoft.OperationalInsights/workspaces",
        "applicationInsightsName": "Microsoft.Insights/components",
        "communicationServicesName": "Microsoft.Communication/communicationServices",
        "emailServiceName": "Microsoft.Communication/emailServices",
    }
    if values.get("useMicrosoftManagedAgentData", False):
        expected.pop("cosmosAccountName")
        expected.pop("searchServiceName")
    for mode, (field, _, _, _, _) in SHARED_RESOURCES.items():
        if values[mode] == "existing":
            expected.pop(field)
    if values["containerAppsEnvironmentMode"] == "existing":
        expected.pop("logAnalyticsWorkspaceName")
    if values["communicationServicesMode"] == "existing":
        expected.pop("emailServiceName")
    for field, resource_type in expected.items():
        resource_id = f"{cli.group_id}/providers/{resource_type}/{values[field]}"
        existing = by_id.get(resource_id.casefold())
        if stage == "foundation" and existing is not None:
            raise ValueError(
                "Infrastructure targets already exist; only explicit completion may resume a matching foundation"
            )
        if stage == "complete":
            if existing is None:
                raise ValueError("Completion requires every existing foundation resource")
            _owned(existing, resource_id)
    for field, resource_type in (
        ("containerAppName", "containerApps"),
        ("schedulerJobName", "jobs"),
    ):
        if (
            f"{cli.group_id}/providers/Microsoft.App/{resource_type}/{values[field]}".casefold()
            in by_id
        ):
            raise ValueError(
                "An installed application must never be overwritten by infrastructure deployment"
            )
    prepared = {
        **values,
        "infrastructureStage": stage,
        "existingPrivateDnsZoneIds": _private_dns_bindings(cli, values, vnet_id),
    }
    if saved_options:
        saved_dns = saved_options["existingPrivateDnsZoneIds"]
        resolved_dns = prepared["existingPrivateDnsZoneIds"]
        _verify_saved_dns_bindings(
            cli,
            saved_dns,
            resolved_dns,
            use_microsoft_managed_agent_data=values.get("useMicrosoftManagedAgentData", False),
            shared_environment=values["containerAppsEnvironmentMode"] == "existing",
        )
        prepared["existingPrivateDnsZoneIds"] = saved_dns
    if stage == "complete":
        account_id = bindings["foundryAccountResourceId"]
        _verify_foundry_publishing_access(cli, values, cli.get(account_id, FOUNDRY_API))
        if values["foundryAccountMode"] == "existing":
            _owned_project(cli, values, account_id, allow_missing=True)
        reused, telemetry = validate_existing_host(cli, account_id, values)
        service_plan = automatic_service_plan(
            cli,
            {
                **values,
                "deployCapabilityHost": True,
                "provisionModels": True,
                "provisionEmail": False,
                "communicationServicesEndpoint": "",
                "strictModelSelection": True,
                "foundryAccountResourceId": account_id,
            },
            by_id,
        )
        prepared.update(
            {
                key: service_plan[key]
                for key in ("reuseCoreModel", "reuseSimpleModel", "useCoreForSimpleModel")
            }
        )
        prepared.update(
            reuseExistingFoundrySetup=reused, reuseExistingTelemetryConnection=telemetry
        )
    return prepared


def _verify_saved_dns_bindings(
    cli: AzureCli,
    saved: dict[str, str],
    resolved: dict[str, str],
    *,
    use_microsoft_managed_agent_data: bool = False,
    shared_environment: bool = False,
) -> None:
    """Keep explicit DNS overrides and recognize the owned default zones foundation created."""
    if len(resolved) != (5 if use_microsoft_managed_agent_data else 7) - int(
        shared_environment
    ) or set(saved) - set(resolved):
        raise ValueError("Completed foundation private DNS inventory is incomplete")
    for namespace, resource_id in resolved.items():
        expected = saved.get(
            namespace, f"{cli.group_id}/providers/Microsoft.Network/privateDnsZones/{namespace}"
        )
        if resource_id.casefold() != expected.casefold():
            raise ValueError("Refusing to redirect saved private DNS bindings")
        if namespace not in saved:
            _owned(cli.get(resource_id, "2024-06-01"), resource_id)


def run_infrastructure(
    cli: AzureCli,
    values: dict[str, Any],
    mode: str,
    name: str,
    stage: str = "both",
    *,
    allow_model_reselection: bool = False,
) -> None:
    """Run foundation then completion once, preserving Foundry's account-host initialization boundary."""
    initial = "complete" if stage == "complete" else "foundation"
    if allow_model_reselection and stage != "complete":
        raise ValueError("Explicit model reselection requires --stage complete")
    account_id = (
        values["existingFoundryAccountResourceId"]
        if values.get("foundryAccountMode", "new") == "existing"
        else f"{cli.group_id}/providers/Microsoft.CognitiveServices/accounts/{values['foundryAccountName']}"
    )
    if mode != "deploy":
        prepared = prepare_infrastructure(
            cli, values, initial, name, allow_model_reselection=allow_model_reselection
        )
        if mode != "preflight":
            deploy_template(cli, V2 / "infrastructure.json", prepared, mode, name + "-" + initial)
        logger.info("v2_infrastructure_preflight_complete", stage=initial, azure_mutations=False)
        return
    stages = ("complete",) if stage == "complete" else ("foundation", "complete")
    for current in stages:
        if current == "complete":
            wait_for_host(cli, account_id)
        for operation in ("validate", "what-if", "create"):
            prepared = prepare_infrastructure(
                cli, values, current, name, allow_model_reselection=allow_model_reselection
            )
            deploy_template(
                cli, V2 / "infrastructure.json", prepared, operation, name + "-" + current
            )
    wait_for_host(cli, f"{account_id}/projects/{values['projectName']}", "agents")
    group = cli.json("group", "show", "--name", cli.group, "--subscription", cli.subscription)
    contract = parse_tag_contract(group.get("tags") or {}, require_ready=False)
    if contract[PREFIX + "state"] == "mcp_pending":
        run_mcp_authentication(cli, "deploy", name)
    read_infrastructure(cli)
    logger.info(
        "v2_infrastructure_deployed", application_ready=False, hosted_runtime_roles_pending=True
    )


def run_hosted_roles(cli: AzureCli, principal_id: str, mode: str, name: str) -> None:
    """Complete only the infrastructure-owned permissions unavailable before first Agent publication."""
    principal_id = str(UUID(principal_id))
    operations = ("validate", "what-if", "create") if mode == "deploy" else (mode,)
    for operation in operations:
        tags, values = read_infrastructure(cli)
        project_id = f"{values['foundryAccountResourceId']}/projects/{values['projectName']}"
        project_principal = cli.get(project_id, FOUNDRY_API)["identity"]["principalId"]
        if principal_id.casefold() in {
            values["controlPlanePrincipalId"].casefold(),
            project_principal.casefold(),
        }:
            raise ValueError(
                "Use the verified Hosted Agent principal, never the control-plane or project identity"
            )
        if operation != "preflight":
            deploy_template(
                cli,
                V2 / "runtime-roles.json",
                {"infrastructureTags": tags, "hostedPrincipalId": principal_id},
                operation,
                name + "-hosted-roles",
            )
        logger.info(
            "v2_hosted_roles_operation_complete", operation=operation, application_ready=False
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("infrastructure", "application"))
    parser.add_argument("--subscription", type=UUID, required=True)
    parser.add_argument("--tenant", type=UUID, required=True)
    parser.add_argument("--resource-group", required=True)
    parser.add_argument(
        "--mode", choices=("preflight", "validate", "what-if", "deploy"), default="preflight"
    )
    parser.add_argument("--name", default="azbrief-v2")
    parser.add_argument("--stage", choices=("both", "complete"), default="both")
    parser.add_argument("--parameters", type=Path)
    parser.add_argument("--image")
    parser.add_argument("--configuration", type=Path)
    parser.add_argument("--hosted-principal-id", type=UUID)
    parser.add_argument("--prepare-mcp-authentication", action="store_true")
    parser.add_argument("--no-automatic-setup", action="store_true")
    parser.add_argument(
        "--recover-initial-install",
        action="store_true",
        help="Explicit never-deployed App recovery; preserve its existing API secret and create only a missing Job",
    )
    parser.add_argument(
        "--list-model-options",
        action="store_true",
        help="Read-only regional GPT-5 catalog, quota and provider capacity",
    )
    parser.add_argument(
        "--model-regions",
        nargs="+",
        help="Read-only model-resource quota/capacity regions; never changes VNet, Environment or account location",
    )
    parser.add_argument(
        "--candidate-models", nargs="+", help="Optional exact GPT-5 model names to narrow discovery"
    )
    parser.add_argument(
        "--reselect-models",
        action="store_true",
        help="Explicit model-only reselection on saved foundation with no models/projects; requires --stage complete",
    )
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,45}", args.name):
        parser.error("Use a deployment-name prefix of at most 46 characters")
    if not re.fullmatch(r"[A-Za-z0-9_.()-]{1,90}", args.resource_group):
        parser.error("Use an explicit resource-group name")
    try:
        cli = AzureCli(str(args.subscription), str(args.tenant), args.resource_group)
        if (args.model_regions or args.candidate_models) and not args.list_model_options:
            raise ValueError("Model discovery filters require --list-model-options")
        if args.phase == "infrastructure":
            if (
                args.image
                or args.configuration
                or args.no_automatic_setup
                or args.recover_initial_install
            ):
                raise ValueError("Application inputs do not belong in infrastructure deployment")
            if args.list_model_options:
                if (
                    args.mode != "preflight"
                    or args.prepare_mcp_authentication
                    or args.hosted_principal_id
                    or args.stage != "both"
                    or args.reselect_models
                ):
                    raise ValueError(
                        "Model discovery is read-only and cannot prepare or deploy resources"
                    )
                regions = args.model_regions
                if not regions and args.parameters:
                    regions = [load_infrastructure_parameters(args.parameters)["location"]]
                if not regions:
                    raise ValueError(
                        "Model discovery requires explicit --model-regions or parameter location"
                    )
                rows = discover_model_options(cli, regions, args.candidate_models)
                logger.info(
                    "v2_model_options",
                    profiles=rows,
                    azure_mutations=False,
                    region_scope="model_resource_quota_and_capacity",
                    existing_installation_changed=False,
                )
            elif args.prepare_mcp_authentication:
                if (
                    args.parameters
                    or args.hosted_principal_id
                    or args.stage != "both"
                    or args.reselect_models
                ):
                    raise ValueError(
                        "MCP preparation never replays general infrastructure or Hosted roles"
                    )
                run_mcp_authentication(cli, args.mode, args.name)
            elif args.hosted_principal_id:
                if args.parameters or args.stage != "both" or args.reselect_models:
                    raise ValueError("Hosted-role completion never replays general infrastructure")
                run_hosted_roles(cli, str(args.hosted_principal_id), args.mode, args.name)
            else:
                if not args.parameters:
                    raise ValueError("Infrastructure requires the explicit parameter file")
                run_infrastructure(
                    cli,
                    load_infrastructure_parameters(args.parameters),
                    args.mode,
                    args.name,
                    args.stage,
                    allow_model_reselection=args.reselect_models,
                )
        else:
            if (
                args.parameters
                or args.list_model_options
                or args.reselect_models
                or args.hosted_principal_id
                or args.prepare_mcp_authentication
                or args.stage != "both"
                or not args.image
            ):
                raise ValueError(
                    "Application deployment requires an image, not infrastructure parameters or roles"
                )
            configuration = (
                json.loads(args.configuration.read_text(encoding="utf-8-sig"))
                if args.configuration
                else {}
            )
            run_application(
                cli,
                args.image,
                configuration,
                args.mode,
                args.name + "-application",
                automatic_setup=not args.no_automatic_setup,
                recover_initial_install=args.recover_initial_install,
            )
    except (
        ValueError,
        KeyError,
        TypeError,
        OSError,
        RuntimeError,
        subprocess.SubprocessError,
    ) as error:
        logger.error("v2_deployment_failed", phase=args.phase, error_type=type(error).__name__)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
