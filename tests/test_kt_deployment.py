"""Offline contracts for the KT private infrastructure and existing-network preflight."""

import ast
import base64
import copy
import json
import re
import subprocess
from itertools import combinations
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

import pytest

from scripts import deploy_kt as kt
from src.admin.readiness import AdminReadinessCollector
from src.config import Settings

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
SUBNET_SELECTION_LABEL = "서브넷을 선택하십시오."
WEB_INPUTS = {
    "adminEntraClientId": "33333333-3333-3333-3333-333333333333",
    "adminEntraClientSecret": "test-only-entra-client-secret",
    "adminAllowedPrincipals": "44444444-4444-4444-4444-444444444444",
    "archiveAllowedPrincipals": "",
}
EXISTING_API_KEY = "existing-test-only-api-key-0123456789abcdef0123456789abcdef"
UAMI_PRINCIPAL = "55555555-5555-5555-5555-555555555555"
EMAIL_ROLE = "66666666-6666-6666-6666-666666666666"


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
        provisionModels=False,
        provisionEmail=False,
        modelDeploymentName="",
        simpleModelDeploymentName="",
        communicationServicesName="",
    )
    result["promptAgentNames"] = {
        role: f"{result['projectName']}-{suffix}" for role, suffix in kt._ROLE_NAME_SUFFIXES.items()
    }
    result["schedulerJobName"] = f"job-{result['containerAppName']}"
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
    assert defaulted_name_parameters == set(RESOURCE_NAME_DEFAULTS) | {
        "foundryHostedAgentName",
        "schedulerJobName",
        "modelDeploymentName",
        "simpleModelDeploymentName",
        "applicationInsightsName",
        "azureMcpProjectConnectionName",
        "communicationServicesName",
        "coreModelName",
        "simpleModelName",
        "emailServiceName",
    }


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


def test_default_runtime_names_fit_legal_long_resource_names(
    tmp_path: Path, values: dict, wizard: dict
):
    project_name = "p" * 63
    app_name = "a" * 28 + "z" * 4
    controls = _ui_controls(wizard, "names")
    assert re.fullmatch(_ui_regex(controls["projectName"]), project_name)
    assert re.fullmatch(_ui_regex(controls["containerAppName"]), app_name)
    parameters = {
        key: {"value": values[key]}
        for key in (
            "location",
            "virtualNetworkResourceGroupName",
            "virtualNetworkName",
            *SUBNET_NAMES,
        )
    }
    parameters["projectName"] = {"value": project_name}
    parameters["containerAppName"] = {"value": app_name}
    path = tmp_path / "parameters.json"
    path.write_text(json.dumps({"parameters": parameters}), encoding="utf-8")
    loaded = kt.load_parameters(path)
    assert loaded["projectName"] == project_name
    assert loaded["containerAppName"] == app_name
    template = json.loads(kt.TEMPLATE.read_text(encoding="utf-8"))
    arm_names = {
        role: _evaluate_ui_expression(expression, {}, parameters=loaded)
        for role, expression in template["parameters"]["promptAgentNames"]["defaultValue"].items()
    }
    assert arm_names == loaded["promptAgentNames"]
    assert len(set(loaded["promptAgentNames"].values())) == 6
    assert max(map(len, loaded["promptAgentNames"].values())) <= 64
    assert len(loaded["schedulerJobName"]) <= 60
    assert loaded["schedulerJobName"].endswith("z")


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
    if step == "basics":
        return {control["name"]: control for control in wizard["basics"]}
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
    review: dict[str, object] | None = None,
    parameters: dict[str, object] | None = None,
    variables: dict[str, object] | None = None,
    network: dict[str, object] | None = None,
    basics: dict[str, object] | None = None,
    eager_conditionals: bool = False,
) -> object:
    """Evaluate the tested Portal expression subset with strict equality."""

    def parse_expression(value: str) -> ast.expr:
        source = re.sub(r"\((\w+)\)\s*=>", r"lambda \1:", value[1:-1])
        source = re.sub(r"\b(and|or|not|if)\(", r"ui_\1(", source)
        source = re.sub(r"\blambda\(", "arm_lambda(", source)
        return ast.parse(source, mode="eval").body

    def evaluate(node: ast.expr, bindings: dict[str, object]) -> object:
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            value = evaluate(node.operand, bindings)
            assert type(value) is int
            return -value
        if isinstance(node, ast.Name):
            return {"true": True, "false": False, "null": None, **bindings}[node.id]
        if isinstance(node, ast.Attribute):
            value = evaluate(node.value, bindings)
            if value is None:
                return None
            assert isinstance(value, dict)
            return value.get(node.attr)
        if isinstance(node, ast.Subscript):
            value = evaluate(node.value, bindings)
            index = evaluate(node.slice, bindings)
            if value is None:
                return None
            if isinstance(value, dict) and isinstance(index, str):
                return value.get(index)
            assert isinstance(value, list) and isinstance(index, int)
            return value[index]
        assert isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        function = node.func.id
        if function == "ui_if":
            condition = evaluate(node.args[0], bindings)
            assert isinstance(condition, bool)
            if eager_conditionals:
                branches = [evaluate(argument, bindings) for argument in node.args[1:]]
                return branches[0] if condition else branches[1]
            return evaluate(node.args[1] if condition else node.args[2], bindings)
        if function in {"filter", "map"}:
            rows = evaluate(node.args[0], bindings)
            predicate = node.args[1]
            assert isinstance(rows, list)
            if isinstance(predicate, ast.Lambda):
                assert len(predicate.args.args) == 1
                variable_name = predicate.args.args[0].arg
                body = predicate.body
            else:
                assert isinstance(predicate, ast.Call) and isinstance(predicate.func, ast.Name)
                assert predicate.func.id == "arm_lambda" and len(predicate.args) == 2
                variable_name = evaluate(predicate.args[0], bindings)
                assert isinstance(variable_name, str)
                body = predicate.args[1]
            if function == "map":
                return [evaluate(body, {**bindings, variable_name: row}) for row in rows]
            return [row for row in rows if evaluate(body, {**bindings, variable_name: row}) is True]
        arguments = [evaluate(argument, bindings) for argument in node.args]
        if function == "resourceGroup":
            assert not arguments and resource_group is not None
            return resource_group
        if function == "subscription":
            assert not arguments and subscription is not None
            return subscription
        if function == "concat":
            if all(isinstance(value, str) for value in arguments):
                return "".join(arguments)
            assert parameters is not None, "CreateUiDefinition concat must use strings"
            assert all(isinstance(value, list) for value in arguments)
            return [item for value in arguments for item in value]
        if function == "steps":
            if arguments == ["review"]:
                assert review is not None
                return review
            if arguments == ["network"]:
                assert network is not None
                return network
            if arguments != ["names"] and len(arguments) == 1:
                value = names.get(arguments[0])
                assert isinstance(value, dict)
                return value
            return names
        if function == "basics":
            assert len(arguments) == 1 and isinstance(arguments[0], str)
            return (basics or {"deploymentStage": "foundation"})[arguments[0]]
        if function == "parameters":
            assert parameters is not None and len(arguments) == 1 and isinstance(arguments[0], str)
            return parameters[arguments[0]]
        if function == "lambdaVariables":
            assert len(arguments) == 1 and isinstance(arguments[0], str)
            return bindings[arguments[0]]
        if function == "variables":
            assert variables is not None and len(arguments) == 1 and isinstance(arguments[0], str)
            value = variables[arguments[0]]
            if isinstance(value, str) and value.startswith("[") and value.endswith("]"):
                return evaluate(parse_expression(value), bindings)
            return value
        if function == "null":
            assert not arguments
            return None
        if function in {"true", "false"}:
            assert not arguments
            return function == "true"
        if function == "createObject":
            assert len(arguments) % 2 == 0
            assert all(isinstance(value, str) for value in arguments[::2])
            return dict(zip(arguments[::2], arguments[1::2]))
        if function == "createArray":
            return arguments
        if function == "environment":
            assert not arguments
            return {"authentication": {"loginEndpoint": "https://login.microsoftonline.com/"}}
        if function == "tenant":
            assert not arguments
            return {"tenantId": TENANT}
        if function == "reference":
            assert parameters is not None
            if arguments == ["containerEnvironment"]:
                return {
                    "outputs": {"defaultDomain": {"value": "kt-test.eastus2.azurecontainerapps.io"}}
                }
            if arguments == ["storage"]:
                return {
                    "outputs": {
                        "primaryBlobEndpoint": {
                            "value": f"https://{parameters['storageAccountName']}.blob.core.windows.net/"
                        }
                    }
                }
            if arguments == ["containerApp"]:
                return {
                    "outputs": {
                        "fqdn": {
                            "value": f"{parameters['containerAppName']}.kt-test.eastus2.azurecontainerapps.io"
                        }
                    }
                }
            raise AssertionError(f"Unexpected offline ARM reference: {arguments}")
        if function == "listSecrets":
            assert arguments == ["existingContainerApp", "2026-01-01"]
            return {
                "value": [
                    {"name": "ghcr-pull-token", "value": "previous-test-only-ghcr-token"},
                    {"name": "orchestrator-api-key", "value": EXISTING_API_KEY},
                ]
            }
        if function == "trim":
            assert len(arguments) == 1 and isinstance(arguments[0], str)
            return arguments[0].strip()
        if function == "replace":
            assert len(arguments) == 3 and all(isinstance(value, str) for value in arguments)
            return arguments[0].replace(arguments[1], arguments[2])
        if function == "string":
            assert len(arguments) == 1
            if type(arguments[0]) is bool and parameters is not None:
                return str(arguments[0])
            return json.dumps(arguments[0], ensure_ascii=False, separators=(",", ":"))
        if function == "base64":
            assert len(arguments) == 1 and isinstance(arguments[0], str)
            return base64.b64encode(arguments[0].encode("utf-8")).decode("ascii")
        if function == "format":
            assert isinstance(arguments[0], str)
            return arguments[0].format(*arguments[1:])
        if function == "parse":
            assert len(arguments) == 1 and isinstance(arguments[0], str)
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
        if function == "startsWith":
            assert len(arguments) == 2 and all(isinstance(value, str) for value in arguments)
            return arguments[0].startswith(arguments[1])
        if function == "split":
            assert len(arguments) == 2 and all(isinstance(value, str) for value in arguments)
            return arguments[0].split(arguments[1])
        if function == "last":
            assert len(arguments) == 1 and isinstance(arguments[0], list) and arguments[0]
            return arguments[0][-1]
        if function == "first":
            assert len(arguments) == 1 and isinstance(arguments[0], list) and arguments[0]
            return arguments[0][0]
        if function == "skip":
            assert len(arguments) == 2 and isinstance(arguments[0], list)
            assert type(arguments[1]) is int
            return arguments[0][arguments[1] :]
        if function == "take":
            assert len(arguments) == 2 and isinstance(arguments[0], (list, str))
            assert type(arguments[1]) is int
            return arguments[0][: arguments[1]]
        if function == "int":
            assert len(arguments) == 1 and isinstance(arguments[0], str)
            return int(arguments[0])
        if function == "length":
            assert len(arguments) == 1 and isinstance(arguments[0], (list, dict, str))
            return len(arguments[0])
        if function in {"greater", "greaterOrEquals", "lessOrEquals"}:
            assert len(arguments) == 2 and all(type(value) is int for value in arguments)
            if function == "greater":
                return arguments[0] > arguments[1]
            if function == "greaterOrEquals":
                return arguments[0] >= arguments[1]
            return arguments[0] <= arguments[1]
        if function == "sub":
            assert len(arguments) == 2 and all(type(value) is int for value in arguments)
            return arguments[0] - arguments[1]
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

    return evaluate(parse_expression(expression), {})


def _evaluate_ui_guard(
    expression: str,
    names: dict[str, object],
    resource_group: dict[str, object] | None = None,
    subscription: dict[str, object] | None = None,
    network: dict[str, object] | None = None,
) -> bool:
    result = _evaluate_ui_expression(
        expression,
        names,
        resource_group=resource_group,
        subscription=subscription,
        network=network,
    )
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
    assert secure_outputs == {"containerRegistryPassword", "adminEntraClientSecret"}
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
        assert subnet_selector["defaultValue"] == SUBNET_SELECTION_LABEL
        assert subnet_selector["filter"] is True
        assert "regex" not in subnet_selector["constraints"]
        choices = subnet_selector["constraints"]["allowedValues"]
        assert "virtualNetworkApi.properties.subnets" in choices
        assert len(choices) < 500
        assert not re.search(r"first\(|last\(|split\(|int\(|addressPrefix|delegation", choices)
        role_label = subnet_selector["label"].removesuffix(" 이름")
        rule = next(
            rule
            for rule in _ui_controls(wizard, "names")["containerAppName"]["constraints"][
                "validations"
            ]
            if rule["message"].startswith(role_label + " 이름")
        )
        assert "virtualNetworkApi.properties.subnets" in rule["isValid"]
        assert "s.properties.addressPrefixes" in rule["isValid"]
        assert "s.properties.delegations" in rule["isValid"]
        assert "map(" not in rule["isValid"] and "int(" not in rule["isValid"]
        assert rule["isValid"].count("(") == rule["isValid"].count(")")
        assert f"/{maximum_prefix}" in rule["message"]
        assert wizard["outputs"][name_control] == f"[steps('network').{name_control}]"
        assert wizard["outputs"][flag] is False
        assert order.index("virtualNetworkApi") < order.index(name_control)
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


def _subnet_form(subnets: list[dict]) -> dict:
    return {
        "virtualNetwork": {"id": VNET, "location": "eastus2"},
        "virtualNetworkApi": {"id": VNET, "properties": {"subnets": copy.deepcopy(subnets)}},
        "peSubnetName": SUBNET_NAMES["peSubnetName"],
        "foundrySubnetName": SUBNET_NAMES["foundrySubnetName"],
        "containerAppsSubnetName": SUBNET_NAMES["containerAppsSubnetName"],
    }


@pytest.mark.parametrize("prefixes", [None, []])
def test_subnet_input_initialization_does_not_require_a_plural_prefix_array(
    wizard: dict, prefixes: list[str] | None
):
    subnet = _subnet("snet-container-apps", "10.70.0.64/27", True)
    subnet["properties"]["addressPrefixes"] = prefixes
    network = _subnet_form([subnet])
    for parameter in SUBNET_NAMES:
        control = _ui_controls(wizard, "network")[parameter]
        initializer = {key: value for key, value in control.items() if key not in {"constraints"}}
        if "allowedValues" in control["constraints"]:
            initializer["allowedValues"] = control["constraints"]["allowedValues"]
        for value in initializer.values():
            if isinstance(value, str) and value.startswith("["):
                _evaluate_ui_expression(value, {}, network=network, eager_conditionals=True)
        assert control["type"] == "Microsoft.Common.DropDown"
        assert _evaluate_ui_expression(
            control["constraints"]["allowedValues"],
            {},
            network=network,
            eager_conditionals=True,
        ) == [
            {"label": SUBNET_SELECTION_LABEL, "value": ""},
            {"label": "snet-container-apps", "value": "snet-container-apps"},
        ]


def test_portal_expression_contract_does_not_assume_arm_array_concat():
    with pytest.raises(AssertionError, match="concat must use strings"):
        _evaluate_ui_expression("[concat(parse('[]'), parse('[]'))]", {})
    assert _evaluate_ui_expression("[concat('left', 'right')]", {}) == "leftright"


@pytest.mark.parametrize("parameter", SUBNET_NAMES)
@pytest.mark.parametrize("stage", ["foundation", "complete"])
def test_vnet_subnet_choices_are_name_only_and_independent_of_role_inputs(
    wizard: dict, parameter: str, stage: str
):
    network = _subnet_form(
        [
            _subnet("private-endpoints", "10.70.0.0/28"),
            _subnet("foundry-prod", "10.70.0.32/27", True),
            _subnet("containers-prod", "10.70.0.64/27", True),
            _subnet("unrelated-v6", "fd00::/64", True),
            {"name": "missing-prefix", "properties": {}},
            {"properties": {}},
        ]
    )
    for name in SUBNET_NAMES:
        network.pop(name)
    control = _ui_controls(wizard, "network")[parameter]
    assert _resolve_selection(
        control["constraints"]["allowedValues"],
        {},
        stage=stage,
        network=network,
        eager_conditionals=True,
    ) == [
        {"label": SUBNET_SELECTION_LABEL, "value": ""},
        {"label": "private-endpoints", "value": "private-endpoints"},
        {"label": "foundry-prod", "value": "foundry-prod"},
        {"label": "containers-prod", "value": "containers-prod"},
        {"label": "unrelated-v6", "value": "unrelated-v6"},
        {"label": "missing-prefix", "value": "missing-prefix"},
    ]
    assert control["defaultValue"] == SUBNET_SELECTION_LABEL


def test_subnet_choices_update_with_the_vnet_and_old_selection_is_invalid(wizard: dict):
    parameter = "containerAppsSubnetName"
    control = _ui_controls(wizard, "network")[parameter]
    network = _subnet_form([_subnet("snet-old-container", "10.70.0.64/27", True)])
    choices = _resolve_selection(control["constraints"]["allowedValues"], {}, network=network)
    network[parameter] = choices[1]["value"]
    assert network[parameter] == "snet-old-container"
    assert (
        _resolve_selection(_manual_subnet_validation(wizard, parameter), {}, network=network)
        is True
    )
    network["virtualNetworkApi"]["properties"]["subnets"] = [
        _subnet("snet-new-container", "10.80.0.64/27", True)
    ]
    new_vnet = VNET.replace("vnet-kt", "vnet-new")
    network["virtualNetwork"]["id"] = new_vnet
    network["virtualNetworkApi"]["id"] = new_vnet
    choices = _resolve_selection(control["constraints"]["allowedValues"], {}, network=network)
    assert choices == [
        {"label": SUBNET_SELECTION_LABEL, "value": ""},
        {"label": "snet-new-container", "value": "snet-new-container"},
    ]
    assert (
        _resolve_selection(_manual_subnet_validation(wizard, parameter), {}, network=network)
        is False
    )
    network[parameter] = choices[1]["value"]
    assert (
        _resolve_selection(_manual_subnet_validation(wizard, parameter), {}, network=network)
        is True
    )
    assert _resolve_selection(wizard["outputs"][parameter], {}, network=network) == (
        "snet-new-container"
    )


