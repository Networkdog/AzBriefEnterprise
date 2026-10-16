"""Offline Portal contracts for independently reusing v2 provider resources."""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import pytest

from scripts import build_v2_ui
from tests.test_kt_deployment import _evaluate_ui_expression

ROOT = Path(__file__).resolve().parents[1]
SUBSCRIPTION = "00000000-0000-0000-0000-000000000001"
OTHER_SUBSCRIPTION = "00000000-0000-0000-0000-000000000002"
GROUP = "rg-workload"
NETWORK_GROUP = "RG-Shared-Network"
VNET = (
    f"/subscriptions/{SUBSCRIPTION}/resourceGroups/{NETWORK_GROUP}"
    "/providers/Microsoft.Network/virtualNetworks/vnet-private"
)
CUSTOMER_ID = "00000000-0000-0000-0000-000000000003"
NAME_TAGS = {
    "foundryAccountName": "foundry",
    "storageAccountName": "storage",
    "containerAppsEnvironmentName": "environment",
    "communicationServicesName": "acs",
    "logAnalyticsWorkspaceName": "workspace",
    "emailServiceName": "email",
}
SHARED_NAMES = {
    "foundryAccount": "Shared-Foundry",
    "storageAccount": "sharedstorage",
    "containerAppsEnvironment": "shared-environment",
    "communicationServices": "Shared-ACS",
    "logAnalyticsWorkspace": "Shared-Workspace",
    "emailService": "Shared-Email",
}


@lru_cache
def definition(name: str = "infrastructure") -> dict[str, Any]:
    return json.loads(
        (ROOT / "infra" / "v2" / f"{name}.createUiDefinition.json").read_text(encoding="utf-8")
    )["parameters"]


def controls(name: str = "infrastructure") -> dict[str, dict[str, Any]]:
    return {
        element["name"]: element
        for step in definition(name)["steps"]
        for element in step["elements"]
    }


@dataclass
class UiState:
    values: dict[str, Any]
    network: dict[str, Any]
    group: dict[str, Any] = field(
        default_factory=lambda: {"mode": "New", "name": GROUP, "location": "koreacentral"}
    )
    stage: str = "foundation"
    selected_location: str = "eastus"


def evaluate(expression: str, state: UiState, eager: bool = True) -> Any:
    return _evaluate_ui_expression(
        expression,
        state.values,
        network=state.network,
        resource_group=state.group,
        subscription={"subscriptionId": SUBSCRIPTION},
        basics={"infrastructureStage": state.stage},
        eager_conditionals=eager,
        selected_location=state.selected_location,
    )


def output(name: str, state: UiState, eager: bool = True) -> Any:
    return evaluate(definition()["outputs"][name], state, eager)


def foundation() -> UiState:
    subnets = []
    for name, address, delegated in (
        ("private-endpoints", "10.24.1.32/28", False),
        ("foundry-agents", "10.24.0.0/24", True),
        ("container-apps", "10.24.1.0/27", True),
    ):
        subnets.append(
            {
                "id": f"{VNET}/subnets/{name}",
                "name": name,
                "properties": {
                    "addressPrefix": address,
                    "delegations": (
                        [{"properties": {"serviceName": "Microsoft.App/environments"}}]
                        if delegated
                        else []
                    ),
                },
            }
        )
    return UiState(
        values={
            "namingPrefix": "team",
            "namingSuffix": "prd",
            "contract": {"resourceGroupApi": None, "contractAccepted": True},
            "options": {},
        },
        network={
            "virtualNetworkMode": "existing",
            "virtualNetwork": {"id": VNET, "name": "vnet-private", "location": "eastus"},
            "virtualNetworkApi": {
                "id": VNET,
                "location": "eastus",
                "properties": {"subnets": subnets},
            },
            "peSubnetName": "private-endpoints",
            "foundrySubnetName": "foundry-agents",
            "containerAppsSubnetName": "container-apps",
        },
    )


def resource(resource_type: str, name: str, group: str, **properties: Any) -> dict[str, Any]:
    return {
        "id": f"/subscriptions/{SUBSCRIPTION}/resourceGroups/{group}/providers/{resource_type}/{name}",
        "name": name,
        "type": resource_type,
        "location": "eastus",
        "properties": {"provisioningState": "Succeeded", **properties},
    }


def child_resource(
    parent: dict[str, Any], child_type: str, name: str, **properties: Any
) -> dict[str, Any]:
    return {
        "id": parent["id"] + f"/{child_type}/{name}",
        "type": parent["type"] + "/" + child_type,
        "name": name,
        "properties": {"provisioningState": "Succeeded", **properties},
    }


def model_deployment(state: UiState, role: str = "Core") -> dict[str, Any]:
    deployment = child_resource(
        state.values["existingFoundryAccountApi"],
        "deployments",
        "gpt-5-terra" if role == "Core" else "gpt-5-luna",
        model={
            "format": "OpenAI",
            "name": "gpt-5.2" if role == "Core" else "gpt-5-mini",
            "version": "2025-12-11" if role == "Core" else "2025-08-07",
        },
    )
    deployment["sku"] = {"name": "GlobalStandard", "capacity": 200}
    return deployment


def select_existing(state: UiState, name: str, cross_group: bool = True) -> None:
    group = "RG-Shared-Providers" if cross_group else GROUP
    resource_type = build_v2_ui.SHARED_RESOURCES[name][1]
    selected = resource(resource_type, SHARED_NAMES[name], group)
    if name == "foundryAccount":
        selected["kind"] = "AIServices"
        selected["properties"].update(
            allowProjectManagement=True,
            disableLocalAuth=True,
            customSubDomainName="actual-shared-endpoint",
            publicNetworkAccess="Disabled",
            networkInjections=[
                {
                    "scenario": "agent",
                    "useMicrosoftManagedNetwork": False,
                    "subnetArmId": f"{VNET}/subnets/foundry-agents",
                }
            ],
        )
        state.values["sharedFoundryHostsApi"] = {
            "value": [child_resource(selected, "capabilityHosts", "agents")]
        }
        state.values["sharedFoundryDeploymentsApi"] = {"value": []}
        state.values["sharedFoundryProjectsApi"] = {"value": []}
    elif name == "storageAccount":
        selected["kind"] = "StorageV2"
        selected["location"] = "japaneast"
        selected["properties"].update(
            publicNetworkAccess="Disabled",
            allowBlobPublicAccess=False,
            allowSharedKeyAccess=False,
            supportsHttpsTrafficOnly=True,
            minimumTlsVersion="TLS1_2",
        )
    elif name == "containerAppsEnvironment":
        workspace = resource(
            "Microsoft.OperationalInsights/workspaces",
            SHARED_NAMES["logAnalyticsWorkspace"],
            "RG-Shared-Logs" if cross_group else GROUP,
            customerId=CUSTOMER_ID,
        )
        selected["properties"].update(
            publicNetworkAccess="Disabled",
            vnetConfiguration={
                "internal": True,
                "infrastructureSubnetId": f"{VNET}/subnets/container-apps",
            },
            defaultDomain="internal.eastus.azurecontainerapps.io",
            staticIp="10.24.1.5",
            workloadProfiles=[{"name": "Consumption", "workloadProfileType": "Consumption"}],
            appLogsConfiguration={
                "destination": "log-analytics",
                "logAnalyticsConfiguration": {"customerId": CUSTOMER_ID},
            },
        )
        state.values["logAnalyticsWorkspacesApi"] = {"value": [workspace]}
        state.values["existingLogAnalyticsWorkspaceApi"] = workspace
    elif name == "communicationServices":
        email = resource(
            "Microsoft.Communication/emailServices",
            SHARED_NAMES["emailService"],
            "RG-Shared-Mail" if cross_group else GROUP,
            dataLocation="Asia Pacific",
        )
        domain = {
            **resource(
                "Microsoft.Communication/emailServices/domains",
                "AzureManagedDomain",
                group,
                domainManagement="AzureManaged",
                fromSenderDomain="test.azurecomm.net",
            ),
            "id": email["id"] + "/domains/AzureManagedDomain",
        }
        selected["location"] = "global"
        selected["properties"].update(
            hostName="shared-acs.asiapacific.communication.azure.com",
            dataLocation="Asia Pacific",
            linkedDomains=[domain["id"]],
        )
        state.values.update(
            existingEmailServiceApi=email,
            existingEmailDomainApi=domain,
            existingEmailDomain=domain["id"],
        )
    selector = "existing" + name[0].upper() + name[1:]
    state.values[name + "Mode"] = "existing"
    state.values[selector] = {key: selected[key] for key in ("id", "name", "location")}
    state.values[selector + "Api"] = selected


