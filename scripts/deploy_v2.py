"""Deploy AzBrief v2 infrastructure once and install or update applications separately."""

from __future__ import annotations

import argparse
import copy
import json
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

import structlog

from scripts.build_v2_ui import derive_resource_names
from scripts.deploy_kt import (
    CONTAINER_ENV_API,
    FOUNDRY_API,
    MANAGEMENT,
    NETWORK_API,
    AzureCli,
    automatic_service_plan,
    plan_subnets,
    validate_existing_host,
    wait_for_host,
)
from src.error_logging import configure_redaction

logger = structlog.get_logger(__name__)
ROOT = Path(__file__).resolve().parents[1]
V2 = ROOT / "infra" / "v2"
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
    }
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


def parse_tag_contract(tags: dict[str, Any], require_ready: bool = True) -> dict[str, str]:
    """Read the bounded, non-secret infrastructure contract, never an arbitrary ARM ID."""
    validate_tag_budget(tags)
    contract = {key: value for key, value in tags.items() if key.casefold().startswith(PREFIX)}
    required = TAG_KEYS - {PREFIX + "mcpApp", PREFIX + "mcpIdentity"}
    if not required <= set(contract) or set(contract) - (TAG_KEYS | set(OPTION_TAG_KEYS)):
        raise ValueError("Missing, unknown or non-canonical AzBrief v2 resource-group tag keys")
    if (
        contract[PREFIX + "schema"] != "2"
        or contract[PREFIX + "profile"] != PROFILE
        or contract[PREFIX + "state"]
        not in ({"ready"} if require_ready else {"foundation", "ready"})
        or contract[PREFIX + "acaPeEnabled"] not in {"true", "false"}
    ):
        raise ValueError("Use the completed AzBrief v2 infrastructure contract")
    for suffix in TAG_FIELDS.values():
        value = contract.get(PREFIX + suffix, "")
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
    return contract


def decode_tag_options(tags: dict[str, str]) -> dict[str, Any]:
    present = set(OPTION_TAG_KEYS).intersection(tags)
    if not present:
        return {}
    if present != set(OPTION_TAG_KEYS):
        raise ValueError("Incomplete infrastructure option tags")
    options = json.loads("".join(tags[key] for key in OPTION_TAG_KEYS))
    optional = {
        "manageAzureMcp",
        "azureMcpClientId",
        "azureMcpServicePrincipalId",
        "azureMcpAppRoleId",
        "namingPrefix",
        "namingSuffix",
    }
    if not isinstance(options, dict) or set(options) - (set(OPTION_FIELDS) | optional):
        raise ValueError("Unknown or secret-valued infrastructure options")
    if set(OPTION_FIELDS) - set(options):
        raise ValueError("Missing saved infrastructure options")
    if any(type(options[field]) is not kind for field, kind in OPTION_FIELDS.items()):
        raise ValueError("Invalid saved infrastructure option type")
    if "manageAzureMcp" in options and type(options["manageAzureMcp"]) is not bool:
        raise ValueError("Invalid managed MCP setting")
    for field in optional - {"manageAzureMcp", "namingPrefix", "namingSuffix"}:
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
    """Resolve leaf names within the explicit CLI scope, not a scope supplied in tags."""
    values = {field: tags.get(PREFIX + suffix, "") for field, suffix in TAG_FIELDS.items()}
    values.update(decode_tag_options(tags))
    values.update(
        tenantId=cli.tenant,
        subscriptionId=cli.subscription,
        resourceGroup=cli.group,
        deployContainerAppsPrivateEndpoint=tags[PREFIX + "acaPeEnabled"] == "true",
    )
    return values


def _owned(resource: dict[str, Any], resource_id: str) -> None:
    if (
        resource.get("id", "").casefold() != resource_id.casefold()
        or (resource.get("tags") or {}).get("deploymentProfile") != PROFILE
    ):
        raise ValueError("Refusing to adopt a foreign or redirected resource")