def _manual_subnet_validation(wizard: dict, parameter: str) -> str:
    label = _ui_controls(wizard, "network")[parameter]["label"]
    return next(
        rule["isValid"]
        for rule in _ui_controls(wizard, "names")["containerAppName"]["constraints"]["validations"]
        if rule["message"].startswith(label)
    )


@pytest.mark.parametrize("parameter", SUBNET_NAMES)
@pytest.mark.parametrize(
    ("prefixes", "valid"),
    [
        ({}, False),
        ({"addressPrefix": "10.70.0.0/27"}, True),
        ({"addressPrefix": "10.70.0.0/27", "addressPrefixes": None}, True),
        ({"addressPrefix": "10.70.0.0/27", "addressPrefixes": []}, True),
        ({"addressPrefixes": []}, False),
        ({"addressPrefixes": None}, False),
        ({"addressPrefixes": [None]}, False),
        ({"addressPrefixes": [""]}, False),
        ({"addressPrefixes": ["10.70.0.0/27"]}, True),
        (
            {"addressPrefix": "10.70.0.0/27", "addressPrefixes": ["10.70.0.0/27"]},
            True,
        ),
        (
            {"addressPrefix": "10.70.0.0/27", "addressPrefixes": ["10.70.1.0/27"]},
            False,
        ),
        (
            {
                "addressPrefix": "10.70.0.0/27",
                "addressPrefixes": ["10.70.0.0/27", "10.70.1.0/27"],
            },
            False,
        ),
        ({"addressPrefixes": ["10.70.0.0/27", "fd00::/64"]}, False),
        ({"addressPrefix": None, "addressPrefixes": ["10.70.0.0/27"]}, True),
        ({"addressPrefix": ""}, False),
        ({"addressPrefix": "fd00::/64"}, False),
        ({"addressPrefix": "not-a-prefix"}, False),
    ],
)
def test_manual_subnet_validation_handles_prefix_shapes_without_lazy_branches(
    wizard: dict, parameter: str, prefixes: dict, valid: bool
):
    subnet = _subnet(SUBNET_NAMES[parameter], "10.70.0.0/27", parameter != "peSubnetName")
    subnet["properties"].pop("addressPrefix")
    subnet["properties"].update(prefixes)
    unrelated = _subnet("IPv6-unrelated", "fd00::/64", True)
    network = _subnet_form([unrelated, subnet])
    result = _resolve_selection(
        _manual_subnet_validation(wizard, parameter),
        {},
        network=network,
        eager_conditionals=True,
    )
    assert result is valid


@pytest.mark.parametrize(
    ("parameter", "prefix", "valid"),
    [
        ("peSubnetName", "10.70.0.0/28", True),
        ("peSubnetName", "10.70.0.0/29", False),
        ("foundrySubnetName", "10.70.0.0/27", True),
        ("foundrySubnetName", "10.70.0.0/28", False),
        ("containerAppsSubnetName", "10.70.0.0/27", True),
        ("containerAppsSubnetName", "10.70.0.0/28", False),
        ("containerAppsSubnetName", "10.0.0.0/8", True),
        ("containerAppsSubnetName", "10.0.0.0/7", False),
        ("containerAppsSubnetName", "192.168.0.0/16", True),
        ("containerAppsSubnetName", "192.168.0.0/15", False),
        ("foundrySubnetName", "172.16.0.0/12", True),
        ("foundrySubnetName", "172.16.0.0/11", False),
        ("containerAppsSubnetName", "172.16.0.0/12", False),
        ("containerAppsSubnetName", "172.16.0.0/13", True),
        ("containerAppsSubnetName", "172.24.0.0/13", False),
        ("containerAppsSubnetName", "172.24.0.0/14", True),
        ("containerAppsSubnetName", "172.28.0.0/14", False),
        ("containerAppsSubnetName", "172.28.0.0/15", True),
        ("containerAppsSubnetName", "172.29.0.0/27", True),
        ("containerAppsSubnetName", "172.30.0.0/27", False),
        ("containerAppsSubnetName", "172.31.0.0/27", False),
        ("containerAppsSubnetName", "169.254.0.0/27", False),
        ("containerAppsSubnetName", "100.100.0.0/27", False),
        ("containerAppsSubnetName", "100.100.128.0/27", False),
        ("containerAppsSubnetName", "172.32.0.0/27", False),
        ("containerAppsSubnetName", "8.8.8.0/27", False),
        ("containerAppsSubnetName", "10.70.0.0/not-a-number", False),
    ],
)
def test_manual_subnet_validation_preserves_exact_network_boundaries(
    wizard: dict, parameter: str, prefix: str, valid: bool
):
    subnet = _subnet(SUBNET_NAMES[parameter], prefix, parameter != "peSubnetName")
    network = _subnet_form([subnet])
    result = _resolve_selection(
        _manual_subnet_validation(wizard, parameter),
        {},
        network=network,
        eager_conditionals=True,
    )
    assert result is valid
    values = {
        "location": "eastus2",
        **SUBNET_NAMES,
        **{
            row[2]: (
                ""
                if row[1] == parameter
                else f"{'172.16' if prefix.startswith('10.') else '10.70'}.{10 + index}.0/27"
            )
            for index, row in enumerate(kt.SUBNETS)
        },
    }
    vnet = {
        "location": "eastus2",
        "properties": {
            "addressSpace": {"addressPrefixes": ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"]},
            "subnets": [subnet],
        },
    }
    if valid:
        kt.plan_subnets(values, vnet)
    else:
        with pytest.raises(ValueError):
            kt.plan_subnets(values, vnet)


@pytest.mark.parametrize("parameter", SUBNET_NAMES)
@pytest.mark.parametrize("change", ["no-delegation", "wrong-delegation", "multiple-delegations"])
def test_manual_subnet_validation_requires_the_role_delegation(
    wizard: dict, parameter: str, change: str
):
    subnet = _subnet(SUBNET_NAMES[parameter], "10.70.0.0/27", parameter != "peSubnetName")
    if change == "no-delegation":
        subnet["properties"]["delegations"] = []
    elif change == "wrong-delegation":
        subnet["properties"]["delegations"] = [
            {"properties": {"serviceName": "Microsoft.Web/serverFarms"}}
        ]
    else:
        subnet["properties"]["delegations"] = [
            {"properties": {"serviceName": "Microsoft.App/environments"}},
            {"properties": {"serviceName": "Microsoft.Web/serverFarms"}},
        ]
    assert _resolve_selection(
        _manual_subnet_validation(wizard, parameter),
        {},
        network=_subnet_form([subnet]),
        eager_conditionals=True,
    ) is (parameter == "peSubnetName" and change == "no-delegation")


@pytest.mark.parametrize("parameter", SUBNET_NAMES)
@pytest.mark.parametrize(
    "value", ["", "/subscriptions/tenant/subnets/name", "10.70.0.0/27", "bad name"]
)
def test_subnet_selections_reject_ids_cidrs_and_blank_values(
    wizard: dict, parameter: str, value: str
):
    control = _ui_controls(wizard, "network")[parameter]
    network = _subnet_form(
        [_subnet(SUBNET_NAMES[parameter], "10.70.0.0/27", parameter != "peSubnetName")]
    )
    options = _resolve_selection(control["constraints"]["allowedValues"], {}, network=network)
    assert value == "" or not any(option["value"] == value for option in options)
    network[parameter] = value
    assert (
        _resolve_selection(_manual_subnet_validation(wizard, parameter), {}, network=network)
        is False
    )


@pytest.mark.parametrize("parameter", SUBNET_NAMES)
@pytest.mark.parametrize("value", ["a", "a" * 80, "snet_Name.zone-01"])
def test_subnet_selections_preserve_supported_names(wizard: dict, parameter: str, value: str):
    network = _subnet_form([_subnet(value, "10.70.0.0/27", parameter != "peSubnetName")])
    control = _ui_controls(wizard, "network")[parameter]
    options = _resolve_selection(control["constraints"]["allowedValues"], {}, network=network)
    assert {"label": value, "value": value} in options
    network[parameter] = value
    assert (
        _resolve_selection(_manual_subnet_validation(wizard, parameter), {}, network=network)
        is True
    )
    assert _resolve_selection(wizard["outputs"][parameter], {}, network=network) == value


def test_subnet_names_preserve_case_and_cannot_share_roles(wizard: dict):
    network = _subnet_form(
        [
            _subnet(SUBNET_NAMES["peSubnetName"], "10.70.0.0/28"),
            _subnet(SUBNET_NAMES["foundrySubnetName"], "10.70.0.32/27", True),
            _subnet(SUBNET_NAMES["containerAppsSubnetName"], "10.70.0.64/27", True),
        ]
    )
    for parameter in SUBNET_NAMES:
        network[parameter] = network[parameter].upper()
        assert (
            _resolve_selection(_manual_subnet_validation(wizard, parameter), {}, network=network)
            is True
        )
        assert (
            _resolve_selection(wizard["outputs"][parameter], {}, network=network)
            == network[parameter]
        )
    rule = next(
        rule["isValid"]
        for rule in _ui_controls(wizard, "names")["containerAppName"]["constraints"]["validations"]
        if rule["message"].startswith("Private Endpoint·Foundry·Container Apps")
    )
    assert _resolve_selection(rule, {}, network=network) is True
    network["containerAppsSubnetName"] = network["foundrySubnetName"].lower()
    assert _resolve_selection(rule, {}, network=network) is False


@pytest.mark.parametrize(
    "response",
    [{}, {"properties": {}}, {"properties": {"subnets": None}}, {"properties": {"subnets": []}}],
)
def test_subnet_initialization_and_validation_handle_an_unloaded_vnet(wizard: dict, response: dict):
    network = {"virtualNetworkApi": response}
    for parameter in SUBNET_NAMES:
        control = _ui_controls(wizard, "network")[parameter]
        assert control["type"] == "Microsoft.Common.DropDown"
        assert all(
            not (isinstance(value, str) and value.startswith("[")) for value in control.values()
        )
        assert _resolve_selection(
            control["constraints"]["allowedValues"],
            {},
            network=network,
            eager_conditionals=True,
        ) == [{"label": SUBNET_SELECTION_LABEL, "value": ""}]
        assert (
            _resolve_selection(
                _manual_subnet_validation(wizard, parameter),
                {},
                network=network,
                eager_conditionals=True,
            )
            is False
        )


@pytest.mark.parametrize(
    "failure", ["missing-id", "wrong-vnet", "read-error", "no-subnet-response"]
)
def test_vnet_read_identity_and_errors_block_stale_subnet_defaults(wizard: dict, failure: str):
    form = _subnet_form([_subnet("snet-azbrief-pe", "10.70.0.0/28")])
    if failure == "missing-id":
        del form["virtualNetworkApi"]["id"]
    elif failure == "wrong-vnet":
        form["virtualNetworkApi"]["id"] = VNET.replace("vnet-kt", "vnet-old")
    elif failure == "read-error":
        form["virtualNetworkApi"]["error"] = {"code": "Forbidden"}
    else:
        form["virtualNetworkApi"]["properties"].pop("subnets")
    assert _ui_controls(wizard, "network")["peSubnetName"]["defaultValue"] == SUBNET_SELECTION_LABEL
    rule = next(
        rule["isValid"]
        for rule in _ui_controls(wizard, "names")["containerAppName"]["constraints"]["validations"]
        if rule["message"].startswith("현재 선택한 VNet")
    )
    assert _resolve_selection(rule, {}, network=form) is False


def test_subnet_manual_choice_is_preserved_in_outputs_and_revalidated(wizard: dict):
    manual = "snet-manual-choice"
    form = _subnet_form([_subnet(manual, "10.70.0.0/28")])
    form["peSubnetName"] = manual
    assert _ui_controls(wizard, "network")["peSubnetName"]["defaultValue"] == SUBNET_SELECTION_LABEL
    assert _resolve_selection(wizard["outputs"]["peSubnetName"], {}, network=form) == manual
    rule = next(
        rule["isValid"]
        for rule in _ui_controls(wizard, "names")["containerAppName"]["constraints"]["validations"]
        if rule["message"].startswith("Private Endpoint subnet 이름")
    )
    assert _resolve_selection(rule, {}, network=form) is True
    form["peSubnetName"] = ""
    assert _resolve_selection(rule, {}, network=form) is False
    form["peSubnetName"] = "snet-from-another-vnet"
    assert _resolve_selection(rule, {}, network=form) is False


def test_wizard_name_defaults_and_validation_remain_editable(wizard: dict, template: dict):
    controls = _ui_controls(wizard, "names")
    environment_api = controls["containerAppsEnvironmentApi"]["request"]
    assert environment_api["method"] == "GET"
    assert "Microsoft.App/managedEnvironments/" in environment_api["path"]
    assert "steps('names').containerAppsEnvironmentName" in environment_api["path"]
    assert "api-version=2026-01-01" in environment_api["path"]
    for name, expected in RESOURCE_NAME_DEFAULTS.items():
        control = controls[name]
        assert control["type"] == "Microsoft.Common.TextBox"
        assert control["defaultValue"] == template["parameters"][name]["defaultValue"] == expected
        assert control["constraints"]["required"] is True
        assert re.fullmatch(_ui_regex(control), expected)
        assert re.fullmatch(_ui_regex(control), expected + "01")
        assert not re.fullmatch(_ui_regex(control), "bad name!")
        assert "visible" not in control
    assert not re.fullmatch(_ui_regex(controls["storageAccountName"]), "st-azbrief")
    assert "agentStorageAccountName" not in controls
    assert "stateStorageAccountName" not in controls
    assert "계정 범위 권한" in controls["storageAccountName"]["toolTip"]
    assert not re.fullmatch(_ui_regex(controls["containerAppName"]), "ca--bad")
    existing_environment = next(
        rule
        for rule in controls["containerAppName"]["constraints"]["validations"]
        if rule["message"].startswith("입력한 기존 Environment")
    )
    assert "containerAppsEnvironmentApi.properties" in existing_environment["isValid"]
    assert "first(" not in existing_environment["isValid"]
    assert existing_environment["isValid"].startswith("[if(equals(basics('deploymentStage')")
    assert ".vnetConfiguration.internal, true)" in existing_environment["isValid"]
    assert ".publicNetworkAccess, 'Disabled')" in existing_environment["isValid"]
    assert ".tags.deploymentProfile, 'kt-private-foundation')" in existing_environment["isValid"]
    assert ".infrastructureSubnetId" in existing_environment["isValid"]
    assert "이름이 같다는 이유로 삭제하지 마십시오." in existing_environment["message"]


@pytest.fixture
def selection_names() -> dict:
    names = dict(RESOURCE_NAME_DEFAULTS)
    names["containerAppsEnvironmentApi"] = {
        "id": (
            f"{GROUP}/providers/Microsoft.App/managedEnvironments/"
            f"{names['containerAppsEnvironmentName']}"
        ),
        "properties": {
            "publicNetworkAccess": "Disabled",
            "vnetConfiguration": {
                "internal": True,
                "infrastructureSubnetId": f"{VNET}/subnets/{SUBNET_NAMES['containerAppsSubnetName']}",
            },
        },
        "tags": {"deploymentProfile": kt.PROFILE},
    }
    names["containerAppsApi"] = {
        "value": [
            {
                "type": "Microsoft.App/containerApps",
                "name": names["containerAppName"],
                "tags": {"deploymentProfile": kt.PROFILE},
                "properties": {
                    "template": {
                        "containers": [
                            {
                                "env": [
                                    {
                                        "name": "FOUNDRY_PROJECT_ENDPOINT",
                                        "value": f"https://{names['foundryAccountName']}.services.ai.azure.com/api/projects/{names['projectName']}",
                                    }
                                ]
                            }
                        ]
                    }
                },
            }
        ]
    }
    names["foundryProjectInventoryApi"] = {"value": []}
    names["foundryHostInventoryApi"] = {"value": []}
    names["foundryConnectionInventoryApi"] = {"value": []}
    return names


def _resolve_selection(
    expression: str,
    names: dict,
    mode: str = "Existing",
    group: str = "rg-kt-test",
    stage: str = "complete",
    network: dict[str, object] | None = None,
    eager_conditionals: bool = False,
) -> object:
    return _evaluate_ui_expression(
        expression,
        names,
        resource_group={"mode": mode, "name": group},
        subscription={"subscriptionId": SUBSCRIPTION},
        network=network or {"virtualNetwork": {"location": "eastus2"}},
        basics={"deploymentStage": stage},
        eager_conditionals=eager_conditionals,
    )


def test_manual_portal_omits_the_reported_failed_dropdown_and_its_dependencies(wizard: dict):
    serialized = json.dumps(wizard)
    retired = {
        "existingFoundryAccount",
        "existingStorageAccount",
        "existingCosmosAccount",
        "existingSearchService",
        "existingFoundryProject",
        "existingContainerAppsEnvironment",
        "existingResourcesApi",
        "foundryProjectsApi",
        "privateEndpointsApi",
        "foundryAccountsApi",
        "managedEnvironmentsApi",
        "subnetDefaultNotice",
    }
    for name in retired:
        assert name not in serialized
    for step in wizard["steps"]:
        for control in step["elements"]:
            if control["type"] == "Microsoft.Common.DropDown":
                assert not isinstance(control.get("defaultValue"), str) or not control[
                    "defaultValue"
                ].startswith("[")
    assert all(
        _ui_controls(wizard, "names")[name]["type"] == "Microsoft.Common.TextBox"
        for name in RESOURCE_NAME_DEFAULTS
    )


def test_manual_form_initializers_only_depend_on_preceding_controls(wizard: dict):
    order = {("basics", control["name"]): index for index, control in enumerate(wizard["basics"])}
    sequence = [("basics", control) for control in wizard["basics"]]
    for step in wizard["steps"]:
        for control in step["elements"]:
            order[(step["name"], control["name"])] = len(sequence)
            sequence.append((step["name"], control))
    for section, control in sequence:
        initializer = {key: value for key, value in control.items() if key != "constraints"}
        if "allowedValues" in control.get("constraints", {}):
            initializer["allowedValues"] = control["constraints"]["allowedValues"]
        source = json.dumps(initializer)
        references = re.findall(r"steps\('(\w+)'\)\.(\w+)", source)
        references.extend(("basics", name) for name in re.findall(r"basics\('(\w+)'\)", source))
        for reference in references:
            assert reference in order
            assert order[reference] < order[(section, control["name"])], (
                control["name"],
                reference,
            )


@pytest.mark.parametrize(
    ("stage", "mode"), [("foundation", "New"), ("foundation", "Existing"), ("complete", "Existing")]
)
def test_valid_manual_portal_inputs_pass_without_optional_resource_discovery(
    wizard: dict, selection_names: dict, stage: str, mode: str
):
    if stage == "foundation":
        selection_names["containerAppsApi"] = {"value": []}
        selection_names["containerAppsEnvironmentApi"] = {}
    network = _subnet_form(
        [
            _subnet(SUBNET_NAMES["peSubnetName"], "10.70.0.0/28"),
            _subnet(SUBNET_NAMES["foundrySubnetName"], "10.70.0.32/27", True),
            _subnet(SUBNET_NAMES["containerAppsSubnetName"], "10.70.0.64/27", True),
        ]
    )
    rules = _ui_controls(wizard, "names")["containerAppName"]["constraints"]["validations"]
    for rule in rules:
        if "isValid" in rule:
            assert (
                _resolve_selection(
                    rule["isValid"], selection_names, mode=mode, stage=stage, network=network
                )
                is True
            ), rule["message"]
    for name in RESOURCE_NAME_DEFAULTS:
        assert (
            _resolve_selection(
                wizard["outputs"][name], selection_names, mode=mode, stage=stage, network=network
            )
            == selection_names[name]
        )
    assert _resolve_selection(
        wizard["outputs"]["deployCapabilityHost"], selection_names, stage=stage
    ) is (stage == "complete")
    assert _resolve_selection(
        wizard["outputs"]["reuseExistingApiKey"], selection_names, stage=stage
    ) is (stage == "complete")


@pytest.mark.parametrize("stage", ["foundation", "complete"])
@pytest.mark.parametrize("mode", ["New", "Existing"])
def test_environment_validation_reads_the_typed_target_only_for_stage_two(
    wizard: dict, selection_names: dict, stage: str, mode: str
):
    request = _ui_controls(wizard, "names")["containerAppsEnvironmentApi"]["request"]
    assert request["method"] == "GET"
    path = _resolve_selection(request["path"], selection_names, stage=stage, mode=mode)
    if stage == "complete" and mode == "Existing":
        assert path == (
            f"{GROUP}/providers/Microsoft.App/managedEnvironments/"
            f"{selection_names['containerAppsEnvironmentName']}?api-version=2026-01-01"
        )
    else:
        assert path == (
            f"/subscriptions/{SUBSCRIPTION}/resources?api-version=2021-04-01"
            "&$filter=resourceGroup%20eq%20%27rg-kt-test%27%20and%20resourceType%20eq%20%27Microsoft.App/managedEnvironments%27"
        )
    selection_names["containerAppsEnvironmentName"] = "cae-manual-override"
    if stage == "complete" and mode == "Existing":
        assert "cae-manual-override?api-version=2026-01-01" in _resolve_selection(
            request["path"], selection_names, stage=stage, mode=mode
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("internal", False),
        ("internal", 1),
        ("internal", "true"),
        ("internal", None),
        ("publicNetworkAccess", "Enabled"),
        ("publicNetworkAccess", None),
        ("deploymentProfile", "foreign"),
        ("deploymentProfile", None),
        ("subnet", f"{VNET}/subnets/other-container-subnet"),
        ("id", f"{GROUP}/providers/Microsoft.App/managedEnvironments/cae-other"),
        ("error", {"code": "Forbidden"}),
    ],
)
def test_manual_environment_validation_preserves_private_network_and_ownership(
    wizard: dict, selection_names: dict, field: str, value: object
):
    environment = selection_names["containerAppsEnvironmentApi"]
    if field == "internal":
        environment["properties"]["vnetConfiguration"]["internal"] = value
    elif field == "publicNetworkAccess":
        environment["properties"]["publicNetworkAccess"] = value
    elif field == "deploymentProfile":
        environment["tags"]["deploymentProfile"] = value
    elif field == "subnet":
        environment["properties"]["vnetConfiguration"]["infrastructureSubnetId"] = value
    else:
        environment[field] = value
    network = _subnet_form([])
    rule = next(
        rule["isValid"]
        for rule in _ui_controls(wizard, "names")["containerAppName"]["constraints"]["validations"]
        if rule["message"].startswith("입력한 기존 Environment")
    )
    assert _resolve_selection(rule, selection_names, network=network) is False
    assert _resolve_selection(rule, selection_names, stage="foundation", network=network) is True