def binding_values(state: UiState) -> dict[str, str]:
    generated = build_v2_ui.derive_resource_names(
        state.values["namingPrefix"], state.values["namingSuffix"]
    )
    bindings = {}
    for name, (_, resource_type, _, _) in build_v2_ui.SHARED_RESOURCES.items():
        selector = "existing" + name[0].upper() + name[1:]
        bindings[name + "ResourceId"] = (
            state.values[selector]["id"]
            if state.values.get(name + "Mode") == "existing"
            else resource(resource_type, generated[name + "Name"], state.group["name"])["id"]
        )
    bindings["logAnalyticsWorkspaceResourceId"] = (
        state.values["existingLogAnalyticsWorkspaceApi"]["id"]
        if state.values.get("containerAppsEnvironmentMode") == "existing"
        else resource(
            "Microsoft.OperationalInsights/workspaces",
            generated["logAnalyticsWorkspaceName"],
            state.group["name"],
        )["id"]
    )
    bindings["emailDomainResourceId"] = (
        state.values["existingEmailDomain"]
        if state.values.get("communicationServicesMode") == "existing"
        else resource(
            "Microsoft.Communication/emailServices",
            generated["emailServiceName"],
            state.group["name"],
        )["id"]
        + "/domains/AzureManagedDomain"
    )
    for name, prefix in (
        ("stateContainerName", "azbrief-state"),
        ("archiveContainerName", "azbrief-archive"),
    ):
        bindings[name] = prefix + (
            "-" + generated["containerAppName"]
            if state.values.get("storageAccountMode") == "existing"
            else ""
        )
    return bindings


def save_tags(state: UiState, *, bindings: bool = True) -> dict[str, str]:
    tags = {
        "azbrief.v2.schema": "2",
        "azbrief.v2.profile": "kt-private-v2",
        "azbrief.v2.state": "foundation",
        "azbrief.v2.location": "eastus",
        "azbrief.v2.vnetRg": NETWORK_GROUP,
        "azbrief.v2.vnet": "vnet-private",
        "azbrief.v2.peSubnet": "private-endpoints",
        "azbrief.v2.foundrySubnet": "foundry-agents",
        "azbrief.v2.appSubnet": "container-apps",
        "azbrief.v2.coreModel": "gpt-5-terra",
        "azbrief.v2.simpleModel": "gpt-5-luna",
        "azbrief.v2.project": output("projectName", state),
    }
    tags.update({"azbrief.v2." + tag: output(name, state) for name, tag in NAME_TAGS.items()})
    tags["azbrief.v2.app"] = output("containerAppName", state)
    options = {
        name + "Mode": state.values.get(name + "Mode", "new")
        for name in build_v2_ui.SHARED_RESOURCES
    }
    options.update(
        virtualNetworkMode="existing",
        useMicrosoftManagedAgentData=True,
        namingPrefix=state.values["namingPrefix"],
        namingSuffix=state.values["namingSuffix"],
        coreModelName=state.values["options"].get("coreModelName", "gpt-5.2"),
        simpleModelName=state.values["options"].get("simpleModelName", "gpt-5-mini"),
        modelVersion=state.values["options"].get("modelVersion", "2025-12-11"),
        simpleModelVersion=state.values["options"].get("simpleModelVersion", "2025-08-07"),
        modelCapacity=int(state.values["options"].get("modelCapacity", "200")),
        simpleModelCapacity=int(state.values["options"].get("simpleModelCapacity", "200")),
    )
    text = json.dumps(options, separators=(",", ":"))
    tags.update({f"azbrief.v2.options{i}": text[i * 256 : (i + 1) * 256] for i in range(8)})
    if bindings:
        set_resource_tags(tags, binding_values(state))
    return tags


def set_resource_tags(tags: dict[str, str], bindings: dict[str, str]) -> None:
    for index in range(8):
        tags.pop(f"azbrief.v2.resources{index}", None)
    text = json.dumps(bindings, separators=(",", ":"))
    tags.update(
        {f"azbrief.v2.resources{i // 256}": text[i : i + 256] for i in range(0, len(text), 256)}
    )


def complete(state: UiState, tags: dict[str, str] | None = None) -> UiState:
    completed = copy.deepcopy(state)
    completed.values["contract"]["resourceGroupApi"] = {"tags": tags or save_tags(state)}
    completed.network = {}
    completed.group["mode"] = "Existing"
    completed.stage = "complete"
    return completed


def test_default_all_new_keeps_names_network_validation_and_no_existing_ids() -> None:
    state = foundation()
    expected = build_v2_ui.derive_resource_names("team", "prd")
    assert output("portalValidationPassed", state) is True
    for name in build_v2_ui.SHARED_RESOURCES:
        assert output(name + "Mode", state) == "new"
        selector = "existing" + name[0].upper() + name[1:]
        assert output(selector + "ResourceId", state) == ""
        assert output(name + "Name", state) == expected[name + "Name"]
    assert output("logAnalyticsWorkspaceName", state) == expected["logAnalyticsWorkspaceName"]
    assert output("emailServiceName", state) == expected["emailServiceName"]
    assert output("existingLogAnalyticsWorkspaceResourceId", state) == ""
    assert output("existingEmailDomainResourceId", state) == ""
    state.network["containerAppsSubnetName"] = state.network["foundrySubnetName"]
    assert output("portalValidationPassed", state) is False


def test_model_inputs_never_move_an_existing_network_or_replace_its_known_region() -> None:
    state = foundation()
    state.network["virtualNetwork"]["location"] = "koreacentral"
    state.network["virtualNetworkApi"]["location"] = "koreacentral"
    state.network["modelDeploymentRegion"] = "eastus"
    state.selected_location = "swedencentral"
    state.values["options"].update(coreModelName="gpt-5.1", modelCapacity="50")
    assert output("location", state) == "koreacentral"
    assert output("portalValidationPassed", state) is True
    notice = evaluate(controls()["modelRegionNotice"]["options"]["text"], state)
    assert "koreacentral" in notice
    assert "전역" in notice and "별도 location" in notice
    assert "modelDeploymentRegion" not in controls()
    assert definition()["config"]["basics"]["location"]["visible"] is True


def test_new_infrastructure_uses_only_its_native_location_not_a_legacy_model_input() -> None:
    state = foundation()
    state.network.update(
        virtualNetworkMode="new",
        newVirtualNetworkName="vnet-private-new",
        newPeSubnetName="private-endpoints",
        newFoundrySubnetName="foundry-agents",
        newContainerAppsSubnetName="container-apps",
        newNetworkRange="10.240",
        modelDeploymentRegion="japaneast",
    )
    state.selected_location = "koreacentral"
    assert output("location", state) == "koreacentral"
    assert output("portalValidationPassed", state) is True
    state.selected_location = ""
    assert output("location", state) == ""
    assert output("portalValidationPassed", state) is False


