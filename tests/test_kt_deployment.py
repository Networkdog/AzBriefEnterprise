"""Offline contracts for the KT private infrastructure and existing-network preflight."""

import copy
import json
import re
import subprocess
from itertools import combinations
from pathlib import Path
from typing import Any

import pytest

from scripts import deploy_kt as kt

SUBSCRIPTION = "11111111-1111-1111-1111-111111111111"
TENANT = "22222222-2222-2222-2222-222222222222"
GROUP = f"/subscriptions/{SUBSCRIPTION}/resourceGroups/rg-kt-test"
VNET = f"/subscriptions/{SUBSCRIPTION}/resourceGroups/rg-network/providers/Microsoft.Network/virtualNetworks/vnet-kt"
RESOURCE_NAME_DEFAULTS = {
    "foundryAccountName": "ai-azbrief-kt",
    "agentStorageAccountName": "stazbriefktagent",
    "stateStorageAccountName": "stazbriefktstate",
    "cosmosAccountName": "cosmos-azbrief-kt",
    "searchServiceName": "srch-azbrief-kt",
    "projectName": "azbrief-kt",
    "containerAppsEnvironmentName": "cae-azbrief-kt",
    "containerAppName": "ca-azbrief-kt",
}
SUBNET_NAMES = {
    "peSubnetName": "snet-private-endpoints",
    "foundrySubnetName": "snet-foundry-agent",
    "containerAppsSubnetName": "snet-container-apps",
}


@pytest.fixture(scope="module")
def template() -> dict:
    return json.loads(kt.TEMPLATE.read_text(encoding="utf-8"))


@pytest.fixture
def values(template: dict) -> dict:
    result = {
        name: copy.deepcopy(spec["defaultValue"])
        for name, spec in template["parameters"].items()
        if "defaultValue" in spec
    }
    result.update(
        location="eastus2",
        virtualNetworkResourceGroupName="rg-network",
        virtualNetworkName="vnet-kt",
        **SUBNET_NAMES,
        foundryAccountName="ai-kt-test",
        agentStorageAccountName="stktagenttest",
        stateStorageAccountName="stktstatetest",
        cosmosAccountName="cosmos-kt-test",
        searchServiceName="search-kt-test",
        peSubnetAddressPrefix="10.70.0.0/28",
        foundrySubnetAddressPrefix="10.70.0.32/27",
        containerAppsSubnetAddressPrefix="10.70.0.64/27",
    )
    return result


@pytest.fixture
def vnet() -> dict:
    return {
        "id": VNET,
        "location": "eastus2",
        "properties": {"addressSpace": {"addressPrefixes": ["10.70.0.0/16"]}, "subnets": []},
    }


def _subnet(name: str, prefix: str, delegated: bool = False) -> dict:
    return {
        "name": name,
        "id": f"{VNET}/subnets/{name}",
        "properties": {
            "addressPrefix": prefix,
            "delegations": (
                [{"properties": {"serviceName": "Microsoft.App/environments"}}] if delegated else []
            ),
            "networkSecurityGroup": {"id": "/existing/nsg"},
            "routeTable": {"id": "/existing/route-table"},
            "privateEndpointNetworkPolicies": "Enabled",
        },
    }


def _resources(document: dict) -> list[dict]:
    resources = document.get("resources", {})
    items = list(resources.values()) if isinstance(resources, dict) else resources
    result = list(items)
    for resource in items:
        props = resource.get("properties", {})
        nested = props.get("template") if isinstance(props, dict) else None
        if nested:
            result.extend(_resources(nested))
    return result


def _params(template: dict, module: str) -> dict:
    return {
        key: value["value"] if isinstance(value, dict) else value
        for key, value in template["resources"][module]["properties"]["parameters"].items()
    }