@pytest.mark.parametrize(
    ("left", "right"),
    list(
        combinations(
            [
                name
                for name in RESOURCE_NAME_DEFAULTS
                if name not in {"projectName", "containerAppName"}
            ],
            2,
        )
    ),
)
def test_manual_endpoint_names_still_require_unique_target_names(
    wizard: dict, selection_names: dict, left: str, right: str
):
    rule = next(
        rule["isValid"]
        for rule in _ui_controls(wizard, "names")["containerAppName"]["constraints"]["validations"]
        if rule["message"].startswith("Private Endpoint 이름")
    )
    assert _resolve_selection(rule, selection_names) is True
    selection_names[left] = selection_names[right].upper()
    assert _resolve_selection(rule, selection_names) is False


@pytest.mark.parametrize(
    ("mode", "apps", "ok"),
    [("New", [], False), ("Existing", [], False), ("Existing", ["ca-azbrief-kt"], True)],
)
def test_stage_two_requires_existing_group_and_app_to_reuse_the_key(
    wizard: dict, selection_names: dict, mode: str, apps: list[str], ok: bool
):
    selection_names["containerAppsApi"] = {
        "value": [
            {
                "type": "Microsoft.App/containerApps",
                "name": app,
                "tags": {"deploymentProfile": kt.PROFILE},
            }
            for app in apps
        ]
    }
    rules = [
        rule["isValid"]
        for rule in _ui_controls(wizard, "names")["containerAppName"]["constraints"]["validations"]
        if rule["message"].startswith(("2단계는 1단계", "2단계의 기존 Container App"))
    ]
    assert all(_resolve_selection(rule, selection_names, mode=mode) for rule in rules) is ok


def test_stage_selected_in_basics_does_not_silently_change_with_resources(
    wizard: dict, selection_names: dict
):
    control = _ui_controls(wizard, "basics")["deploymentStage"]
    assert "deploymentStage" not in _ui_controls(wizard, "review")
    assert control["type"] == "Microsoft.Common.DropDown"
    assert control["defaultValue"] == "1단계 — 네트워크·저장소·계정 기반"
    assert [option["value"] for option in control["constraints"]["allowedValues"]] == [
        "foundation",
        "complete",
    ]
    for selected_stage in ("foundation", "complete"):
        output = _resolve_selection(
            wizard["outputs"]["deployCapabilityHost"], selection_names, stage=selected_stage
        )
        assert output is (selected_stage == "complete")
    assert "steps('review').deploymentStage" not in json.dumps(wizard)


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
    assert "UAMI 필수 역할은 자동 구성" in review["scopeAccepted"]["label"]
    assert "Hosted Agent 전용 권한은 게시 후 별도" in review["scopeAccepted"]["label"]
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
    stage = _ui_controls(wizard, "basics")["deploymentStage"]
    assert stage["defaultValue"] == "1단계 — 네트워크·저장소·계정 기반"
    assert "별도 복구" in stage["toolTip"]
    assert outputs["deployCapabilityHost"] == "[equals(basics('deploymentStage'), 'complete')]"
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
    basic_names = set(_ui_controls(wizard, "basics"))
    for control in re.findall(r"basics\('(\w+)'\)", json.dumps(wizard)):
        assert control in basic_names
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
    assert template["resources"]["scheduler"]["condition"] == "[variables('schedulerRequested')]"
    assert (
        template["resources"]["scheduler"]["properties"]["configuration"]["triggerType"] == "Manual"
    )
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
    for name in ("agentBindings", "capabilityHost"):
        assert template["resources"][name]["condition"] == condition
    assert template["resources"]["project"]["condition"] == (
        "[and(parameters('deployCapabilityHost'), not(variables('reuseFoundrySetup')))]"
    )
    assert template["parameters"]["deployCapabilityHost"]["defaultValue"] is False
    assert template["resources"]["foundry"]["condition"] == (
        "[not(parameters('deployCapabilityHost'))]"
    )
    for name in ("storage", "cosmos", "search", "containerApp"):
        assert "condition" not in template["resources"][name]
    assert (
        _params(template, "foundry")["customSubDomainName"] == "[parameters('foundryAccountName')]"
    )
    assert "project" in template["resources"]["agentBindings"]["dependsOn"]
    assert "agentBindings" in template["resources"]["capabilityHost"]["dependsOn"]
    output = template["outputs"]["ktFoundation"]["value"]
    assert output["foundryProjectPrincipalId"].startswith("[if(parameters('deployCapabilityHost'),")
    assert output["foundryProjectPrincipalId"].endswith(", '')]")
    assert "reference(resourceId(" in output["foundryProjectPrincipalId"]
    assert "reference('project'" in output["foundryProjectPrincipalId"]
    assert template["parameters"]["reuseExistingFoundrySetup"]["defaultValue"] is False
    assert "existingProject" not in template["resources"]
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
        assert resource["condition"] == "[parameters('createConnections')]"
    host_template = template["resources"]["capabilityHost"]["properties"]["template"]
    host, blob_role, cosmos_role = host_template["resources"]
    assert host["properties"] == {
        "capabilityHostKind": "Agents",
        "storageConnections": ["agent-storage"],
        "threadStorageConnections": ["agent-cosmos"],
        "vectorStoreConnections": ["agent-search"],
    }
    assert "agentBindings" in template["resources"]["capabilityHost"]["dependsOn"]
    assert host["condition"] == "[parameters('createHost')]"
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


def test_runtime_permissions_are_confined_to_the_stage_two_role_module(template: dict):
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
    assert app["scaleSettings"] == {
        "minReplicas": "[if(variables('adminUiEnabled'), 1, 0)]",
        "maxReplicas": 1,
    }
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
    assert set(env) == (
        kt.FOUNDATION_ENV_NAMES
        | kt.WEB_ENV_NAMES
        | kt.READINESS_ENV_NAMES
        | kt.RUNTIME_SETUP_ENV_NAMES
    )
    assert env["AZURE_TENANT_ID"]["value"] == "[tenant().tenantId]"
    assert env["AZURE_SUBSCRIPTION_ID"]["value"] == "[subscription().subscriptionId]"
    assert "controlPlaneIdentity" in env["AZURE_CLIENT_ID"]["value"]
    assert env["FOUNDRY_PROJECT_ENDPOINT"]["value"] == "[variables('foundryProjectEndpoint')]"
    assert env["FOUNDRY_HOSTED_AGENT_NAME"]["value"] == "[parameters('foundryHostedAgentName')]"
    assert env["API_KEY"] == {"name": "API_KEY", "secretRef": "orchestrator-api-key"}
    assert "azbrief-archive" in env["ARCHIVE_BLOB_CONTAINER_URL"]["value"]
    assert "azbrief-state/checkpoint.json" in env["CHECKPOINT_BLOB_URL"]["value"]
    assert env["ADMIN_UI_ENABLED"]["value"] == "[toLower(string(variables('adminUiEnabled')))]"
    assert env["ARCHIVE_UI_ENABLED"]["value"] == "[toLower(string(variables('archiveUiEnabled')))]"
    assert env["FEEDBACK_UI_ENABLED"]["value"] == "false"
    for name in ("ADMIN_REQUIRE_AUTH", "ARCHIVE_REQUIRE_AUTH"):
        assert env[name]["value"] == "[toLower(string(not(variables('privateAnonymousWebUi'))))]"
    assert env["MAX_CONCURRENT_ANALYSES"]["value"] == "1"
    assert (
        "variables('telemetryConfigured')" in env["APPLICATIONINSIGHTS_CONNECTION_STRING"]["value"]
    )
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
    assert "parameters('adminEntraClientSecret')" in app["secrets"]
    assert app["registries"] == "[variables('controlPlaneRegistry')]"
    assert "ghcr.io" in json.dumps(template["variables"]["controlPlaneRegistry"])
    assert "ghcr-pull-token" in json.dumps(template["variables"]["controlPlaneRegistry"])
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
    assert not {"apiKey", "containerRegistryPassword", "adminEntraClientSecret"} & outputs.keys()
    for resource in template["resources"].values():
        parameters = resource.get("properties", {}).get("parameters", {})
        if "enableTelemetry" in parameters:
            assert parameters["enableTelemetry"]["value"] is False


def test_kt_readiness_metadata_resolves_to_deployed_resources(template: dict, values: dict):
    app = _params(template, "containerApp")
    environment = {item["name"]: item for item in app["containers"][0]["env"]}
    support = [
        {
            key: (
                _evaluate_ui_expression(value, {}, parameters=values)
                if isinstance(value, str) and value.startswith("[")
                else value
            )
            for key, value in resource.items()
        }
        for resource in template["variables"]["adminReadinessSupportResources"]
    ]
    variables = {**template["variables"], "adminReadinessSupportResources": support}
    resolved = {
        name: _evaluate_ui_expression(
            environment[name]["value"], {}, parameters=values, variables=variables
        )
        for name in kt.READINESS_ENV_NAMES
    }
    assert set(resolved) == {
        "ADMIN_READINESS_CONTAINER_ENVIRONMENTS",
        "ADMIN_READINESS_CONTAINER_APPS",
        "ADMIN_READINESS_SUPPORT_RESOURCES",
    }
    settings = Settings(
        _env_file=None,
        azure_subscription_id=SUBSCRIPTION,
        admin_readiness_resource_group="rg-kt-test",
        admin_readiness_foundry_account=values["foundryAccountName"],
        admin_readiness_foundry_project=values["projectName"],
        admin_readiness_foundry_model_deployment=None,
        admin_readiness_foundry_simple_model_deployment=None,
        **{name.lower(): value for name, value in resolved.items()},
    )
    expected = _readiness_metadata(values)
    assert (
        settings.get_admin_readiness_container_environments()
        == expected["ADMIN_READINESS_CONTAINER_ENVIRONMENTS"]
    )
    assert (
        settings.get_admin_readiness_container_apps() == expected["ADMIN_READINESS_CONTAINER_APPS"]
    )
    assert (
        settings.get_admin_readiness_support_resources()
        == expected["ADMIN_READINESS_SUPPORT_RESOURCES"]
    )
    assert settings.get_admin_readiness_container_jobs() == []
    assert settings.get_admin_readiness_prompt_agents() == {}
    requests = AdminReadinessCollector(settings=settings)._arm_requests()
    assert requests[f"environment:{values['containerAppsEnvironmentName']}"][0] == (
        f"{GROUP}/providers/Microsoft.App/managedEnvironments/"
        f"{values['containerAppsEnvironmentName']}"
    )
    assert requests[f"app:{values['containerAppName']}"][0] == (
        f"{GROUP}/providers/Microsoft.App/containerApps/{values['containerAppName']}"
    )
    for resource in expected["ADMIN_READINESS_SUPPORT_RESOURCES"]:
        assert requests[f"support:{resource['id']}"] == (
            f"{GROUP}/providers/{resource['resource_type']}/{resource['name']}",
            resource["api_version"],
        )
    assert len(requests) == 8
    assert kt.RUNTIME_SETUP_ENV_NAMES <= environment.keys()
    assert template["outputs"]["ktFoundation"]["value"]["applicationReady"] is False
    assert template["outputs"]["ktFoundation"]["value"]["adminReadinessSupportResources"] == (
        "[variables('adminReadinessSupportResources')]"
    )


@pytest.mark.parametrize("stage_two", [False, True])
@pytest.mark.parametrize("approve_roles", [False, True])
def test_control_plane_role_grants_default_on_and_remain_stage_two_only(
    template: dict, values: dict, stage_two: bool, approve_roles: bool
):
    values.update(deployCapabilityHost=stage_two, assignControlPlaneRoles=approve_roles)
    module = template["resources"]["controlPlaneRoles"]
    assert template["parameters"]["assignControlPlaneRoles"]["defaultValue"] is True
    assert _evaluate_ui_expression(module["condition"], {}, parameters=values) is (
        stage_two and approve_roles
    )
    assignments = [
        resource
        for resource in _resources(module["properties"]["template"])
        if resource["type"] == "Microsoft.Authorization/roleAssignments"
    ]
    assert len(assignments) == 6
    reader = next(
        resource for resource in assignments if "guid(resourceGroup()" in resource["name"]
    )
    assert "scope" not in reader
    for assignment in assignments:
        props = assignment["properties"]
        assert props["principalId"] == "[parameters('controlPlanePrincipalId')]"
        assert props["principalType"] == "ServicePrincipal"
        assert "guid(" in assignment["name"]
    roles = {resource["properties"]["roleDefinitionId"] for resource in assignments}
    assert all(
        role in " ".join(roles)
        for role in (
            "acdd72a7-3385-48ef-bd42-f606fba81ae7",
            "53ca6127-db72-4b80-b1b0-d745d6d5456d",
            "ba92f5b4-2d11-453d-a403-e96b0029c9fe",
            "3913510d-42f4-4e42-8a64-420c390055eb",
        )
    )
    blob = [r for r in assignments if "ba92f5b4" in r["properties"]["roleDefinitionId"]]
    assert len(blob) == 2
    assert all("/blobServices/containers" in r["scope"] for r in blob)
    project = next(r for r in assignments if "53ca6127" in r["properties"]["roleDefinitionId"])
    assert "/projects" in project["scope"]
    sender = next(
        r
        for r in assignments
        if "emailSenderRoleDefinitionId" in r["properties"]["roleDefinitionId"]
    )
    assert "Microsoft.Communication/communicationServices" in sender["scope"]
    telemetry = next(r for r in assignments if "3913510d" in r["properties"]["roleDefinitionId"])
    assert "Microsoft.Insights/components" in telemetry["scope"]
    assert "Hosted" not in json.dumps(_params(template, "controlPlaneRoles"))
    assert not {"Owner", "Contributor", "Delete"} & roles