def test_model_region_notice_uses_shared_foundrys_actual_location_without_moving_it() -> None:
    state = foundation()
    select_existing(state, "foundryAccount")
    state.values["existingFoundryAccountApi"]["location"] = "koreacentral"
    state.network["modelDeploymentRegion"] = "swedencentral"
    notice = evaluate(controls()["modelRegionNotice"]["options"]["text"], state)
    assert "GlobalStandard 모델·quota 리전: koreacentral" in notice
    assert "swedencentral" not in notice
    assert output("location", state) == "eastus"
    assert output("portalValidationPassed", state) is False
    state.network["virtualNetwork"]["location"] = "koreacentral"
    state.network["virtualNetworkApi"]["location"] = "koreacentral"
    assert output("location", state) == "koreacentral"
    assert output("portalValidationPassed", state) is True


@pytest.mark.parametrize("name", build_v2_ui.SHARED_RESOURCES)
@pytest.mark.parametrize("cross_group", [False, True])
def test_each_provider_is_independent_and_can_use_another_resource_group(
    name: str, cross_group: bool
) -> None:
    state = foundation()
    select_existing(state, name, cross_group)
    assert output("portalValidationPassed", state) is True
    for candidate in build_v2_ui.SHARED_RESOURCES:
        assert output(candidate + "Mode", state) == ("existing" if candidate == name else "new")
    selector = "existing" + name[0].upper() + name[1:]
    assert output(selector + "ResourceId", state) == state.values[selector]["id"]
    assert output(name + "Name", state) == SHARED_NAMES[name]
    preview = evaluate(controls()["resourceNamePreview"]["options"]["text"], state)
    assert name + "Name:" not in preview
    assert "containerAppName: ca-team-prd" in preview
    assert "projectName: team-prd" in preview
    if name == "containerAppsEnvironment":
        assert "logAnalyticsWorkspaceName:" not in preview
        assert output("logAnalyticsWorkspaceName", state) == SHARED_NAMES["logAnalyticsWorkspace"]
    if name == "communicationServices":
        assert "emailServiceName:" not in preview
        assert output("emailServiceName", state) == SHARED_NAMES["emailService"]


def test_existing_selectors_are_subscription_scoped_and_not_manual_id_fields() -> None:
    selected_controls = controls()
    for name, (_, resource_type, _, _) in build_v2_ui.SHARED_RESOURCES.items():
        selector = "existing" + name[0].upper() + name[1:]
        control = selected_controls[selector]
        assert control["type"] == "Microsoft.Solutions.ResourceSelector"
        assert control["resourceType"] == resource_type
        assert control["options"] == {"filter": {"subscription": "onBasics", "location": "all"}}
        assert selected_controls[name + "Mode"]["defaultValue"] == "신규 생성"
        assert {
            row["value"] for row in selected_controls[name + "Mode"]["constraints"]["allowedValues"]
        } == {"new", "existing"}
        assert selector + "ResourceId" not in selected_controls
    assert selected_controls["existingLogAnalyticsWorkspace"]["options"]["filter"] == {
        "subscription": "onBasics",
        "location": "all",
    }
    assert selected_controls["existingEmailDomain"]["type"] == "Microsoft.Common.DropDown"
    assert (
        "linkedDomains" in selected_controls["existingEmailDomain"]["constraints"]["allowedValues"]
    )
    assert selected_controls["namingPrefix"]["constraints"]["validations"]
    assert not any(name.endswith("Guard") for name in selected_controls)


@pytest.mark.parametrize("name", build_v2_ui.SHARED_RESOURCES)
@pytest.mark.parametrize("stage", ["foundation", "complete"])
def test_exact_typed_get_uses_selected_or_saved_id_not_the_workload_rg(
    name: str, stage: str
) -> None:
    state = foundation()
    select_existing(state, name)
    selector = "existing" + name[0].upper() + name[1:]
    expected_id = state.values[selector]["id"]
    if stage == "complete":
        state = complete(state)
        state.values[selector] = {"id": expected_id.replace("RG-Shared-Providers", "redirected")}
    path = evaluate(controls()[selector + "Api"]["request"]["path"], state)
    assert path == expected_id + "?api-version=" + build_v2_ui.SHARED_RESOURCES[name][2]


@pytest.mark.parametrize("name", build_v2_ui.SHARED_RESOURCES)
@pytest.mark.parametrize(
    "failure",
    ["empty", "denied", "paged", "redirected", "type", "subscription", "missing_id", "pending"],
)
def test_existing_readbacks_fail_closed(name: str, failure: str) -> None:
    state = foundation()
    select_existing(state, name)
    selector = "existing" + name[0].upper() + name[1:]
    api = state.values[selector + "Api"]
    if failure == "empty":
        state.values[selector + "Api"] = None
    elif failure == "denied":
        api["error"] = {"code": "AuthorizationFailed"}
    elif failure == "paged":
        api["nextLink"] = "https://management.azure.com/next"
    elif failure == "redirected":
        api["id"] = api["id"].replace("RG-Shared-Providers", "another-rg")
    elif failure == "type":
        api["type"] = "Microsoft.Network/virtualNetworks"
    elif failure == "subscription":
        state.values[selector]["id"] = state.values[selector]["id"].replace(
            SUBSCRIPTION, OTHER_SUBSCRIPTION
        )
        api["id"] = state.values[selector]["id"]
    elif failure == "missing_id":
        state.values[selector].pop("id")
    elif failure == "pending":
        api["properties"]["provisioningState"] = "Updating"
    assert output("portalValidationPassed", state) is False


@pytest.mark.parametrize("name", ["foundryAccount", "containerAppsEnvironment"])
@pytest.mark.parametrize(
    "failure", ["region", "unknown_region", "subnet", "vnet", "public", "new_vnet"]
)
def test_shared_network_owners_require_exact_existing_network_and_region(
    name: str, failure: str
) -> None:
    state = foundation()
    select_existing(state, name)
    api = state.values["existing" + name[0].upper() + name[1:] + "Api"]
    if failure == "region":
        api["location"] = "koreacentral"
    elif failure == "unknown_region":
        api.pop("location")
    elif failure == "public":
        api["properties"]["publicNetworkAccess"] = "Enabled"
    elif failure == "new_vnet":
        state.network.update(
            virtualNetworkMode="new",
            newVirtualNetworkName="new-vnet",
            newPeSubnetName="private-endpoints",
            newFoundrySubnetName="foundry-agents",
            newContainerAppsSubnetName="container-apps",
        )
    else:
        props = api["properties"]
        owner = (
            props["networkInjections"][0]
            if name == "foundryAccount"
            else props["vnetConfiguration"]
        )
        key = "subnetArmId" if name == "foundryAccount" else "infrastructureSubnetId"
        owner[key] = owner[key].replace(
            "/subnets/", "/subnets/other-" if failure == "subnet" else "/other-vnet/subnets/"
        )
    assert output("portalValidationPassed", state) is False
    if failure == "new_vnet":
        rule = controls()["newVirtualNetworkName"]["constraints"]["validations"][0]
        assert evaluate(rule["isValid"], state) is False
        assert "신규 VNet" in rule["message"]


@pytest.mark.parametrize("internal", [None, False, 1, "true"])
def test_shared_environment_requires_typed_internal_true(internal: object) -> None:
    state = foundation()
    select_existing(state, "containerAppsEnvironment")
    state.values["existingContainerAppsEnvironmentApi"]["properties"]["vnetConfiguration"][
        "internal"
    ] = internal
    assert output("portalValidationPassed", state) is False


