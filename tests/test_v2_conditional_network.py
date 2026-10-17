"""Resource-first network bindings retain exact private owners without redundant subnet inputs."""

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from scripts import deploy_v2
from scripts.deploy_kt import normalize_region, plan_subnets
from tests import test_deployment_v2 as fixtures
from tests import test_v2_shared_resources as shared_fixtures
from tests.test_deployment_v2 import IMAGE, SUBSCRIPTION, FakeV2Azure, template
from tests.test_kt_deployment import _evaluate_ui_expression
from tests.test_v2_shared_resources import shared_fixture

ROLE_FIELDS = ("peSubnetName", "foundrySubnetName", "containerAppsSubnetName")
OWNERS = ("foundryAccountMode", "containerAppsEnvironmentMode")


@pytest.fixture(autouse=True)
def explicit_fixture_subnets(monkeypatch: pytest.MonkeyPatch) -> None:
    original = fixtures.infrastructure_parameter_file
    original_cli = fixtures.infrastructure_cli

    def parameter_file(path: Path) -> Path:
        result = original(path)
        document = json.loads(result.read_text(encoding="utf-8"))
        names = fixtures.tag_contract()
        for field in ROLE_FIELDS:
            document["parameters"].setdefault(
                field, {"value": names[deploy_v2.PREFIX + deploy_v2.TAG_FIELDS[field]]}
            )
        result.write_text(json.dumps(document), encoding="utf-8")
        return result

    monkeypatch.setattr(fixtures, "infrastructure_parameter_file", parameter_file)

    def ready_network(path: Path) -> tuple[FakeV2Azure, dict[str, Any]]:
        cli, values = original_cli(path)
        cli.documents[_vnet_id(values)]["properties"]["provisioningState"] = "Succeeded"
        return cli, values

    monkeypatch.setattr(fixtures, "infrastructure_cli", ready_network)
    monkeypatch.setattr(shared_fixtures, "infrastructure_cli", ready_network)


def _vnet_id(values: dict[str, Any]) -> str:
    return (
        f"/subscriptions/{SUBSCRIPTION}/resourceGroups/"
        f"{values['virtualNetworkResourceGroupName']}/providers/Microsoft.Network/"
        f"virtualNetworks/{values['virtualNetworkName']}"
    )


def _prepared_shared(tmp_path: Path) -> tuple[FakeV2Azure, dict[str, Any]]:
    cli, values = shared_fixture(tmp_path)
    values["peSubnetName"] = ""
    return cli, deploy_v2.prepare_infrastructure(cli, values, "foundation", "shared")


def _complete_shared(
    cli: FakeV2Azure, values: dict[str, Any], state: str = "ready"
) -> dict[str, Any]:
    bindings = deploy_v2.resource_bindings(cli, values)
    cli.tags = deploy_v2.make_tag_contract({**values, **bindings}, "shared-complete", state)
    cli.values = deploy_v2.infrastructure_values(cli, cli.tags)
    cli.account_id = bindings["foundryAccountResourceId"]
    cli.environment_id = bindings["containerAppsEnvironmentResourceId"]
    cli.project_id = f"{cli.account_id}/projects/{values['projectName']}"
    cli.documents[cli.project_id] = cli.resource(cli.project_id, {"provisioningState": "Succeeded"})
    cli.documents[cli.project_id].update(
        identity={"principalId": "00000000-0000-0000-0000-000000000071"}
    )
    cli.documents[cli.project_id]["tags"]["azbriefWorkloadResourceGroup"] = cli.group_id
    cli.collections[f"{cli.account_id}/projects"].append({"name": values["projectName"]})
    cli.collections[f"{cli.project_id}/capabilityHosts"] = [
        {
            "name": "agents",
            "properties": {"provisioningState": "Succeeded", "capabilityHostKind": "Agents"},
        }
    ]
    insights = (
        f"{cli.group_id}/providers/Microsoft.Insights/components/"
        f"{values['applicationInsightsName']}"
    )
    cli.collections[f"{cli.project_id}/connections"] = [
        {
            "name": "azbrief-app-insights",
            "properties": {
                "category": "AppInsights",
                "authType": "ProjectManagedIdentity",
                "target": insights,
                "metadata": {"ResourceId": insights},
            },
        }
    ]
    cli.collections[f"{bindings['storageAccountResourceId']}/blobServices/default/containers"] = [
        {"name": bindings[field], "properties": {"publicAccess": "None"}}
        for field in ("stateContainerName", "archiveContainerName")
    ]
    for field, resource_type in (
        ("controlPlaneIdentityName", "Microsoft.ManagedIdentity/userAssignedIdentities"),
        ("applicationInsightsName", "Microsoft.Insights/components"),
        ("azureMcpIdentityName", "Microsoft.ManagedIdentity/userAssignedIdentities"),
    ):
        resource_id = f"{cli.group_id}/providers/{resource_type}/{values[field]}"
        document = cli.resource(
            resource_id,
            {
                "provisioningState": "Succeeded",
                "clientId": "00000000-0000-0000-0000-000000000003",
                "principalId": "00000000-0000-0000-0000-000000000004",
            },
        )
        cli.documents[resource_id] = document
        cli.inventory.append(document)
    cli.collections[f"{cli.account_id}/deployments"] = [
        {
            "name": values[alias],
            "sku": {"name": "GlobalStandard", "capacity": 200},
            "properties": {
                "provisioningState": "Succeeded",
                "model": {
                    "format": "OpenAI",
                    "name": values[model],
                    "version": values[version],
                },
            },
        }
        for alias, model, version in (
            ("modelDeploymentName", "coreModelName", "modelVersion"),
            ("simpleModelDeploymentName", "simpleModelName", "simpleModelVersion"),
        )
    ]
    return bindings