@pytest.mark.parametrize("stage_two", [False, True])
@pytest.mark.parametrize("job_requested", [False, True])
def test_scheduler_job_is_opt_in_manual_and_shares_the_control_plane(
    template: dict, values: dict, stage_two: bool, job_requested: bool
):
    values.update(deployCapabilityHost=stage_two, deploySchedulerJob=job_requested)
    scheduler = template["resources"]["scheduler"]
    assert _evaluate_ui_expression(
        scheduler["condition"], {}, parameters=values, variables=template["variables"]
    ) is (stage_two and job_requested)
    configuration = scheduler["properties"]["configuration"]
    assert configuration["triggerType"] == "Manual"
    assert configuration["replicaTimeout"] == 43200
    assert configuration["replicaRetryLimit"] == 0
    assert configuration["manualTriggerConfig"] == {"parallelism": 1, "replicaCompletionCount": 1}
    assert "scheduleTriggerConfig" not in configuration
    container = scheduler["properties"]["template"]["containers"][0]
    assert container["image"] == _params(template, "containerApp")["containers"][0]["image"]
    assert container["command"] == ["python", "-m", "src.scheduler"]
    assert "RUN_TIME_BUDGET_S" in container["env"]
    assert "39600" in container["env"]
    assert "SCHEDULE_DISPATCH_ENABLED" in container["env"]
    assert "controlPlaneRoles" in scheduler["dependsOn"]
    assert configuration["registries"] == _params(template, "containerApp")["registries"]
    assert scheduler["identity"]["type"] == "UserAssigned"


@pytest.mark.parametrize("stage_two", [False, True])
def test_runtime_names_and_email_inputs_resolve_in_the_application(
    template: dict, values: dict, stage_two: bool
):
    values.update(
        deployCapabilityHost=stage_two,
        deploySchedulerJob=True,
        modelDeploymentName="approved-core",
        simpleModelDeploymentName="approved-simple",
        communicationServicesEndpoint="https://approved.communication.azure.com",
        communicationServicesName="approved-acs",
        emailSenderRoleDefinitionId=EMAIL_ROLE,
        emailSenderAddress="sender@example.invalid",
        emailRecipientAddress="operator@example.invalid",
    )
    env = {e["name"]: e for e in _params(template, "containerApp")["containers"][0]["env"]}
    resolved = {
        name: _evaluate_ui_expression(
            env[name]["value"], {}, parameters=values, variables=template["variables"]
        )
        for name in kt.runtime_setup_env(values)
    }
    assert resolved == kt.runtime_setup_env(values)
    settings = Settings(
        _env_file=None,
        azure_tenant_id=TENANT,
        **{name.lower(): value for name, value in resolved.items()},
    )
    assert settings.get_admin_readiness_prompt_agents() == values["promptAgentNames"]
    assert len(set(settings.get_admin_readiness_prompt_agents().values())) == 6
    assert settings.admin_readiness_foundry_model_deployment == (
        "approved-core" if stage_two else ""
    )
    assert settings.use_email is stage_two
    assert settings.get_admin_readiness_container_jobs() == (
        [values["schedulerJobName"]] if stage_two else []
    )
    assert settings.otel_enabled is False


@pytest.mark.parametrize(
    ("overrides", "error"),
    [
        ({"promptAgentNames": {}}, "exactly the six"),
        ({"promptAgentNames": {role: "duplicate" for role in kt._ROLE_NAME_SUFFIXES}}, "distinct"),
        ({"modelDeploymentName": "https://unapproved.invalid"}, "deployment name"),
        ({"simpleModelDeploymentName": "simple"}, "separate, configured core"),
        ({"modelDeploymentName": "same", "simpleModelDeploymentName": "SAME"}, "separate"),
        ({"deploySchedulerJob": "true"}, "boolean"),
        ({"assignControlPlaneRoles": "true"}, "boolean"),
        (
            {"communicationServicesEndpoint": "https://x.communication.azure.com"},
            "Email setup needs",
        ),
        (
            {
                "communicationServicesEndpoint": "http://x.communication.azure.com",
                "emailSenderAddress": "sender@example.com",
                "emailRecipientAddress": "to@example.com",
            },
            "ACS HTTPS origin",
        ),
        (
            {
                "communicationServicesEndpoint": "https://x.communication.azure.com:invalid",
                "emailSenderAddress": "sender@example.com",
                "emailRecipientAddress": "to@example.com",
            },
            "ACS HTTPS origin",
        ),
        (
            {
                "communicationServicesEndpoint": "https://x.communication.azure.com",
                "emailSenderAddress": "not-email",
                "emailRecipientAddress": "to@example.com",
            },
            "valid email",
        ),
        ({"azureMcpServerUrl": "https://mcp.example.com"}, "both server URL"),
        (
            {
                "azureMcpServerUrl": "https://mcp.example.com:99999/mcp",
                "azureMcpProjectConnectionName": "approved-mcp",
            },
            "valid HTTPS server URL",
        ),
        (
            {
                "azureMcpServerUrl": "https://mcp.example.com:invalid/mcp",
                "azureMcpProjectConnectionName": "approved-mcp",
            },
            "valid HTTPS server URL",
        ),
        (
            {
                "azureMcpServerUrl": "https://mcp.example.com:0/mcp",
                "azureMcpProjectConnectionName": "approved-mcp",
            },
            "valid HTTPS server URL",
        ),
    ],
)
def test_invalid_runtime_setup_stops_before_deployment(
    tmp_path: Path, values: dict, overrides: dict, error: str
):
    values.update(overrides)
    parameters = {
        name: {"value": value}
        for name, value in values.items()
        if name not in kt.INTERNAL_PARAMETERS
    }
    path = tmp_path / "parameters.json"
    path.write_text(json.dumps({"parameters": parameters}), encoding="utf-8")
    with pytest.raises(ValueError, match=error):
        kt.load_parameters(path)


def test_legacy_application_can_receive_runtime_bindings_and_the_manual_job(
    values: dict, vnet: dict
):
    values["enablePrivateAnonymousWebUi"] = True
    cli = FakeAzure(values, vnet)
    kt.run(cli, values, "deploy")
    assert cli.application is not None
    container = cli.application["template"]["containers"][0]
    container["env"] = [e for e in container["env"] if e["name"] not in kt.RUNTIME_SETUP_ENV_NAMES]
    requested = {
        **values,
        "deploySchedulerJob": True,
        "modelDeploymentName": "approved-core",
        "communicationServicesEndpoint": "https://approved.communication.azure.com",
        "communicationServicesName": "approved-acs",
        "emailSenderRoleDefinitionId": EMAIL_ROLE,
        "emailSenderAddress": "sender@example.com",
        "emailRecipientAddress": "to@example.com",
    }
    cli.deployments.clear()
    kt.run(cli, requested, "deploy", "complete")
    assert len(cli.deployments) == 1 and cli.deployments[0]["reuseExistingApiKey"] is True
    assert cli.scheduler is not None and cli.scheduler["configuration"]["triggerType"] == "Manual"
    assert cli.application is not None
    env = {e["name"]: e for e in cli.application["template"]["containers"][0]["env"]}
    assert env["ADMIN_READINESS_FOUNDRY_MODEL_DEPLOYMENT"]["value"] == "approved-core"
    assert (
        env["COMMUNICATION_SERVICES_ENDPOINT"]["value"]
        == "https://approved.communication.azure.com"
    )
    kt.run(cli, requested, "deploy", "complete")


def test_stage_two_cannot_reset_existing_scheduler_schedule(values: dict, vnet: dict):
    values.update(deploySchedulerJob=True, enablePrivateAnonymousWebUi=True)
    cli = FakeAzure(values, vnet)
    kt.run(cli, values, "deploy")
    assert cli.scheduler is not None
    cli.scheduler["configuration"]["triggerType"] = "Schedule"
    cli.deployments.clear()
    with pytest.raises(ValueError, match="operational or foreign scheduler"):
        kt.run(cli, values, "deploy", "complete")
    assert not cli.deployments


def test_stage_two_reuses_exact_existing_uami_role_assignment_names(values: dict, vnet: dict):
    cli = FakeAzure(values, vnet)
    kt.run(cli, values, "deploy")
    requested = {**values, "deployCapabilityHost": True}
    targets = kt.control_plane_role_targets(requested, GROUP)
    assert len(targets) == len(cli.role_assignments) == 4
    expected = {}
    for key, (scope, _) in targets.items():
        assignment = next(row for row in cli.role_assignments if row["scope"] == scope)
        assignment["name"] = str(uuid5(NAMESPACE_URL, f"manual-{key}"))
        expected[key] = assignment["name"]
    cli.deployments.clear()
    kt.run(cli, values, "deploy", "complete")
    assert cli.deployments[0]["existingControlPlaneRoleAssignmentNames"] == expected
    assert len(cli.role_assignments) == 4


@pytest.mark.parametrize("storage", ["1Gi", "2Gi", None])
def test_provider_ephemeral_storage_default_is_not_app_customization(
    values: dict, vnet: dict, storage: str | None
):
    values["enablePrivateAnonymousWebUi"] = True
    cli = FakeAzure(values, vnet)
    kt.run(cli, values, "deploy")
    assert cli.application is not None
    cli.application["template"]["containers"][0]["resources"]["ephemeralStorage"] = storage
    cli.deployments.clear()
    if storage == "1Gi":
        kt.run(cli, values, "deploy", "complete")
        assert len(cli.deployments) == 1
    else:
        with pytest.raises(ValueError, match="promoted or customized"):
            kt.run(cli, values, "deploy", "complete")
        assert not cli.deployments


@pytest.mark.parametrize("ephemeral", ["4Gi", ""])
def test_provider_ephemeral_storage_default_is_not_scheduler_customization(
    values: dict, vnet: dict, ephemeral: str
):
    values.update(deploySchedulerJob=True, enablePrivateAnonymousWebUi=True)
    cli = FakeAzure(values, vnet)
    kt.run(cli, values, "deploy")
    assert cli.scheduler is not None
    cli.scheduler["template"]["containers"][0]["resources"]["ephemeralStorage"] = ephemeral
    kt.run(cli, values, "deploy", "complete")


@pytest.mark.parametrize("anonymous", [False, True])
def test_existing_arm_bool_strings_do_not_block_safe_stage_two_reuse(
    values: dict, vnet: dict, anonymous: bool
):
    values["enablePrivateAnonymousWebUi"] = anonymous
    cli = FakeAzure(values, vnet)
    kt.run(cli, values, "deploy")
    assert cli.application is not None
    for entry in cli.application["template"]["containers"][0]["env"]:
        if entry["name"] in {
            "ADMIN_UI_ENABLED",
            "ADMIN_REQUIRE_AUTH",
            "ARCHIVE_UI_ENABLED",
            "ARCHIVE_REQUIRE_AUTH",
            "OTEL_ENABLED",
        }:
            entry["value"] = entry["value"].capitalize()
    cli.deployments.clear()
    kt.run(cli, values, "deploy", "complete")
    assert len(cli.deployments) == 1


def test_inherited_reader_is_preserved_but_not_reused_as_an_rg_assignment(values: dict, vnet: dict):
    cli = FakeAzure(values, vnet)
    requested = {**values, "deployCapabilityHost": True}
    cli.role_assignments = [
        {
            "scope": f"/subscriptions/{SUBSCRIPTION}",
            "principalId": UAMI_PRINCIPAL,
            "roleDefinitionId": "acdd72a7-3385-48ef-bd42-f606fba81ae7",
            "name": str(uuid5(NAMESPACE_URL, "subscription-reader")),
        }
    ]
    assert kt.existing_control_plane_roles(cli, requested, UAMI_PRINCIPAL) == {}
    assert len(cli.role_assignments) == 1


def test_conditional_uami_roles_block_replacement_before_any_write(values: dict, vnet: dict):
    cli = FakeAzure(values, vnet)
    kt.run(cli, values, "deploy")
    cli.role_assignments[0]["condition"] = "@Resource[type] StringEquals 'limited'"
    cli.deployments.clear()
    with pytest.raises(ValueError, match="ambiguous or conditional"):
        kt.run(cli, values, "deploy", "complete")
    assert not cli.deployments


def test_role_assignment_inventory_failure_never_becomes_an_empty_create_plan(
    monkeypatch: pytest.MonkeyPatch, values: dict, vnet: dict
):
    cli = FakeAzure(values, vnet)
    kt.run(cli, values, "deploy")
    original = cli.json

    def json_request(*args: str) -> Any:
        if args[:3] == ("role", "assignment", "list"):
            raise RuntimeError("role inventory denied")
        return original(*args)

    monkeypatch.setattr(cli, "json", json_request)
    cli.deployments.clear()
    with pytest.raises(RuntimeError, match="inventory denied"):
        kt.run(cli, values, "deploy", "complete")
    assert not cli.deployments


@pytest.mark.parametrize(
    "change",
    [
        "command",
        "timeout",
        "retries",
        "parallelism",
        "client",
        "literal-key",
        "resources",
        "extra-env",
    ],
)
def test_scheduler_customization_cannot_be_overwritten(values: dict, vnet: dict, change: str):
    values.update(deploySchedulerJob=True, enablePrivateAnonymousWebUi=True)
    cli = FakeAzure(values, vnet)
    kt.run(cli, values, "deploy")
    assert cli.scheduler is not None
    container = cli.scheduler["template"]["containers"][0]
    config = cli.scheduler["configuration"]
    if change == "command":
        container["command"] = ["python", "-m", "custom.scheduler"]
    elif change == "timeout":
        config["replicaTimeout"] = 86400
    elif change == "retries":
        config["replicaRetryLimit"] = 1
    elif change == "parallelism":
        config["manualTriggerConfig"]["parallelism"] = 2
    elif change == "client":
        next(e for e in container["env"] if e["name"] == "AZURE_CLIENT_ID")["value"] = "foreign"
    elif change == "literal-key":
        next(e for e in container["env"] if e["name"] == "API_KEY")["value"] = "private-test-key"
    elif change == "resources":
        container["resources"]["memory"] = "4Gi"
    elif change == "extra-env":
        container["env"].append({"name": "CUSTOM_SETTING", "value": "preserve"})
    cli.deployments.clear()
    with pytest.raises(ValueError, match="scheduler|Scheduler"):
        kt.run(cli, values, "deploy", "complete")
    assert not cli.deployments


@pytest.mark.parametrize("change", ["wildcard", "deletion", "not-actions", "foreign-scope"])
def test_email_sender_role_is_scoped_and_cannot_grant_owner_permissions(
    monkeypatch: pytest.MonkeyPatch, values: dict, vnet: dict, change: str
):
    values.update(
        communicationServicesName="approved-acs",
        emailSenderRoleDefinitionId=EMAIL_ROLE,
        communicationServicesEndpoint="https://approved.communication.azure.com",
        emailSenderAddress="sender@example.com",
        emailRecipientAddress="operator@example.com",
    )
    cli = FakeAzure(values, vnet)
    kt.run(cli, values, "deploy")
    original = cli.get

    def get(resource_id: str, api_version: str) -> dict:
        result = original(resource_id, api_version)
        if resource_id.endswith(f"/roleDefinitions/{EMAIL_ROLE}"):
            props = result["properties"]
            if change == "wildcard":
                props["permissions"][0]["actions"].append("*")
            elif change == "deletion":
                props["permissions"][0]["actions"].append(
                    "Microsoft.Communication/CommunicationServices/delete"
                )
            elif change == "not-actions":
                props["permissions"][0]["notActions"] = [
                    "Microsoft.Communication/CommunicationServices/write"
                ]
            else:
                props["assignableScopes"] = ["/subscriptions/foreign"]
        return result

    monkeypatch.setattr(cli, "get", get)
    cli.deployments.clear()
    with pytest.raises(ValueError, match="minimal ACS custom role"):
        kt.run(cli, values, "deploy", "complete")
    assert not cli.deployments


def test_runtime_telemetry_reuses_only_an_approved_entra_component(
    monkeypatch: pytest.MonkeyPatch, values: dict, vnet: dict
):
    values.update(applicationInsightsName="ai-approved", enablePrivateAnonymousWebUi=True)
    cli = FakeAzure(values, vnet)
    original = cli.get
    state = {"DisableLocalAuth": False, "ConnectionString": "InstrumentationKey=nonsecret-test"}

    def get(resource_id: str, api_version: str) -> dict:
        if "/Microsoft.Insights/components/" in resource_id:
            return {"properties": state}
        return original(resource_id, api_version)

    monkeypatch.setattr(cli, "get", get)
    with pytest.raises(ValueError, match="Entra-only"):
        kt.prepare(cli, {**values, "deployCapabilityHost": True})
    assert not cli.deployments
    state["DisableLocalAuth"] = True
    prepared = kt.prepare(cli, {**values, "deployCapabilityHost": False})
    assert prepared["deployCapabilityHost"] is False


@pytest.mark.parametrize(
    ("auth_mode", "projection"),
    [("disabled", "legacy-minimal")]
    + [
        (auth, projection)
        for auth in ("disabled", "anonymous", "entra")
        for projection in ("web-before-readiness", "current")
    ],
)
def test_existing_kt_projections_receive_readiness_metadata_without_being_downgraded(
    values: dict, vnet: dict, auth_mode: str, projection: str
):
    if auth_mode == "anonymous":
        values["enablePrivateAnonymousWebUi"] = True
    elif auth_mode == "entra":
        values.update(WEB_INPUTS)
    cli = FakeAzure(values, vnet)
    kt.run(cli, values, "deploy")
    assert cli.application is not None
    if projection == "legacy-minimal":
        allowed_names = kt.FOUNDATION_ENV_NAMES
    elif projection == "web-before-readiness":
        allowed_names = kt.FOUNDATION_ENV_NAMES | kt.WEB_ENV_NAMES
    else:
        allowed_names = kt.FOUNDATION_ENV_NAMES | kt.WEB_ENV_NAMES | kt.READINESS_ENV_NAMES
    container = cli.application["template"]["containers"][0]
    container["env"] = [entry for entry in container["env"] if entry["name"] in allowed_names]
    cli.deployments.clear()
    kt.run(cli, values, "deploy", "complete")
    assert len(cli.deployments) == 1
    assert cli.deployments[0]["reuseExistingApiKey"] is True
    environment = {
        entry["name"]: entry for entry in cli.application["template"]["containers"][0]["env"]
    }
    for name, rows in _readiness_metadata(values).items():
        assert Settings._decode_readiness_json(environment[name]["value"]) == rows
    assert environment["ADMIN_REQUIRE_AUTH"]["value"] == (
        "false" if auth_mode == "anonymous" else "true"
    )