@pytest.mark.parametrize(
    "subdomain,valid",
    [
        (None, False),
        ("", False),
        ("actual-shared-endpoint", True),
        ("Shared-Endpoint-123", True),
        ("a" * 64, True),
        ("a" * 65, False),
        ("-invalid", False),
        ("invalid.host", False),
        ("https://invalid", False),
        ("invalid/path", False),
        ("invalid host", False),
        ("유효하지않음", False),
    ],
)
def test_foundry_requires_its_actual_custom_subdomain_not_an_invented_account_endpoint(
    subdomain: str | None, valid: bool
) -> None:
    state = foundation()
    select_existing(state, "foundryAccount")
    state.values["existingFoundryAccountApi"]["properties"]["customSubDomainName"] = subdomain
    assert output("portalValidationPassed", state) is valid
    assert output("foundryAccountName", state) == SHARED_NAMES["foundryAccount"]


@pytest.mark.parametrize("local_auth_disabled", [None, False, "true", 1])
def test_shared_foundry_retains_entra_only_authentication(local_auth_disabled: object) -> None:
    state = foundation()
    select_existing(state, "foundryAccount")
    state.values["existingFoundryAccountApi"]["properties"][
        "disableLocalAuth"
    ] = local_auth_disabled
    assert output("portalValidationPassed", state) is False


@pytest.mark.parametrize("stage", ["foundation", "complete"])
@pytest.mark.parametrize(
    "control,collection,version",
    [
        ("sharedFoundryHostsApi", "capabilityHosts", "2025-04-01-preview"),
        ("sharedFoundryDeploymentsApi", "deployments", "2025-06-01"),
        ("sharedFoundryProjectsApi", "projects", "2025-06-01"),
    ],
)
def test_shared_foundry_inventory_reads_the_selected_or_saved_account(
    stage: str, control: str, collection: str, version: str
) -> None:
    state = foundation()
    select_existing(state, "foundryAccount")
    account_id = state.values["existingFoundryAccount"]["id"]
    if stage == "complete":
        state = complete(state)
        state.values["existingFoundryAccount"] = {"id": "/do/not/reselect"}
    request = controls()[control]["request"]
    assert request["method"] == "GET"
    assert evaluate(request["path"], state) == account_id + f"/{collection}?api-version={version}"


@pytest.mark.parametrize(
    "control", ["sharedFoundryHostsApi", "sharedFoundryDeploymentsApi", "sharedFoundryProjectsApi"]
)
@pytest.mark.parametrize(
    "failure", ["missing", "no_value", "null", "denied", "paged", "redirected", "type"]
)
def test_shared_foundry_inventory_is_complete_typed_and_account_scoped(
    control: str, failure: str
) -> None:
    state = foundation()
    select_existing(state, "foundryAccount")
    inventory = state.values[control]
    if failure == "missing":
        state.values[control] = None
    elif failure == "no_value":
        inventory.pop("value")
    elif failure == "null":
        inventory["value"] = None
    elif failure == "denied":
        inventory["error"] = {"code": "AuthorizationFailed"}
    elif failure == "paged":
        inventory["nextLink"] = "https://management.azure.com/next"
    else:
        kind = {
            "sharedFoundryHostsApi": "capabilityHosts",
            "sharedFoundryDeploymentsApi": "deployments",
            "sharedFoundryProjectsApi": "projects",
        }[control]
        row = child_resource(state.values["existingFoundryAccountApi"], kind, "unrelated")
        if failure == "redirected":
            row["id"] = row["id"].replace("RG-Shared-Providers", "another-group")
        else:
            row["type"] = "Microsoft.Network/virtualNetworks"
        inventory["value"] = [row]
    assert output("portalValidationPassed", state) is False
    assert output("reuseCoreModel", state) is False
    assert output("reuseSimpleModel", state) is False


@pytest.mark.parametrize("host_state", ["empty", None, "Creating", "Failed"])
def test_shared_foundry_requires_a_ready_account_capability_host(host_state: str | None) -> None:
    state = foundation()
    select_existing(state, "foundryAccount")
    hosts = state.values["sharedFoundryHostsApi"]["value"]
    if host_state == "empty":
        hosts.clear()
    else:
        hosts[0]["properties"]["provisioningState"] = host_state
    assert output("portalValidationPassed", state) is False


@pytest.mark.parametrize("role", ["Core", "Simple"])
@pytest.mark.parametrize("stage", ["foundation", "complete"])
def test_shared_model_alias_reuses_exact_ready_selection_or_creates_only_when_missing(
    role: str, stage: str
) -> None:
    state = foundation()
    select_existing(state, "foundryAccount")
    assert output("reuseCoreModel", state) is False
    assert output("reuseSimpleModel", state) is False
    assert output("portalValidationPassed", state) is True
    deployment = model_deployment(state, role)
    deployment["sku"]["capacity"] = 300
    state.values["sharedFoundryDeploymentsApi"]["value"].append(deployment)
    if stage == "complete":
        state = complete(state)
        state.values["options"].update(
            coreModelName="gpt-5-do-not-reselect",
            simpleModelName="gpt-5-do-not-reselect",
            modelVersion="do-not-reselect",
            simpleModelVersion="do-not-reselect",
            modelCapacity="1000",
            simpleModelCapacity="1000",
        )
    assert output(f"reuse{role}Model", state) is True
    assert output("reuseSimpleModel" if role == "Core" else "reuseCoreModel", state) is False
    assert output("portalValidationPassed", state) is True


def test_shared_foundry_can_reuse_both_exact_ready_aliases_without_model_puts() -> None:
    state = foundation()
    select_existing(state, "foundryAccount")
    state.values["sharedFoundryDeploymentsApi"]["value"] = [
        model_deployment(state, role) for role in ("Core", "Simple")
    ]
    state = complete(state)
    assert output("reuseCoreModel", state) is True
    assert output("reuseSimpleModel", state) is True
    assert output("portalValidationPassed", state) is True


@pytest.mark.parametrize("empty_version", [False, True])
def test_existing_simple_alias_uses_the_saved_legacy_core_version_fallback(
    empty_version: bool,
) -> None:
    state = foundation()
    select_existing(state, "foundryAccount")
    deployment = model_deployment(state, "Simple")
    deployment["properties"]["model"]["version"] = "2025-12-11"
    state.values["sharedFoundryDeploymentsApi"]["value"] = [deployment]
    tags = save_tags(state)
    options = json.loads("".join(tags[f"azbrief.v2.options{i}"] for i in range(8)))
    if empty_version:
        options["simpleModelVersion"] = ""
    else:
        options.pop("simpleModelVersion")
    encoded = json.dumps(options, separators=(",", ":"))
    tags.update({f"azbrief.v2.options{i}": encoded[i * 256 : (i + 1) * 256] for i in range(8)})
    state = complete(state, tags)
    assert output("reuseSimpleModel", state) is True
    assert output("portalValidationPassed", state) is True


@pytest.mark.parametrize("role", ["Core", "Simple"])
@pytest.mark.parametrize(
    "failure",
    [
        "model",
        "version",
        "format",
        "sku",
        "pending",
        "capacity",
        "unknown_capacity",
        "string_capacity",
        "duplicate",
    ],
)
def test_shared_model_alias_never_overwrites_a_mismatching_or_unready_deployment(
    role: str, failure: str
) -> None:
    state = foundation()
    select_existing(state, "foundryAccount")
    deployment = model_deployment(state, role)
    if failure in {"model", "version", "format"}:
        key = "name" if failure == "model" else failure
        deployment["properties"]["model"][key] = "incompatible"
    elif failure == "sku":
        deployment["sku"]["name"] = "GlobalProvisionedManaged"
    elif failure == "pending":
        deployment["properties"]["provisioningState"] = "Updating"
    elif failure == "capacity":
        deployment["sku"]["capacity"] = 199
    elif failure == "unknown_capacity":
        deployment["sku"].pop("capacity")
    elif failure == "string_capacity":
        deployment["sku"]["capacity"] = "200"
    rows = state.values["sharedFoundryDeploymentsApi"]["value"]
    rows.append(deployment)
    if failure == "duplicate":
        rows.append(copy.deepcopy(deployment))
    assert output("portalValidationPassed", state) is False
    assert output(f"reuse{role}Model", state) is False