@pytest.mark.parametrize("modes", [(OWNERS[0],), (OWNERS[1],), OWNERS])
@pytest.mark.parametrize("omit", [False, True])
def test_attached_subnets_and_network_are_derived_from_exact_owners(
    tmp_path: Path, modes: tuple[str, ...], omit: bool
) -> None:
    cli, values = shared_fixture(tmp_path, modes)
    expected = copy.deepcopy(values)
    fields = ["virtualNetworkResourceGroupName", "virtualNetworkName", "location"]
    fields += [
        "foundrySubnetName" if mode == OWNERS[0] else "containerAppsSubnetName" for mode in modes
    ]
    for field in fields:
        if omit:
            values.pop(field)
        else:
            values[field] = ""
    before = copy.deepcopy(cli.documents)
    prepared = deploy_v2.prepare_infrastructure(cli, values, "foundation", "derived")
    assert all(prepared[field] == expected[field] for field in fields)
    assert all(field not in values or values[field] == "" for field in fields)
    assert cli.documents == before
    assert not cli.operations


@pytest.mark.parametrize(
    "field", [*ROLE_FIELDS[1:], "virtualNetworkResourceGroupName", "virtualNetworkName"]
)
def test_explicit_matching_names_preserve_customer_casing(tmp_path: Path, field: str) -> None:
    cli, values = shared_fixture(tmp_path)
    values[field] = values[field].upper()
    resolved, _ = deploy_v2._shared_network_bindings(cli, values)
    assert resolved[field] == values[field]


@pytest.mark.parametrize(
    "field",
    [*ROLE_FIELDS[1:], "virtualNetworkResourceGroupName", "virtualNetworkName", "location"],
)
def test_explicit_conflicting_network_values_never_override_bound_owners(
    tmp_path: Path, field: str
) -> None:
    cli, values = shared_fixture(tmp_path)
    values[field] = "another-region" if field == "location" else "another-value"
    with pytest.raises(ValueError):
        deploy_v2.prepare_infrastructure(cli, values, "foundation", "conflict")
    assert not cli.operations


def test_shared_network_owners_cannot_bind_different_vnets(tmp_path: Path) -> None:
    cli, values = shared_fixture(tmp_path)
    environment = cli.documents[values["existingContainerAppsEnvironmentResourceId"]]
    configuration = environment["properties"]["vnetConfiguration"]
    configuration["infrastructureSubnetId"] = configuration["infrastructureSubnetId"].replace(
        f"/{values['virtualNetworkName']}/", "/another-vnet/"
    )
    for field in ("virtualNetworkResourceGroupName", "virtualNetworkName", *ROLE_FIELDS[1:]):
        values[field] = ""
    with pytest.raises(ValueError, match="same existing VNet"):
        deploy_v2.prepare_infrastructure(cli, values, "foundation", "different-networks")


@pytest.mark.parametrize("region", ["Korea Central", "koreacentral", "KOREA CENTRAL"])
def test_region_display_and_canonical_names_bind_the_same_private_network(
    tmp_path: Path, region: str
) -> None:
    cli, values = shared_fixture(tmp_path)
    cli.documents[_vnet_id(values)]["location"] = "Korea Central"
    for field in ("existingFoundryAccountResourceId", "existingContainerAppsEnvironmentResourceId"):
        cli.documents[values[field]]["location"] = region
    values["location"] = "koreacentral"
    prepared = deploy_v2.prepare_infrastructure(cli, values, "foundation", "region")
    assert prepared["location"] == normalize_region(region) == "koreacentral"