@pytest.mark.parametrize("encoding", ["json", "base64"])
def test_cli_accepts_equivalent_readiness_encodings_and_order(
    values: dict, vnet: dict, encoding: str
):
    values["enablePrivateAnonymousWebUi"] = True
    cli = FakeAzure(values, vnet)
    kt.run(cli, values, "deploy")
    assert cli.application is not None
    environment = {
        entry["name"]: entry for entry in cli.application["template"]["containers"][0]["env"]
    }
    for name, rows in _readiness_metadata(values).items():
        normalized = [
            (
                {key: value.upper() for key, value in row.items()}
                if isinstance(row, dict)
                else row.upper()
            )
            for row in reversed(rows)
        ]
        raw = json.dumps(normalized)
        environment[name]["value"] = (
            base64.b64encode(raw.encode("utf-8")).decode("ascii") if encoding == "base64" else raw
        )
    cli.deployments.clear()
    prepared = kt.prepare(cli, {**values, "deployCapabilityHost": True})
    assert prepared["reuseExistingApiKey"] is True
    assert not cli.deployments


@pytest.mark.parametrize("name", sorted(kt.READINESS_ENV_NAMES))
@pytest.mark.parametrize("invalid", [123, "not-valid-json-or-base64", "{}", "[]"])
def test_readiness_projection_errors_stop_before_mutation(
    values: dict, vnet: dict, name: str, invalid: object
):
    values["enablePrivateAnonymousWebUi"] = True
    cli = FakeAzure(values, vnet)
    kt.run(cli, values, "deploy")
    assert cli.application is not None
    environment = {
        entry["name"]: entry for entry in cli.application["template"]["containers"][0]["env"]
    }
    environment[name]["value"] = invalid
    cli.deployments.clear()
    with pytest.raises(ValueError, match="(?i)readiness"):
        kt.run(cli, values, "deploy", "complete")
    assert not cli.deployments


@pytest.mark.parametrize(
    "invalid",
    [
        [{"ID": "managed_identity"}],
        [{"id": "managed_identity", "name": "id-ca"}],
        ["not-a-resource-descriptor"],
    ],
)
def test_readiness_descriptor_keys_remain_case_sensitive(values: dict, vnet: dict, invalid: list):
    values["enablePrivateAnonymousWebUi"] = True
    cli = FakeAzure(values, vnet)
    kt.run(cli, values, "deploy")
    assert cli.application is not None
    environment = {
        entry["name"]: entry for entry in cli.application["template"]["containers"][0]["env"]
    }
    environment["ADMIN_READINESS_SUPPORT_RESOURCES"]["value"] = json.dumps(invalid)
    cli.deployments.clear()
    with pytest.raises(ValueError, match="descriptors have invalid fields"):
        kt.run(cli, values, "deploy", "complete")
    assert not cli.deployments


@pytest.mark.parametrize("name", sorted(kt.READINESS_ENV_NAMES))
def test_readiness_metadata_cannot_redirect_or_expand_the_expected_inventory(
    values: dict, vnet: dict, name: str
):
    values["enablePrivateAnonymousWebUi"] = True
    cli = FakeAzure(values, vnet)
    kt.run(cli, values, "deploy")
    assert cli.application is not None
    environment = {
        entry["name"]: entry for entry in cli.application["template"]["containers"][0]["env"]
    }
    metadata = _readiness_metadata(values)[name]
    if name == "ADMIN_READINESS_SUPPORT_RESOURCES":
        metadata[0]["name"] = "different-identity"
    else:
        metadata.append("different-resource")
    environment[name]["value"] = json.dumps(metadata)
    cli.deployments.clear()
    with pytest.raises(ValueError, match="cannot overwrite customized readiness metadata"):
        kt.run(cli, values, "deploy", "complete")
    assert not cli.deployments


@pytest.mark.parametrize("reuse_api_key", [False, True])
@pytest.mark.parametrize(
    ("stage_two", "inputs", "admin", "archive", "authenticated"),
    [
        (False, WEB_INPUTS, False, False, False),
        (True, {}, False, False, False),
        (True, {"adminEntraClientId": WEB_INPUTS["adminEntraClientId"]}, False, False, False),
        (
            True,
            {**WEB_INPUTS, "adminAllowedPrincipals": ""},
            False,
            False,
            True,
        ),
        (True, WEB_INPUTS, True, True, True),
        (
            True,
            {**WEB_INPUTS, "adminAllowedPrincipals": "", "archiveAllowedPrincipals": "reader"},
            False,
            True,
            True,
        ),
        (
            True,
            {**WEB_INPUTS, "adminAllowedPrincipals": " ,  , "},
            False,
            False,
            True,
        ),
    ],
)
def test_compiled_web_setup_is_stage_gated_and_fail_closed(
    template: dict,
    values: dict,
    stage_two: bool,
    inputs: dict[str, str],
    admin: bool,
    archive: bool,
    authenticated: bool,
    reuse_api_key: bool,
):
    values.update(inputs, deployCapabilityHost=stage_two, reuseExistingApiKey=reuse_api_key)

    def resolve(value: object) -> object:
        if isinstance(value, str) and value.startswith("["):
            return _evaluate_ui_expression(
                value, {}, parameters=values, variables=template["variables"]
            )
        return value

    app = _params(template, "containerApp")
    env = {entry["name"]: entry for entry in app["containers"][0]["env"]}
    assert resolve(env["ADMIN_UI_ENABLED"]["value"]) == str(admin).lower()
    assert resolve(env["ARCHIVE_UI_ENABLED"]["value"]) == str(archive).lower()
    assert resolve(env["ADMIN_REQUIRE_AUTH"]["value"]) == "true"
    assert resolve(env["ARCHIVE_REQUIRE_AUTH"]["value"]) == "true"
    assert resolve(env["ADMIN_ALLOWED_PRINCIPALS"]["value"]) == (
        values["adminAllowedPrincipals"] if admin else ""
    )
    assert resolve(env["ARCHIVE_ALLOWED_PRINCIPALS"]["value"]) == (
        values["archiveAllowedPrincipals"] if archive else ""
    )
    assert resolve(app["scaleSettings"]["minReplicas"]) == (1 if admin else 0)
    auth_parameter = resolve(app["authConfig"])
    assert isinstance(auth_parameter, dict) and set(auth_parameter) == {"value"}
    assert (auth_parameter["value"] is not None) is authenticated
    secrets = resolve(app["secrets"])
    assert isinstance(secrets, list)
    assert [secret for secret in secrets if secret["name"] == "orchestrator-api-key"] == [
        {
            "name": "orchestrator-api-key",
            "value": EXISTING_API_KEY if reuse_api_key else values["apiKey"],
        }
    ]
    auth_secrets = [
        secret for secret in secrets if secret["name"] == "microsoft-provider-authentication-secret"
    ]
    assert auth_secrets == (
        [
            {
                "name": "microsoft-provider-authentication-secret",
                "value": values["adminEntraClientSecret"],
            }
        ]
        if authenticated
        else []
    )
    origin = f"https://{values['containerAppName']}.kt-test.eastus2.azurecontainerapps.io"
    assert resolve(env["ARCHIVE_BASE_URL"]["value"]) == (origin if archive else "")
    assert resolve(env["ADMIN_CONFIG_BLOB_URL"]["value"]) == (
        f"https://{values['storageAccountName']}.blob.core.windows.net/azbrief-state/admin-config.json"
        if admin
        else ""
    )
    outputs = template["outputs"]["ktFoundation"]["value"]
    assert resolve(outputs["webAuthenticationConfigured"]) is authenticated
    assert resolve(outputs["adminUiEnabled"]) is admin
    assert resolve(outputs["archiveUiEnabled"]) is archive
    assert resolve(outputs["adminPageUrl"]) == (f"{origin}/admin" if admin else "")
    assert resolve(outputs["archivePageUrl"]) == (f"{origin}/archive" if archive else "")
    assert resolve(outputs["entraRedirectUri"]) == f"{origin}/.auth/login/aad/callback"
    assert outputs["applicationReady"] is False
    assert kt._web_ui_flags(values) == (admin, archive)


def test_web_auth_uses_existing_tenant_and_preserves_api_key_routes(template: dict, values: dict):
    values.update(WEB_INPUTS, deployCapabilityHost=True)
    app = _params(template, "containerApp")
    auth_parameter = _evaluate_ui_expression(
        app["authConfig"], {}, parameters=values, variables=template["variables"]
    )
    assert isinstance(auth_parameter, dict)
    auth = auth_parameter["value"]
    assert auth["platform"] == {"enabled": True}
    assert auth["globalValidation"] == {"unauthenticatedClientAction": "AllowAnonymous"}
    assert auth["httpSettings"] == {"requireHttps": True}
    provider = auth["identityProviders"]["azureActiveDirectory"]
    assert provider["enabled"] is True
    registration = provider["registration"]
    assert registration["clientSecretSettingName"] == "microsoft-provider-authentication-secret"
    assert registration["openIdIssuer"] == f"https://login.microsoftonline.com/{TENANT}/v2.0"
    assert "tenant().tenantId" in app["authConfig"]
    for actual, expected in (
        (registration["clientId"], WEB_INPUTS["adminEntraClientId"]),
        (
            provider["validation"]["allowedAudiences"][0],
            f"api://{WEB_INPUTS['adminEntraClientId']}",
        ),
        (provider["validation"]["allowedAudiences"][1], WEB_INPUTS["adminEntraClientId"]),
    ):
        assert actual == expected
    assert auth["login"] == {
        "allowedExternalRedirectUrls": [
            f"https://{values['containerAppName']}.kt-test.eastus2.azurecontainerapps.io"
        ],
        "preserveUrlFragmentsForLogins": False,
    }
    assert "variables('adminAuthConfigured')" in app["authConfig"]
    env = {entry["name"]: entry for entry in app["containers"][0]["env"]}
    assert "adminEntraClientSecret" not in json.dumps(env)
    assert "variables('adminUiEnabled')" in env["ADMIN_CONFIG_BLOB_URL"]["value"]
    assert "azbrief-state/admin-config.json" in env["ADMIN_CONFIG_BLOB_URL"]["value"]
    assert (
        "reference('storage').outputs.primaryBlobEndpoint.value"
        in env["ADMIN_CONFIG_BLOB_URL"]["value"]
    )
    assert "variables('archiveUiEnabled')" in env["ARCHIVE_BASE_URL"]["value"]
    assert (
        "reference('containerEnvironment').outputs.defaultDomain.value"
        in env["ARCHIVE_BASE_URL"]["value"]
    )
    nested = template["resources"]["containerApp"]["properties"]["template"]
    auth_module = nested["resources"]["containerAppAuthConfigs"]
    assert auth_module["condition"] == "[not(empty(parameters('authConfig')))]"
    assert "containerApp" in auth_module["dependsOn"]
    leaf = auth_module["properties"]["template"]["resources"]["containerAppAuthConfigs"]
    assert leaf["type"] == "Microsoft.App/containerApps/authConfigs"
    assert leaf["apiVersion"] == "2026-01-01"


@pytest.mark.parametrize(
    ("stage", "enabled", "forwarded"),
    [("foundation", True, False), ("complete", False, False), ("complete", True, True)],
)
def test_wizard_forwards_web_inputs_only_for_opted_in_stage_two(
    wizard: dict, stage: str, enabled: bool, forwarded: bool
):
    controls = _ui_controls(wizard, "review")
    assert controls["configureWebUi"]["defaultValue"] is False
    assert controls["configureWebUi"]["visible"] == (
        "[and(equals(basics('deploymentStage'), 'complete'), "
        "not(steps('review').privateAnonymousWebUi))]"
    )
    review = {
        "deploymentStage": stage,
        "configureWebUi": enabled,
        "privateAnonymousWebUi": False,
        "adminClientId": WEB_INPUTS["adminEntraClientId"],
        "adminSecret": WEB_INPUTS["adminEntraClientSecret"],
        "adminPrincipals": WEB_INPUTS["adminAllowedPrincipals"],
        "archivePrincipals": "55555555-5555-5555-5555-555555555555",
    }
    for control in (
        "adminClientId",
        "adminSecret",
        "adminPrincipals",
        "archivePrincipals",
        "webPrerequisitesReady",
        "webSetupNotice",
    ):
        assert (
            _evaluate_ui_expression(
                controls[control]["visible"], {}, review=review, basics={"deploymentStage": stage}
            )
            is forwarded
        )
    for parameter, control in (
        ("adminEntraClientId", "adminClientId"),
        ("adminEntraClientSecret", "adminSecret"),
        ("adminAllowedPrincipals", "adminPrincipals"),
        ("archiveAllowedPrincipals", "archivePrincipals"),
    ):
        assert _evaluate_ui_expression(
            wizard["outputs"][parameter], {}, review=review, basics={"deploymentStage": stage}
        ) == (review[control] if forwarded else "")
    assert controls["adminSecret"]["type"] == "Microsoft.Common.PasswordBox"
    assert "defaultValue" not in controls["adminSecret"]
    for name in ("adminClientId", "adminSecret", "adminPrincipals", "webPrerequisitesReady"):
        assert controls[name]["constraints"]["required"] is True
    assert controls["archivePrincipals"]["constraints"]["required"] is False
    regex = _ui_regex(controls["adminPrincipals"])
    assert re.fullmatch(regex, " , , ") is None
    assert re.fullmatch(regex, f" {WEB_INPUTS['adminAllowedPrincipals']},admin@example.invalid ")


@pytest.mark.parametrize("stage_two", [False, True])
@pytest.mark.parametrize("opted_in", [False, True])
def test_private_anonymous_template_is_explicit_and_keeps_workload_auth(
    template: dict, values: dict, stage_two: bool, opted_in: bool
):
    values.update(
        deployCapabilityHost=stage_two,
        enablePrivateAnonymousWebUi=opted_in,
        reuseExistingApiKey=True,
    )
    enabled = stage_two and opted_in

    def resolve(expression: object) -> object:
        if isinstance(expression, str) and expression.startswith("["):
            return _evaluate_ui_expression(
                expression, {}, parameters=values, variables=template["variables"]
            )
        return expression

    app = _params(template, "containerApp")
    env = {entry["name"]: entry for entry in app["containers"][0]["env"]}
    assert template["parameters"]["enablePrivateAnonymousWebUi"]["defaultValue"] is False
    assert resolve(env["ADMIN_UI_ENABLED"]["value"]) == str(enabled).lower()
    assert resolve(env["ARCHIVE_UI_ENABLED"]["value"]) == str(enabled).lower()
    assert resolve(env["ADMIN_REQUIRE_AUTH"]["value"]) == str(not enabled).lower()
    assert resolve(env["ARCHIVE_REQUIRE_AUTH"]["value"]) == str(not enabled).lower()
    assert resolve(env["ADMIN_ALLOWED_PRINCIPALS"]["value"]) == ""
    assert resolve(env["ARCHIVE_ALLOWED_PRINCIPALS"]["value"]) == ""
    assert env["API_KEY"] == {"name": "API_KEY", "secretRef": "orchestrator-api-key"}
    assert "controlPlaneIdentity" in env["AZURE_CLIENT_ID"]["value"]
    assert resolve(app["scaleSettings"]["minReplicas"]) == (1 if enabled else 0)
    secrets = resolve(app["secrets"])
    assert isinstance(secrets, list)
    assert not any(s["name"] == "microsoft-provider-authentication-secret" for s in secrets)
    assert (
        next(s["value"] for s in secrets if s["name"] == "orchestrator-api-key") == EXISTING_API_KEY
    )
    auth_parameter = resolve(app["authConfig"])
    assert isinstance(auth_parameter, dict)
    auth = auth_parameter["value"]
    if enabled:
        assert auth["platform"] == {"enabled": False}
        assert auth["identityProviders"] == {"azureActiveDirectory": {"enabled": False}}
        assert auth["globalValidation"] == {"unauthenticatedClientAction": "AllowAnonymous"}
        assert auth["httpSettings"] == {"requireHttps": True}
        assert "registration" not in auth["identityProviders"]["azureActiveDirectory"]
    else:
        assert auth is None
    output = template["outputs"]["ktFoundation"]["value"]
    assert resolve(output["privateAnonymousWebUiEnabled"]) is enabled
    assert resolve(output["webAuthenticationConfigured"]) is False
    assert output["applicationReady"] is False
    environment = _params(template, "containerEnvironment")
    assert environment["internal"] is True
    assert environment["publicNetworkAccess"] == "Disabled"
    assert app["ingressAllowInsecure"] is False