@pytest.mark.parametrize("stage", ["foundation", "complete"])
def test_unrelated_shared_projects_and_models_do_not_prevent_a_new_workload(stage: str) -> None:
    state = foundation()
    select_existing(state, "foundryAccount")
    account = state.values["existingFoundryAccountApi"]
    project = child_resource(account, "projects", "another-workload")
    project["tags"] = {"deploymentProfile": "foreign"}
    project["properties"]["provisioningState"] = "Updating"
    state.values["sharedFoundryProjectsApi"]["value"] = [project]
    deployment = model_deployment(state)
    deployment.update(id=account["id"] + "/deployments/unrelated", name="unrelated")
    deployment["properties"]["model"]["name"] = "unrelated-model"
    deployment["sku"]["name"] = "Standard"
    state.values["sharedFoundryDeploymentsApi"]["value"] = [deployment]
    if stage == "complete":
        state = complete(state)
    assert output("portalValidationPassed", state) is True
    assert output("reuseCoreModel", state) is False
    assert output("reuseSimpleModel", state) is False


@pytest.mark.parametrize("owned", [False, True])
@pytest.mark.parametrize("stage", ["foundation", "complete"])
def test_existing_workload_project_requires_cli_host_and_telemetry_verification(
    owned: bool, stage: str
) -> None:
    state = foundation()
    select_existing(state, "foundryAccount")
    project = child_resource(
        state.values["existingFoundryAccountApi"], "projects", output("projectName", state)
    )
    project["tags"] = {"deploymentProfile": "kt-private-v2" if owned else "foreign"}
    state.values["sharedFoundryProjectsApi"]["value"] = [project]
    if stage == "complete":
        state = complete(state)
    assert output("portalValidationPassed", state) is False
    rule = controls()["namingPrefix"]["constraints"]["validations"][1]
    assert evaluate(rule["isValid"], state) is False
    assert "CLI" in rule["message"]
    assert "Host" in rule["message"] and "텔레메트리" in rule["message"]


@pytest.mark.parametrize("kind", ["StorageV2", "BlobStorage", "BlockBlobStorage"])
def test_existing_storage_accepts_ready_private_blob_capable_kinds(kind: str) -> None:
    state = foundation()
    select_existing(state, "storageAccount")
    state.values["existingStorageAccountApi"]["kind"] = kind
    assert output("portalValidationPassed", state) is True


@pytest.mark.parametrize("stage", ["foundation", "complete"])
def test_shared_environment_never_requests_a_new_private_endpoint(stage: str) -> None:
    state = foundation()
    select_existing(state, "containerAppsEnvironment")
    if stage == "foundation":
        state.network["deployContainerAppsPrivateEndpoint"] = True
    else:
        tags = save_tags(state)
        tags["azbrief.v2.acaPeEnabled"] = "true"
        state = complete(state, tags)
    assert output("portalValidationPassed", state) is False


@pytest.mark.parametrize(
    "profiles", [None, [], [{"name": "dedicated", "workloadProfileType": "D4"}]]
)
def test_shared_environment_has_the_application_consumption_profile(
    profiles: list[dict[str, str]] | None,
) -> None:
    state = foundation()
    select_existing(state, "containerAppsEnvironment")
    state.values["existingContainerAppsEnvironmentApi"]["properties"]["workloadProfiles"] = profiles
    assert output("portalValidationPassed", state) is False


@pytest.mark.parametrize("name", ["foundryAccount", "containerAppsEnvironment"])
@pytest.mark.parametrize("missing", [False, True])
def test_shared_owner_cannot_use_a_redirected_or_unknown_subnet_record(
    name: str, missing: bool
) -> None:
    state = foundation()
    select_existing(state, name)
    expected = "foundry-agents" if name == "foundryAccount" else "container-apps"
    subnet = next(
        row
        for row in state.network["virtualNetworkApi"]["properties"]["subnets"]
        if row["name"] == expected
    )
    if missing:
        subnet.pop("id")
    else:
        subnet["id"] = subnet["id"].replace("vnet-private", "other-vnet")
    assert output("portalValidationPassed", state) is False


def test_new_vnet_is_compatible_with_independently_shared_storage_and_acs() -> None:
    state = foundation()
    for name in ("storageAccount", "communicationServices"):
        select_existing(state, name)
    state.network.update(
        virtualNetworkMode="new",
        newVirtualNetworkName="new-vnet",
        newPeSubnetName="private-endpoints",
        newFoundrySubnetName="foundry-agents",
        newContainerAppsSubnetName="container-apps",
    )
    state.network.pop("virtualNetwork")
    state.network.pop("virtualNetworkApi")
    assert output("portalValidationPassed", state) is True


def test_arm_id_matching_is_case_insensitive_but_name_outputs_preserve_leaf_casing() -> None:
    state = foundation()
    select_existing(state, "communicationServices")
    state.values["existingCommunicationServices"]["id"] = (
        state.values["existingCommunicationServices"]["id"]
        .replace("/subscriptions/", "/Subscriptions/")
        .replace("/resourceGroups/", "/ResourceGroups/")
    )
    state.values["existingEmailDomain"] = state.values["existingEmailDomain"].replace(
        "/domains/", "/Domains/"
    )
    assert output("portalValidationPassed", state) is True
    assert output("communicationServicesName", state) == SHARED_NAMES["communicationServices"]
    assert output("emailServiceName", state) == SHARED_NAMES["emailService"]


@pytest.mark.parametrize("name", ["foundryAccount", "containerAppsEnvironment"])
@pytest.mark.parametrize("shared", [False, True])
def test_occupied_subnet_only_allows_a_link_to_the_selected_existing_owner(
    name: str, shared: bool
) -> None:
    state = foundation()
    select_existing(state, name)
    selector = "existing" + name[0].upper() + name[1:]
    subnet_name = "foundry-agents" if name == "foundryAccount" else "container-apps"
    subnet = next(
        row
        for row in state.network["virtualNetworkApi"]["properties"]["subnets"]
        if row["name"] == subnet_name
    )
    subnet["properties"]["serviceAssociationLinks"] = [
        {"properties": {"link": state.values[selector]["id"] if shared else "/foreign/owner"}}
    ]
    assert output("portalValidationPassed", state) is shared
    state.values[name + "Mode"] = "new"
    assert output("portalValidationPassed", state) is False


def managed_subnet_occupancy(state: UiState, name: str) -> dict[str, Any]:
    subnet_name = "foundry-agents" if name == "foundryAccount" else "container-apps"
    subnet = next(
        row
        for row in state.network["virtualNetworkApi"]["properties"]["subnets"]
        if row["name"] == subnet_name
    )
    managed_network = (
        f"/subscriptions/{SUBSCRIPTION}/resourceGroups/MC-RP-managed-network"
        "/providers/Microsoft.Network"
    )
    subnet["properties"].update(
        ipConfigurations=[
            {"id": managed_network + "/networkInterfaces/service-nic/ipConfigurations/ipconfig1"}
        ],
        privateEndpoints=[{"id": managed_network + "/privateEndpoints/service-endpoint"}],
        serviceAssociationLinks=[
            {
                "id": subnet["id"] + "/serviceAssociationLinks/legionservicelink",
                "properties": {
                    "linkedResourceType": "Microsoft.App/environments",
                    "allowDelete": True,
                    "provisioningState": "Succeeded",
                },
            }
        ],
        resourceNavigationLinks=[
            {
                "id": subnet["id"] + "/resourceNavigationLinks/service-navigation",
                "properties": {"linkedResourceType": "Microsoft.App/environments"},
            }
        ],
    )
    return subnet