@pytest.mark.parametrize("field", ROLE_FIELDS)
def test_new_parents_require_their_explicit_subnet_roles(tmp_path: Path, field: str) -> None:
    cli, values = fixtures.infrastructure_cli(tmp_path)
    values[field] = ""
    with pytest.raises(ValueError, match=field):
        deploy_v2.prepare_infrastructure(cli, values, "foundation", "missing-new-role")


def test_all_shared_parents_need_no_selected_pe_subnet_or_new_ip_budget(tmp_path: Path) -> None:
    cli, values = shared_fixture(tmp_path)
    pe = cli.documents[_vnet_id(values)]["properties"]["subnets"][0]["properties"]
    pe["privateEndpoints"] = [{"id": "retained-customer-endpoint"}]
    values["peSubnetName"] = ""
    prepared = deploy_v2.prepare_infrastructure(cli, values, "foundation", "no-new-pe")
    assert deploy_v2._private_endpoint_count(prepared) == 0
    assert prepared["peSubnetName"] == ""
    bindings = deploy_v2.resource_bindings(cli, prepared)
    tags = deploy_v2.make_tag_contract({**prepared, **bindings}, "no-new-pe", "foundation")
    assert deploy_v2.parse_tag_contract(tags, require_ready=False)
    assert deploy_v2.infrastructure_values(cli, tags)["peSubnetName"] == ""
    expression = template("infrastructure")["outputs"]["infrastructure"]["value"][
        "privateEndpointSubnetId"
    ]
    assert (
        _evaluate_ui_expression(
            expression, {}, parameters=prepared, variables={"vnetId": _vnet_id(prepared)}
        )
        == ""
    )
    assert not cli.operations


@pytest.mark.parametrize(
    "modes,aca_pe",
    [
        (("storageAccountMode", "containerAppsEnvironmentMode"), False),
        (("foundryAccountMode", "containerAppsEnvironmentMode"), False),
        (("foundryAccountMode", "storageAccountMode"), True),
    ],
)
def test_partial_endpoint_creation_still_requires_a_pe_subnet(
    tmp_path: Path, modes: tuple[str, ...], aca_pe: bool
) -> None:
    cli, values = shared_fixture(tmp_path, modes)
    values.update(peSubnetName="", deployContainerAppsPrivateEndpoint=aca_pe)
    with pytest.raises(ValueError, match="peSubnetName"):
        deploy_v2.prepare_infrastructure(cli, values, "foundation", "partial-pe")


@pytest.mark.parametrize(
    "patch",
    [
        {"foundryAccountMode": "new"},
        {"storageAccountMode": "new"},
        {"containerAppsEnvironmentMode": "new", "deployContainerAppsPrivateEndpoint": True},
        {"useMicrosoftManagedAgentData": False},
    ],
)
def test_saved_empty_pe_requires_explicit_modes_and_managed_agent_data(
    tmp_path: Path, patch: dict[str, Any]
) -> None:
    cli, values = _prepared_shared(tmp_path)
    tags = deploy_v2.make_tag_contract(
        {**values, **deploy_v2.resource_bindings(cli, values)}, "bad-empty-pe", "foundation"
    )
    options = deploy_v2.decode_tag_options(tags)
    options.update(
        {
            field: value
            for field, value in patch.items()
            if field != "deployContainerAppsPrivateEndpoint"
        }
    )
    tags[deploy_v2.PREFIX + "acaPeEnabled"] = str(
        patch.get("deployContainerAppsPrivateEndpoint", False)
    ).lower()
    encoded = json.dumps(options, separators=(",", ":"))
    tags.update(
        {
            key: encoded[index * 256 : (index + 1) * 256]
            for index, key in enumerate(deploy_v2.OPTION_TAG_KEYS)
        }
    )
    with pytest.raises(ValueError):
        deploy_v2.parse_tag_contract(tags, require_ready=False)


def test_optional_pe_planning_never_skips_the_bound_foundry_or_environment(tmp_path: Path) -> None:
    cli, values = shared_fixture(tmp_path)
    vnet = cli.documents[_vnet_id(values)]
    values = {**values, **{field: "" for field in values if field.endswith("SubnetAddressPrefix")}}
    values["peSubnetName"] = ""
    plans = plan_subnets(values, vnet, roles={"foundry", "container_apps"})
    assert [plan.role for plan in plans] == ["foundry", "container_apps"]
    assert not any(plan.create for plan in plans)
    with pytest.raises(ValueError, match="non-empty"):
        plan_subnets(values, vnet)
    with pytest.raises(ValueError, match="Only the unused"):
        plan_subnets(values, vnet, roles={"foundry"})
    vnet["properties"]["subnets"][1]["properties"]["delegations"] = []
    with pytest.raises(ValueError, match="delegation"):
        plan_subnets(values, vnet, roles={"foundry", "container_apps"})