@pytest.mark.parametrize("stage", ["foundation", "complete"])
def test_anonymous_wizard_does_not_apply_hidden_entra_values(wizard: dict, stage: str):
    controls = _ui_controls(wizard, "review")
    assert controls["privateAnonymousWebUi"]["defaultValue"] is True
    assert controls["privateAnonymousReady"]["constraints"]["required"] is True
    assert "관리자 기능" in controls["privateAnonymousNotice"]["options"]["text"]
    review = {
        "deploymentStage": stage,
        "privateAnonymousWebUi": True,
        "configureWebUi": True,
        "adminClientId": WEB_INPUTS["adminEntraClientId"],
        "adminSecret": WEB_INPUTS["adminEntraClientSecret"],
        "adminPrincipals": WEB_INPUTS["adminAllowedPrincipals"],
        "archivePrincipals": "reader",
    }
    for name in (
        "configureWebUi",
        "adminClientId",
        "adminSecret",
        "adminPrincipals",
        "archivePrincipals",
        "webPrerequisitesReady",
    ):
        assert (
            _evaluate_ui_expression(
                controls[name]["visible"], {}, review=review, basics={"deploymentStage": stage}
            )
            is False
        )
    assert _evaluate_ui_expression(
        wizard["outputs"]["enablePrivateAnonymousWebUi"],
        {},
        review=review,
        basics={"deploymentStage": stage},
    ) is (stage == "complete")
    for name in WEB_INPUTS:
        assert (
            _evaluate_ui_expression(
                wizard["outputs"][name], {}, review=review, basics={"deploymentStage": stage}
            )
            == ""
        )


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
    wizard: dict, selection_names: dict, inventory: dict, valid: bool, reuse: bool | None
):
    names = _ui_controls(wizard, "names")
    context = {
        **selection_names,
        "containerAppsApi": inventory,
        "containerAppName": "ca-azbrief-kt",
    }
    network = _subnet_form(
        [
            _subnet(SUBNET_NAMES["peSubnetName"], "10.70.0.0/28"),
            _subnet(SUBNET_NAMES["foundrySubnetName"], "10.70.0.32/27", True),
            _subnet(SUBNET_NAMES["containerAppsSubnetName"], "10.70.0.64/27", True),
        ]
    )
    guards = [
        rule["isValid"]
        for rule in names["containerAppName"]["constraints"]["validations"]
        if "isValid" in rule
    ]
    assert (
        all(
            _resolve_selection(guard, context, network=network, stage="foundation")
            for guard in guards
        )
        is valid
    )
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
        self.authentication: dict | None = None
        self.scheduler: dict | None = None
        self.role_assignments: list[dict] = []
        self.connection_puts = 0

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
        if args[:3] == ("role", "assignment", "list"):
            return copy.deepcopy(self.role_assignments)
        if args[:3] == ("network", "vnet", "check-ip-address"):
            return {"available": self.free_ips}
        raise AssertionError(f"Unexpected CLI call: {args}")

    def get(self, resource_id: str, api_version: str) -> dict:
        self.calls.append(("get", resource_id, api_version))
        if resource_id == VNET:
            return copy.deepcopy(self.vnet)
        identity_id = (
            f"{GROUP}/providers/Microsoft.ManagedIdentity/userAssignedIdentities/"
            f"id-{self.values['containerAppName']}"
        )
        if resource_id == identity_id:
            return {"properties": {"principalId": UAMI_PRINCIPAL, "clientId": "configured"}}
        if (
            resource_id
            == f"{GROUP}/providers/Microsoft.Communication/communicationServices/approved-acs"
        ):
            return {
                "properties": {
                    "hostName": "approved.communication.azure.com",
                    "provisioningState": "Succeeded",
                }
            }
        if resource_id.endswith(f"/roleDefinitions/{EMAIL_ROLE}"):
            return {
                "properties": {
                    "type": "CustomRole",
                    "assignableScopes": [GROUP],
                    "permissions": [{"actions": sorted(kt.EMAIL_SENDER_ACTIONS)}],
                }
            }
        if (
            resource_id
            == (f"{GROUP}/providers/Microsoft.App/jobs/{self.values['schedulerJobName']}")
            and self.scheduler is not None
        ):
            return {
                "identity": {"type": "UserAssigned", "userAssignedIdentities": {identity_id: {}}},
                "properties": copy.deepcopy(self.scheduler),
            }
        if resource_id == (
            f"{GROUP}/providers/Microsoft.App/containerApps/"
            f"{self.values['containerAppName']}/authConfigs/current"
        ):
            if self.authentication is None:
                raise RuntimeError("Container App authentication is absent")
            return {"properties": copy.deepcopy(self.authentication)}
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
                        "category": ("AzureStorageAccount", "CosmosDb", "CognitiveSearch")[
                            index - 1
                        ],
                        "target": (
                            f"https://{self.values['storageAccountName']}.blob.core.windows.net/",
                            f"https://{self.values['cosmosAccountName']}.documents.azure.com:443/",
                            f"https://{self.values['searchServiceName']}.search.windows.net",
                        )[index - 1],
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
                if not values["reuseExistingFoundrySetup"]:
                    if self.project_host_exists:
                        raise RuntimeError(
                            "Connection agent-storage already exists and can only be "
                            "updated by its creator workspace"
                        )
                    self.connection_puts += 3
                self.project_exists = True
                self.project_host_exists = True
                self.project_host_name = "agents"
            app_id = f"{GROUP}/providers/Microsoft.App/containerApps/{values['containerAppName']}"
            self.resources[app_id] = {"id": app_id, "tags": {"deploymentProfile": kt.PROFILE}}
            identity_id = (
                f"{GROUP}/providers/Microsoft.ManagedIdentity/userAssignedIdentities/"
                f"id-{values['containerAppName']}"
            )
            self.resources[identity_id] = {
                "id": identity_id,
                "tags": {"deploymentProfile": kt.PROFILE},
            }
            self.application = _foundation_app(values)
            for key, (scope, role) in kt.control_plane_role_targets(values, GROUP).items():
                if not any(
                    row["scope"] == scope and row["roleDefinitionId"].endswith(role)
                    for row in self.role_assignments
                ):
                    self.role_assignments.append(
                        {
                            "principalId": UAMI_PRINCIPAL,
                            "scope": scope,
                            "roleDefinitionId": f"/subscriptions/{SUBSCRIPTION}/providers/Microsoft.Authorization/roleDefinitions/{role}",
                            "name": values["existingControlPlaneRoleAssignmentNames"].get(key)
                            or str(uuid5(NAMESPACE_URL, f"{scope}/{role}/{UAMI_PRINCIPAL}")),
                        }
                    )
            if values["deployCapabilityHost"] and values["deploySchedulerJob"]:
                job_id = f"{GROUP}/providers/Microsoft.App/jobs/{values['schedulerJobName']}"
                self.resources[job_id] = {
                    "id": job_id,
                    "tags": {"deploymentProfile": kt.PROFILE},
                }
                self.scheduler = {
                    "provisioningState": "Succeeded",
                    "environmentId": kt.endpoint_specs(values, GROUP)[4]["target"],
                    "workloadProfileName": "Consumption",
                    "configuration": {
                        "triggerType": "Manual",
                        "replicaTimeout": 43200,
                        "replicaRetryLimit": 0,
                        "manualTriggerConfig": {"parallelism": 1, "replicaCompletionCount": 1},
                        "registries": copy.deepcopy(
                            self.application["configuration"]["registries"]
                        ),
                        "secrets": [
                            secret
                            for secret in self.application["configuration"]["secrets"]
                            if secret["name"] != "microsoft-provider-authentication-secret"
                        ],
                    },
                    "template": {
                        "containers": [
                            {
                                "name": "azbrief-scheduler",
                                "image": values["bootstrapImage"],
                                "command": ["python", "-m", "src.scheduler"],
                                "resources": {"cpu": 1, "memory": "2Gi"},
                                "env": [
                                    {"name": name, "value": value}
                                    for name, value in {
                                        **kt.runtime_setup_env(values),
                                        "APPLICATIONINSIGHTS_CONNECTION_STRING": "",
                                        "AZURE_TENANT_ID": TENANT,
                                        "AZURE_SUBSCRIPTION_ID": SUBSCRIPTION,
                                        "AZURE_CLIENT_ID": "configured",
                                        "FOUNDRY_PROJECT_ENDPOINT": f"https://{values['foundryAccountName']}.services.ai.azure.com/api/projects/{values['projectName']}",
                                        "FOUNDRY_HOSTED_AGENT_NAME": values[
                                            "foundryHostedAgentName"
                                        ],
                                        "ARCHIVE_BLOB_CONTAINER_URL": f"https://{values['storageAccountName']}.blob.core.windows.net/azbrief-archive",
                                        "CHECKPOINT_BLOB_URL": f"https://{values['storageAccountName']}.blob.core.windows.net/azbrief-state/checkpoint.json",
                                        "ADMIN_CONFIG_BLOB_URL": next(
                                            e["value"]
                                            for e in self.application["template"]["containers"][0][
                                                "env"
                                            ]
                                            if e["name"] == "ADMIN_CONFIG_BLOB_URL"
                                        ),
                                        "MAX_CONCURRENT_ANALYSES": "1",
                                        "SCHEDULE_DISPATCH_ENABLED": "true",
                                        "RUN_TIME_BUDGET_S": "39600",
                                    }.items()
                                ]
                                + [{"name": "API_KEY", "secretRef": "orchestrator-api-key"}],
                            }
                        ]
                    },
                }
            if (
                values["deployCapabilityHost"]
                and not values["enablePrivateAnonymousWebUi"]
                and values["adminEntraClientId"]
                and values["adminEntraClientSecret"]
            ):
                fqdn = self.application["configuration"]["ingress"]["fqdn"]
                self.authentication = {
                    "platform": {"enabled": True},
                    "globalValidation": {"unauthenticatedClientAction": "AllowAnonymous"},
                    "httpSettings": {"requireHttps": True},
                    "identityProviders": {
                        "azureActiveDirectory": {
                            "enabled": True,
                            "registration": {
                                "clientId": values["adminEntraClientId"],
                                "clientSecretSettingName": "microsoft-provider-authentication-secret",
                                "openIdIssuer": f"https://login.microsoftonline.com/{TENANT}/v2.0",
                            },
                            "validation": {
                                "allowedAudiences": [
                                    f"api://{values['adminEntraClientId']}",
                                    values["adminEntraClientId"],
                                ]
                            },
                        }
                    },
                    "login": {
                        "allowedExternalRedirectUrls": [f"https://{fqdn}"],
                        "preserveUrlFragmentsForLogins": False,
                    },
                }
            if values["deployCapabilityHost"] and values["enablePrivateAnonymousWebUi"]:
                fqdn = self.application["configuration"]["ingress"]["fqdn"]
                self.authentication = {
                    "platform": {"enabled": False},
                    "globalValidation": {"unauthenticatedClientAction": "AllowAnonymous"},
                    "httpSettings": {"requireHttps": True},
                    "identityProviders": {"azureActiveDirectory": {"enabled": False}},
                    "login": {
                        "allowedExternalRedirectUrls": [f"https://{fqdn}"],
                        "preserveUrlFragmentsForLogins": False,
                    },
                }
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


def test_complete_stage_requires_existing_foundation(values: dict, vnet: dict):
    values.update(WEB_INPUTS)
    cli = FakeAzure(values, vnet)
    with pytest.raises(ValueError, match="Stage two requires a complete existing KT foundation"):
        kt.run(cli, values, "deploy", "complete")
    assert not cli.deployments


def test_private_anonymous_complete_stage_and_later_entra_upgrade(values: dict, vnet: dict):
    values["enablePrivateAnonymousWebUi"] = True
    cli = FakeAzure(values, vnet)
    kt.run(cli, values, "deploy", "foundation")
    assert cli.authentication is None
    cli.deployments.clear()
    kt.run(cli, values, "deploy", "complete")
    assert len(cli.deployments) == 1
    assert cli.application is not None and cli.authentication is not None
    env = {e["name"]: e for e in cli.application["template"]["containers"][0]["env"]}
    assert env["ADMIN_UI_ENABLED"]["value"] == env["ARCHIVE_UI_ENABLED"]["value"] == "true"
    assert env["ADMIN_REQUIRE_AUTH"]["value"] == env["ARCHIVE_REQUIRE_AUTH"]["value"] == "false"
    assert cli.authentication["platform"]["enabled"] is False
    assert cli.authentication["identityProviders"]["azureActiveDirectory"]["enabled"] is False
    assert cli.deployments[0]["reuseExistingApiKey"] is True
    cli.deployments.clear()
    kt.run(cli, values, "deploy", "complete")
    assert len(cli.deployments) == 1
    cli.deployments.clear()
    with pytest.raises(ValueError, match="cannot overwrite"):
        kt.run(cli, values, "deploy")
    assert not cli.deployments
    upgraded = {**values, **WEB_INPUTS, "enablePrivateAnonymousWebUi": False}
    kt.run(cli, upgraded, "deploy", "complete")
    assert cli.authentication["platform"]["enabled"] is True
    env = {e["name"]: e for e in cli.application["template"]["containers"][0]["env"]}
    assert env["ADMIN_REQUIRE_AUTH"]["value"] == env["ARCHIVE_REQUIRE_AUTH"]["value"] == "true"


def test_private_anonymous_does_not_downgrade_an_existing_authenticated_app(
    values: dict, vnet: dict
):
    values.update(WEB_INPUTS)
    cli = FakeAzure(values, vnet)
    kt.run(cli, values, "deploy")
    requested = {
        **values,
        **{name: "" for name in WEB_INPUTS},
        "enablePrivateAnonymousWebUi": True,
    }
    with pytest.raises(ValueError, match="refusing automatic anonymous downgrade"):
        kt.run(cli, requested, "deploy", "complete")
    assert len(cli.deployments) == 2


@pytest.mark.parametrize("field", ["platform", "provider"])
def test_private_anonymous_readback_requires_disabled_auth(values: dict, vnet: dict, field: str):
    values["enablePrivateAnonymousWebUi"] = True
    cli = FakeAzure(values, vnet)
    kt.run(cli, values, "deploy")
    assert cli.authentication is not None
    if field == "platform":
        cli.authentication["platform"]["enabled"] = True
    else:
        cli.authentication["identityProviders"]["azureActiveDirectory"]["enabled"] = True
    with pytest.raises(RuntimeError, match="EasyAuth does not match"):
        kt.verify_foundation(cli, cli.deployments[-1])


def test_private_anonymous_does_not_bypass_private_network_check(values: dict, vnet: dict):
    values["enablePrivateAnonymousWebUi"] = True
    cli = FakeAzure(values, vnet)
    kt.run(cli, values, "deploy", "foundation")
    cli.public_access = "Enabled"
    with pytest.raises(ValueError, match="public network access"):
        kt.run(cli, values, "deploy", "complete")
    assert len(cli.deployments) == 1


def test_complete_stage_configures_web_without_replaying_stage_one(values: dict, vnet: dict):
    values.update(WEB_INPUTS)
    cli = FakeAzure(values, vnet)
    kt.run(cli, values, "deploy", "foundation")
    assert len(cli.deployments) == 1
    assert cli.deployments[0]["deployCapabilityHost"] is False
    assert cli.authentication is None
    assert cli.application["template"]["scale"]["minReplicas"] == 0
    assert not any(
        secret["name"] == "microsoft-provider-authentication-secret"
        for secret in cli.application["configuration"]["secrets"]
    )
    cli.deployments.clear()
    kt.run(cli, values, "what-if", "complete")
    assert cli.deployments[0]["deployCapabilityHost"] is True
    assert cli.application["template"]["scale"]["minReplicas"] == 0
    cli.deployments.clear()
    kt.run(cli, values, "deploy", "complete")
    assert len(cli.deployments) == 1
    assert cli.deployments[0]["deployCapabilityHost"] is True
    assert cli.deployments[0]["reuseExistingApiKey"] is True
    env = {entry["name"]: entry for entry in cli.application["template"]["containers"][0]["env"]}
    assert env["ADMIN_UI_ENABLED"]["value"] == env["ARCHIVE_UI_ENABLED"]["value"] == "true"
    assert env["ADMIN_REQUIRE_AUTH"]["value"] == env["ARCHIVE_REQUIRE_AUTH"]["value"] == "true"
    assert env["ADMIN_ALLOWED_PRINCIPALS"]["value"] == WEB_INPUTS["adminAllowedPrincipals"]
    assert env["ADMIN_CONFIG_BLOB_URL"]["value"].endswith("azbrief-state/admin-config.json")
    assert cli.application["template"]["scale"]["minReplicas"] == 1
    assert cli.authentication["platform"]["enabled"] is True
    assert values["deployCapabilityHost"] is False
    cli.deployments.clear()
    kt.run(cli, values, "deploy", "complete")
    assert len(cli.deployments) == 1
    cli.deployments.clear()
    with pytest.raises(ValueError, match="cannot overwrite"):
        kt.run(cli, values, "deploy")
    assert not cli.deployments


@pytest.mark.parametrize(
    ("section", "field", "invalid"),
    [
        ("platform", "enabled", False),
        ("globalValidation", "unauthenticatedClientAction", "RedirectToLoginPage"),
        ("httpSettings", "requireHttps", False),
        ("registration", "clientId", "66666666-6666-6666-6666-666666666666"),
        ("registration", "clientSecretSettingName", "unapproved-secret"),
        ("registration", "openIdIssuer", "https://login.microsoftonline.com/foreign/v2.0"),
        ("validation", "allowedAudiences", ["unapproved-audience"]),
        ("login", "allowedExternalRedirectUrls", ["https://unapproved.invalid"]),
    ],
)
def test_complete_stage_auth_readback_rejects_drift(
    values: dict, vnet: dict, section: str, field: str, invalid: object
):
    values.update(WEB_INPUTS)
    cli = FakeAzure(values, vnet)
    kt.run(cli, values, "deploy")
    if section in {"registration", "validation"}:
        settings = cli.authentication["identityProviders"]["azureActiveDirectory"][section]
    else:
        settings = cli.authentication[section]
    settings[field] = invalid
    with pytest.raises(RuntimeError, match="EasyAuth does not match"):
        kt.verify_foundation(cli, cli.deployments[-1])
    assert len(cli.deployments) == 2


def test_unknown_mode_never_falls_through_to_deploy(values: dict, vnet: dict):
    cli = FakeAzure(values, vnet)
    with pytest.raises(ValueError, match="Unsupported KT operation"):
        kt.run(cli, values, "deployment")
    assert not cli.calls
    assert not cli.deployments


def test_unknown_stage_never_falls_through_to_deploy(values: dict, vnet: dict):
    cli = FakeAzure(values, vnet)
    with pytest.raises(ValueError, match="Unsupported KT stage"):
        kt.run(cli, values, "deploy", "unknown")
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