@pytest.mark.parametrize("name", ["foundryAccount", "containerAppsEnvironment"])
def test_bound_existing_owner_allows_id_only_managed_nics_and_subnet_links(name: str) -> None:
    state = foundation()
    select_existing(state, name)
    managed_subnet_occupancy(state, name)
    assert output("portalValidationPassed", state) is True
    state.values[name + "Mode"] = "new"
    assert output("portalValidationPassed", state) is False


@pytest.mark.parametrize("name", ["foundryAccount", "containerAppsEnvironment"])
@pytest.mark.parametrize("failure", ["owner_redirect", "binding_redirect", "subnet_redirect"])
def test_managed_subnet_occupancy_still_requires_verified_exact_owner_binding(
    name: str, failure: str
) -> None:
    state = foundation()
    select_existing(state, name)
    subnet = managed_subnet_occupancy(state, name)
    api = state.values["existing" + name[0].upper() + name[1:] + "Api"]
    if failure == "owner_redirect":
        api["id"] += "-other"
    elif failure == "subnet_redirect":
        subnet["id"] = subnet["id"].replace("vnet-private", "other-vnet")
    elif name == "foundryAccount":
        api["properties"]["networkInjections"][0]["subnetArmId"] += "-other"
    else:
        api["properties"]["vnetConfiguration"]["infrastructureSubnetId"] += "-other"
    assert output("portalValidationPassed", state) is False


@pytest.mark.parametrize("name", ["foundryAccount", "containerAppsEnvironment"])
@pytest.mark.parametrize("field", ["serviceAssociationLinks", "resourceNavigationLinks"])
@pytest.mark.parametrize("owner_field", ["link", "linkedResourceId", "resourceId"])
def test_managed_subnet_links_reject_every_explicit_foreign_owner(
    name: str, field: str, owner_field: str
) -> None:
    state = foundation()
    select_existing(state, name)
    subnet = managed_subnet_occupancy(state, name)
    owner = state.values["existing" + name[0].upper() + name[1:]]["id"]
    properties = subnet["properties"][field][0]["properties"]
    properties["link"] = owner
    properties[owner_field] = owner + "-foreign"
    assert output("portalValidationPassed", state) is False


@pytest.mark.parametrize(
    "inventory",
    [
        None,
        {},
        {"value": None},
        {"value": []},
        {"value": [], "error": {"code": "AuthorizationFailed"}},
    ],
)
def test_shared_environment_never_creates_a_workspace_on_incomplete_inventory(
    inventory: dict[str, Any] | None,
) -> None:
    state = foundation()
    select_existing(state, "containerAppsEnvironment")
    state.values["logAnalyticsWorkspacesApi"] = inventory
    assert output("existingLogAnalyticsWorkspaceResourceId", state) == ""
    assert output("logAnalyticsWorkspaceName", state) == ""
    assert output("portalValidationPassed", state) is False


@pytest.mark.parametrize(
    "failure", ["paged", "duplicate", "wrong_customer", "redirected", "unready"]
)
def test_workspace_resolution_requires_unique_complete_inventory_and_exact_readback(
    failure: str,
) -> None:
    state = foundation()
    select_existing(state, "containerAppsEnvironment")
    if failure == "paged":
        state.values["logAnalyticsWorkspacesApi"]["nextLink"] = "https://management.azure.com/next"
    elif failure == "duplicate":
        state.values["logAnalyticsWorkspacesApi"]["value"].append(
            copy.deepcopy(state.values["existingLogAnalyticsWorkspaceApi"])
        )
    elif failure == "wrong_customer":
        state.values["existingLogAnalyticsWorkspaceApi"]["properties"][
            "customerId"
        ] = OTHER_SUBSCRIPTION
    elif failure == "redirected":
        state.values["existingLogAnalyticsWorkspaceApi"]["id"] += "-redirected"
    else:
        state.values["existingLogAnalyticsWorkspaceApi"]["properties"][
            "provisioningState"
        ] = "Failed"
    assert output("portalValidationPassed", state) is False


@pytest.mark.parametrize("inventory_failure", ["denied", "paged", "duplicate"])
def test_explicit_exact_workspace_is_the_only_incomplete_inventory_fallback(
    inventory_failure: str,
) -> None:
    state = foundation()
    select_existing(state, "containerAppsEnvironment")
    workspace = state.values["existingLogAnalyticsWorkspaceApi"]
    state.values["existingLogAnalyticsWorkspace"] = {
        key: workspace[key] for key in ("id", "name", "location")
    }
    inventory = state.values["logAnalyticsWorkspacesApi"]
    if inventory_failure == "denied":
        inventory.update(error={"code": "AuthorizationFailed"})
    elif inventory_failure == "paged":
        inventory["nextLink"] = "https://management.azure.com/next"
    else:
        inventory["value"].append(copy.deepcopy(workspace))
    assert output("existingLogAnalyticsWorkspaceResourceId", state) == workspace["id"]
    assert output("portalValidationPassed", state) is True
    workspace["properties"]["customerId"] = OTHER_SUBSCRIPTION
    assert output("portalValidationPassed", state) is False


def test_acs_domain_choices_are_only_existing_links_and_have_no_automatic_selection() -> None:
    state = foundation()
    select_existing(state, "communicationServices")
    choice = controls()["existingEmailDomain"]
    rows = evaluate(choice["constraints"]["allowedValues"], state)
    assert [row["value"] for row in rows] == ["", state.values["existingEmailDomain"]]
    assert choice["defaultValue"] == rows[0]["label"]
    state.values["existingEmailDomain"] = ""
    assert output("existingEmailDomainResourceId", state) == ""
    assert output("portalValidationPassed", state) is False


@pytest.mark.parametrize(
    "failure",
    [
        "unlinked",
        "domain_pending",
        "domain_redirect",
        "domain_type",
        "email_redirect",
        "data_location",
        "unverified_custom",
    ],
)
def test_acs_never_adopts_an_unlinked_unready_or_redirected_email_domain(failure: str) -> None:
    state = foundation()
    select_existing(state, "communicationServices")
    domain = state.values["existingEmailDomainApi"]
    if failure == "unlinked":
        state.values["existingCommunicationServicesApi"]["properties"]["linkedDomains"] = []
    elif failure == "domain_pending":
        domain["properties"]["provisioningState"] = "Creating"
    elif failure == "domain_redirect":
        domain["id"] += "-redirected"
    elif failure == "domain_type":
        domain["type"] = "Microsoft.Communication/emailServices"
    elif failure == "email_redirect":
        state.values["existingEmailServiceApi"]["id"] += "-redirected"
    elif failure == "data_location":
        state.values["existingEmailServiceApi"]["properties"]["dataLocation"] = "United States"
    else:
        domain["properties"]["domainManagement"] = "CustomerManaged"
    assert output("portalValidationPassed", state) is False


