"""Prepare or deploy the KT private infrastructure without changing an existing VNet."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from ipaddress import IPv4Address, IPv4Network, ip_network
from pathlib import Path
from typing import Any
from uuid import UUID

import structlog

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
    if not isinstance(values["existingPrivateDnsZoneIds"], dict):
        raise ValueError("existingPrivateDnsZoneIds must be an object")
    if not isinstance(values["tags"], dict) or values["tags"].get("deploymentProfile") != PROFILE:
        raise ValueError(f"Preserve the deploymentProfile={PROFILE} ownership tag")
    if not values["apiKey"].strip() or any(character.isspace() for character in values["apiKey"]):
        raise ValueError("apiKey must be a separate random key without whitespace")
    if values["containerRegistryAuthMode"] == "Credentials":
        if (
            not values["containerRegistryUsername"].strip()
            or len(values["containerRegistryPassword"].strip()) < 8
        ):
            raise ValueError("Private GHCR requires a username and a read:packages token")
        if values["apiKey"] == values["containerRegistryPassword"]:
            raise ValueError("apiKey and the GHCR pull token must be different")
    elif values["containerRegistryPassword"]:
        raise ValueError("Anonymous registry mode must not carry an unused pull token")
    return values


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
        rows = list(page["value"])
        for _ in range(20):
            next_link = page.get("nextLink")
            if not next_link:
                return rows
            if not next_link.startswith(f"{MANAGEMENT}/subscriptions/"):
                raise ValueError("Unexpected ARM continuation URL")
            page = self.json("rest", "--method", "get", "--url", next_link)
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


def validate_existing_host(cli: AzureCli, account_id: str, values: dict[str, Any]) -> None:
    """Reject changes to immutable host bindings instead of implicitly replacing customer data."""
    projects = cli.collection(f"{account_id}/projects", FOUNDRY_API)
    if not any(
        project["name"].split("/")[-1].casefold() == values["projectName"].casefold()
        for project in projects
    ):
        return
    project_id = f"{account_id}/projects/{values['projectName']}"
    hosts = cli.collection(f"{project_id}/capabilityHosts", HOST_API)
    if not hosts:
        return
    if len(hosts) != 1 or hosts[0]["name"].split("/")[-1].casefold() != "agents":
        raise ValueError("An incompatible project Capability Host already exists")
    expected = {
        "storageConnections": ["agent-storage"],
        "threadStorageConnections": ["agent-cosmos"],
        "vectorStoreConnections": ["agent-search"],
    }
    if any(hosts[0]["properties"].get(key) != value for key, value in expected.items()):
        raise ValueError(
            "Capability Host bindings are immutable; a migration needs separate approval"
        )
    connections = {
        item["name"].split("/")[-1]: item["properties"]
        for item in cli.collection(f"{project_id}/connections", FOUNDRY_API)
    }
    targets = endpoint_specs(values, cli.group_id)
    for name, target in (
        ("agent-storage", targets[1]["target"]),
        ("agent-cosmos", targets[2]["target"]),
        ("agent-search", targets[3]["target"]),
    ):
        connection = connections.get(name, {})
        if connection.get("authType") != "AAD" or not _same_id(
            connection.get("metadata", {}).get("ResourceId", ""), target
        ):
            raise ValueError(f"Refusing to redirect an existing Capability Host connection: {name}")


def prepare(cli: AzureCli, values: dict[str, Any]) -> dict[str, Any]:
    """Inventory live resources and derive create flags without modifying existing subnets."""
    cli.assert_context()
    vnet_id = (
        f"/subscriptions/{cli.subscription}/resourceGroups/{values['virtualNetworkResourceGroupName']}"
        f"/providers/Microsoft.Network/virtualNetworks/{values['virtualNetworkName']}"
    )
    vnet = cli.get(vnet_id, NETWORK_API)
    plans = plan_subnets(values, vnet)
    resources = cli.json(
        "resource", "list", "--resource-group", cli.group, "--subscription", cli.subscription
    )
    by_id = {resource["id"].casefold(): resource for resource in resources}
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
    for resource_id in planned_ids:
        existing = by_id.get(resource_id.casefold())
        if (
            existing is not None
            and (existing.get("tags") or {}).get("deploymentProfile") != PROFILE
        ):
            raise ValueError(
                f"Refusing to modify a resource not owned by the KT profile: {resource_id}"
            )
    app_id = planned_ids[-2]
    if app_id.casefold() in by_id:
        app = cli.get(app_id, "2025-01-01")["properties"]
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
            flags = {
                "ADMIN_UI_ENABLED": "false",
                "ADMIN_REQUIRE_AUTH": "true",
                "ARCHIVE_UI_ENABLED": "false",
                "ARCHIVE_REQUIRE_AUTH": "true",
                "FEEDBACK_UI_ENABLED": "false",
                "MAX_CONCURRENT_ANALYSES": "1",
            }
            if (
                container.get("name") != "azbrief"
                or set(env) != FOUNDATION_ENV_NAMES
                or len(entries) != len(env)
                or any(env[name].get("value") != value for name, value in flags.items())
                or env["API_KEY"].get("secretRef") != "orchestrator-api-key"
                or env["API_KEY"].get("value")
                or app.get("template", {}).get("scale", {}).get("minReplicas") != 0
                or app.get("template", {}).get("scale", {}).get("maxReplicas") != 1
                or container.get("resources") != {"cpu": 0.25, "memory": "0.5Gi"}
                or configuration.get("activeRevisionsMode") != "Single"
                or configuration.get("ingress", {}).get("targetPort") != 8000
                or any(
                    registry.get("server") != "ghcr.io"
                    or registry.get("passwordSecretRef") != "ghcr-pull-token"
                    or registry.get("identity")
                    for registry in configuration.get("registries", [])
                )
                or {secret["name"] for secret in configuration.get("secrets", [])}
                - {"orchestrator-api-key", "ghcr-pull-token"}
            ):
                raise ValueError("Foundation cannot overwrite a promoted or customized AzBrief app")
    if specs[0]["target"].casefold() in by_id:
        validate_existing_host(cli, specs[0]["target"], values)
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
    prepared = dict(values, existingPrivateDnsZoneIds=reuse)
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


def run(cli: AzureCli, values: dict[str, Any], mode: str) -> None:
    """Require fresh preflight for every operation, including the second deployment stage."""
    configure_redaction(values)
    if mode not in {"preflight", "validate", "what-if", "deploy"}:
        raise ValueError(f"Unsupported KT operation: {mode}")
    prepared = prepare(cli, values)
    if mode == "preflight":
        logger.info("kt_preflight_complete", azure_mutations=False, application_ready=False)
        return
    if mode in {"validate", "what-if"}:
        result = cli.deploy(prepared, mode, "kt-private-validation")
        logger.info("kt_validation_result", mode=mode, result=redact_fields(result))
        return
    prepared["deployCapabilityHost"] = False
    result = cli.deploy(prepared, "create", "kt-private-foundation")
    if result["properties"]["provisioningState"] != "Succeeded":
        raise RuntimeError("KT foundation deployment did not succeed")
    account_id = endpoint_specs(values, cli.group_id)[0]["target"]
    wait_for_host(cli, account_id)
    prepared = prepare(cli, values)
    prepared["deployCapabilityHost"] = True
    result = cli.deploy(prepared, "create", "kt-private-capability-host")
    if result["properties"]["provisioningState"] != "Succeeded":
        raise RuntimeError("KT Capability Host deployment did not succeed")
    wait_for_host(cli, f"{account_id}/projects/{values['projectName']}", "agents")
    verify_foundation(cli, values)
    logger.info(
        "kt_foundation_deployed",
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
    args = parser.parse_args()
    try:
        values = load_parameters(args.parameters)
        run(
            AzureCli(str(args.subscription), str(args.tenant), args.resource_group),
            values,
            args.mode,
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