@pytest.mark.parametrize("target", ["foundry", "environment", "vnet"])
@pytest.mark.parametrize("failure", ["denied", "redirected", "unready", "missing"])
def test_bound_resource_reads_fail_closed(tmp_path: Path, target: str, failure: str) -> None:
    cli, values = shared_fixture(tmp_path)
    resource_id = {
        "foundry": values["existingFoundryAccountResourceId"],
        "environment": values["existingContainerAppsEnvironmentResourceId"],
        "vnet": _vnet_id(values),
    }[target]
    if failure == "denied":
        cli.denied.add(resource_id)
    elif failure == "redirected":
        cli.documents[resource_id]["id"] = resource_id + "-redirected"
    elif failure == "missing":
        cli.documents[resource_id]["properties"].pop("provisioningState")
    else:
        cli.documents[resource_id]["properties"]["provisioningState"] = "Updating"
    with pytest.raises((RuntimeError, ValueError)):
        deploy_v2.prepare_infrastructure(cli, values, "foundation", "failed-read")
    assert not cli.operations


@pytest.mark.parametrize("failure", ["missing", "managed", "ambiguous", "public"])
def test_public_or_uninjected_foundry_is_not_a_reusable_private_parent(
    tmp_path: Path, failure: str
) -> None:
    cli, values = shared_fixture(tmp_path)
    props = cli.documents[values["existingFoundryAccountResourceId"]]["properties"]
    if failure == "missing":
        props["networkInjections"] = []
    elif failure == "managed":
        props["networkInjections"][0]["useMicrosoftManagedNetwork"] = True
    elif failure == "ambiguous":
        props["networkInjections"] *= 2
    else:
        props["publicNetworkAccess"] = "Enabled"
    with pytest.raises(ValueError, match="Shared Foundry requires"):
        deploy_v2.prepare_infrastructure(cli, values, "foundation", "uninjected")


@pytest.mark.parametrize("failure", ["missing", "external", "public"])
def test_shared_environment_requires_its_actual_internal_private_subnet(
    tmp_path: Path, failure: str
) -> None:
    cli, values = shared_fixture(tmp_path)
    props = cli.documents[values["existingContainerAppsEnvironmentResourceId"]]["properties"]
    if failure == "missing":
        props["vnetConfiguration"].pop("infrastructureSubnetId")
    elif failure == "external":
        props["vnetConfiguration"]["internal"] = False
    else:
        props["publicNetworkAccess"] = "Enabled"
    with pytest.raises(ValueError, match="Shared Environment requires"):
        deploy_v2.prepare_infrastructure(cli, values, "foundation", "not-internal")


@pytest.mark.parametrize("mode", OWNERS)
def test_bound_subnets_reject_cross_subscription_and_wrong_resource_types(
    tmp_path: Path, mode: str
) -> None:
    cli, values = shared_fixture(tmp_path)
    field = deploy_v2.SHARED_RESOURCES[mode][1]
    props = cli.documents[values[field]]["properties"]
    container = props["networkInjections"][0] if mode == OWNERS[0] else props["vnetConfiguration"]
    key = "subnetArmId" if mode == OWNERS[0] else "infrastructureSubnetId"
    original = container[key]
    for invalid in (
        original.replace(SUBSCRIPTION, "00000000-0000-0000-0000-000000000099"),
        original.replace("/subnets/", "/privateEndpoints/"),
        original + "?redirect=true",
    ):
        container[key] = invalid
        with pytest.raises(ValueError, match="same-subscription subnet ARM ID"):
            deploy_v2.prepare_infrastructure(cli, values, "foundation", "invalid-id")


def test_loader_allows_omitted_shared_network_but_does_not_invent_new_roles(tmp_path: Path) -> None:
    cli, values = shared_fixture(tmp_path)
    path = fixtures.infrastructure_parameter_file(tmp_path)
    parameters = json.loads(path.read_text(encoding="utf-8"))["parameters"]
    for mode in OWNERS:
        name, reference, _, _, _ = deploy_v2.SHARED_RESOURCES[mode]
        parameters[mode] = {"value": "existing"}
        parameters[reference] = {"value": values[reference]}
        parameters.pop(name, None)
    fields = [*ROLE_FIELDS[1:], "virtualNetworkName", "virtualNetworkResourceGroupName", "location"]
    for field in fields:
        parameters.pop(field, None)
    path.write_text(json.dumps({"parameters": parameters}), encoding="utf-8")
    loaded = deploy_v2.load_infrastructure_parameters(path)
    assert all(loaded[field] == "" for field in fields)
    prepared = deploy_v2.prepare_infrastructure(cli, loaded, "foundation", "loaded")
    assert all(prepared[field] == values[field] for field in fields)