@pytest.mark.parametrize(
    "selected",
    [(), *[(name,) for name in build_v2_ui.SHARED_RESOURCES], tuple(build_v2_ui.SHARED_RESOURCES)],
)
def test_completion_restores_modes_exact_ids_and_leaf_names_without_reselection(
    selected: tuple[str, ...],
) -> None:
    state = foundation()
    for name in selected:
        select_existing(state, name)
    expected_names = {name: output(name, state) for name in NAME_TAGS}
    bindings = binding_values(state)
    completed = complete(state)
    completed.values["logAnalyticsWorkspacesApi"] = None
    for name in build_v2_ui.SHARED_RESOURCES:
        selector = "existing" + name[0].upper() + name[1:]
        completed.values[name + "Mode"] = "new" if name in selected else "existing"
        completed.values[selector] = {"id": "/do/not/reselect"}
        expected_existing = name in selected
        assert output(name + "Mode", completed) == ("existing" if expected_existing else "new")
        assert output(selector + "ResourceId", completed) == (
            bindings[name + "ResourceId"] if expected_existing else ""
        )
        assert evaluate(controls()[selector]["visible"], completed) is False
    assert output("existingLogAnalyticsWorkspaceResourceId", completed) == (
        bindings["logAnalyticsWorkspaceResourceId"]
        if "containerAppsEnvironment" in selected
        else ""
    )
    assert output("existingEmailDomainResourceId", completed) == (
        bindings["emailDomainResourceId"] if "communicationServices" in selected else ""
    )
    for name, expected in expected_names.items():
        assert output(name, completed) == expected
    assert output("portalValidationPassed", completed) is True
    notice = evaluate(controls()["savedResourceBindings"]["options"]["text"], completed)
    for name in selected:
        assert bindings[name + "ResourceId"] in notice
    assert "재선택하지 않음" in notice


def test_legacy_completion_uses_old_name_tags_and_defaults_modes_to_new() -> None:
    state = foundation()
    tags = save_tags(state, bindings=False)
    options = {"useMicrosoftManagedAgentData": True}
    tags.update({f"azbrief.v2.options{i}": json.dumps(options) if i == 0 else "" for i in range(8)})
    completed = complete(state, tags)
    for name in build_v2_ui.SHARED_RESOURCES:
        assert output(name + "Mode", completed) == "new"
        selector = "existing" + name[0].upper() + name[1:]
        assert output(selector + "ResourceId", completed) == ""
    for name, tag in NAME_TAGS.items():
        assert output(name, completed) == tags["azbrief.v2." + tag]
    assert output("portalValidationPassed", completed) is True


@pytest.mark.parametrize("container", ["stateContainerName", "archiveContainerName"])
@pytest.mark.parametrize("shared", [False, True])
def test_saved_storage_custom_container_bindings_require_shared_mode(
    container: str, shared: bool
) -> None:
    state = foundation()
    if shared:
        select_existing(state, "storageAccount")
    bindings = binding_values(state)
    tags = save_tags(state)
    bindings[container] = "another-workload"
    set_resource_tags(tags, bindings)
    assert output("portalValidationPassed", complete(state, tags)) is shared


def saved_binding_valid(state: UiState, tags: dict[str, str], surface: str) -> bool:
    if surface == "infrastructure":
        return output("portalValidationPassed", complete(state, tags))
    application_tags = {**tags, "azbrief.v2.state": "ready"}
    application = UiState(
        values={
            "application": {
                "resourceGroupApi": {"tags": application_tags},
                "deploymentAccepted": True,
                "containerAppsApi": {"value": []},
                "jobsApi": {"value": []},
            }
        },
        network={},
        group={"mode": "Existing", "name": GROUP},
    )
    return evaluate(definition("application")["outputs"]["portalValidationPassed"], application)


@pytest.mark.parametrize("surface", ["infrastructure", "application"])
@pytest.mark.parametrize(
    "state_name,archive_name",
    [
        ("team-prod-state-v3", "team-prod-archive-v3"),
        ("a" * 63, "b" * 63),
        ("123", "456"),
    ],
)
def test_saved_shared_storage_accepts_valid_custom_cli_container_bindings(
    surface: str, state_name: str, archive_name: str
) -> None:
    state = foundation()
    select_existing(state, "storageAccount")
    bindings = {
        **binding_values(state),
        "stateContainerName": state_name,
        "archiveContainerName": archive_name,
    }
    tags = save_tags(state)
    set_resource_tags(tags, bindings)
    assert saved_binding_valid(state, tags, surface) is True


@pytest.mark.parametrize("surface", ["infrastructure", "application"])
@pytest.mark.parametrize("field", ["stateContainerName", "archiveContainerName"])
@pytest.mark.parametrize(
    "invalid",
    [
        None,
        "",
        "ab",
        "a" * 64,
        "-state",
        "state-",
        "state--v2",
        "State",
        "state_v2",
        "state.v2",
        "state/v2",
        "state v2",
        "상태리소스",
        "azbrief-state",
        "azbrief-archive",
    ],
)
def test_saved_shared_storage_rejects_invalid_or_legacy_fixed_container_names(
    surface: str, field: str, invalid: str | None
) -> None:
    state = foundation()
    select_existing(state, "storageAccount")
    bindings = {**binding_values(state), field: invalid}
    tags = save_tags(state)
    set_resource_tags(tags, bindings)
    assert saved_binding_valid(state, tags, surface) is False


@pytest.mark.parametrize("surface", ["infrastructure", "application"])
def test_saved_shared_storage_requires_distinct_container_names(surface: str) -> None:
    state = foundation()
    select_existing(state, "storageAccount")
    bindings = {
        **binding_values(state),
        "stateContainerName": "same-container",
        "archiveContainerName": "same-container",
    }
    tags = save_tags(state)
    set_resource_tags(tags, bindings)
    assert saved_binding_valid(state, tags, surface) is False


@pytest.mark.parametrize("missing", build_v2_ui.RESOURCE_BINDING_KEYS)
def test_completion_rejects_incomplete_binding_objects(missing: str) -> None:
    state = foundation()
    select_existing(state, "foundryAccount")
    tags = save_tags(state)
    bindings = binding_values(state)
    bindings.pop(missing)
    set_resource_tags(tags, bindings)
    assert output("portalValidationPassed", complete(state, tags)) is False


@pytest.mark.parametrize(
    "failure",
    [
        "all_missing",
        "first_missing",
        "middle_missing",
        "last_missing",
        "empty_chunk",
        "oversized_chunk",
        "unknown_key",
        "name_mismatch",
        "foreign_subscription",
        "wrong_type",
        "unknown_mode",
    ],
)
def test_completion_rejects_missing_partial_or_redirected_saved_bindings(failure: str) -> None:
    state = foundation()
    select_existing(state, "foundryAccount")
    tags = save_tags(state)
    keys = [key for key in tags if key.startswith("azbrief.v2.resources")]
    if failure == "all_missing":
        for key in keys:
            tags.pop(key)
    elif failure in {"first_missing", "middle_missing", "last_missing"}:
        tags.pop(keys[{"first_missing": 0, "middle_missing": 1, "last_missing": -1}[failure]])
    elif failure == "empty_chunk":
        tags[keys[1]] = ""
    elif failure == "oversized_chunk":
        tags[keys[0]] += " "
    elif failure == "unknown_mode":
        encoded = "".join(tags[f"azbrief.v2.options{i}"] for i in range(8))
        encoded = encoded.replace(
            '"foundryAccountMode":"existing"', '"foundryAccountMode":"invalid"'
        )
        tags.update({f"azbrief.v2.options{i}": encoded[i * 256 : (i + 1) * 256] for i in range(8)})
    else:
        bindings = binding_values(state)
        if failure == "unknown_key":
            bindings["unexpected"] = "not allowed"
        else:
            resource_id = bindings["foundryAccountResourceId"]
            bindings["foundryAccountResourceId"] = (
                resource_id + "-other"
                if failure == "name_mismatch"
                else (
                    resource_id.replace(SUBSCRIPTION, OTHER_SUBSCRIPTION)
                    if failure == "foreign_subscription"
                    else resource_id.replace(
                        "Microsoft.CognitiveServices/accounts", "Microsoft.Storage/storageAccounts"
                    )
                )
            )
        set_resource_tags(tags, bindings)
    assert output("portalValidationPassed", complete(state, tags)) is False


