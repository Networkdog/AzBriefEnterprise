"""Offline contracts for the KT private infrastructure and existing-network preflight."""

import ast
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
    "storageAccountName": "stazbriefkt",
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
        storageAccountName="stktsharedtest",
        cosmosAccountName="cosmos-kt-test",
        searchServiceName="search-kt-test",
        peSubnetAddressPrefix="10.70.0.0/28",
        foundrySubnetAddressPrefix="10.70.0.32/27",
        containerAppsSubnetAddressPrefix="10.70.0.64/27",
        apiKey="test-only-api-key-0123456789abcdef0123456789abcdef",
        containerRegistryAuthMode="Credentials",
        containerRegistryPassword="test-only-ghcr-read-token",
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
    if "storageaccount" in parameter.casefold():
        assert re.fullmatch(r"[a-z0-9]{3,24}", expected)
    else:
        assert re.fullmatch(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*", expected)
        assert 3 <= len(expected) <= 32


def test_network_and_key_reuse_decision_are_required(template: dict):
    for parameter in (
        "location",
        "virtualNetworkResourceGroupName",
        "virtualNetworkName",
        *SUBNET_NAMES,
        "reuseExistingApiKey",
    ):
        assert "defaultValue" not in template["parameters"][parameter]
    defaulted_name_parameters = {
        key
        for key, definition in template["parameters"].items()
        if key.endswith("Name")
        and not key.startswith("virtualNetwork")
        and "defaultValue" in definition
    }
    assert defaulted_name_parameters == set(RESOURCE_NAME_DEFAULTS) | {"foundryHostedAgentName"}


def test_cli_resolves_prefilled_names_from_network_and_authentication_inputs(
    tmp_path: Path, values: dict
):
    parameters = {
        key: {"value": values[key]}
        for key in (
            "location",
            "virtualNetworkResourceGroupName",
            "virtualNetworkName",
            *SUBNET_NAMES,
            "apiKey",
            "containerRegistryAuthMode",
            "containerRegistryPassword",
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
        "pe-stazbriefkt",
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
    assert example["deployContainerAppsPrivateEndpoint"]["value"] is False
    assert example["containerRegistryAuthMode"]["value"] == "Anonymous"
    assert not {"apiKey", "containerRegistryPassword", "containerRegistryUsername"} & example.keys()


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


def _evaluate_ui_expression(
    expression: str,
    names: dict[str, object],
    resource_group: dict[str, object] | None = None,
    subscription: dict[str, object] | None = None,
) -> object:
    """Evaluate the tested Portal expression subset with strict equality."""
    source = re.sub(r"\((\w+)\)\s*=>", r"lambda \1:", expression[1:-1])
    source = re.sub(r"\b(and|or|not|if)\(", r"ui_\1(", source)

    def evaluate(node: ast.expr, bindings: dict[str, object]) -> object:
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            return {"true": True, "false": False, "null": None, **bindings}[node.id]
        if isinstance(node, ast.Attribute):
            value = evaluate(node.value, bindings)
            if value is None:
                return None
            assert isinstance(value, dict)
            return value.get(node.attr)
        assert isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        function = node.func.id
        if function == "ui_if":
            condition = evaluate(node.args[0], bindings)
            assert isinstance(condition, bool)
            return evaluate(node.args[1] if condition else node.args[2], bindings)
        if function == "filter":
            rows = evaluate(node.args[0], bindings)
            predicate = node.args[1]
            assert isinstance(rows, list) and isinstance(predicate, ast.Lambda)
            assert len(predicate.args.args) == 1
            return [
                row
                for row in rows
                if evaluate(predicate.body, {**bindings, predicate.args.args[0].arg: row}) is True
            ]
        arguments = [evaluate(argument, bindings) for argument in node.args]
        if function == "resourceGroup":
            assert not arguments and resource_group is not None
            return resource_group
        if function == "subscription":
            assert not arguments and subscription is not None
            return subscription
        if function == "concat":
            assert all(isinstance(value, str) for value in arguments)
            return "".join(arguments)
        if function == "steps":
            assert arguments == ["names"]
            return names
        if function == "parse":
            assert arguments in (["[]"], ["{}"])
            return json.loads(arguments[0])
        if function == "coalesce":
            return next(value for value in arguments if value is not None)
        if function == "empty":
            if arguments == [None]:
                return True
            assert len(arguments) == 1 and isinstance(arguments[0], (list, dict, str))
            return len(arguments[0]) == 0
        if function == "contains":
            return arguments[1] in arguments[0]
        if function == "toLower":
            assert len(arguments) == 1 and isinstance(arguments[0], str)
            return arguments[0].lower()
        if function == "equals":
            assert len(arguments) == 2
            return type(arguments[0]) is type(arguments[1]) and arguments[0] == arguments[1]
        assert all(isinstance(value, bool) for value in arguments)
        if function == "ui_not":
            assert len(arguments) == 1
            return not arguments[0]
        if function in {"ui_and", "ui_or"}:
            assert len(arguments) >= 2
            return all(arguments) if function == "ui_and" else any(arguments)
        raise AssertionError(f"Unsupported UI guard function: {function}")

    return evaluate(ast.parse(source, mode="eval").body, {})


def _evaluate_ui_guard(expression: str, names: dict[str, object]) -> bool:
    result = _evaluate_ui_expression(expression, names)
    assert isinstance(result, bool)
    return result


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
    secure_outputs = {
        key for key in outputs if template["parameters"][key]["type"].casefold() == "securestring"
    }
    assert secure_outputs == {"containerRegistryPassword"}
    assert not secure_outputs & template["outputs"]["ktFoundation"]["value"].keys()


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
    dns_api = controls["linkedPrivateDnsZonesApi"]["request"]
    assert dns_api["method"] == "POST"
    assert dns_api["path"] == (
        "/providers/Microsoft.ResourceGraph/resources?api-version=2022-10-01"
    )
    assert dns_api["body"]["subscriptions"] == ["[subscription().subscriptionId]"]
    assert dns_api["body"]["options"] == {"resultFormat": "objectArray"}
    dns_query = dns_api["body"]["query"]
    assert "microsoft.network/privatednszones/virtualnetworklinks" in dns_query
    assert "steps('network').virtualNetwork.id" in dns_query
    assert "privatelink.search.windows.net" in dns_query
    assert "zoneName, zoneId, registrationEnabled" in dns_query
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


def test_wizard_name_defaults_and_validation_remain_editable(wizard: dict, template: dict):
    controls = _ui_controls(wizard, "names")
    environment_api = controls["containerAppsEnvironmentApi"]["request"]
    assert environment_api["method"] == "POST"
    assert environment_api["path"] == (
        "/providers/Microsoft.ResourceGraph/resources?api-version=2022-10-01"
    )
    assert environment_api["body"]["subscriptions"] == ["[subscription().subscriptionId]"]
    assert environment_api["body"]["options"] == {"resultFormat": "objectArray"}
    environment_query = environment_api["body"]["query"]
    assert "microsoft.app/managedenvironments" in environment_query
    assert "resourceGroup().name" in environment_query
    assert "internal=tostring(tobool(properties.vnetConfiguration.internal))" in environment_query
    for name, expected in RESOURCE_NAME_DEFAULTS.items():
        control = controls[name]
        assert control["type"] == "Microsoft.Common.TextBox"
        assert control["defaultValue"] == template["parameters"][name]["defaultValue"] == expected
        assert control["constraints"]["required"] is True
        assert re.fullmatch(_ui_regex(control), expected)
        assert re.fullmatch(_ui_regex(control), expected + "01")
        assert not re.fullmatch(_ui_regex(control), "bad name!")
    assert not re.fullmatch(_ui_regex(controls["storageAccountName"]), "st-azbrief")
    assert "agentStorageAccountName" not in controls
    assert "stateStorageAccountName" not in controls
    assert "계정 범위 권한" in controls["storageAccountName"]["toolTip"]
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
    existing_environment = controls["containerAppsEnvironmentName"]["constraints"]["validations"][2]
    assert "containerAppsEnvironmentApi.data" in existing_environment["isValid"]
    assert "first(" not in existing_environment["isValid"]
    assert existing_environment["isValid"].startswith("[empty(filter(")
    assert "not(equals(e.internal, 'true'))" in existing_environment["isValid"]
    assert "not(equals(e.publicNetworkAccess, 'Disabled'))" in existing_environment["isValid"]
    assert (
        "not(equals(e.deploymentProfile, 'kt-private-foundation'))"
        in existing_environment["isValid"]
    )
    assert "기존 Environment의 재사용 조건" in existing_environment["message"]
    assert (
        "이름이 같다는 이유만으로 삭제하거나 재생성하지 마십시오."
        in existing_environment["message"]
    )
    notice = controls["existingContainerAppsEnvironmentNotice"]
    assert notice["type"] == "Microsoft.Common.InfoBox"
    assert notice["options"]["icon"] == "Info"
    assert "containerAppsEnvironmentApi.data" in notice["visible"]
    assert "2단계에서 1단계 환경을 재사용하는 것은 정상입니다." in notice["options"]["text"]


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        pytest.param({}, True, id="same-stage-one-environment"),
        pytest.param({"internal": "false"}, False, id="external"),
        pytest.param({"internal": ""}, False, id="unknown-internal"),
        pytest.param({"internal": None}, False, id="null-internal"),
        pytest.param({"internal": 1}, False, id="raw-arg-number"),
        pytest.param({"internal": True}, False, id="raw-boolean"),
        pytest.param({"publicNetworkAccess": "Enabled"}, False, id="public-access"),
        pytest.param({"publicNetworkAccess": ""}, False, id="unknown-pna"),
        pytest.param({"deploymentProfile": "another-profile"}, False, id="other-owner"),
        pytest.param({"deploymentProfile": ""}, False, id="unknown-owner"),
    ],
)
def test_wizard_stage_two_environment_reuse(
    wizard: dict, overrides: dict[str, object], expected: bool
):
    name = RESOURCE_NAME_DEFAULTS["containerAppsEnvironmentName"]
    row: dict[str, object] = {
        "name": name,
        "internal": "true",
        "publicNetworkAccess": "Disabled",
        "deploymentProfile": "kt-private-foundation",
        **overrides,
    }
    controls = _ui_controls(wizard, "names")
    guard = controls["containerAppsEnvironmentName"]["constraints"]["validations"][2]["isValid"]
    names: dict[str, object] = {
        "containerAppsEnvironmentName": name,
        "containerAppsEnvironmentApi": {"data": [{"name": "cae-unrelated"}, row]},
    }
    assert "deploymentStage" not in guard
    assert _evaluate_ui_guard(guard, names) is expected
    assert (
        _evaluate_ui_guard(controls["existingContainerAppsEnvironmentNotice"]["visible"], names)
        is True
    )
    for field in ("internal", "publicNetworkAccess", "deploymentProfile"):
        incomplete = {key: value for key, value in row.items() if key != field}
        names["containerAppsEnvironmentApi"] = {"data": [incomplete]}
        assert _evaluate_ui_guard(guard, names) is False


@pytest.mark.parametrize("rows", [[], [{"name": "cae-unrelated", "internal": "false"}]])
def test_wizard_new_environment_name_is_not_blocked(wizard: dict, rows: list[dict[str, str]]):
    controls = _ui_controls(wizard, "names")
    names: dict[str, object] = {
        "containerAppsEnvironmentName": RESOURCE_NAME_DEFAULTS["containerAppsEnvironmentName"],
        "containerAppsEnvironmentApi": {"data": rows},
    }
    guard = controls["containerAppsEnvironmentName"]["constraints"]["validations"][2]["isValid"]
    assert _evaluate_ui_guard(guard, names) is True
    assert (
        _evaluate_ui_guard(controls["existingContainerAppsEnvironmentNotice"]["visible"], names)
        is False
    )


def test_ui_guard_equality_does_not_confuse_arg_numbers_with_booleans():
    assert _evaluate_ui_guard("[equals(1, true)]", {}) is False
    assert _evaluate_ui_guard("[equals('true', true)]", {}) is False
    assert _evaluate_ui_guard("[equals('true', 'true')]", {}) is True


def test_wizard_exposes_low_cost_dns_logs_and_explicit_stage(wizard: dict):
    options = _ui_controls(wizard, "options")
    review = _ui_controls(wizard, "review")
    outputs = wizard["outputs"]
    assert "Private Endpoint 네 개" in options["costNotice"]["options"]["text"]
    assert "선택 시 다섯 개" in options["costNotice"]["options"]["text"]
    assert options["deployContainerAppsPrivateEndpoint"]["type"] == "Microsoft.Common.CheckBox"
    assert options["deployContainerAppsPrivateEndpoint"]["defaultValue"] is False
    assert outputs["deployContainerAppsPrivateEndpoint"] == (
        "[steps('options').deployContainerAppsPrivateEndpoint]"
    )
    assert options["manualContainerAppsPrivateEndpointNotice"]["visible"] == (
        "[not(steps('options').deployContainerAppsPrivateEndpoint)]"
    )
    assert "공유 Storage의 계정 범위 Foundry 권한" in review["scopeAccepted"]["label"]
    assert "운영 권한은 배포 후 직접 부여" in review["scopeAccepted"]["label"]
    assert {v["value"] for v in options["searchSku"]["constraints"]["allowedValues"]} == {
        "basic",
        "standard",
    }
    assert options["dnsMode"]["defaultValue"].startswith("자동 감지")
    assert {item["value"] for item in options["dnsMode"]["constraints"]["allowedValues"]} == {
        "auto",
        "existing",
    }
    assert options["dnsDiscovery"]["visible"] == "[equals(steps('options').dnsMode, 'auto')]"
    assert "linkedPrivateDnsZonesApi.data" in options["dnsDiscovery"]["options"]["text"]
    assert review["deploymentStage"]["defaultValue"] == "1단계 — 네트워크·저장소·계정 기반"
    assert "1단계는 프로젝트를 생성하지 않습니다" in review["deploymentStage"]["toolTip"]
    assert "별도 복구" in review["deploymentStage"]["toolTip"]
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
    assert outputs["linkedPrivateDnsZones"] == (
        "[if(equals(steps('options').dnsMode, 'auto'), "
        "coalesce(steps('network').linkedPrivateDnsZonesApi.data, parse('[]')), parse('[]'))]"
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


def test_foundation_defers_project_and_all_byo_dependencies(template: dict):
    condition = "[parameters('deployCapabilityHost')]"
    for name in ("project", "agentBindings", "capabilityHost"):
        assert template["resources"][name]["condition"] == condition
    assert template["parameters"]["deployCapabilityHost"]["defaultValue"] is False
    assert template["resources"]["foundry"]["condition"] == (
        "[not(parameters('deployCapabilityHost'))]"
    )
    for name in ("storage", "cosmos", "search", "containerApp"):
        assert "condition" not in template["resources"][name]
    assert (
        _params(template, "foundry")["customSubDomainName"]
        == "[parameters('foundryAccountName')]"
    )
    assert "project" in template["resources"]["agentBindings"]["dependsOn"]
    assert "agentBindings" in template["resources"]["capabilityHost"]["dependsOn"]
    output = template["outputs"]["ktFoundation"]["value"]
    assert output["foundryProjectPrincipalId"].startswith("[if(parameters('deployCapabilityHost'),")
    assert output["foundryProjectPrincipalId"].endswith(", '')]")
    assert output["foundryProjectResourceId"] == (
        "[resourceId('Microsoft.CognitiveServices/accounts/projects', "
        "parameters('foundryAccountName'), parameters('projectName'))]"
    )


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


def test_one_private_storage_account_uses_separate_application_containers(template: dict):
    accounts = [
        resource
        for resource in _resources(template)
        if resource["type"].casefold() == "microsoft.storage/storageaccounts"
        and resource.get("existing") is not True
    ]
    assert len(accounts) == 1
    params = _params(template, "storage")
    assert params["name"] == "[parameters('storageAccountName')]"
    assert params["skuName"] == "Standard_LRS"
    assert params["publicNetworkAccess"] == "Disabled"
    assert params["networkAcls"] == {"defaultAction": "Deny", "bypass": "None"}
    assert params["allowBlobPublicAccess"] is False
    assert params["allowSharedKeyAccess"] is False
    assert params["minimumTlsVersion"] == "TLS1_2"
    assert params["supportsHttpsTrafficOnly"] is True
    assert params["blobServices"]["containers"] == [
        {"name": "azbrief-state", "publicAccess": "None"},
        {"name": "azbrief-archive", "publicAccess": "None"},
    ]
    assert params["blobServices"]["deleteRetentionPolicyDays"] == 7
    assert params["blobServices"]["containerDeleteRetentionPolicyDays"] == 7
    for module in ("agentBindings", "capabilityHost"):
        assert _params(template, module)["agentStorageAccountName"] == (
            "[parameters('storageAccountName')]"
        )
    output = template["outputs"]["ktFoundation"]["value"]
    assert (
        output["storageAccountResourceId"]
        == output["agentStorageAccountResourceId"]
        == output["stateStorageAccountResourceId"]
        == "[variables('storageId')]"
    )
    for field, container in (
        ("stateContainerUrl", "azbrief-state"),
        ("archiveContainerUrl", "azbrief-archive"),
    ):
        assert "reference('storage')" in output[field]
        assert container in output[field]


def test_runtime_permissions_are_assigned_by_the_operator(template: dict):
    resources = template["resources"]
    assert (
        not {"controlPlaneStorageRole", "controlPlaneArchiveRole", "controlPlaneFoundryRole"}
        & resources.keys()
    )
    assert not any(
        resource["type"] == "Microsoft.Authorization/roleAssignments"
        for resource in resources.values()
    )
    for module in ("containerApp", "controlPlaneIdentity"):
        assert "roleAssignments" not in _params(template, module)
    outputs = template["outputs"]["ktFoundation"]["value"]
    assert outputs["applicationReady"] is False
    for name in (
        "controlPlaneIdentityResourceId",
        "controlPlanePrincipalId",
        "controlPlaneClientId",
    ):
        assert "controlPlaneIdentity" in outputs[name]


def test_byo_project_permissions_remain_separate_from_runtime_permissions(template: dict):
    bindings = template["resources"]["agentBindings"]["properties"]["template"]
    project_roles = [
        resource
        for resource in bindings["resources"]
        if resource["type"] == "Microsoft.Authorization/roleAssignments"
        and resource.get("copy", {}).get("name") == "storageRoles"
    ]
    assert len(project_roles) == 1
    assert "agentStorageAccountName" in project_roles[0]["scope"]
    assert "/blobServices/containers" not in project_roles[0]["scope"]
    assert "projectPrincipalId" in project_roles[0]["properties"]["principalId"]
    for module in ("agentBindings", "capabilityHost"):
        for resource in _resources(template["resources"][module]["properties"]["template"]):
            if resource["type"] in {
                "Microsoft.Authorization/roleAssignments",
                "Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments",
            }:
                assert resource["properties"]["principalId"] == "[parameters('projectPrincipalId')]"


def test_other_backing_stores_keep_private_low_cost_settings(template: dict):
    assert _params(template, "cosmos")["capacityMode"] == "Serverless"
    assert _params(template, "cosmos")["networkRestrictions"]["publicNetworkAccess"] == "Disabled"
    assert len(_params(template, "cosmos")["failoverLocations"]) == 1
    search = _params(template, "search")
    assert template["parameters"]["searchSku"]["defaultValue"] == "basic"
    assert search["replicaCount"] == search["partitionCount"] == 1
    assert search["publicNetworkAccess"] == "Disabled"


def test_container_app_uses_published_image_with_fail_closed_startup(template: dict):
    environment = _params(template, "containerEnvironment")
    assert environment["internal"] is True
    assert environment["publicNetworkAccess"] == "Disabled"
    environment_template = template["resources"]["containerEnvironment"]["properties"]["template"]
    managed_environment = environment_template["resources"]["managedEnvironment"]
    assert managed_environment["type"] == "Microsoft.App/managedEnvironments"
    assert (
        managed_environment["properties"]["publicNetworkAccess"]
        == "[parameters('publicNetworkAccess')]"
    )
    assert (
        managed_environment["properties"]["vnetConfiguration"]["internal"]
        == "[parameters('internal')]"
    )
    assert environment["infrastructureSubnetResourceId"] == "[variables('containerAppsSubnetId')]"
    assert environment["workloadProfiles"] == [
        {"name": "Consumption", "workloadProfileType": "Consumption"}
    ]
    assert environment["zoneRedundant"] is False
    app = _params(template, "containerApp")
    assert app["scaleSettings"] == {"minReplicas": 0, "maxReplicas": 1}
    assert app["ingressExternal"] is True
    assert app["ingressTargetPort"] == 8000
    assert app["ingressAllowInsecure"] is False
    assert app["containers"][0]["resources"] == {"cpu": "[json('0.25')]", "memory": "0.5Gi"}
    container = app["containers"][0]
    assert container["name"] == "azbrief"
    assert container["image"] == "[parameters('bootstrapImage')]"
    image = template["parameters"]["bootstrapImage"]["defaultValue"]
    assert re.fullmatch(r"ghcr\.io/networkdog/azbriefenterprise@sha256:[a-f0-9]{64}", image)
    assert template["parameters"]["bootstrapImage"]["allowedValues"] == [image]
    assert container["probes"][0]["httpGet"] == {"path": "/health", "port": 8000}
    env = {entry["name"]: entry for entry in container["env"]}
    assert set(env) == kt.FOUNDATION_ENV_NAMES
    assert env["AZURE_TENANT_ID"]["value"] == "[tenant().tenantId]"
    assert env["AZURE_SUBSCRIPTION_ID"]["value"] == "[subscription().subscriptionId]"
    assert "controlPlaneIdentity" in env["AZURE_CLIENT_ID"]["value"]
    assert env["FOUNDRY_PROJECT_ENDPOINT"]["value"] == "[variables('foundryProjectEndpoint')]"
    assert env["FOUNDRY_HOSTED_AGENT_NAME"]["value"] == "[parameters('foundryHostedAgentName')]"
    assert env["API_KEY"] == {"name": "API_KEY", "secretRef": "orchestrator-api-key"}
    assert "azbrief-archive" in env["ARCHIVE_BLOB_CONTAINER_URL"]["value"]
    assert "azbrief-state/checkpoint.json" in env["CHECKPOINT_BLOB_URL"]["value"]
    for name in ("ADMIN_UI_ENABLED", "ARCHIVE_UI_ENABLED", "FEEDBACK_UI_ENABLED"):
        assert env[name]["value"] == "false"
    for name in ("ADMIN_REQUIRE_AUTH", "ARCHIVE_REQUIRE_AUTH"):
        assert env[name]["value"] == "true"
    assert env["MAX_CONCURRENT_ANALYSES"]["value"] == "1"
    assert "APPLICATIONINSIGHTS_CONNECTION_STRING" not in env
    assert "OTEL_SDK_DISABLED" not in env
    assert "containerRegistryPassword" not in json.dumps(env)
    assert template["parameters"]["apiKey"]["type"].casefold() == "securestring"
    assert template["parameters"]["apiKey"]["minLength"] == 32
    assert template["parameters"]["apiKey"]["maxLength"] == 256
    assert template["parameters"]["apiKey"]["defaultValue"].count("newGuid()") == 2
    assert template["parameters"]["containerRegistryAuthMode"]["defaultValue"] == "Anonymous"
    assert template["parameters"]["containerRegistryPassword"]["type"].casefold() == "securestring"
    api_secret = app["secrets"]
    assert "if(parameters('reuseExistingApiKey')," in api_secret
    assert "listSecrets(" in api_secret and ")[0]" in api_secret
    assert "parameters('apiKey')" in api_secret
    assert "parameters('containerRegistryPassword')" in app["secrets"]
    assert "ghcr.io" in app["registries"]
    assert "ghcr-pull-token" in app["registries"]
    assert "variables('credentialRegistry')" in app["registries"]
    assert "managedIdentity" not in app["registries"]
    app_template = template["resources"]["containerApp"]["properties"]["template"]
    assert app_template["parameters"]["secrets"]["items"]["$ref"] == "#/definitions/secretType"
    assert (
        app_template["definitions"]["secretType"]["properties"]["value"]["type"].casefold()
        == "securestring"
    )
    outputs = template["outputs"]["ktFoundation"]["value"]
    assert outputs["containerImage"] == "[parameters('bootstrapImage')]"
    assert outputs["containerRegistryAuthMode"] == "[parameters('containerRegistryAuthMode')]"
    assert "apiKey" not in outputs and "containerRegistryPassword" not in outputs
    for resource in template["resources"].values():
        parameters = resource.get("properties", {}).get("parameters", {})
        if "enableTelemetry" in parameters:
            assert parameters["enableTelemetry"]["value"] is False


def test_wizard_defaults_to_anonymous_and_does_not_request_an_api_key(wizard: dict):
    options = _ui_controls(wizard, "options")
    assert (
        options["registryAuthMode"]["defaultValue"] == "Public 전환과 익명 다운로드를 확인한 패키지"
    )
    assert options["registryPassword"]["type"] == "Microsoft.Common.PasswordBox"
    assert options["registryPassword"]["constraints"]["required"] is True
    assert "defaultValue" not in options["registryPassword"]
    assert options["registryPassword"]["visible"] == (
        "[equals(steps('options').registryAuthMode, 'Credentials')]"
    )
    assert options["registryUsername"]["visible"] == options["registryPassword"]["visible"]
    assert "apiKey" not in options
    assert "apiKey" not in wizard["outputs"]
    assert "자동 생성" in options["apiKeyNotice"]["options"]["text"]
    assert "재사용" in options["apiKeyNotice"]["options"]["text"]
    assert "게시용 write:packages" in options["registryPassword"]["toolTip"]
    assert wizard["outputs"]["containerRegistryPassword"] == (
        "[if(equals(steps('options').registryAuthMode, 'Credentials'), "
        "steps('options').registryPassword, '')]"
    )


@pytest.mark.parametrize(
    ("inventory", "valid", "reuse"),
    [
        ({"value": []}, True, False),
        ({"value": [], "error": None, "nextLink": None}, True, False),
        ({"value": [], "error": {}, "nextLink": ""}, True, False),
        ({}, False, None),
        ({"value": None}, False, None),
        ({"error": {"code": "Forbidden"}}, False, None),
        ({"value": [], "nextLink": "/another-page"}, False, None),
        ({"value": [], "error": {"code": "Forbidden"}}, False, None),
        ({"value": [{"type": "Microsoft.Storage/storageAccounts", "name": "other"}]}, True, False),
        (
            {"value": [{"type": "Microsoft.App/containerApps", "name": "ca-azbrief-kt"}]},
            False,
            None,
        ),
        (
            {
                "value": [
                    {
                        "type": "Microsoft.App/containerApps",
                        "name": "CA-AZBRIEF-KT",
                        "tags": {"deploymentProfile": kt.PROFILE},
                    }
                ],
                "error": None,
                "nextLink": None,
            },
            True,
            True,
        ),
    ],
)
def test_wizard_reuses_existing_keys_only_after_complete_owned_arm_inventory(
    wizard: dict, inventory: dict, valid: bool, reuse: bool | None
):
    names = _ui_controls(wizard, "names")
    context = {"containerAppsApi": inventory, "containerAppName": "ca-azbrief-kt"}
    guards = [
        rule["isValid"]
        for rule in names["containerAppName"]["constraints"]["validations"]
        if "isValid" in rule
    ]
    assert all(_evaluate_ui_guard(guard, context) for guard in guards) is valid
    if valid:
        assert _evaluate_ui_guard(wizard["outputs"]["reuseExistingApiKey"], context) is reuse


@pytest.mark.parametrize("group_mode", ["Existing", "New"])
def test_wizard_inventory_uses_the_selected_resource_group_scope(wizard: dict, group_mode: str):
    request = _ui_controls(wizard, "names")["containerAppsApi"]["request"]
    assert request["method"] == "GET"
    path = _evaluate_ui_expression(
        request["path"],
        {},
        resource_group={"mode": group_mode, "name": "rg-selected-kt"},
        subscription={"subscriptionId": SUBSCRIPTION},
    )
    if group_mode == "Existing":
        assert path == (
            f"/subscriptions/{SUBSCRIPTION}/resourceGroups/rg-selected-kt"
            "/providers/Microsoft.App/containerApps?api-version=2026-01-01"
        )
        assert "$filter" not in path
    else:
        assert path == (
            f"/subscriptions/{SUBSCRIPTION}/resources?api-version=2021-04-01"
            "&$filter=resourceGroup%20eq%20%27rg-selected-kt%27"
        )


@pytest.mark.parametrize(
    ("inventory", "expected"),
    [
        ({}, "응답 없음"),
        (
            {"error": {"code": "AuthorizationFailed", "message": "private-diagnostic-detail"}},
            "ARM 오류 코드: AuthorizationFailed",
        ),
        (
            {
                "value": [],
                "nextLink": "https://management.azure.com/page?sig=private-diagnostic-detail",
            },
            "다음 페이지: 있음",
        ),
    ],
)
def test_wizard_inventory_error_discloses_only_safe_status(
    wizard: dict, inventory: dict, expected: str
):
    control = _ui_controls(wizard, "names")["containerAppName"]
    rule = next(
        validation
        for validation in control["constraints"]["validations"]
        if "containerAppsApi.nextLink" in validation.get("isValid", "")
    )
    message = _evaluate_ui_expression(rule["message"], {"containerAppsApi": inventory})
    assert isinstance(message, str)
    assert expected in message
    assert "private-diagnostic-detail" not in message
    assert "https://" not in message


def test_kt_readmes_reference_the_actual_template_image(template: dict):
    image = template["parameters"]["bootstrapImage"]["defaultValue"]
    for path in (
        kt.ROOT / "README.md",
        kt.ROOT / "README.ko.md",
        kt.TEMPLATE.parent / "README.md",
    ):
        text = path.read_text(encoding="utf-8")
        assert image in text
        assert "8000" in text and "/health" in text
        assert "Anonymous" in text


@pytest.mark.parametrize(("deploy_aca_pe", "ip_budget"), [(False, 7), (True, 8)])
def test_all_endpoint_groups_and_dns_cover_the_minimum_ip_budget(
    template: dict, values: dict, deploy_aca_pe: bool, ip_budget: int
):
    values["deployContainerAppsPrivateEndpoint"] = deploy_aca_pe
    specs = template["variables"]["endpointSpecs"]
    assert [spec["groupId"] for spec in specs] == [
        "account",
        "blob",
        "Sql",
        "searchService",
        "managedEnvironments",
    ]
    cli_specs = kt.endpoint_specs(values, GROUP)
    assert len(specs) == len(cli_specs) == 5
    assert [spec["deploy"] for spec in cli_specs] == [True, True, True, True, deploy_aca_pe]
    assert sum(spec["ips"] for spec in cli_specs if spec["deploy"]) == ip_budget
    assert specs[1]["target"] == "[variables('storageId')]"
    assert cli_specs[1]["target"].endswith(
        f"/Microsoft.Storage/storageAccounts/{values['storageAccountName']}"
    )
    assert _params(template, "privateEndpoints")["subnetResourceId"] == "[variables('peSubnetId')]"
    assert len(template["variables"]["dnsZoneNames"]) == 7
    assert template["resources"]["privateEndpoints"]["copy"]["batchSize"] == 1
    assert "linkedPrivateDnsZones" in template["variables"]["discoveredPrivateDnsZoneIds"]
    assert "discoveredPrivateDnsZoneIds" in template["variables"]["effectivePrivateDnsZoneIds"]
    assert "existingPrivateDnsZoneIds" in template["variables"]["effectivePrivateDnsZoneIds"]
    assert "effectivePrivateDnsZoneIds" in template["resources"]["dnsZones"]["condition"]
    assert "effectivePrivateDnsZoneIds" in json.dumps(_params(template, "privateEndpoints"))


def test_container_environment_endpoint_targets_the_ready_environment(template: dict):
    assert template["parameters"]["deployContainerAppsPrivateEndpoint"]["defaultValue"] is False
    assert template["outputs"]["ktFoundation"]["value"][
        "containerAppsPrivateEndpointRequested"
    ] == ("[parameters('deployContainerAppsPrivateEndpoint')]")
    assert template["outputs"]["ktFoundation"]["value"]["applicationReady"] is False
    endpoint = template["variables"]["endpointSpecs"][4]
    assert endpoint == {
        "name": "[format('pe-{0}', parameters('containerAppsEnvironmentName'))]",
        "target": "[variables('environmentId')]",
        "groupId": "managedEnvironments",
        "zones": ["[variables('dnsZoneNames')[6]]"],
    }
    deployment = template["resources"]["privateEndpoints"]
    assert deployment["condition"] == (
        "[or(not(equals(variables('endpointSpecs')[copyIndex()].groupId, 'managedEnvironments')), "
        "parameters('deployContainerAppsPrivateEndpoint'))]"
    )
    assert "containerEnvironment" in deployment["dependsOn"]
    assert "containerApp" not in deployment["dependsOn"]
    assert deployment["copy"]["mode"] == "serial"
    assert deployment["copy"]["batchSize"] == 1
    connection = _params(template, "privateEndpoints")["privateLinkServiceConnections"][0]
    assert connection["properties"] == {
        "privateLinkServiceId": "[variables('endpointSpecs')[copyIndex()].target]",
        "groupIds": ["[variables('endpointSpecs')[copyIndex()].groupId]"],
    }
    resources = deployment["properties"]["template"]["resources"]
    endpoint_resource = resources["privateEndpoint"]
    assert endpoint_resource["properties"]["subnet"]["id"] == "[parameters('subnetResourceId')]"
    zone_group_module = resources["privateEndpoint_privateDnsZoneGroup"]
    assert "privateEndpoint" in zone_group_module["dependsOn"]
    zone_group = zone_group_module["properties"]["template"]["resources"]["privateDnsZoneGroup"]
    assert zone_group["type"] == "Microsoft.Network/privateEndpoints/privateDnsZoneGroups"


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
        self.project_exists = False
        self.project_host_exists = False
        self.project_host_name = "agents"
        self.public_access = "Disabled"
        self.account_subscription = SUBSCRIPTION
        self.host_state = "Succeeded"
        self.dns_linked = True
        self.dns_registration_enabled = False
        self.linked_dns_zones: dict[str, str] = {}
        self.host_target_override = ""
        self.application: dict | None = None

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
        if (
            resource_id
            == (f"{GROUP}/providers/Microsoft.App/containerApps/{self.values['containerAppName']}")
            and self.application is not None
        ):
            return {"properties": copy.deepcopy(self.application)}
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
                            ),
                            "internal": True,
                        },
                    }
                }
            if resource_id.endswith(f"/privateEndpoints/{spec['name']}"):
                return {
                    "properties": {
                        "provisioningState": "Succeeded",
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
        if resource_id == (
            f"/subscriptions/{SUBSCRIPTION}/providers/Microsoft.Network/privateDnsZones"
        ):
            return [
                {"name": zone, "id": zone_id} for zone, zone_id in self.linked_dns_zones.items()
            ]
        if resource_id.endswith("/virtualNetworkLinks"):
            return (
                [
                    {
                        "properties": {
                            "virtualNetwork": {"id": VNET},
                            "registrationEnabled": self.dns_registration_enabled,
                        }
                    }
                ]
                if self.dns_linked
                else []
            )
        if resource_id.endswith("/projects"):
            return [{"name": self.values["projectName"]}] if self.project_exists else []
        if resource_id.endswith("/capabilityHosts"):
            project_scope = "/projects/" in resource_id
            if project_scope and not self.project_host_exists:
                return []
            return [
                {
                    "name": self.project_host_name if project_scope else "agents",
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
                for name, index in (("agent-storage", 1), ("agent-cosmos", 2), ("agent-search", 3))
            ]
        raise AssertionError(f"Unexpected collection read: {resource_id}")

    def deploy(self, values: dict, mode: str, name: str) -> dict:
        self.calls.append(("deploy", mode, name))
        self.deployments.append(copy.deepcopy(values))
        if mode == "create":
            self.stage += 1
            if values["deployCapabilityHost"]:
                self.project_exists = True
                self.project_host_exists = True
                self.project_host_name = "agents"
            app_id = f"{GROUP}/providers/Microsoft.App/containerApps/{values['containerAppName']}"
            self.resources[app_id] = {"id": app_id, "tags": {"deploymentProfile": kt.PROFILE}}
            self.application = _foundation_app(values)
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
                resource_ids = [spec["target"]]
                if spec["deploy"]:
                    resource_ids.append(
                        f"{GROUP}/providers/Microsoft.Network/privateEndpoints/{spec['name']}"
                    )
                for resource_id in resource_ids:
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


def test_project_is_absent_during_the_operator_pause(values: dict, vnet: dict):
    cli = FakeAzure(values, vnet)
    prepared = kt.prepare(cli, values)
    prepared["deployCapabilityHost"] = False
    cli.deploy(prepared, "create", "stage-one")
    account_id = kt.endpoint_specs(values, GROUP)[0]["target"]
    assert cli.collection(f"{account_id}/projects", kt.FOUNDRY_API) == []
    assert (
        cli.collection(
            f"{account_id}/projects/{values['projectName']}/capabilityHosts", kt.HOST_API
        )
        == []
    )
    second = kt.prepare(cli, values)
    second["deployCapabilityHost"] = True
    cli.deploy(second, "create", "stage-two")
    assert cli.project_exists and cli.project_host_exists
    assert second["reuseExistingApiKey"] is True


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


@pytest.mark.parametrize("deploy_aca_pe", [False, True])
def test_deploy_stages_reinventory_before_host_and_never_rewrite_subnets(
    values: dict, vnet: dict, deploy_aca_pe: bool
):
    values["deployContainerAppsPrivateEndpoint"] = deploy_aca_pe
    cli = FakeAzure(values, vnet)
    kt.run(cli, values, "deploy")
    assert len(cli.deployments) == 2
    assert cli.deployments[0]["deployCapabilityHost"] is False
    assert cli.deployments[1]["deployCapabilityHost"] is True
    assert cli.deployments[0]["reuseExistingApiKey"] is False
    assert cli.deployments[1]["reuseExistingApiKey"] is True
    assert all(cli.deployments[0][row[3]] for row in kt.SUBNETS)
    assert not any(cli.deployments[1][row[3]] for row in kt.SUBNETS)
    assert len(cli.vnet["properties"]["subnets"]) == 3
    assert {subnet["name"] for subnet in cli.vnet["properties"]["subnets"]} == {
        "snet-private-endpoints",
        "snet-foundry-agent",
        "snet-container-apps",
    }
    assert values["existingPrivateDnsZoneIds"] == {}
    specs = kt.endpoint_specs(values, GROUP)
    assert {
        resource_id
        for resource_id in cli.resources
        if "/Microsoft.Network/privateEndpoints/" in resource_id
    } == {
        f"{GROUP}/providers/Microsoft.Network/privateEndpoints/{spec['name']}"
        for spec in specs
        if spec["deploy"]
    }
    for resource_type, name, api_version in (
        ("Microsoft.CognitiveServices/accounts", values["foundryAccountName"], kt.FOUNDRY_API),
        ("Microsoft.Storage/storageAccounts", values["storageAccountName"], "2023-05-01"),
        ("Microsoft.DocumentDB/databaseAccounts", values["cosmosAccountName"], "2024-11-15"),
        ("Microsoft.Search/searchServices", values["searchServiceName"], "2023-11-01"),
        (
            "Microsoft.App/managedEnvironments",
            values["containerAppsEnvironmentName"],
            kt.CONTAINER_ENV_API,
        ),
    ):
        assert ("get", f"{GROUP}/providers/{resource_type}/{name}", api_version) in cli.calls


def test_context_mismatch_fails_before_deployment(values: dict, vnet: dict):
    cli = FakeAzure(values, vnet)
    cli.account_subscription = "unapproved-subscription"
    with pytest.raises(ValueError, match="explicit KT target"):
        kt.run(cli, values, "deploy")
    assert not cli.deployments


def test_legacy_storage_accounts_block_a_silent_migration(values: dict, vnet: dict):
    cli = FakeAzure(values, vnet)
    old_account = f"{GROUP}/providers/Microsoft.Storage/storageAccounts/stktlegacy"
    cli.resources[old_account] = {
        "id": old_account,
        "tags": {"deploymentProfile": kt.PROFILE},
    }
    with pytest.raises(ValueError, match="Another KT-profile Storage Account"):
        kt.run(cli, values, "deploy")
    assert not cli.deployments
    assert old_account in cli.resources


def test_unrelated_storage_account_does_not_block_new_foundation(values: dict, vnet: dict):
    cli = FakeAzure(values, vnet)
    unrelated = f"{GROUP}/providers/Microsoft.Storage/storageAccounts/customerdata"
    cli.resources[unrelated] = {"id": unrelated, "tags": {"application": "customer-app"}}
    kt.run(cli, values, "preflight")
    assert not cli.deployments
    assert unrelated in cli.resources


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
    with pytest.raises(ValueError, match="verify 7 free"):
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


def _foundation_app(values: dict) -> dict:
    flags = {
        "ADMIN_UI_ENABLED": "false",
        "ADMIN_REQUIRE_AUTH": "true",
        "ARCHIVE_UI_ENABLED": "false",
        "ARCHIVE_REQUIRE_AUTH": "true",
        "FEEDBACK_UI_ENABLED": "false",
        "MAX_CONCURRENT_ANALYSES": "1",
    }
    env = [
        {"name": name, "value": flags.get(name, "configured")}
        for name in sorted(kt.FOUNDATION_ENV_NAMES - {"API_KEY"})
    ] + [{"name": "API_KEY", "secretRef": "orchestrator-api-key"}]
    return {
        "template": {
            "containers": [
                {
                    "name": "azbrief",
                    "image": values["bootstrapImage"],
                    "env": env,
                    "resources": {"cpu": 0.25, "memory": "0.5Gi"},
                }
            ],
            "scale": {"minReplicas": 0, "maxReplicas": 1},
        },
        "configuration": {
            "activeRevisionsMode": "Single",
            "ingress": {"targetPort": 8000},
            "secrets": [{"name": "orchestrator-api-key"}]
            + (
                [{"name": "ghcr-pull-token"}]
                if values["containerRegistryAuthMode"] == "Credentials"
                else []
            ),
            "registries": (
                [
                    {
                        "server": "ghcr.io",
                        "username": "Networkdog",
                        "passwordSecretRef": "ghcr-pull-token",
                    }
                ]
                if values["containerRegistryAuthMode"] == "Credentials"
                else []
            ),
        },
    }


@pytest.mark.parametrize("legacy", [True, False])
def test_kt_image_can_replace_plain_legacy_or_reuse_unchanged_foundation(
    monkeypatch: pytest.MonkeyPatch, values: dict, vnet: dict, legacy: bool
):
    cli = FakeAzure(values, vnet)
    app_id = f"{GROUP}/providers/Microsoft.App/containerApps/{values['containerAppName']}"
    props = (
        {"template": {"containers": [{"name": "bootstrap", "image": kt.LEGACY_BOOTSTRAP_IMAGE}]}}
        if legacy
        else _foundation_app(values)
    )
    cli.resources[app_id] = {"id": app_id, "tags": {"deploymentProfile": kt.PROFILE}}
    original_get = cli.get

    def get(resource_id: str, api_version: str) -> dict:
        return (
            {"properties": props}
            if resource_id == app_id
            else original_get(resource_id, api_version)
        )

    monkeypatch.setattr(cli, "get", get)
    prepared = kt.prepare(cli, values)
    assert prepared["reuseExistingApiKey"] is not legacy
    kt.run(cli, values, "preflight")
    assert not cli.deployments


@pytest.mark.parametrize("secret_count", [0, 2])
def test_existing_application_never_regenerates_a_missing_or_ambiguous_key(
    monkeypatch: pytest.MonkeyPatch, values: dict, vnet: dict, secret_count: int
):
    cli = FakeAzure(values, vnet)
    app_id = f"{GROUP}/providers/Microsoft.App/containerApps/{values['containerAppName']}"
    props = _foundation_app(values)
    props["configuration"]["secrets"] = [
        {"name": "orchestrator-api-key"} for _ in range(secret_count)
    ]
    cli.resources[app_id] = {"id": app_id, "tags": {"deploymentProfile": kt.PROFILE}}
    original_get = cli.get

    def get(resource_id: str, api_version: str) -> dict:
        return (
            {"properties": props}
            if resource_id == app_id
            else original_get(resource_id, api_version)
        )

    monkeypatch.setattr(cli, "get", get)
    with pytest.raises(ValueError, match="refusing automatic rotation"):
        kt.run(cli, values, "deploy")
    assert not cli.deployments


def test_default_credentials_are_generated_by_arm_not_serialized_by_cli(
    tmp_path: Path, values: dict, vnet: dict
):
    supplied = {
        key: {"value": value}
        for key, value in values.items()
        if key not in kt.INTERNAL_PARAMETERS
        and key
        not in {
            "apiKey",
            "containerRegistryAuthMode",
            "containerRegistryUsername",
            "containerRegistryPassword",
        }
    }
    path = tmp_path / "parameters.json"
    path.write_text(json.dumps({"parameters": supplied}), encoding="utf-8")
    loaded = kt.load_parameters(path)
    assert "apiKey" not in loaded
    assert "reuseExistingApiKey" not in loaded
    assert loaded["containerRegistryAuthMode"] == "Anonymous"
    assert loaded["containerRegistryPassword"] == ""
    prepared = kt.prepare(FakeAzure(loaded, vnet), loaded)
    assert prepared["reuseExistingApiKey"] is False
    assert "apiKey" not in prepared
    assert "newGuid" not in json.dumps(prepared)
    cli = FakeAzure(loaded, vnet)
    kt.run(cli, loaded, "deploy")
    assert [stage["reuseExistingApiKey"] for stage in cli.deployments] == [False, True]
    assert all("apiKey" not in stage for stage in cli.deployments)


@pytest.mark.parametrize(
    "change",
    ["admin", "auth", "extra-env", "plain-key", "scale", "resources", "secret", "entrypoint"],
)
def test_same_image_does_not_allow_overwriting_operational_settings(
    monkeypatch: pytest.MonkeyPatch, values: dict, vnet: dict, change: str
):
    cli = FakeAzure(values, vnet)
    app_id = f"{GROUP}/providers/Microsoft.App/containerApps/{values['containerAppName']}"
    props = _foundation_app(values)
    container = props["template"]["containers"][0]
    env = {entry["name"]: entry for entry in container["env"]}
    if change == "admin":
        env["ADMIN_UI_ENABLED"]["value"] = "true"
    elif change == "auth":
        env["ADMIN_REQUIRE_AUTH"]["value"] = "false"
    elif change == "extra-env":
        container["env"].append({"name": "SUBSCRIBERS", "value": "configured"})
    elif change == "plain-key":
        env["API_KEY"]["value"] = values["apiKey"]
    elif change == "scale":
        props["template"]["scale"]["minReplicas"] = 1
    elif change == "resources":
        container["resources"] = {"cpu": 1.0, "memory": "2Gi"}
    elif change == "secret":
        props["configuration"]["secrets"].append({"name": "customer-mail-secret"})
    else:
        container["command"] = ["custom-entrypoint"]
    cli.resources[app_id] = {"id": app_id, "tags": {"deploymentProfile": kt.PROFILE}}
    original_get = cli.get

    def get(resource_id: str, api_version: str) -> dict:
        return (
            {"properties": props}
            if resource_id == app_id
            else original_get(resource_id, api_version)
        )

    monkeypatch.setattr(cli, "get", get)
    with pytest.raises(ValueError, match="cannot overwrite"):
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


def test_vnet_linked_dns_is_discovered_and_reused(values: dict, vnet: dict):
    zone = "privatelink.search.windows.net"
    zone_id = (
        f"/subscriptions/{SUBSCRIPTION}/resourceGroups/central-dns/"
        f"providers/Microsoft.Network/privateDnsZones/{zone}"
    )
    cli = FakeAzure(values, vnet)
    cli.linked_dns_zones[zone] = zone_id
    prepared = kt.prepare(cli, values)
    assert prepared["existingPrivateDnsZoneIds"] == {zone: zone_id}
    assert values["existingPrivateDnsZoneIds"] == {}


def test_explicit_dns_cannot_override_vnet_linked_zone(values: dict, vnet: dict):
    zone = "privatelink.search.windows.net"
    linked_id = (
        f"/subscriptions/{SUBSCRIPTION}/resourceGroups/central-dns/"
        f"providers/Microsoft.Network/privateDnsZones/{zone}"
    )
    values["existingPrivateDnsZoneIds"] = {
        zone: f"{GROUP}/providers/Microsoft.Network/privateDnsZones/{zone}"
    }
    cli = FakeAzure(values, vnet)
    cli.linked_dns_zones[zone] = linked_id
    with pytest.raises(ValueError, match="conflicts with the VNet-linked zone"):
        kt.prepare(cli, values)


def test_discovered_private_endpoint_zone_requires_non_registration_link(values: dict, vnet: dict):
    zone = "privatelink.search.windows.net"
    cli = FakeAzure(values, vnet)
    cli.linked_dns_zones[zone] = (
        f"/subscriptions/{SUBSCRIPTION}/resourceGroups/central-dns/"
        f"providers/Microsoft.Network/privateDnsZones/{zone}"
    )
    cli.dns_registration_enabled = True
    with pytest.raises(ValueError, match="must use a non-registration link"):
        kt.prepare(cli, values)


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


@pytest.mark.parametrize("state", ["missing", "automatic", "not_ready"])
def test_legacy_or_incomplete_project_requires_explicit_recovery(
    values: dict, vnet: dict, state: str
):
    cli = FakeAzure(values, vnet)
    kt.run(cli, values, "deploy")
    if state == "missing":
        cli.project_host_exists = False
        expected = "without a verifiable BYO"
    elif state == "automatic":
        cli.project_host_name = "account@project@aml_aiagentservice"
        expected = "legacy automatic"
    else:
        cli.host_state = "Creating"
        expected = "not ready"
    before = copy.deepcopy(cli.deployments)
    with pytest.raises(ValueError, match=expected):
        kt.run(cli, values, "deploy")
    assert cli.deployments == before


def test_existing_completed_byo_setup_can_be_redeployed(values: dict, vnet: dict):
    cli = FakeAzure(values, vnet)
    kt.run(cli, values, "deploy")
    kt.run(cli, values, "deploy")
    assert len(cli.deployments) == 4
    assert all(stage["reuseExistingApiKey"] for stage in cli.deployments[1:])
    assert cli.project_exists and cli.project_host_exists


def test_readback_rejects_public_network_access(values: dict, vnet: dict):
    cli = FakeAzure(values, vnet)
    cli.public_access = "Enabled"
    with pytest.raises(RuntimeError, match="not disabled"):
        kt.run(cli, values, "deploy")


@pytest.mark.parametrize(
    ("endpoint_index", "provisioning_state"),
    [(0, "Failed"), (4, "Failed"), (4, "Creating"), (4, None), (4, "Succeeded")],
)
def test_readback_does_not_confuse_endpoint_approval_with_provisioning(
    monkeypatch: pytest.MonkeyPatch,
    values: dict,
    vnet: dict,
    endpoint_index: int,
    provisioning_state: str | None,
):
    values["deployContainerAppsPrivateEndpoint"] = True
    cli = FakeAzure(values, vnet)
    kt.run(cli, values, "deploy")
    endpoint_name = kt.endpoint_specs(values, GROUP)[endpoint_index]["name"]
    endpoint_id = f"{GROUP}/providers/Microsoft.Network/privateEndpoints/{endpoint_name}"
    original_get = cli.get

    def get(resource_id: str, api_version: str) -> dict:
        result = original_get(resource_id, api_version)
        if resource_id == endpoint_id:
            if provisioning_state is None:
                result["properties"].pop("provisioningState")
            else:
                result["properties"]["provisioningState"] = provisioning_state
        return result

    monkeypatch.setattr(cli, "get", get)
    deployments_before = copy.deepcopy(cli.deployments)
    if provisioning_state == "Succeeded":
        kt.verify_foundation(cli, values)
    else:
        with pytest.raises(RuntimeError, match=f"Private endpoint is not ready: {endpoint_name}"):
            kt.verify_foundation(cli, values)
    assert cli.deployments == deployments_before


def test_manual_aca_endpoint_is_not_created_read_or_overwritten(
    monkeypatch: pytest.MonkeyPatch, values: dict, vnet: dict
):
    assert values["deployContainerAppsPrivateEndpoint"] is False
    cli = FakeAzure(values, vnet)
    aca = kt.endpoint_specs(values, GROUP)[4]
    endpoint_id = f"{GROUP}/providers/Microsoft.Network/privateEndpoints/{aca['name']}"
    manual_endpoint = {
        "id": endpoint_id,
        "tags": {"owner": "operator"},
        "properties": {"provisioningState": "Failed"},
    }
    cli.resources[endpoint_id] = copy.deepcopy(manual_endpoint)
    original_get = cli.get

    def get(resource_id: str, api_version: str) -> dict:
        assert resource_id != endpoint_id, "Manual ACA PE must remain outside template management"
        return original_get(resource_id, api_version)

    monkeypatch.setattr(cli, "get", get)
    kt.run(cli, values, "deploy")
    assert cli.resources[endpoint_id] == manual_endpoint
    assert ("get", aca["target"], kt.CONTAINER_ENV_API) in cli.calls
    assert all(d["deployContainerAppsPrivateEndpoint"] is False for d in cli.deployments)


def test_manual_aca_endpoint_does_not_skip_environment_pna_check(
    monkeypatch: pytest.MonkeyPatch, values: dict, vnet: dict
):
    assert values["deployContainerAppsPrivateEndpoint"] is False
    cli = FakeAzure(values, vnet)
    kt.run(cli, values, "deploy")
    environment_id = kt.endpoint_specs(values, GROUP)[4]["target"]
    original_get = cli.get

    def get(resource_id: str, api_version: str) -> dict:
        result = original_get(resource_id, api_version)
        if resource_id == environment_id:
            result["properties"]["publicNetworkAccess"] = "Enabled"
        return result

    monkeypatch.setattr(cli, "get", get)
    with pytest.raises(RuntimeError, match="Public network access is not disabled"):
        kt.verify_foundation(cli, values)


@pytest.mark.parametrize("invalid_flag", ["false", "true", 0, None])
def test_aca_endpoint_flag_rejects_non_boolean_inputs(
    tmp_path: Path, values: dict, invalid_flag: str | int | None
):
    supplied = {
        name: {"value": value}
        for name, value in values.items()
        if name not in kt.INTERNAL_PARAMETERS
    }
    supplied["deployContainerAppsPrivateEndpoint"] = {"value": invalid_flag}
    path = tmp_path / "parameters.json"
    path.write_text(json.dumps({"parameters": supplied}), encoding="utf-8")
    with pytest.raises(ValueError, match="deployContainerAppsPrivateEndpoint must be a boolean"):
        kt.load_parameters(path)


def test_existing_external_environment_is_not_adopted(
    monkeypatch: pytest.MonkeyPatch, values: dict, vnet: dict
):
    cli = FakeAzure(values, vnet)
    environment_id = kt.endpoint_specs(values, GROUP)[4]["target"]
    cli.resources[environment_id] = {
        "id": environment_id,
        "tags": {"deploymentProfile": kt.PROFILE},
    }
    original_get = cli.get

    def get(resource_id: str, api_version: str) -> dict:
        result = original_get(resource_id, api_version)
        if resource_id == environment_id:
            result["properties"]["vnetConfiguration"]["internal"] = False
        return result

    monkeypatch.setattr(cli, "get", get)
    with pytest.raises(ValueError, match="Existing Container Apps Environment is external"):
        kt.prepare(cli, values)
    assert ("get", environment_id, kt.CONTAINER_ENV_API) in cli.calls
    assert not cli.deployments


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


def test_load_parameters_rejects_internal_flags(tmp_path: Path, values: dict):
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
    supplied["linkedPrivateDnsZones"] = {"value": []}
    path.write_text(json.dumps({"parameters": supplied}), encoding="utf-8")
    with pytest.raises(ValueError, match="reserved"):
        kt.load_parameters(path)
    del supplied["linkedPrivateDnsZones"]


@pytest.mark.parametrize(
    ("overrides", "error"),
    [
        ({"apiKey": "x" * 31}, "minimum length"),
        ({"apiKey": "x" * 257}, "maximum length"),
        ({"apiKey": " " * 32}, "without whitespace"),
        ({"apiKey": 12345}, "must be a string"),
        ({"containerRegistryPassword": ""}, "Private GHCR"),
        ({"containerRegistryPassword": 12345}, "must be a string"),
        ({"containerRegistryUsername": " "}, "Private GHCR"),
        ({"containerRegistryAuthMode": "Anonymous"}, "unused pull token"),
    ],
)
def test_kt_rejects_missing_or_invalid_runtime_credentials(
    tmp_path: Path, values: dict, overrides: dict[str, object], error: str
):
    values.update(overrides)
    supplied = {
        key: {"value": value} for key, value in values.items() if key not in kt.INTERNAL_PARAMETERS
    }
    path = tmp_path / "parameters.json"
    path.write_text(json.dumps({"parameters": supplied}), encoding="utf-8")
    with pytest.raises(ValueError, match=error):
        kt.load_parameters(path)


@pytest.mark.parametrize("key_length", [32, 256])
def test_kt_accepts_api_key_boundaries_and_explicit_anonymous_registry(
    tmp_path: Path, values: dict, key_length: int
):
    values.update(
        apiKey="x" * key_length,
        containerRegistryAuthMode="Anonymous",
        containerRegistryPassword="",
    )
    path = tmp_path / "parameters.json"
    path.write_text(
        json.dumps(
            {
                "parameters": {
                    key: {"value": value}
                    for key, value in values.items()
                    if key not in kt.INTERNAL_PARAMETERS
                }
            }
        ),
        encoding="utf-8",
    )
    loaded = kt.load_parameters(path)
    assert loaded["apiKey"] == "x" * key_length
    assert loaded["containerRegistryAuthMode"] == "Anonymous"
    assert loaded["containerRegistryPassword"] == ""


def test_kt_never_reuses_a_registry_token_as_the_application_key(tmp_path: Path, values: dict):
    values["containerRegistryPassword"] = values["apiKey"]
    path = tmp_path / "parameters.json"
    path.write_text(
        json.dumps(
            {
                "parameters": {
                    key: {"value": value}
                    for key, value in values.items()
                    if key not in kt.INTERNAL_PARAMETERS
                }
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="must be different"):
        kt.load_parameters(path)


def test_kt_validation_logs_redact_credentials(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
    values: dict,
    vnet: dict,
):
    cli = FakeAzure(values, vnet)
    monkeypatch.setattr(
        cli,
        "deploy",
        lambda *_: {
            "status": "Succeeded",
            "apiKey": values["apiKey"],
            "changes": [{"before": values["apiKey"], "after": values["containerRegistryPassword"]}],
        },
    )
    kt.run(cli, values, "what-if")
    captured = capsys.readouterr()
    output = captured.out + captured.err + caplog.text
    assert "kt_validation_result" in output
    assert values["apiKey"] not in output
    assert values["containerRegistryPassword"] not in output
    assert "[REDACTED]" in output


def test_kt_failed_cli_redacts_secrets_and_removes_parameter_file(
    monkeypatch: pytest.MonkeyPatch, values: dict
):
    cli = kt.AzureCli(SUBSCRIPTION, TENANT, "rg-kt-test")
    paths: list[Path] = []

    def fail(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        path = Path(args[args.index("--parameters") + 1][1:])
        parameters = json.loads(path.read_text(encoding="utf-8"))["parameters"]
        assert parameters["apiKey"]["value"] == values["apiKey"]
        assert (
            parameters["containerRegistryPassword"]["value"] == values["containerRegistryPassword"]
        )
        assert values["apiKey"] not in args
        assert values["containerRegistryPassword"] not in args
        paths.append(path)
        return subprocess.CompletedProcess(
            args,
            1,
            stdout="",
            stderr=f"Failure: {values['apiKey']} / {values['containerRegistryPassword']}",
        )

    monkeypatch.setattr(kt.shutil, "which", lambda _: "az-test")
    monkeypatch.setattr(kt.subprocess, "run", fail)
    with pytest.raises(RuntimeError) as failure:
        cli.deploy(values, "what-if", "offline-failure")
    assert "[REDACTED]" in str(failure.value)
    assert values["apiKey"] not in str(failure.value)
    assert values["containerRegistryPassword"] not in str(failure.value)
    assert paths and all(not path.exists() and not path.parent.exists() for path in paths)


@pytest.mark.parametrize("legacy_name", ["agentStorageAccountName", "stateStorageAccountName"])
def test_load_parameters_rejects_legacy_account_selection(
    tmp_path: Path, values: dict, legacy_name: str
):
    supplied = {
        name: {"value": value}
        for name, value in values.items()
        if name not in kt.INTERNAL_PARAMETERS
    }
    supplied[legacy_name] = {"value": "stktlegacy"}
    path = tmp_path / "parameters.json"
    path.write_text(json.dumps({"parameters": supplied}), encoding="utf-8")
    with pytest.raises(ValueError, match="Use storageAccountName"):
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