def read_infrastructure(cli: AzureCli) -> tuple[dict[str, str], dict[str, Any]]:
    """Verify context, private ingress and immutable BYO bindings before application writes."""
    cli.assert_context()
    group = cli.json("group", "show", "--name", cli.group, "--subscription", cli.subscription)
    tags = parse_tag_contract(group.get("tags") or {})
    values = infrastructure_values(cli, tags)
    environment_id = f"{cli.group_id}/providers/Microsoft.App/managedEnvironments/{values['containerAppsEnvironmentName']}"
    environment = cli.get(environment_id, CONTAINER_ENV_API)
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
    account_id = f"{cli.group_id}/providers/Microsoft.CognitiveServices/accounts/{values['foundryAccountName']}"
    account = cli.get(account_id, FOUNDRY_API)
    _owned(account, account_id)
    if str(account["properties"].get("publicNetworkAccess", "")).casefold() != "disabled":
        raise ValueError("The tagged Foundry account must retain disabled public access")
    reused, telemetry = validate_existing_host(cli, account_id, values)
    if not reused or not telemetry:
        raise ValueError("Completed v2 project/BYO and Application Insights bindings are required")
    values.update(
        environmentResourceId=environment_id,
        environmentDefaultDomain=props["defaultDomain"],
        controlPlaneIdentityResourceId=identity_id,
        controlPlaneClientId=identity["properties"]["clientId"],
        controlPlanePrincipalId=identity["properties"]["principalId"],
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
    cli: AzureCli, resource_id: str, collection: str, values: dict[str, Any]
) -> dict[str, Any] | None:
    rows = cli.collection(f"{cli.group_id}/providers/Microsoft.App/{collection}", CONTAINER_ENV_API)
    matches = [row for row in rows if row["id"].casefold() == resource_id.casefold()]
    if len(matches) > 1:
        raise ValueError("Ambiguous application inventory")
    if not matches:
        return None
    resource = cli.get(resource_id, CONTAINER_ENV_API)
    _owned(resource, resource_id)
    props = resource["properties"]
    identities = resource.get("identity", {})
    if (
        props.get("provisioningState") != "Succeeded"
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
    state = f"https://{storage}.blob.core.windows.net/azbrief-state"
    protected = {
        "AZURE_TENANT_ID": values["tenantId"],
        "AZURE_SUBSCRIPTION_ID": values["subscriptionId"],
        "AZURE_CLIENT_ID": values["controlPlaneClientId"],
        "FOUNDRY_PROJECT_ENDPOINT": f"https://{values['foundryAccountName']}.services.ai.azure.com/api/projects/{values['projectName']}",
        "FOUNDRY_HOSTED_AGENT_NAME": values["foundryHostedAgentName"],
        "CHECKPOINT_BLOB_URL": f"{state}/checkpoint.json",
        "ARCHIVE_BLOB_CONTAINER_URL": f"https://{storage}.blob.core.windows.net/azbrief-archive",
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
    cli: AzureCli, image: str, configuration: dict[str, Any] | None = None
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
    app_id = f"{cli.group_id}/providers/Microsoft.App/containerApps/{values['containerAppName']}"
    job_id = f"{cli.group_id}/providers/Microsoft.App/jobs/{values['schedulerJobName']}"
    app = _surface(cli, app_id, "containerApps", values)
    job = _surface(cli, job_id, "jobs", values)
    if (app is None) != (job is None):
        raise ValueError(
            "Partial application installation needs explicit recovery, not a guessed key rotation"
        )
    parameters: dict[str, Any] = {
        "infrastructureTags": tags,
        "containerImage": image,
        "reuseExistingApiKey": app is not None,
        "existingSecrets": {"app": [], "job": []},
    }
    if app is not None and job is not None:
        for label, resource in (("app", app), ("job", job)):
            props = resource["properties"]
            parameters[f"{label}Configuration"] = copy.deepcopy(props["configuration"])
            parameters[f"{label}Configuration"].pop("secrets", None)
            parameters[f"{label}Template"] = copy.deepcopy(props["template"])
            parameters[f"{label}Environment"] = props["template"]["containers"][0].get("env") or []
            parameters["existingSecrets"][label] = _read_secrets(cli, resource["id"])
            parameters["tags" if label == "app" else "jobTags"] = resource["tags"]
        parameters["appConfiguration"].get("ingress", {}).pop("fqdn", None)
        auth = cli.collection(f"{app_id}/authConfigs", CONTAINER_ENV_API)
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
        if len(keys) != 1 or job_keys != keys:
            raise ValueError(
                "App and Job must have one matching readable API secret; never rotate on failure"
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
    cli: AzureCli, image: str, configuration: dict[str, Any], mode: str, name: str
) -> None:
    """Require fresh preflight before every validate, preview and application write."""
    operations = ("validate", "what-if", "create") if mode == "deploy" else (mode,)
    for operation in operations:
        parameters = prepare_application(cli, image, configuration)
        if operation != "preflight":
            deploy_template(cli, V2 / "application.json", parameters, operation, name)
        logger.info(
            "v2_application_operation_complete",
            operation=operation,
            update=parameters["reuseExistingApiKey"],
            application_ready=False,
        )


def load_infrastructure_parameters(path: Path) -> dict[str, Any]:
    """Resolve literal parameters and safe name defaults without reading developer .env files."""
    supplied = json.loads(path.read_text(encoding="utf-8-sig"))["parameters"]
    if "namingPrefix" in supplied or "namingSuffix" in supplied:
        if "namingPrefix" not in supplied or "namingSuffix" not in supplied:
            raise ValueError("Supply both namingPrefix and namingSuffix")
        names = derive_resource_names(
            supplied["namingPrefix"]["value"], supplied["namingSuffix"]["value"]
        )
        for field, value in names.items():
            supplied.setdefault(field, {"value": value})
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
            if "defaultValue" not in definition:
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
        }
    )
    options = {field: values[field] for field in OPTION_FIELDS}
    options.update(
        {
            "manageAzureMcp": not bool(values.get("azureMcpServerUrl")),
            "azureMcpClientId": values.get("azureMcpClientId", ""),
            "azureMcpServicePrincipalId": values.get("azureMcpServicePrincipalId", ""),
            "azureMcpAppRoleId": values.get("azureMcpAppRoleId", ""),
            "namingPrefix": values.get("namingPrefix", ""),
            "namingSuffix": values.get("namingSuffix", ""),
        }
    )
    encoded = json.dumps(options, ensure_ascii=False, separators=(",", ":"))
    if len(encoded) > 8 * 256:
        raise ValueError("Saved infrastructure options exceed the 2048-character tag budget")
    contract.update(
        {key: encoded[index * 256 : (index + 1) * 256] for index, key in enumerate(OPTION_TAG_KEYS)}
    )
    return parse_tag_contract(contract, require_ready=state == "ready")


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
    cli: AzureCli, values: dict[str, Any], stage: str, name: str
) -> dict[str, Any]:
    """Keep infrastructure one-time, reuse the approved network and derive completion decisions."""
    cli.assert_context()
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
        for field, suffix in TAG_FIELDS.items():
            if field in {"deploymentName", "azureMcpServerUrl"}:
                continue
            if str(values.get(field, "")).casefold() != saved.get(PREFIX + suffix, "").casefold():
                raise ValueError(f"Refusing to change saved foundation option: {field}")
        for field in OPTION_FIELDS:
            if field != "existingPrivateDnsZoneIds" and values[field] != saved_options[field]:
                raise ValueError(f"Refusing to change saved foundation option: {field}")
        for field in ("namingPrefix", "namingSuffix"):
            if values.get(field, "") != saved_options.get(field, ""):
                raise ValueError(f"Refusing to change saved foundation option: {field}")
    contract = make_tag_contract(values, name + "-complete")
    validate_tag_budget({**group_tags, **contract})
    vnet_id = f"/subscriptions/{cli.subscription}/resourceGroups/{values['virtualNetworkResourceGroupName']}/providers/Microsoft.Network/virtualNetworks/{values['virtualNetworkName']}"
    vnet = cli.get(vnet_id, NETWORK_API)
    plans = plan_subnets(values, vnet)
    if any(plan.create for plan in plans):
        raise ValueError(
            "Prepare all three approved existing subnets before v2 infrastructure deployment"
        )
    existing_subnets = {
        subnet["name"].casefold(): subnet for subnet in vnet["properties"]["subnets"]
    }
    if stage == "foundation":
        for plan in plans:
            properties = existing_subnets[plan.name.casefold()]["properties"]
            if plan.role != "private_endpoint" and any(
                properties.get(field)
                for field in ("ipConfigurations", "privateEndpoints", "serviceAssociationLinks")
            ):
                raise ValueError(
                    "Foundry and Container Apps subnets must be unused by another workload"
                )
        pe_plan = next(plan for plan in plans if plan.role == "private_endpoint")
        pe_props = existing_subnets[pe_plan.name.casefold()]["properties"]
        used = {row["id"].casefold() for row in pe_props.get("ipConfigurations", [])}
        if pe_props.get("privateEndpoints") and not used:
            raise ValueError(
                "Private Endpoint IP occupancy is unknown; verify free addresses before deployment"
            )
        needed = 8 if values["deployContainerAppsPrivateEndpoint"] else 7
        if pe_plan.network.num_addresses - 5 - len(used) < needed:
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
    if (
        saved_options
        and prepared["existingPrivateDnsZoneIds"] != saved_options["existingPrivateDnsZoneIds"]
    ):
        raise ValueError("Refusing to redirect saved private DNS bindings")
    if stage == "complete":
        account_id = f"{cli.group_id}/providers/Microsoft.CognitiveServices/accounts/{values['foundryAccountName']}"
        reused, telemetry = validate_existing_host(cli, account_id, values)
        service_plan = automatic_service_plan(
            cli,
            {
                **values,
                "deployCapabilityHost": True,
                "provisionModels": True,
                "provisionEmail": False,
                "communicationServicesEndpoint": "",
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


def run_infrastructure(
    cli: AzureCli, values: dict[str, Any], mode: str, name: str, stage: str = "both"
) -> None:
    """Run foundation then completion once, preserving Foundry's account-host initialization boundary."""
    initial = "complete" if stage == "complete" else "foundation"
    account_id = f"{cli.group_id}/providers/Microsoft.CognitiveServices/accounts/{values['foundryAccountName']}"
    if mode != "deploy":
        prepared = prepare_infrastructure(cli, values, initial, name)
        if mode != "preflight":
            deploy_template(cli, V2 / "infrastructure.json", prepared, mode, name + "-" + initial)
        logger.info("v2_infrastructure_preflight_complete", stage=initial, azure_mutations=False)
        return
    stages = ("complete",) if stage == "complete" else ("foundation", "complete")
    for current in stages:
        if current == "complete":
            wait_for_host(cli, account_id)
        for operation in ("validate", "what-if", "create"):
            prepared = prepare_infrastructure(cli, values, current, name)
            deploy_template(
                cli, V2 / "infrastructure.json", prepared, operation, name + "-" + current
            )
    wait_for_host(cli, f"{account_id}/projects/{values['projectName']}", "agents")
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
        project_id = f"{cli.group_id}/providers/Microsoft.CognitiveServices/accounts/{values['foundryAccountName']}/projects/{values['projectName']}"
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
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,45}", args.name):
        parser.error("Use a deployment-name prefix of at most 46 characters")
    if not re.fullmatch(r"[A-Za-z0-9_.()-]{1,90}", args.resource_group):
        parser.error("Use an explicit resource-group name")
    try:
        cli = AzureCli(str(args.subscription), str(args.tenant), args.resource_group)
        if args.phase == "infrastructure":
            if args.image or args.configuration:
                raise ValueError("Application inputs do not belong in infrastructure deployment")
            if args.hosted_principal_id:
                if args.parameters or args.stage != "both":
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
                )
        else:
            if (
                args.parameters
                or args.hosted_principal_id
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
            run_application(cli, args.image, configuration, args.mode, args.name + "-application")
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