@pytest.mark.parametrize("omit", [False, True])
def test_completion_cannot_change_an_attached_subnet_even_if_input_is_omitted(
    tmp_path: Path, omit: bool
) -> None:
    cli, values = _prepared_shared(tmp_path)
    _complete_shared(cli, values, "foundation")
    props = cli.documents[values["existingFoundryAccountResourceId"]]["properties"]
    props["networkInjections"][0]["subnetArmId"] += "-changed"
    if omit:
        values["foundrySubnetName"] = ""
    with pytest.raises(ValueError):
        deploy_v2.prepare_infrastructure(cli, values, "complete", "changed-subnet")
    assert not cli.operations


def test_application_and_runtime_handoffs_preserve_no_pe_and_resolved_resource_ids(
    tmp_path: Path,
) -> None:
    cli, values = _prepared_shared(tmp_path)
    bindings = _complete_shared(cli, values)
    _, resolved = deploy_v2.read_infrastructure(cli)
    assert resolved["privateEndpointSubnetId"] == ""
    assert resolved["foundrySubnetResourceId"].endswith("/" + values["foundrySubnetName"])
    assert resolved["infrastructureSubnetId"].endswith("/" + values["containerAppsSubnetName"])
    parameters = deploy_v2.prepare_application(cli, IMAGE)
    assert deploy_v2.decode_resource_bindings(parameters["infrastructureTags"]) == bindings
    deploy_v2.run_hosted_roles(cli, "00000000-0000-0000-0000-000000000072", "preflight", "roles")
    assert not cli.operations


def test_mcp_core_handoff_accepts_no_pe_but_rejects_a_fabricated_subnet_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cli, values = _prepared_shared(tmp_path)
    _complete_shared(cli, values, "mcp_pending")
    domain = cli.documents[cli.environment_id]["properties"]["defaultDomain"]
    cli.tags[deploy_v2.PREFIX + "mcpUrl"] = f"https://{values['azureMcpContainerAppName']}.{domain}"
    _, resolved = deploy_v2._read_infrastructure(cli, "mcp_pending")
    core = {
        **resolved,
        "schemaVersion": 2,
        "profile": deploy_v2.PROFILE,
        "infrastructureComplete": True,
        "azureMcpEntraAppUniqueName": "azbrief-v2-mcp-" + "a" * 13,
    }
    core.pop("peSubnetName")
    original = cli.json

    def json_response(*arguments: str) -> Any:
        if arguments[:3] == ("deployment", "group", "show"):
            return {"state": "Succeeded", "infrastructure": copy.deepcopy(core)}
        if arguments[:3] == ("ad", "app", "list"):
            return []
        return original(*arguments)

    monkeypatch.setattr(cli, "json", json_response)
    handoff = deploy_v2.prepare_mcp_authentication(cli)["infrastructure"]
    assert handoff["peSubnetName"] == handoff["privateEndpointSubnetId"] == ""
    core["privateEndpointSubnetId"] = _vnet_id(values) + "/subnets/"
    with pytest.raises(ValueError, match="peSubnetName"):
        deploy_v2.prepare_mcp_authentication(cli)
    assert not cli.operations


def test_compiled_arm_resolves_shared_subnets_and_validates_before_writes() -> None:
    document = template("infrastructure")
    for field in ROLE_FIELDS:
        assert document["parameters"][field]["defaultValue"] == ""
    contract = document["outputs"]["infrastructure"]["value"]
    assert "subnetArmId" in contract["foundrySubnetResourceId"]
    assert "infrastructureSubnetId" in contract["infrastructureSubnetId"]
    assert "subnetArmId" in contract["foundrySubnetName"]
    assert "infrastructureSubnetId" in contract["containerAppsSubnetName"]
    validation = fixtures.module(document, "v2-network-binding-validation")
    assert validation["properties"]["template"]["resources"] == []
    assert all(
        parameter["allowedValues"] == [True]
        for parameter in validation["properties"]["template"]["parameters"].values()
    )
    for name in ("v2-foundation", "v2-new-network", "v2-required-models"):
        assert any(
            "v2-network-binding-validation" in dependency
            for dependency in fixtures.module(document, name)["dependsOn"]
        )