@pytest.mark.parametrize(("parameter", "expected"), RESOURCE_NAME_DEFAULTS.items())
def test_new_resource_names_have_editable_literal_defaults(
    template: dict, parameter: str, expected: str
):
    definition = template["parameters"][parameter]
    assert definition["defaultValue"] == expected
    assert "allowedValues" not in definition
    if "StorageAccount" in parameter:
        assert re.fullmatch(r"[a-z0-9]{3,24}", expected)
    else:
        assert re.fullmatch(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*", expected)
        assert 3 <= len(expected) <= 32


def test_only_existing_network_parameters_remain_required(template: dict):
    for parameter in (
        "location",
        "virtualNetworkResourceGroupName",
        "virtualNetworkName",
        *SUBNET_NAMES,
    ):
        assert "defaultValue" not in template["parameters"][parameter]
    defaulted_name_parameters = {
        key
        for key, definition in template["parameters"].items()
        if key.endswith("Name")
        and not key.startswith("virtualNetwork")
        and "defaultValue" in definition
    }
    assert defaulted_name_parameters == set(RESOURCE_NAME_DEFAULTS)


def test_cli_resolves_prefilled_names_from_only_explicit_network_inputs(
    tmp_path: Path, values: dict
):
    parameters = {
        key: {"value": values[key]}
        for key in (
            "location",
            "virtualNetworkResourceGroupName",
            "virtualNetworkName",
            *SUBNET_NAMES,
        )
    }
    path = tmp_path / "parameters.json"
    path.write_text(json.dumps({"parameters": parameters}), encoding="utf-8")
    loaded = kt.load_parameters(path)
    assert {key: loaded[key] for key in RESOURCE_NAME_DEFAULTS} == RESOURCE_NAME_DEFAULTS
    assert {key: loaded[key] for key in SUBNET_NAMES} == SUBNET_NAMES
    for key in parameters:
        assert loaded[key] == values[key]
    assert [spec["name"] for spec in kt.endpoint_specs(loaded, GROUP)] == [
        "pe-ai-azbrief-kt",
        "pe-stazbriefktagent",
        "pe-stazbriefktstate",
        "pe-cosmos-azbrief-kt",
        "pe-srch-azbrief-kt",
        "pe-cae-azbrief-kt",
    ]


def test_cli_requires_explicit_subnet_role_names(tmp_path: Path, values: dict):
    parameters = {
        key: {"value": values[key]}
        for key in ("location", "virtualNetworkResourceGroupName", "virtualNetworkName")
    }
    path = tmp_path / "parameters.json"
    path.write_text(json.dumps({"parameters": parameters}), encoding="utf-8")
    with pytest.raises(ValueError, match="Missing required parameter: peSubnetName"):
        kt.load_parameters(path)


def test_cli_keeps_explicit_name_overrides(tmp_path: Path, values: dict):
    supplied = {
        key: {"value": value} for key, value in values.items() if key not in kt.INTERNAL_PARAMETERS
    }
    for key, default in RESOURCE_NAME_DEFAULTS.items():
        supplied[key] = {"value": default + "01"}
    supplied["peSubnetName"] = {"value": "private-endpoints-prod"}
    supplied["foundrySubnetName"] = {"value": "foundry-agents-prod"}
    supplied["containerAppsSubnetName"] = {"value": "container-apps-prod"}
    path = tmp_path / "parameters.json"
    path.write_text(json.dumps({"parameters": supplied}), encoding="utf-8")
    loaded = kt.load_parameters(path)
    assert {key: loaded[key] for key in RESOURCE_NAME_DEFAULTS} == {
        key: default + "01" for key, default in RESOURCE_NAME_DEFAULTS.items()
    }
    assert {key: loaded[key] for key in SUBNET_NAMES} == {
        "peSubnetName": "private-endpoints-prod",
        "foundrySubnetName": "foundry-agents-prod",
        "containerAppsSubnetName": "container-apps-prod",
    }


def test_example_prefills_the_same_resource_names():
    example = json.loads(
        (kt.TEMPLATE.parent / "main.parameters.example.json").read_text(encoding="utf-8")
    )["parameters"]
    assert {key: example[key]["value"] for key in RESOURCE_NAME_DEFAULTS} == RESOURCE_NAME_DEFAULTS
    assert example["virtualNetworkName"]["value"] == "<existing-vnet-name>"
    assert example["peSubnetName"]["value"] == "<existing-private-endpoint-subnet-name>"
    assert example["foundrySubnetName"]["value"] == "<existing-foundry-subnet-name>"
    assert example["containerAppsSubnetName"]["value"] == "<existing-container-apps-subnet-name>"


@pytest.fixture(scope="module")
def wizard() -> dict:
    return json.loads((kt.TEMPLATE.parent / "createUiDefinition.json").read_text(encoding="utf-8"))[
        "parameters"
    ]


def _ui_controls(wizard: dict, step: str) -> dict[str, dict]:
    return {
        control["name"]: control
        for section in wizard["steps"]
        if section["name"] == step
        for control in section["elements"]
    }


def _ui_regex(control: dict) -> str:
    constraints = control["constraints"]
    return constraints.get("regex") or next(
        rule["regex"] for rule in constraints["validations"] if "regex" in rule
    )


def test_kt_wizard_has_distinct_steps_and_matching_outputs(wizard: dict, template: dict):
    assert [step["name"] for step in wizard["steps"]] == ["network", "names", "options", "review"]
    outputs = wizard["outputs"]
    assert set(outputs) <= set(template["parameters"])
    required = {name for name, spec in template["parameters"].items() if "defaultValue" not in spec}
    assert required <= set(outputs)
    assert outputs["location"] == "[steps('network').virtualNetwork.location]"
    assert outputs["virtualNetworkName"] == "[steps('network').virtualNetwork.name]"
    assert outputs["virtualNetworkResourceGroupName"] == (
        "[first(skip(split(steps('network').virtualNetwork.id, '/'), 4))]"
    )
    for parameter in SUBNET_NAMES:
        assert outputs[parameter] == f"[steps('network').{parameter}]"
    for parameter in RESOURCE_NAME_DEFAULTS:
        assert outputs[parameter] == f"[steps('names').{parameter}]"
    assert "bootstrapImage" not in outputs
    assert not any("secret" in key.lower() for key in outputs)


def test_wizard_only_selects_existing_same_region_network_and_reads_subnets(wizard: dict):
    controls = _ui_controls(wizard, "network")
    order = [
        control["name"]
        for step in wizard["steps"]
        if step["name"] == "network"
        for control in step["elements"]
    ]
    selector = controls["virtualNetwork"]
    assert selector["type"] == "Microsoft.Solutions.ResourceSelector"
    assert selector["resourceType"] == "Microsoft.Network/virtualNetworks"
    assert selector["options"]["filter"] == {"subscription": "onBasics", "location": "onBasics"}
    assert "defaultValue" not in selector
    assert controls["virtualNetworkApi"]["request"] == {
        "method": "GET",
        "path": "[concat(steps('network').virtualNetwork.id, '?api-version=2024-05-01')]",
    }
    for name_control, flag, maximum_prefix in (
        ("peSubnetName", "createPESubnet", 28),
        ("foundrySubnetName", "createFoundrySubnet", 27),
        ("containerAppsSubnetName", "createContainerAppsSubnet", 27),
    ):
        subnet_selector = controls[name_control]
        assert subnet_selector["type"] == "Microsoft.Common.DropDown"
        assert subnet_selector["constraints"]["required"] is True
        assert subnet_selector["multiLine"] is True
        allowed = subnet_selector["constraints"]["allowedValues"]
        assert "virtualNetworkApi.properties.subnets" in allowed
        assert "s.name" in allowed
        assert '"description":"' in allowed
        assert f"lessOrEquals(int(last(split(s.prefix, '/'))), {maximum_prefix})" in allowed
        assert "startsWith(s.prefix, '10.')" in allowed
        assert "startsWith(s.prefix, '172.')" in allowed
        assert "startsWith(s.prefix, '192.168.')" in allowed
        assert allowed.count("(") == allowed.count(")")
        assert allowed.startswith("[map(") and allowed.endswith(")]")
        assert wizard["outputs"][name_control] == f"[steps('network').{name_control}]"
        assert wizard["outputs"][flag] is False
        assert order.index("virtualNetworkApi") < order.index(name_control)
    assert (
        "equals(s.delegationCount, 0)" in controls["peSubnetName"]["constraints"]["allowedValues"]
    )
    for name_control in ("foundrySubnetName", "containerAppsSubnetName"):
        assert (
            "equals(s.appDelegationCount, 1)"
            in controls[name_control]["constraints"]["allowedValues"]
        )
    assert (
        "not(equals(s.name, steps('network').foundrySubnetName))"
        in controls["containerAppsSubnetName"]["constraints"]["allowedValues"]
    )
    assert (
        "lessOrEquals(int(first(skip("
        in controls["containerAppsSubnetName"]["constraints"]["allowedValues"]
    )
    container_candidates = controls["containerAppsSubnetName"]["constraints"]["allowedValues"]
    for upper_octet, minimum_prefix in ((23, 13), (27, 14), (29, 15)):
        assert f"), {upper_octet})" in container_candidates
        assert f"), {minimum_prefix})" in container_candidates
    for removed in (
        "peSubnetApi",
        "pePrefix",
        "foundrySubnetApi",
        "foundryPrefix",
        "containerAppsSubnetApi",
        "containerAppsPrefix",
    ):
        assert removed not in controls
    serialized = json.dumps(wizard)
    for fixed_name in ("PESubnet", "FoundrySubnet", "ContainerAppsSubnet"):
        assert f"/subnets/{fixed_name}?api-version" not in serialized
    assert "Microsoft.Network.VirtualNetworkCombo" not in serialized
    assert "Microsoft.Common.PasswordBox" not in serialized


def test_wizard_name_defaults_and_validation_remain_editable(wizard: dict, template: dict):
    controls = _ui_controls(wizard, "names")
    for name, expected in RESOURCE_NAME_DEFAULTS.items():
        control = controls[name]
        assert control["type"] == "Microsoft.Common.TextBox"
        assert control["defaultValue"] == template["parameters"][name]["defaultValue"] == expected
        assert control["constraints"]["required"] is True
        assert re.fullmatch(_ui_regex(control), expected)
        assert re.fullmatch(_ui_regex(control), expected + "01")
        assert not re.fullmatch(_ui_regex(control), "bad name!")
    assert not re.fullmatch(_ui_regex(controls["agentStorageAccountName"]), "st-azbrief")
    assert not re.fullmatch(_ui_regex(controls["containerAppName"]), "ca--bad")
    uniqueness = controls["containerAppsEnvironmentName"]["constraints"]["validations"][1][
        "isValid"
    ]
    pairs = re.findall(r"equals\(steps\('names'\)\.(\w+), steps\('names'\)\.(\w+)\)", uniqueness)
    expected_names = [
        key for key in RESOURCE_NAME_DEFAULTS if key not in {"projectName", "containerAppName"}
    ]
    assert {frozenset(pair) for pair in pairs} == {
        frozenset(pair) for pair in combinations(expected_names, 2)
    }
    assert uniqueness.startswith("[not(or(")


def test_wizard_exposes_low_cost_dns_logs_and_explicit_stage(wizard: dict):
    options = _ui_controls(wizard, "options")
    review = _ui_controls(wizard, "review")
    outputs = wizard["outputs"]
    assert {v["value"] for v in options["searchSku"]["constraints"]["allowedValues"]} == {
        "basic",
        "standard",
    }
    assert review["deploymentStage"]["defaultValue"] == "1단계 — 기반·연결·권한만 배포"
    assert (
        outputs["deployCapabilityHost"] == "[equals(steps('review').deploymentStage, 'complete')]"
    )
    assert review["hostReady"]["visible"] == outputs["deployCapabilityHost"]
    for name in ("hostReady", "networkReady", "ownershipReady", "scopeAccepted"):
        assert review[name]["constraints"]["required"] is True
    assert options["workspaceId"]["visible"] == "[steps('options').collectLogs]"
    assert outputs["logAnalyticsWorkspaceResourceId"] == (
        "[if(steps('options').collectLogs, steps('options').workspaceId, '')]"
    )
    dns = outputs["existingPrivateDnsZoneIds"]
    for zone in (
        "cognitiveservices.azure.com",
        "openai.azure.com",
        "services.ai.azure.com",
        "blob.core.windows.net",
        "documents.azure.com",
        "search.windows.net",
    ):
        assert f'"privatelink.{zone}"' in dns
        assert f"/providers/Microsoft.Network/privateDnsZones/privatelink.{zone}" in dns
    assert ".azurecontainerapps.io" in dns
    assert "steps('network').virtualNetwork.location" in dns
    assert dns.startswith("[if(equals(steps('options').dnsMode, 'existing'), parse(concat(")
    assert dns.endswith("parse('{}'))]")
    assert outputs["searchSku"] == "[steps('options').searchSku]"


def test_managed_environment_omits_app_logs_when_workspace_is_not_selected(template: dict):
    app_logs = _params(template, "containerEnvironment")["appLogsConfiguration"]
    assert "createObject('value', null())" in app_logs
    assert "'log-analytics'" in app_logs
    assert "'none'" not in app_logs


def test_wizard_step_references_resolve_and_hidden_fields_do_not_escape(wizard: dict):
    steps = {
        step["name"]: {control["name"] for control in step["elements"]} for step in wizard["steps"]
    }
    for step, control in re.findall(r"steps\('(\w+)'\)\.(\w+)", json.dumps(wizard)):
        assert step in steps and control in steps[step]
    assert not {"pePrefix", "foundryPrefix", "containerAppsPrefix"} & set(wizard["outputs"])


def test_compiled_template_has_no_vnet_or_insights_and_fits_arm_limit(template: dict):
    resources = _resources(template)
    types = {
        resource["type"].casefold()
        for resource in resources
        if resource.get("existing") is not True
    }
    assert "microsoft.network/virtualnetworks" not in types
    assert "microsoft.insights/components" not in types
    assert "microsoft.operationalinsights/workspaces" not in types
    assert "microsoft.app/jobs" not in types
    assert kt.TEMPLATE.stat().st_size < 4 * 1024 * 1024
    assert template["outputs"]["ktFoundation"]["value"]["applicationReady"] is False
    assert "customerSetup" not in template["outputs"]
    references = [
        r for r in resources if r["type"].casefold() == "microsoft.network/virtualnetworks"
    ]
    assert len(references) == 3
    assert all(r["existing"] is True and "properties" not in r for r in references)


def test_minimum_subnets_are_conditional_child_modules(template: dict):
    for module, flag, name_parameter in (
        ("peSubnet", "createPESubnet", "peSubnetName"),
        ("foundrySubnet", "createFoundrySubnet", "foundrySubnetName"),
        ("containerAppsSubnet", "createContainerAppsSubnet", "containerAppsSubnetName"),
    ):
        definition = template["resources"][module]
        assert definition["condition"] == f"[parameters('{flag}')]"
        assert template["parameters"][flag]["defaultValue"] is False
        assert _params(template, module)["name"] == f"[parameters('{name_parameter}')]"
        assert definition["resourceGroup"] == "[parameters('virtualNetworkResourceGroupName')]"
        if module != "peSubnet":
            assert _params(template, module)["delegation"] == "Microsoft.App/environments"
    assert "peSubnet" in template["resources"]["foundrySubnet"]["dependsOn"]
    assert "foundrySubnet" in template["resources"]["containerAppsSubnet"]["dependsOn"]
    for variable, parameter in (
        ("peSubnetId", "peSubnetName"),
        ("foundrySubnetId", "foundrySubnetName"),
        ("containerAppsSubnetId", "containerAppsSubnetName"),
    ):
        assert f"parameters('{parameter}')" in template["variables"][variable]


def test_foundry_standard_setup_and_connection_roles(template: dict):
    foundry = _params(template, "foundry")
    assert foundry["publicNetworkAccess"] == "Disabled"
    assert foundry["disableLocalAuth"] is True
    assert foundry["networkInjections"] == {
        "scenario": "agent",
        "subnetResourceId": "[variables('foundrySubnetId')]",
        "useMicrosoftManagedNetwork": False,
    }
    bindings = template["resources"]["agentBindings"]["properties"]["template"]
    connections = [
        resource
        for resource in bindings["resources"]
        if resource["type"] == "Microsoft.CognitiveServices/accounts/projects/connections"
    ]
    assert [resource["properties"]["category"] for resource in connections] == [
        "AzureStorageAccount",
        "CosmosDb",
        "CognitiveSearch",
    ]
    for resource in connections:
        props = resource["properties"]
        assert props["authType"] == "AAD"
        assert "ResourceId" in props["metadata"]
    host_template = template["resources"]["capabilityHost"]["properties"]["template"]
    host, blob_role, cosmos_role = host_template["resources"]
    assert host["properties"] == {
        "capabilityHostKind": "Agents",
        "storageConnections": ["agent-storage"],
        "threadStorageConnections": ["agent-cosmos"],
        "vectorStoreConnections": ["agent-search"],
    }
    assert "agentBindings" in template["resources"]["capabilityHost"]["dependsOn"]
    for role in (blob_role, cosmos_role):
        assert any("/capabilityHosts" in dependency for dependency in role["dependsOn"])
    assert "enterprise_memory" in cosmos_role["properties"]["scope"]
    assert not any(
        resource["type"].casefold() == "microsoft.cognitiveservices/accounts/capabilityhosts"
        for resource in _resources(template)
    )


def test_backing_stores_are_private_low_cost_and_isolated(template: dict):
    for name in ("agentStorage", "stateStorage"):
        params = _params(template, name)
        assert params["skuName"] == "Standard_LRS"
        assert params["publicNetworkAccess"] == "Disabled"
        assert params["networkAcls"] == {"defaultAction": "Deny", "bypass": "None"}
        assert params["allowBlobPublicAccess"] is False
        assert params["allowSharedKeyAccess"] is False
    assert _params(template, "cosmos")["capacityMode"] == "Serverless"
    assert _params(template, "cosmos")["networkRestrictions"]["publicNetworkAccess"] == "Disabled"
    assert len(_params(template, "cosmos")["failoverLocations"]) == 1
    search = _params(template, "search")
    assert template["parameters"]["searchSku"]["defaultValue"] == "basic"
    assert search["replicaCount"] == search["partitionCount"] == 1
    assert search["publicNetworkAccess"] == "Disabled"
    role = template["resources"]["controlPlaneStorageRole"]
    assert "stateStorageAccountName" in role["scope"]
    bindings = template["resources"]["agentBindings"]["properties"]["template"]
    assert "stateStorageAccountName" not in json.dumps(bindings)


def test_container_app_is_private_minimum_scale_to_zero_bootstrap(template: dict):
    environment = _params(template, "containerEnvironment")
    assert environment["publicNetworkAccess"] == "Disabled"
    assert environment["infrastructureSubnetResourceId"] == "[variables('containerAppsSubnetId')]"
    assert environment["workloadProfiles"] == [
        {"name": "Consumption", "workloadProfileType": "Consumption"}
    ]
    assert environment["zoneRedundant"] is False
    app = _params(template, "containerApp")
    assert app["scaleSettings"] == {"minReplicas": 0, "maxReplicas": 1}
    assert app["ingressExternal"] is True
    assert app["ingressTargetPort"] == 80
    assert app["ingressAllowInsecure"] is False
    assert app["containers"][0]["resources"] == {"cpu": "[json('0.25')]", "memory": "0.5Gi"}
    assert "secrets" not in app
    assert app["containers"][0]["name"] == "bootstrap"
    for resource in template["resources"].values():
        parameters = resource.get("properties", {}).get("parameters", {})
        if "enableTelemetry" in parameters:
            assert parameters["enableTelemetry"]["value"] is False


def test_all_endpoint_groups_and_dns_cover_the_minimum_ip_budget(template: dict, values: dict):
    specs = template["variables"]["endpointSpecs"]
    assert [spec["groupId"] for spec in specs] == [
        "account",
        "blob",
        "blob",
        "Sql",
        "searchService",
        "managedEnvironments",
    ]
    assert sum(spec["ips"] for spec in kt.endpoint_specs(values, GROUP)) == 9
    assert _params(template, "privateEndpoints")["subnetResourceId"] == "[variables('peSubnetId')]"
    assert len(template["variables"]["dnsZoneNames"]) == 7
    assert template["resources"]["privateEndpoints"]["copy"]["batchSize"] == 1
    assert "existingPrivateDnsZoneIds" in template["resources"]["dnsZones"]["condition"]


def test_new_subnets_use_exact_example_minima(values: dict, vnet: dict):
    original = copy.deepcopy(vnet)
    plans = kt.plan_subnets(values, vnet)
    assert [plan.network.prefixlen for plan in plans] == [28, 27, 27]
    assert [plan.name for plan in plans] == [
        "snet-private-endpoints",
        "snet-foundry-agent",
        "snet-container-apps",
    ]
    assert all(plan.create for plan in plans)
    assert vnet == original


def test_existing_subnets_preserve_nsg_routes_policies_and_larger_sizes(values: dict, vnet: dict):
    vnet["properties"]["subnets"] = [
        _subnet(values["peSubnetName"], "10.70.1.0/24"),
        _subnet(values["foundrySubnetName"], "10.70.2.0/24", True),
        _subnet(values["containerAppsSubnetName"], "10.70.3.0/24", True),
    ]
    for _, _, parameter, _, _ in kt.SUBNETS:
        values[parameter] = ""
    original = copy.deepcopy(vnet)
    plans = kt.plan_subnets(values, vnet)
    assert not any(plan.create for plan in plans)
    assert [plan.name for plan in plans] == [
        "snet-private-endpoints",
        "snet-foundry-agent",
        "snet-container-apps",
    ]
    assert vnet == original


@pytest.mark.parametrize(
    ("parameter", "value", "message"),
    [
        ("peSubnetAddressPrefix", "", "Supply peSubnet"),
        ("peSubnetAddressPrefix", "10.70.0.0/29", "/28 or larger"),
        ("foundrySubnetAddressPrefix", "10.70.0.32/28", "/27 or larger"),
        ("containerAppsSubnetAddressPrefix", "10.70.0.64/28", "/27 or larger"),
        ("foundrySubnetAddressPrefix", "10.70.0.0/27", "overlaps"),
        ("foundrySubnetAddressPrefix", "10.70.0.33/27", "host bits"),
        ("foundrySubnetAddressPrefix", "10.71.0.0/27", "outside"),
        ("foundrySubnetAddressPrefix", "8.8.8.0/27", "RFC1918"),
        ("foundrySubnetAddressPrefix", "fd00::/64", "IPv4"),
        ("location", "koreacentral", "same Azure region"),
    ],
)
def test_bad_network_inputs_fail_closed(
    values: dict, vnet: dict, parameter: str, value: str, message: str
):
    values[parameter] = value
    with pytest.raises(ValueError, match=message):
        kt.plan_subnets(values, vnet)


def test_existing_subnet_is_not_resized_or_redelegated(values: dict, vnet: dict):
    vnet["properties"]["subnets"] = [_subnet(values["foundrySubnetName"], "10.70.0.32/27")]
    with pytest.raises(ValueError, match="incompatible delegation"):
        kt.plan_subnets(values, vnet)
    vnet["properties"]["subnets"][0] = _subnet(values["foundrySubnetName"], "10.70.0.128/27", True)
    with pytest.raises(ValueError, match="will not be resized"):
        kt.plan_subnets(values, vnet)


def test_subnet_roles_require_distinct_names(values: dict, vnet: dict):
    values["containerAppsSubnetName"] = values["foundrySubnetName"].upper()
    with pytest.raises(ValueError, match="must be distinct"):
        kt.plan_subnets(values, vnet)


def test_unrelated_subnet_overlap_and_aca_reserved_space_are_rejected(values: dict, vnet: dict):
    vnet["properties"]["subnets"] = [_subnet("customer-workload", "10.70.0.0/25")]
    with pytest.raises(ValueError, match="overlaps"):
        kt.plan_subnets(values, vnet)
    vnet["properties"]["subnets"] = []
    vnet["properties"]["addressSpace"]["addressPrefixes"].append("172.30.0.0/16")
    values["containerAppsSubnetAddressPrefix"] = "172.30.0.0/27"
    with pytest.raises(ValueError, match="reserved range"):
        kt.plan_subnets(values, vnet)


class FakeAzure(kt.AzureCli):
    """Strict offline ARM fixture; never invokes Azure CLI."""

    def __init__(self, values: dict, vnet: dict):
        super().__init__(SUBSCRIPTION, TENANT, "rg-kt-test")
        self.values = copy.deepcopy(values)
        self.vnet = copy.deepcopy(vnet)
        self.resources: dict[str, dict] = {}
        self.calls: list[tuple[str, ...]] = []
        self.deployments: list[dict] = []
        self.free_ips = True
        self.stage = 0
        self.public_access = "Disabled"
        self.account_subscription = SUBSCRIPTION
        self.host_state = "Succeeded"
        self.dns_linked = True
        self.host_target_override = ""

    def json(self, *args: str) -> Any:
        self.calls.append(args)
        if args[:2] == ("account", "show"):
            return {"id": self.account_subscription, "tenantId": TENANT}
        if args[:2] == ("cloud", "show"):
            return {"name": "AzureCloud"}
        if args[:2] == ("group", "show"):
            return {"id": GROUP}
        if args[:2] == ("resource", "list"):
            return list(copy.deepcopy(self.resources).values())
        if args[:3] == ("network", "vnet", "check-ip-address"):
            return {"available": self.free_ips}
        raise AssertionError(f"Unexpected CLI call: {args}")

    def get(self, resource_id: str, api_version: str) -> dict:
        self.calls.append(("get", resource_id, api_version))
        if resource_id == VNET:
            return copy.deepcopy(self.vnet)
        for spec in kt.endpoint_specs(self.values, GROUP):
            if resource_id == spec["target"]:
                return {
                    "properties": {
                        "publicNetworkAccess": self.public_access,
                        "networkInjections": [
                            {
                                "scenario": "agent",
                                "subnetArmId": (
                                    f"{VNET}/subnets/{self.values['foundrySubnetName']}"
                                ),
                            }
                        ],
                        "vnetConfiguration": {
                            "infrastructureSubnetId": (
                                f"{VNET}/subnets/{self.values['containerAppsSubnetName']}"
                            )
                        },
                    }
                }
            if resource_id.endswith(f"/privateEndpoints/{spec['name']}"):
                return {
                    "properties": {
                        "subnet": {"id": f"{VNET}/subnets/{self.values['peSubnetName']}"},
                        "privateLinkServiceConnections": [
                            {
                                "properties": {
                                    "privateLinkServiceId": spec["target"],
                                    "groupIds": [spec["group"]],
                                    "privateLinkServiceConnectionState": {"status": "Approved"},
                                }
                            }
                        ],
                    }
                }
        raise AssertionError(f"Unexpected resource read: {resource_id}")

    def collection(self, resource_id: str, api_version: str) -> list[dict]:
        self.calls.append(("collection", resource_id, api_version))
        if resource_id.endswith("/virtualNetworkLinks"):
            return (
                [{"properties": {"virtualNetwork": {"id": VNET}, "registrationEnabled": False}}]
                if self.dns_linked
                else []
            )
        if resource_id.endswith("/projects"):
            return [{"name": self.values["projectName"]}] if self.stage else []
        if resource_id.endswith("/capabilityHosts"):
            if "/projects/" in resource_id and self.stage < 2:
                return []
            return [
                {
                    "name": "agents",
                    "properties": {
                        "provisioningState": self.host_state,
                        "storageConnections": ["agent-storage"],
                        "threadStorageConnections": ["agent-cosmos"],
                        "vectorStoreConnections": ["agent-search"],
                    },
                }
            ]
        if resource_id.endswith("/connections"):
            specs = kt.endpoint_specs(self.values, GROUP)
            return [
                {
                    "name": name,
                    "properties": {
                        "authType": "AAD",
                        "metadata": {
                            "ResourceId": self.host_target_override or specs[index]["target"]
                        },
                    },
                }
                for name, index in (("agent-storage", 1), ("agent-cosmos", 3), ("agent-search", 4))
            ]
        raise AssertionError(f"Unexpected collection read: {resource_id}")

    def deploy(self, values: dict, mode: str, name: str) -> dict:
        self.calls.append(("deploy", mode, name))
        self.deployments.append(copy.deepcopy(values))
        if mode == "create":
            self.stage += 1
            for role, name_parameter, prefix, create_flag, _ in kt.SUBNETS:
                if values[create_flag]:
                    self.vnet["properties"]["subnets"].append(
                        _subnet(
                            values[name_parameter],
                            values[prefix],
                            role != "private_endpoint",
                        )
                    )
            for spec in kt.endpoint_specs(values, GROUP):
                for resource_id in (
                    spec["target"],
                    f"{GROUP}/providers/Microsoft.Network/privateEndpoints/{spec['name']}",
                ):
                    self.resources[resource_id] = {
                        "id": resource_id,
                        "tags": {"deploymentProfile": kt.PROFILE},
                    }
        return {
            "properties": {
                "provisioningState": "Succeeded",
                "outputs": {"ktFoundation": {"value": {"applicationReady": False}}},
            }
        }


def test_preflight_never_mutates_azure(values: dict, vnet: dict):
    cli = FakeAzure(values, vnet)
    kt.run(cli, values, "preflight")
    assert not cli.deployments
    assert cli.vnet == vnet


def test_unknown_mode_never_falls_through_to_deploy(values: dict, vnet: dict):
    cli = FakeAzure(values, vnet)
    with pytest.raises(ValueError, match="Unsupported KT operation"):
        kt.run(cli, values, "deployment")
    assert not cli.calls
    assert not cli.deployments


def test_deploy_stages_reinventory_before_host_and_never_rewrite_subnets(values: dict, vnet: dict):
    cli = FakeAzure(values, vnet)
    kt.run(cli, values, "deploy")
    assert len(cli.deployments) == 2
    assert cli.deployments[0]["deployCapabilityHost"] is False
    assert cli.deployments[1]["deployCapabilityHost"] is True
    assert all(cli.deployments[0][row[3]] for row in kt.SUBNETS)
    assert not any(cli.deployments[1][row[3]] for row in kt.SUBNETS)
    assert len(cli.vnet["properties"]["subnets"]) == 3
    assert {subnet["name"] for subnet in cli.vnet["properties"]["subnets"]} == {
        "snet-private-endpoints",
        "snet-foundry-agent",
        "snet-container-apps",
    }
    assert values["existingPrivateDnsZoneIds"] == {}


def test_context_mismatch_fails_before_deployment(values: dict, vnet: dict):
    cli = FakeAzure(values, vnet)
    cli.account_subscription = "unapproved-subscription"
    with pytest.raises(ValueError, match="explicit KT target"):
        kt.run(cli, values, "deploy")
    assert not cli.deployments


def test_unowned_resource_and_busy_subnet_are_not_adopted(values: dict, vnet: dict):
    cli = FakeAzure(values, vnet)
    resource_id = kt.endpoint_specs(values, GROUP)[0]["target"]
    cli.resources[resource_id] = {"id": resource_id, "tags": {}}
    with pytest.raises(ValueError, match="not owned"):
        kt.run(cli, values, "deploy")
    assert not cli.deployments
    cli.resources = {}
    busy = _subnet(values["foundrySubnetName"], values["foundrySubnetAddressPrefix"], True)
    busy["properties"]["serviceAssociationLinks"] = [{"name": "other-account"}]
    cli.vnet["properties"]["subnets"].append(busy)
    with pytest.raises(ValueError, match="occupied"):
        kt.run(cli, values, "deploy")
    assert not cli.deployments


def test_existing_pe_subnet_requires_enough_actual_free_ips(values: dict, vnet: dict):
    cli = FakeAzure(values, vnet)
    cli.vnet["properties"]["subnets"].append(
        _subnet(values["peSubnetName"], values["peSubnetAddressPrefix"])
    )
    cli.free_ips = False
    with pytest.raises(ValueError, match="verify 9 free"):
        kt.run(cli, values, "deploy")
    assert not cli.deployments
    assert sum(call[:3] == ("network", "vnet", "check-ip-address") for call in cli.calls) == 11


def test_promoted_application_is_not_replaced_by_bootstrap(
    monkeypatch: pytest.MonkeyPatch, values: dict, vnet: dict
):
    cli = FakeAzure(values, vnet)
    app_id = f"{GROUP}/providers/Microsoft.App/containerApps/{values['containerAppName']}"
    cli.resources[app_id] = {"id": app_id, "tags": {"deploymentProfile": kt.PROFILE}}
    original_get = cli.get

    def get(resource_id: str, api_version: str) -> dict:
        if resource_id == app_id:
            return {
                "properties": {
                    "template": {"containers": [{"image": "customer/azbrief@sha256:approved"}]}
                }
            }
        return original_get(resource_id, api_version)

    monkeypatch.setattr(cli, "get", get)
    with pytest.raises(ValueError, match="cannot downgrade"):
        kt.run(cli, values, "deploy")
    assert not cli.deployments


def test_existing_dns_is_reused_without_relinking(values: dict, vnet: dict):
    zone = "privatelink.blob.core.windows.net"
    zone_id = f"{GROUP}/providers/Microsoft.Network/privateDnsZones/{zone}"
    cli = FakeAzure(values, vnet)
    cli.resources[zone_id] = {"id": zone_id}
    prepared = kt.prepare(cli, values)
    assert prepared["existingPrivateDnsZoneIds"] == {zone: zone_id}
    assert values["existingPrivateDnsZoneIds"] == {}
    cli.dns_linked = False
    with pytest.raises(ValueError, match="non-registration link"):
        kt.prepare(cli, values)
    assert not cli.deployments


@pytest.mark.parametrize("case_changed", [False, True])
def test_existing_host_is_not_redirected(values: dict, vnet: dict, case_changed: bool):
    cli = FakeAzure(values, vnet)
    kt.run(cli, values, "deploy")
    if case_changed:
        values["projectName"] = values["projectName"].upper()
    cli.host_target_override = "/wrong/backing/store"
    with pytest.raises(ValueError, match="Refusing to redirect"):
        kt.run(cli, values, "deploy")
    assert len(cli.deployments) == 2


def test_readback_rejects_public_network_access(values: dict, vnet: dict):
    cli = FakeAzure(values, vnet)
    cli.public_access = "Enabled"
    with pytest.raises(RuntimeError, match="not disabled"):
        kt.run(cli, values, "deploy")


def test_failed_account_host_stops_before_project_host(values: dict, vnet: dict):
    cli = FakeAzure(values, vnet)
    cli.host_state = "Failed"
    with pytest.raises(RuntimeError, match="provisioning failed"):
        kt.run(cli, values, "deploy")
    assert len(cli.deployments) == 1


def test_host_wait_is_bounded(monkeypatch: pytest.MonkeyPatch, values: dict, vnet: dict):
    cli = FakeAzure(values, vnet)
    cli.host_state = "Creating"
    sleeps: list[int] = []
    monkeypatch.setattr(kt.time, "sleep", sleeps.append)
    with pytest.raises(RuntimeError, match="did not become ready"):
        kt.wait_for_host(cli, kt.endpoint_specs(values, GROUP)[0]["target"])
    assert sleeps == [10] * 30


def test_load_parameters_rejects_internal_flags_and_shared_storage(tmp_path: Path, values: dict):
    path = tmp_path / "parameters.json"
    supplied = {
        name: {"value": value}
        for name, value in values.items()
        if name not in kt.INTERNAL_PARAMETERS
    }
    path.write_text(json.dumps({"parameters": supplied}), encoding="utf-8")
    assert kt.load_parameters(path)["location"] == values["location"]
    supplied["createPESubnet"] = {"value": True}
    path.write_text(json.dumps({"parameters": supplied}), encoding="utf-8")
    with pytest.raises(ValueError, match="reserved"):
        kt.load_parameters(path)
    del supplied["createPESubnet"]
    supplied["stateStorageAccountName"] = supplied["agentStorageAccountName"]
    path.write_text(json.dumps({"parameters": supplied}), encoding="utf-8")
    with pytest.raises(ValueError, match="separate accounts"):
        kt.load_parameters(path)


def test_cli_uses_incremental_json_what_if_and_removes_temporary_file(
    monkeypatch: pytest.MonkeyPatch, values: dict
):
    cli = kt.AzureCli(SUBSCRIPTION, TENANT, "rg-kt-test")
    calls: list[tuple[str, ...]] = []

    def capture(*args: str) -> dict:
        calls.append(args)
        path = Path(args[args.index("--parameters") + 1][1:])
        assert path.is_file()
        assert json.loads(path.read_text())["parameters"]["location"]["value"] == "eastus2"
        return {"status": "Succeeded"}

    monkeypatch.setattr(cli, "json", capture)
    cli.deploy(values, "what-if", "offline-test")
    args = calls[0]
    assert args[args.index("--mode") + 1] == "Incremental"
    assert "--no-pretty-print" in args
    assert not Path(args[args.index("--parameters") + 1][1:]).exists()


def test_cli_failures_are_not_success_shaped(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(kt.shutil, "which", lambda name: "az.cmd")
    monkeypatch.setattr(
        kt.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 1, "", "AuthorizationFailed"),
    )
    with pytest.raises(RuntimeError, match="AuthorizationFailed"):
        kt.AzureCli(SUBSCRIPTION, TENANT, "rg-kt-test").json("account", "show")


def test_arm_adapter_consumes_pages_and_rejects_external_continuations(
    monkeypatch: pytest.MonkeyPatch,
):
    cli = kt.AzureCli(SUBSCRIPTION, TENANT, "rg-kt-test")
    calls: list[tuple[str, ...]] = []
    next_link = f"{kt.MANAGEMENT}{GROUP}/resources?api-version=2021-04-01&next=2"

    def page(*args: str) -> dict:
        calls.append(args)
        if len(calls) == 1:
            return {"value": [{"name": "first"}], "nextLink": next_link}
        return {"value": [{"name": "second"}]}

    monkeypatch.setattr(cli, "json", page)
    assert cli.collection(f"{GROUP}/resources", "2021-04-01") == [
        {"name": "first"},
        {"name": "second"},
    ]
    assert calls[0] == (
        "rest",
        "--method",
        "get",
        "--url",
        f"{kt.MANAGEMENT}{GROUP}/resources?api-version=2021-04-01",
    )
    assert calls[1][-1] == next_link
    monkeypatch.setattr(
        cli, "json", lambda *args: {"value": [], "nextLink": "https://other.invalid/"}
    )
    with pytest.raises(ValueError, match="Unexpected ARM continuation"):
        cli.collection(f"{GROUP}/resources", "2021-04-01")