@pytest.mark.parametrize("resource_group", ["r", "RG-" + "x" * 80])
def test_tag_budget_counts_actual_variable_resource_chunks(resource_group: str) -> None:
    state = foundation()
    state.group.update(name=resource_group, mode="Existing")
    text = json.dumps(binding_values(state), separators=(",", ":"))
    chunk_count = (len(text) + 255) // 256
    assert 1 < chunk_count < 8
    available = 50 - 41 - chunk_count
    tags = {f"customer{i}": "keep" for i in range(available)}
    state.values["contract"]["resourceGroupApi"] = {"tags": tags}
    assert output("portalValidationPassed", state) is True
    tags["one-too-many"] = "keep"
    assert output("portalValidationPassed", state) is False


def test_tag_budget_preserves_customer_keys_that_resemble_internal_projection_names() -> None:
    state = foundation()
    state.group["mode"] = "Existing"
    count = (len(json.dumps(binding_values(state), separators=(",", ":"))) + 255) // 256
    tags = {"azbrief_v2_storage": "customer-owned"}
    tags.update({f"customer{i}": "keep" for i in range(50 - 41 - count - 1)})
    state.values["contract"]["resourceGroupApi"] = {"tags": tags}
    assert output("portalValidationPassed", state) is True
    tags["one-too-many"] = "keep"
    assert output("portalValidationPassed", state) is False
    assert tags["azbrief_v2_storage"] == "customer-owned"


def test_scoped_tag_projection_does_not_rewrite_options_or_shadow_canonical_keys() -> None:
    state = foundation()
    options = {"tags": {"azbrief.v2.schema": "retain", "azbrief_v2_storage": "retain"}}
    state.values["contract"]["resourceGroupApi"] = {
        "tags": {
            "azbrief.v2.options0": json.dumps(options, separators=(",", ":")),
            "azbrief_v2_options0": '{"shadow":"must not win"}',
        }
    }
    expression = "[" + build_v2_ui._shared_context("saved.options") + "]"
    assert evaluate(expression, state) == options


@pytest.mark.parametrize("eager", [False, True])
def test_empty_controls_and_initial_responses_evaluate_safely(eager: bool) -> None:
    state = UiState(values={"contract": {"resourceGroupApi": None}, "options": {}}, network={})
    for expression in definition()["outputs"].values():
        evaluate(expression, state, eager)
    assert output("portalValidationPassed", state, eager) is False
    for step in definition()["steps"]:
        for element in step["elements"]:
            for key in ("visible", "defaultValue"):
                expression = element.get(key)
                if isinstance(expression, str) and expression.startswith("["):
                    evaluate(expression, state, eager)
            for expression in (
                element.get("request", {}).get("path"),
                element.get("constraints", {}).get("allowedValues"),
                element.get("options", {}).get("text"),
            ):
                if isinstance(expression, str) and expression.startswith("["):
                    if (
                        element["name"] == "virtualNetworkApi"
                        and expression == element["request"]["path"]
                    ):
                        assert expression == (
                            "[concat(steps('network').virtualNetwork.id, '?api-version=2024-05-01')]"
                        )
                        with pytest.raises(AssertionError, match="concat must use strings"):
                            evaluate(expression, state, eager)
                    else:
                        evaluate(expression, state, eager)
            for rule in element.get("constraints", {}).get("validations", []):
                evaluate(rule["isValid"], state, eager)


def test_initial_all_new_guard_needs_no_optional_subscription_fixture() -> None:
    state = foundation()
    state.values.pop("options")
    assert (
        _evaluate_ui_expression(
            definition()["outputs"]["portalValidationPassed"],
            state.values,
            network=state.network,
            resource_group=state.group,
            basics={"infrastructureStage": "foundation"},
            eager_conditionals=True,
        )
        is True
    )


def test_app_image_keeps_ready_only_guards_and_validates_saved_binding_chunks() -> None:
    state = foundation()
    select_existing(state, "communicationServices")
    tags = save_tags(state)
    tags["azbrief.v2.state"] = "ready"
    application = UiState(
        values={
            "application": {
                "resourceGroupApi": {"tags": tags},
                "deploymentAccepted": True,
                "containerAppsApi": {"value": []},
                "jobsApi": {"value": []},
            }
        },
        network={},
        group={"mode": "Existing", "name": GROUP},
    )
    expression = definition("application")["outputs"]["portalValidationPassed"]
    assert evaluate(expression, application) is True
    for role in ("Core", "Simple"):
        assert (
            _evaluate_ui_expression(
                definition()["outputs"][f"reuse{role}Model"],
                state.values,
                network=state.network,
                resource_group=state.group,
                basics={"infrastructureStage": "foundation"},
                eager_conditionals=True,
            )
            is False
        )
    rules = controls("application")["containerImage"]["constraints"]["validations"]
    assert all(evaluate(rule["isValid"], application) for rule in rules)
    tags.pop("azbrief.v2.resources1")
    assert evaluate(expression, application) is False
    assert evaluate(rules[-1]["isValid"], application) is False
    tags["azbrief.v2.state"] = "mcp_pending"
    assert evaluate(rules[1]["isValid"], application) is False
    for name in ("infrastructure", "application"):
        template = json.loads((ROOT / "infra" / "v2" / f"{name}.json").read_text(encoding="utf-8"))
        assert template["parameters"]["portalValidationPassed"]["allowedValues"] == [True]
        assert set(definition(name)["outputs"]) <= set(template["parameters"])


@pytest.mark.parametrize("name", tuple(build_v2_ui.SHARED_RESOURCES))
def test_app_shared_mode_without_id_chunks_never_uses_legacy_same_group_fallback(name: str) -> None:
    state = foundation()
    select_existing(state, name)
    tags = {**save_tags(state, bindings=False), "azbrief.v2.state": "ready"}
    application = UiState(
        values={
            "application": {
                "resourceGroupApi": {"tags": tags},
                "deploymentAccepted": True,
                "containerAppsApi": {"value": []},
                "jobsApi": {"value": []},
            }
        },
        network={},
        group={"mode": "Existing", "name": GROUP},
    )
    assert (
        evaluate(definition("application")["outputs"]["portalValidationPassed"], application)
        is False
    )
    assert (
        evaluate(
            controls("application")["containerImage"]["constraints"]["validations"][-1]["isValid"],
            application,
        )
        is False
    )


def test_generator_is_idempotent_and_preserves_supported_functions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    legacy = json.loads(
        (ROOT / "infra" / "kt" / "createUiDefinition.json").read_text(encoding="utf-8")
    )
    validations = [
        rule
        for step in legacy["parameters"]["steps"]
        for element in step["elements"]
        for rule in element.get("constraints", {}).get("validations", [])
        if rule.get("isValid", "").startswith(
            (
                "[not(empty(filter(coalesce(steps('network')",
                "[and(not(equals(coalesce(steps('network')",
                "[not(or(equals(toLower(coalesce(steps('network')",
            )
        )
    ]
    assert len(validations) == 5
    for name in ("infrastructure", "application"):
        document = json.loads(
            (ROOT / "infra" / "v2" / f"{name}.createUiDefinition.json").read_text(encoding="utf-8")
        )
        once = build_v2_ui.normalize_ui(document, validations)
        assert once == document
        assert build_v2_ui.normalize_ui(once, validations) == once
        serialized = json.dumps(once)
        for unsupported in (
            "isValidJson(",
            "tryParse(",
            "createObject(",
            "createArray(",
            "listSecrets(",
        ):
            assert unsupported not in serialized
        assert "tags['azbrief.v2." not in serialized
        assert not any(name.endswith("Guard") for name in controls(name))
    monkeypatch.setattr("sys.argv", ["build_v2_ui", "--check"])
    assert build_v2_ui.main() == 0