def _readiness_metadata(values: dict) -> dict:
    return {
        "ADMIN_READINESS_CONTAINER_ENVIRONMENTS": [values["containerAppsEnvironmentName"]],
        "ADMIN_READINESS_CONTAINER_APPS": [values["containerAppName"]],
        "ADMIN_READINESS_SUPPORT_RESOURCES": [
            {
                "id": key,
                "label": label,
                "name": name,
                "resource_type": resource_type,
                "api_version": api_version,
            }
            for key, label, name, resource_type, api_version in (
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


def _foundation_app(values: dict) -> dict:
    anonymous = bool(values["deployCapabilityHost"] and values["enablePrivateAnonymousWebUi"])
    authenticated = bool(
        values["deployCapabilityHost"]
        and not anonymous
        and values["adminEntraClientId"]
        and values["adminEntraClientSecret"]
    )
    admin = anonymous or (
        authenticated and any(part.strip() for part in values["adminAllowedPrincipals"].split(","))
    )
    archive = anonymous or (
        authenticated
        and (admin or any(part.strip() for part in values["archiveAllowedPrincipals"].split(",")))
    )
    fqdn = f"{values['containerAppName']}.kt-test.eastus2.azurecontainerapps.io"
    runtime_settings = {
        **kt.runtime_setup_env(values),
        "APPLICATIONINSIGHTS_CONNECTION_STRING": "",
    }
    flags = {
        "FOUNDRY_PROJECT_ENDPOINT": (
            f"https://{values['foundryAccountName']}.services.ai.azure.com/"
            f"api/projects/{values['projectName']}"
        ),
        "ADMIN_UI_ENABLED": str(admin).lower(),
        "ADMIN_REQUIRE_AUTH": str(not anonymous).lower(),
        "ARCHIVE_UI_ENABLED": str(archive).lower(),
        "ARCHIVE_REQUIRE_AUTH": str(not anonymous).lower(),
        "FEEDBACK_UI_ENABLED": "false",
        "MAX_CONCURRENT_ANALYSES": "1",
        "ADMIN_ALLOWED_PRINCIPALS": (
            values["adminAllowedPrincipals"] if authenticated and admin else ""
        ),
        "ARCHIVE_ALLOWED_PRINCIPALS": (
            values["archiveAllowedPrincipals"] if authenticated and archive else ""
        ),
        "ADMIN_CONFIG_BLOB_URL": (
            f"https://{values['storageAccountName']}.blob.core.windows.net/"
            "azbrief-state/admin-config.json"
            if admin
            else ""
        ),
        "ARCHIVE_BASE_URL": f"https://{fqdn}" if archive else "",
        "ADMIN_READINESS_RESOURCE_GROUP": "rg-kt-test",
        "ADMIN_READINESS_FOUNDRY_ACCOUNT": values["foundryAccountName"],
        "ADMIN_READINESS_FOUNDRY_PROJECT": values["projectName"],
        **{
            name: base64.b64encode(json.dumps(rows).encode("utf-8")).decode("ascii")
            for name, rows in _readiness_metadata(values).items()
        },
        **runtime_settings,
    }
    env = [
        {"name": name, "value": flags.get(name, "configured")}
        for name in sorted(
            (
                kt.FOUNDATION_ENV_NAMES
                | kt.WEB_ENV_NAMES
                | kt.READINESS_ENV_NAMES
                | kt.RUNTIME_SETUP_ENV_NAMES
            )
            - {"API_KEY"}
        )
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
            "scale": {"minReplicas": 1 if admin else 0, "maxReplicas": 1},
        },
        "configuration": {
            "activeRevisionsMode": "Single",
            "ingress": {"targetPort": 8000, "fqdn": fqdn},
            "secrets": [{"name": "orchestrator-api-key"}]
            + (
                [{"name": "ghcr-pull-token"}]
                if values["containerRegistryAuthMode"] == "Credentials"
                else []
            )
            + ([{"name": "microsoft-provider-authentication-secret"}] if authenticated else []),
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
    assert cli.connection_puts == 3
    assert cli.deployments[1]["reuseExistingFoundrySetup"] is False
    assert cli.deployments[3]["reuseExistingFoundrySetup"] is True


def _existing_foundry_inventories(names: dict) -> None:
    project_id = (
        f"{GROUP}/providers/Microsoft.CognitiveServices/accounts/"
        f"{names['foundryAccountName']}/projects/{names['projectName']}"
    )
    names["foundryProjectInventoryApi"] = {
        "value": [
            {
                "id": project_id,
                "name": f"{names['foundryAccountName']}/{names['projectName']}",
            }
        ]
    }
    names["foundryHostInventoryApi"] = {
        "value": [
            {
                "id": f"{project_id}/capabilityHosts/agents",
                "properties": {
                    "provisioningState": "Succeeded",
                    "storageConnections": ["agent-storage"],
                    "threadStorageConnections": ["agent-cosmos"],
                    "vectorStoreConnections": ["agent-search"],
                },
            }
        ]
    }
    names["foundryConnectionInventoryApi"] = {
        "value": [
            {
                "id": f"{project_id}/connections/{name}",
                "name": name,
                "properties": {
                    "category": category,
                    "authType": "AAD",
                    "target": endpoint,
                    "metadata": {"ResourceId": f"{GROUP}/providers/{resource_type}/{names[key]}"},
                },
            }
            for name, category, resource_type, key, endpoint in (
                (
                    "agent-storage",
                    "AzureStorageAccount",
                    "Microsoft.Storage/storageAccounts",
                    "storageAccountName",
                    f"https://{names['storageAccountName']}.blob.core.windows.net/",
                ),
                (
                    "agent-cosmos",
                    "CosmosDb",
                    "Microsoft.DocumentDB/databaseAccounts",
                    "cosmosAccountName",
                    f"https://{names['cosmosAccountName']}.documents.azure.com:443/",
                ),
                (
                    "agent-search",
                    "CognitiveSearch",
                    "Microsoft.Search/searchServices",
                    "searchServiceName",
                    f"https://{names['searchServiceName']}.search.windows.net",
                ),
            )
        ]
    }


def _foundry_reuse_rules(wizard: dict) -> list[str]:
    return [
        rule["isValid"]
        for rule in _ui_controls(wizard, "names")["containerAppName"]["constraints"]["validations"]
        if rule["message"].startswith(
            ("Foundry 프로젝트", "계정에 다른", "최초 2단계", "기존 프로젝트", "기존 agent-")
        )
    ]


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("eager", [False, True])
def test_portal_creates_initial_setup_and_reuses_only_matching_completed_setup(
    wizard: dict, selection_names: dict, existing: bool, eager: bool
):
    if existing:
        _existing_foundry_inventories(selection_names)
    assert all(
        _resolve_selection(rule, selection_names, eager_conditionals=eager)
        for rule in _foundry_reuse_rules(wizard)
    )
    assert (
        _resolve_selection(wizard["outputs"]["reuseExistingFoundrySetup"], selection_names)
        is existing
    )
    assert (
        _resolve_selection(
            wizard["outputs"]["reuseExistingFoundrySetup"], selection_names, stage="foundation"
        )
        is False
    )


@pytest.mark.parametrize(
    "control",
    ["foundryProjectInventoryApi", "foundryHostInventoryApi", "foundryConnectionInventoryApi"],
)
@pytest.mark.parametrize("failure", ["pending", "denied", "paged", "no-value"])
def test_portal_unreadable_foundry_inventory_never_authorizes_new_setup(
    wizard: dict, selection_names: dict, control: str, failure: str
):
    _existing_foundry_inventories(selection_names)
    if failure == "pending":
        selection_names[control] = None
    elif failure == "denied":
        selection_names[control]["error"] = {"code": "Forbidden"}
    elif failure == "paged":
        selection_names[control]["nextLink"] = "private-continuation-not-for-display"
    else:
        selection_names[control].pop("value")
    assert not all(
        _resolve_selection(rule, selection_names, eager_conditionals=True)
        for rule in _foundry_reuse_rules(wizard)
    )


@pytest.mark.parametrize("existing", [False, True])
def test_foundry_safety_reads_follow_exact_manual_names_and_never_get_missing_children(
    wizard: dict, selection_names: dict, existing: bool
):
    if existing:
        _existing_foundry_inventories(selection_names)
    controls = _ui_controls(wizard, "names")
    account_path = (
        f"{GROUP}/providers/Microsoft.CognitiveServices/accounts/"
        f"{selection_names['foundryAccountName']}"
    )
    assert (
        _resolve_selection(
            controls["foundryProjectInventoryApi"]["request"]["path"], selection_names
        )
        == f"{account_path}/projects?api-version=2025-06-01"
    )
    for name, child, api in (
        ("foundryHostInventoryApi", "capabilityHosts", "2025-04-01-preview"),
        ("foundryConnectionInventoryApi", "connections", "2025-06-01"),
    ):
        expression = controls[name]["request"]["path"]
        path = _resolve_selection(expression, selection_names)
        if existing:
            assert path == (
                f"{account_path}/projects/{selection_names['projectName']}/{child}?api-version={api}"
            )
            selection_names["projectName"] = "different-project"
            assert "/resources?" in _resolve_selection(expression, selection_names)
            selection_names["projectName"] = RESOURCE_NAME_DEFAULTS["projectName"]
        else:
            assert "/resources?" in path
        assert "/resources?" in _resolve_selection(
            expression, selection_names, mode="New", stage="foundation"
        )


@pytest.mark.parametrize("matching", [False, True])
def test_portal_telemetry_connection_is_reused_only_for_the_same_component(
    wizard: dict, selection_names: dict, matching: bool
):
    _existing_foundry_inventories(selection_names)
    insight_id = f"{GROUP}/providers/Microsoft.Insights/components/approved-insights"
    selection_names["foundryConnectionInventoryApi"]["value"].append(
        {
            "name": "azbrief-app-insights",
            "properties": {
                "category": "AppInsights",
                "authType": "ProjectManagedIdentity",
                "target": insight_id if matching else f"{insight_id}-foreign",
                "metadata": {"ResourceId": insight_id},
            },
        }
    )
    review = {"configureTelemetry": True, "applicationInsightsName": "approved-insights"}
    arguments = {
        "names": selection_names,
        "resource_group": {"name": "rg-kt-test", "mode": "Existing"},
        "subscription": {"subscriptionId": SUBSCRIPTION},
        "basics": {"deploymentStage": "complete"},
        "review": review,
    }
    output = wizard["outputs"]["reuseExistingTelemetryConnection"]
    assert _evaluate_ui_expression(output, **arguments) is matching
    validation = next(
        rule["isValid"]
        for rule in _ui_controls(wizard, "review")["applicationInsightsName"]["constraints"][
            "validations"
        ]
        if "isValid" in rule
    )
    assert _evaluate_ui_expression(validation, **arguments) is matching


@pytest.mark.parametrize(
    "change",
    [
        "project",
        "host-state",
        "host-name",
        "host-binding",
        "storage-id",
        "storage-url",
        "cosmos-category",
        "search-auth",
    ],
)
def test_portal_reuse_blocks_changed_project_or_byo_bindings(
    wizard: dict, selection_names: dict, change: str
):
    _existing_foundry_inventories(selection_names)
    host = selection_names["foundryHostInventoryApi"]["value"][0]
    connections = selection_names["foundryConnectionInventoryApi"]["value"]
    if change == "project":
        selection_names["projectName"] = selection_names["projectName"].replace("-", "")
    elif change == "host-state":
        host["properties"]["provisioningState"] = "Creating"
    elif change == "host-name":
        host["id"] += "-automatic"
    elif change == "host-binding":
        host["properties"]["storageConnections"] = ["unrelated-storage"]
    elif change == "storage-id":
        connections[0]["properties"]["metadata"]["ResourceId"] += "-foreign"
    elif change == "storage-url":
        connections[0]["properties"]["target"] = "https://unrelated.invalid"
    elif change == "cosmos-category":
        connections[1]["properties"]["category"] = "unrelated"
    else:
        connections[2]["properties"]["authType"] = "ApiKey"
    assert not all(
        _resolve_selection(rule, selection_names, eager_conditionals=True)
        for rule in _foundry_reuse_rules(wizard)
    )


def test_initial_stage_two_cannot_change_the_foundation_project_name(values: dict, vnet: dict):
    cli = FakeAzure(values, vnet)
    kt.run(cli, values, "deploy", "foundation")
    cli.deployments.clear()
    with pytest.raises(ValueError, match="exact project name bound by stage one"):
        kt.run(cli, {**values, "projectName": "different-project"}, "deploy", "complete")
    assert not cli.deployments


def test_near_name_project_cannot_take_another_projects_connections(values: dict, vnet: dict):
    cli = FakeAzure(values, vnet)
    kt.run(cli, values, "deploy")
    cli.deployments.clear()
    with pytest.raises(ValueError, match="exact original project name"):
        kt.run(
            cli,
            {**values, "projectName": values["projectName"].replace("-", "")},
            "deploy",
            "complete",
        )
    assert not cli.deployments
    assert cli.connection_puts == 3


class OperationalAzure(FakeAzure):
    """Offline model/email inventory with observable immutable service reuse."""

    def __init__(self, values: dict, vnet: dict):
        super().__init__(values, vnet)
        self.model_deployments: dict[str, dict] = {}
        self.quota = {"gpt-5.6-terra": 500, "gpt-5.6-luna": 0}
        self.model_writes = 0
        self.email_writes = 0
        self.email_service: dict | None = None
        self.email_domain: dict | None = None
        self.communication: dict | None = None
        self.custom_roles: list[dict] = []

    def json(self, *args: str) -> Any:
        if args[:3] == ("role", "definition", "list"):
            self.calls.append(args)
            return copy.deepcopy(self.custom_roles)
        return super().json(*args)

    def collection(self, resource_id: str, api_version: str) -> list[dict]:
        if resource_id.endswith("/deployments"):
            self.calls.append(("collection", resource_id, api_version))
            return copy.deepcopy(list(self.model_deployments.values()))
        if resource_id.endswith("/models"):
            return [
                {
                    "name": name,
                    "version": self.values["modelVersion"],
                    "capabilities": {"responses": "true", "agentsV2": "true"},
                    "skus": [
                        {"name": "GlobalStandard", "usageName": f"OpenAI.GlobalStandard.{name}"}
                    ],
                }
                for name in self.quota
            ]
        if resource_id.endswith("/usages"):
            return [
                {
                    "name": {"value": f"OpenAI.GlobalStandard.{name}"},
                    "currentValue": 1000 - remaining,
                    "limit": 1000,
                }
                for name, remaining in self.quota.items()
            ]
        if resource_id.endswith("/domains"):
            return [copy.deepcopy(self.email_domain)] if self.email_domain else []
        return super().collection(resource_id, api_version)

    def get(self, resource_id: str, api_version: str) -> dict:
        if self.email_service and resource_id == self.email_service["id"]:
            return copy.deepcopy(self.email_service)
        if self.communication and resource_id == self.communication["id"]:
            return copy.deepcopy(self.communication)
        return super().get(resource_id, api_version)

    def deploy(self, values: dict, mode: str, name: str) -> dict:
        effective = copy.deepcopy(values)
        if mode == "create" and values["deployCapabilityHost"]:
            if values["provisionModels"]:
                for role, alias, model, capacity in (
                    (
                        "Core",
                        values["modelDeploymentName"],
                        values["coreModelName"],
                        values["modelCapacity"],
                    ),
                    (
                        "Simple",
                        values["simpleModelDeploymentName"],
                        (
                            values["coreModelName"]
                            if values["useCoreForSimpleModel"]
                            else values["simpleModelName"]
                        ),
                        values["simpleModelCapacity"],
                    ),
                ):
                    if not values[f"reuse{role}Model"]:
                        assert (
                            alias not in self.model_deployments
                        ), "Existing models must not be PUT"
                        self.model_writes += 1
                        self.model_deployments[alias] = {
                            "name": alias,
                            "sku": {"name": "GlobalStandard", "capacity": capacity},
                            "properties": {
                                "provisioningState": "Succeeded",
                                "model": {
                                    "name": model,
                                    "format": "OpenAI",
                                    "version": values["modelVersion"],
                                },
                            },
                        }
            if values["provisionEmail"]:
                email_id = f"{GROUP}/providers/Microsoft.Communication/emailServices/{values['emailServiceName']}"
                acs_id = f"{GROUP}/providers/Microsoft.Communication/communicationServices/{values['communicationServicesName']}"
                if not values["reuseEmailService"]:
                    self.email_writes += 1
                    self.email_service = {
                        "id": email_id,
                        "properties": {
                            "provisioningState": "Succeeded",
                            "dataLocation": values["emailDataLocation"],
                        },
                    }
                    self.resources[email_id] = {"id": email_id}
                if not values["reuseEmailDomain"]:
                    self.email_writes += 1
                    self.email_domain = {
                        "id": f"{email_id}/domains/AzureManagedDomain",
                        "name": "AzureManagedDomain",
                        "properties": {
                            "provisioningState": "Succeeded",
                            "domainManagement": "AzureManaged",
                            "fromSenderDomain": "test-only.azurecomm.net",
                        },
                    }
                if not values["reuseCommunicationService"]:
                    self.email_writes += 1
                    host_name = (
                        self.communication["properties"]["hostName"]
                        if self.communication
                        else "test-only.asiapacific.communication.azure.com"
                    )
                    self.communication = {
                        "id": acs_id,
                        "properties": {
                            "provisioningState": "Succeeded",
                            "dataLocation": values["emailDataLocation"],
                            "hostName": host_name,
                            "linkedDomains": [
                                f"{email_id}/domains/AzureManagedDomain",
                                *values["existingLinkedEmailDomains"],
                            ],
                        },
                        "tags": values["existingCommunicationTags"],
                        "identity": values["existingCommunicationIdentity"],
                    }
                    self.resources[acs_id] = {"id": acs_id}
                if not values["reuseEmailSenderRole"]:
                    self.custom_roles.append(
                        {
                            "name": values["emailSenderRoleDefinitionId"],
                            "roleName": f"AzBrief Email Sender {GROUP.casefold()}/{values['communicationServicesName'].casefold()}",
                            "permissions": [{"actions": sorted(kt.EMAIL_SENDER_ACTIONS)}],
                        }
                    )
                assert self.communication and self.email_domain
                effective["communicationServicesEndpoint"] = (
                    f"https://{self.communication['properties']['hostName']}"
                )
                effective["emailSenderAddress"] = (
                    f"DoNotReply@{self.email_domain['properties']['fromSenderDomain']}"
                )
        return super().deploy(effective, mode, name)


def _automatic_services(values: dict) -> dict:
    return {
        **values,
        "provisionModels": True,
        "provisionEmail": True,
        "modelDeploymentName": "gpt-5-terra",
        "simpleModelDeploymentName": "gpt-5-luna",
        "communicationServicesName": f"acs-{values['containerAppName']}",
        "emailServiceName": f"email-{values['containerAppName']}",
    }


def test_model_email_defaults_are_real_stage_two_resources(template: dict):
    assert template["parameters"]["provisionModels"]["defaultValue"] is True
    assert template["parameters"]["provisionEmail"]["defaultValue"] is True
    models = template["resources"]["models"]
    email = template["resources"]["email"]
    assert (
        "deployCapabilityHost" in models["condition"] and "provisionModels" in models["condition"]
    )
    assert "automaticEmail" in email["condition"]
    deployments = _resources(models["properties"]["template"])
    deployments = [row for row in deployments if row["type"].endswith("/deployments")]
    assert len(deployments) == 2
    assert all("reuse" in row["condition"].casefold() for row in deployments)
    assert all(row["properties"]["versionUpgradeOption"] == "NoAutoUpgrade" for row in deployments)
    assert "coreDeploymentName" in " ".join(deployments[1]["dependsOn"])
    services = _resources(email["properties"]["template"])
    assert any(row["type"] == "Microsoft.Communication/emailServices/domains" for row in services)
    role = next(row for row in services if row["type"] == "Microsoft.Authorization/roleDefinitions")
    assert set(role["properties"]["permissions"][0]["actions"]) == {
        "Microsoft.Communication/CommunicationServices/read",
        "Microsoft.Communication/CommunicationServices/write",
        "Microsoft.Communication/EmailServices/write",
    }
    assert "listKeys" not in json.dumps(email)


def test_automatic_services_create_and_replay_without_model_or_domain_writes(
    values: dict, vnet: dict
):
    values = _automatic_services(values)
    values["enablePrivateAnonymousWebUi"] = True
    cli = OperationalAzure(values, vnet)
    kt.run(cli, values, "deploy")
    assert cli.model_writes == 2 and cli.email_writes == 3
    assert cli.deployments[1]["useCoreForSimpleModel"] is True
    assert cli.model_deployments["gpt-5-luna"]["properties"]["model"]["name"] == "gpt-5.6-terra"
    before = copy.deepcopy((cli.model_deployments, cli.email_domain, cli.communication))
    kt.run(cli, values, "deploy", "complete")
    assert cli.model_writes == 2 and cli.email_writes == 3
    assert before == (cli.model_deployments, cli.email_domain, cli.communication)
    assert cli.deployments[-1]["reuseCoreModel"] is True
    assert cli.deployments[-1]["reuseSimpleModel"] is True
    assert cli.deployments[-1]["reuseEmailSenderRole"] is True
    assert cli.deployments[-1]["emailRecipientAddress"] == ""


def test_models_prefer_luna_when_its_quota_is_available(values: dict, vnet: dict):
    values = _automatic_services(values)
    cli = OperationalAzure(values, vnet)
    cli.quota["gpt-5.6-luna"] = 500
    kt.run(cli, values, "deploy")
    assert cli.deployments[1]["useCoreForSimpleModel"] is False
    assert cli.model_deployments["gpt-5-luna"]["properties"]["model"]["name"] == "gpt-5.6-luna"


def test_model_quota_failure_does_not_modify_other_allocations(values: dict, vnet: dict):
    values = _automatic_services(values)
    cli = OperationalAzure(values, vnet)
    kt.run(cli, values, "deploy", "foundation")
    cli.quota["gpt-5.6-terra"] = 300
    before = copy.deepcopy(cli.quota)
    cli.deployments.clear()
    with pytest.raises(ValueError, match="fallback quota"):
        kt.run(cli, values, "deploy", "complete")
    assert not cli.deployments and cli.quota == before


def test_partial_existing_email_is_completed_without_losing_identity_or_domain_links(
    values: dict, vnet: dict
):
    values = _automatic_services(values)
    cli = OperationalAzure(values, vnet)
    kt.run(cli, values, "deploy", "foundation")
    acs_id = f"{GROUP}/providers/Microsoft.Communication/communicationServices/{values['communicationServicesName']}"
    cli.resources[acs_id] = {"id": acs_id}
    cli.communication = {
        "id": acs_id,
        "identity": {"type": "SystemAssigned", "principalId": "existing"},
        "tags": {"operator": "preserved"},
        "properties": {
            "provisioningState": "Succeeded",
            "hostName": "test-only.communication.azure.com",
            "dataLocation": "Canada",
            "linkedDomains": ["/existing/approved/domain"],
        },
    }
    kt.run(cli, values, "deploy", "complete")
    assert cli.communication["identity"]["type"] == "SystemAssigned"
    assert cli.communication["tags"] == {"operator": "preserved"}
    assert "/existing/approved/domain" in cli.communication["properties"]["linkedDomains"]
    assert cli.email_service["properties"]["dataLocation"] == "Canada"


def _automatic_service_form(names: dict, existing: bool = False) -> dict:
    account = (
        f"{GROUP}/providers/Microsoft.CognitiveServices/accounts/{names['foundryAccountName']}"
    )
    names["modelDeploymentsApi"] = {
        "value": (
            [
                {
                    "id": f"{account}/deployments/{alias}",
                    "name": alias,
                    "properties": {
                        "provisioningState": "Succeeded",
                        "model": {"format": "OpenAI", "name": "gpt-5.6-terra"},
                    },
                }
                for alias in ("gpt-5-terra", "gpt-5-luna")
            ]
            if existing
            else []
        )
    }
    names["modelQuotaApi"] = {
        "value": [
            {
                "name": {"value": "OpenAI.GlobalStandard.gpt-5.6-terra"},
                "currentValue": 500,
                "limit": 1000,
            },
            {
                "name": {"value": "OpenAI.GlobalStandard.gpt-5.6-luna"},
                "currentValue": 1000,
                "limit": 1000,
            },
        ]
    }
    names["emailResourcesApi"] = {"value": []}
    review = {
        "provisionModels": True,
        "provisionEmail": True,
        "modelDeploymentName": "gpt-5-terra",
        "simpleModelDeploymentName": "gpt-5-luna",
        "communicationServicesName": "",
        "emailServiceName": "",
        "communicationServiceApi": {"value": []},
        "emailServiceApi": {"value": []},
        "emailDomainsApi": {"value": []},
        "emailRolesApi": {"value": []},
    }
    if existing:
        acs_id = f"{GROUP}/providers/Microsoft.Communication/communicationServices/acs-{names['containerAppName']}"
        email_id = f"{GROUP}/providers/Microsoft.Communication/emailServices/email-{names['containerAppName']}"
        domain_id = f"{email_id}/domains/AzureManagedDomain"
        review["communicationServiceApi"] = {
            "id": acs_id,
            "properties": {
                "provisioningState": "Succeeded",
                "hostName": "test.communication.azure.com",
                "dataLocation": "Asia Pacific",
                "linkedDomains": [domain_id],
            },
        }
        review["emailServiceApi"] = {
            "id": email_id,
            "properties": {
                "provisioningState": "Succeeded",
                "dataLocation": "Asia Pacific",
            },
        }
        review["emailDomainsApi"] = {
            "value": [
                {
                    "id": domain_id,
                    "name": "AzureManagedDomain",
                    "properties": {
                        "domainManagement": "AzureManaged",
                        "provisioningState": "Succeeded",
                        "fromSenderDomain": "test.azurecomm.net",
                    },
                }
            ]
        }
        review["emailRolesApi"] = {
            "value": [
                {
                    "name": EMAIL_ROLE,
                    "properties": {
                        "roleName": f"AzBrief Email Sender {GROUP.casefold()}/acs-{names['containerAppName']}",
                        "permissions": [{"actions": sorted(kt.EMAIL_SENDER_ACTIONS)}],
                    },
                }
            ]
        }
    return {
        "names": names,
        "review": review,
        "basics": {"deploymentStage": "complete"},
        "resource_group": {"name": "rg-kt-test", "mode": "Existing"},
        "subscription": {"subscriptionId": SUBSCRIPTION},
        "network": {"virtualNetwork": {"location": "eastus2"}},
        "eager_conditionals": True,
    }


@pytest.mark.parametrize("existing", [False, True])
def test_portal_automatic_services_are_new_or_reused_without_operator_answers(
    wizard: dict, selection_names: dict, existing: bool
):
    arguments = _automatic_service_form(selection_names, existing)
    controls = _ui_controls(wizard, "review")
    assert controls["provisionModels"]["defaultValue"] is True
    assert controls["provisionEmail"]["defaultValue"] is True
    for rule in controls["automaticServicesGuard"]["constraints"]["validations"]:
        assert _evaluate_ui_expression(rule["isValid"], **arguments) is True, rule["message"]
    for flag in (
        "reuseCoreModel",
        "reuseSimpleModel",
        "reuseEmailDomain",
        "reuseEmailService",
        "reuseCommunicationService",
        "reuseEmailSenderRole",
    ):
        assert _evaluate_ui_expression(wizard["outputs"][flag], **arguments) is existing
    assert _evaluate_ui_expression(wizard["outputs"]["useCoreForSimpleModel"], **arguments) is (
        not existing
    )
    assert _evaluate_ui_expression(
        wizard["outputs"]["existingEmailSenderRoleDefinitionId"], **arguments
    ) == (EMAIL_ROLE if existing else "")


@pytest.mark.parametrize(
    "change",
    [
        "quota-missing",
        "quota-denied",
        "quota-unknown",
        "quota-insufficient",
        "model-foreign",
        "model-failed",
        "email-denied",
        "email-foreign",
        "domain-foreign",
        "email-location",
        "role-wildcard",
        "role-extra-block",
        "empty-alias",
    ],
)
def test_portal_automatic_services_block_unknown_or_incompatible_evidence(
    wizard: dict, selection_names: dict, change: str
):
    arguments = _automatic_service_form(selection_names, existing=not change.startswith("quota"))
    review = arguments["review"]
    if change == "quota-missing":
        selection_names["modelQuotaApi"] = {}
    elif change == "quota-denied":
        selection_names["modelQuotaApi"]["error"] = {"code": "Forbidden"}
    elif change == "quota-unknown":
        selection_names["modelQuotaApi"]["value"][1]["currentValue"] = None
    elif change == "quota-insufficient":
        selection_names["modelQuotaApi"]["value"][0]["currentValue"] = 900
    elif change == "model-foreign":
        selection_names["modelDeploymentsApi"]["value"][0]["id"] += "-foreign"
    elif change == "model-failed":
        selection_names["modelDeploymentsApi"]["value"][0]["properties"][
            "provisioningState"
        ] = "Failed"
    elif change == "email-denied":
        review["communicationServiceApi"]["error"] = {"code": "Forbidden"}
    elif change == "email-foreign":
        review["communicationServiceApi"]["id"] += "-foreign"
    elif change == "domain-foreign":
        review["emailDomainsApi"]["value"][0]["id"] += "-foreign"
    elif change == "email-location":
        review["communicationServiceApi"]["properties"]["dataLocation"] = "Canada"
    elif change == "role-wildcard":
        review["emailRolesApi"]["value"][0]["properties"]["permissions"][0]["actions"] = ["*"]
    elif change == "role-extra-block":
        review["emailRolesApi"]["value"][0]["properties"]["permissions"].append({"actions": ["*"]})
    else:
        review["modelDeploymentName"] = ""
    rules = _ui_controls(wizard, "review")["automaticServicesGuard"]["constraints"]["validations"]
    assert not all(_evaluate_ui_expression(rule["isValid"], **arguments) for rule in rules)


def test_automatic_role_inventory_cannot_silently_treat_an_object_as_absence(
    monkeypatch: pytest.MonkeyPatch, values: dict, vnet: dict
):
    values = _automatic_services(values)
    cli = OperationalAzure(values, vnet)
    kt.run(cli, values, "deploy", "foundation")
    original = cli.json

    def json_request(*args: str) -> Any:
        return {} if args[:3] == ("role", "definition", "list") else original(*args)

    monkeypatch.setattr(cli, "json", json_request)
    cli.deployments.clear()
    with pytest.raises(ValueError, match="complete array"):
        kt.run(cli, values, "deploy", "complete")
    assert not cli.deployments


def test_arm_inventory_array_shape_is_required_before_creation(monkeypatch: pytest.MonkeyPatch):
    cli = kt.AzureCli(SUBSCRIPTION, TENANT, "rg-kt-test")
    monkeypatch.setattr(cli, "get", lambda *_args: {"value": {}})
    with pytest.raises(ValueError, match="must be an array"):
        cli.collection(
            f"{GROUP}/providers/Microsoft.Communication/emailServices", kt.COMMUNICATION_API
        )


@pytest.mark.parametrize("error", [ValueError("invalid inventory"), RuntimeError("Forbidden")])
def test_failed_connection_inventory_never_turns_into_a_connection_create_request(
    monkeypatch: pytest.MonkeyPatch, values: dict, vnet: dict, error: Exception
):
    cli = FakeAzure(values, vnet)
    kt.run(cli, values, "deploy")
    original = cli.collection

    def collection(resource_id: str, api_version: str) -> list[dict]:
        if resource_id.endswith("/connections"):
            raise error
        return original(resource_id, api_version)

    monkeypatch.setattr(cli, "collection", collection)
    cli.deployments.clear()
    with pytest.raises(type(error), match=str(error)):
        kt.run(cli, values, "deploy", "complete")
    assert not cli.deployments
    assert cli.connection_puts == 3


@pytest.mark.parametrize("field", ["target", "category", "authType"])
def test_cli_reuse_requires_real_connection_properties_not_only_resource_id(
    monkeypatch: pytest.MonkeyPatch, values: dict, vnet: dict, field: str
):
    cli = FakeAzure(values, vnet)
    kt.run(cli, values, "deploy")
    original = cli.collection

    def collection(resource_id: str, api_version: str) -> list[dict]:
        result = original(resource_id, api_version)
        if resource_id.endswith("/connections"):
            result[0]["properties"][field] = "unrelated"
        return result

    monkeypatch.setattr(cli, "collection", collection)
    cli.deployments.clear()
    with pytest.raises(ValueError, match="Refusing to redirect"):
        kt.run(cli, values, "deploy", "complete")
    assert not cli.deployments


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


@pytest.mark.parametrize(
    ("overrides", "error"),
    [
        ({"adminEntraClientId": ""}, "Authenticated web setup requires"),
        ({"adminEntraClientSecret": ""}, "Authenticated web setup requires"),
        ({"adminAllowedPrincipals": " , , "}, "Authenticated web setup requires"),
        ({"adminEntraClientId": "not-a-guid"}, "client ID GUID"),
        ({"adminEntraClientSecret": 123}, "must be a string"),
        ({"adminEntraClientSecret": "secret with whitespace"}, "must not contain whitespace"),
        ({"adminAllowedPrincipals": "object id"}, "comma-separated"),
    ],
)
def test_kt_rejects_incomplete_or_invalid_web_inputs(
    tmp_path: Path, values: dict, overrides: dict[str, object], error: str
):
    values.update(WEB_INPUTS)
    values.update(overrides)
    path = tmp_path / "parameters.json"
    path.write_text(
        json.dumps(
            {
                "parameters": {
                    name: {"value": value}
                    for name, value in values.items()
                    if name not in kt.INTERNAL_PARAMETERS
                }
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match=error):
        kt.load_parameters(path)


@pytest.mark.parametrize("field", list(WEB_INPUTS))
def test_private_anonymous_rejects_unused_entra_inputs(tmp_path: Path, values: dict, field: str):
    values["enablePrivateAnonymousWebUi"] = True
    values[field] = WEB_INPUTS[field] or "reader@example.invalid"
    path = tmp_path / "parameters.json"
    path.write_text(
        json.dumps(
            {
                "parameters": {
                    name: {"value": value}
                    for name, value in values.items()
                    if name not in kt.INTERNAL_PARAMETERS
                }
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="must not carry unused Entra inputs"):
        kt.load_parameters(path)


def test_kt_normalizes_web_inputs_and_supports_archive_only(tmp_path: Path, values: dict):
    values.update(WEB_INPUTS)
    values["adminEntraClientId"] = f" {WEB_INPUTS['adminEntraClientId']} "
    values["adminAllowedPrincipals"] = ""
    values["archiveAllowedPrincipals"] = " reader@example.invalid, , another@example.invalid "
    path = tmp_path / "parameters.json"
    path.write_text(
        json.dumps(
            {
                "parameters": {
                    name: {"value": value}
                    for name, value in values.items()
                    if name not in kt.INTERNAL_PARAMETERS
                }
            }
        ),
        encoding="utf-8",
    )
    loaded = kt.load_parameters(path)
    assert loaded["adminEntraClientId"] == WEB_INPUTS["adminEntraClientId"]
    assert loaded["archiveAllowedPrincipals"] == "reader@example.invalid,another@example.invalid"
    assert kt._web_ui_flags(loaded) == (False, False)
    loaded["deployCapabilityHost"] = True
    assert kt._web_ui_flags(loaded) == (False, True)


@pytest.mark.parametrize("reused", ["apiKey", "containerRegistryPassword"])
def test_web_secret_must_be_distinct_from_other_credentials(
    tmp_path: Path, values: dict, reused: str
):
    values.update(WEB_INPUTS)
    values["adminEntraClientSecret"] = values[reused]
    path = tmp_path / "parameters.json"
    path.write_text(
        json.dumps(
            {
                "parameters": {
                    name: {"value": value}
                    for name, value in values.items()
                    if name not in kt.INTERNAL_PARAMETERS
                }
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="must be different"):
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
    values.update(WEB_INPUTS)
    cli = FakeAzure(values, vnet)
    monkeypatch.setattr(
        cli,
        "deploy",
        lambda *_: {
            "status": "Succeeded",
            "apiKey": values["apiKey"],
            "changes": [{"before": values["apiKey"], "after": values["containerRegistryPassword"]}],
            "adminEntraClientSecret": values["adminEntraClientSecret"],
            "unlabelled": values["adminEntraClientSecret"],
        },
    )
    kt.run(cli, values, "what-if")
    captured = capsys.readouterr()
    output = captured.out + captured.err + caplog.text
    assert "kt_validation_result" in output
    assert values["apiKey"] not in output
    assert values["containerRegistryPassword"] not in output
    assert values["adminEntraClientSecret"] not in output
    assert "[REDACTED]" in output


def test_kt_failed_cli_redacts_secrets_and_removes_parameter_file(
    monkeypatch: pytest.MonkeyPatch, values: dict
):
    values.update(WEB_INPUTS)
    cli = kt.AzureCli(SUBSCRIPTION, TENANT, "rg-kt-test")
    paths: list[Path] = []

    def fail(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        path = Path(args[args.index("--parameters") + 1][1:])
        parameters = json.loads(path.read_text(encoding="utf-8"))["parameters"]
        assert parameters["apiKey"]["value"] == values["apiKey"]
        assert (
            parameters["containerRegistryPassword"]["value"] == values["containerRegistryPassword"]
        )
        assert parameters["adminEntraClientSecret"]["value"] == values["adminEntraClientSecret"]
        assert values["apiKey"] not in args
        assert values["containerRegistryPassword"] not in args
        assert values["adminEntraClientSecret"] not in args
        paths.append(path)
        return subprocess.CompletedProcess(
            args,
            1,
            stdout="",
            stderr=(
                f"Failure: {values['apiKey']} / {values['containerRegistryPassword']} "
                f"/ {values['adminEntraClientSecret']}"
            ),
        )

    monkeypatch.setattr(kt.shutil, "which", lambda _: "az-test")
    monkeypatch.setattr(kt.subprocess, "run", fail)
    with pytest.raises(RuntimeError) as failure:
        cli.deploy(values, "what-if", "offline-failure")
    assert "[REDACTED]" in str(failure.value)
    assert values["apiKey"] not in str(failure.value)
    assert values["containerRegistryPassword"] not in str(failure.value)
    assert values["adminEntraClientSecret"] not in str(failure.value)
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
