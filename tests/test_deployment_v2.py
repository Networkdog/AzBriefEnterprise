"""Offline contracts for infrastructure-once and repeatable application deployment."""

import copy
import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from scripts import deploy_v2
from scripts.deploy_kt import AzureCli
from tests.test_kt_deployment import _evaluate_ui_expression as _evaluate_portal_expression

ROOT = Path(__file__).resolve().parents[1]
V2 = ROOT / "infra" / "v2"
SUBSCRIPTION = "00000000-0000-0000-0000-000000000001"
TENANT = "00000000-0000-0000-0000-000000000002"
IMAGE = "ghcr.io/example/azbrief@sha256:" + "a" * 64


def _evaluate_ui_expression(expression: str, names: dict[str, Any], **options: Any) -> Any:
    values = {**names, "resources": names.get("resources", {})}
    return _evaluate_portal_expression(expression, values, **options)


def template(name: str) -> dict[str, Any]:
    return json.loads((V2 / f"{name}.json").read_text(encoding="utf-8"))


def resources(document: dict[str, Any]) -> Iterator[dict[str, Any]]:
    declared = document.get("resources", [])
    entries = declared.values() if isinstance(declared, dict) else declared
    for resource in entries:
        if resource.get("existing") is True:
            continue
        scope = resource.get("scope")
        target = (
            declared.get(scope) if isinstance(declared, dict) and isinstance(scope, str) else None
        )
        if isinstance(target, dict):
            resource = {**resource, "_scope_resource_type": target["type"]}
        yield resource
        properties = resource.get("properties", {})
        nested = properties.get("template") if isinstance(properties, dict) else None
        if isinstance(nested, dict):
            yield from resources(nested)


def module(document: dict[str, Any], name: str) -> dict[str, Any]:
    for resource in resources(document):
        label = resource.get("name", "")
        if isinstance(label, str) and (
            label == name or f"'{name}'" in label or f"'{name}-{{0}}'" in label
        ):
            return resource
    raise StopIteration(name)


def test_default_basic_vnet_host_omits_byo_and_keeps_private_injection() -> None:
    document = template("infrastructure")
    assert document["parameters"]["useMicrosoftManagedAgentData"]["defaultValue"] is True
    managed = module(document, "v2-managed-project-host")
    assert "useMicrosoftManagedAgentData" in managed["condition"]
    host = next(
        resource
        for resource in resources(managed["properties"]["template"])
        if resource["type"] == "Microsoft.CognitiveServices/accounts/projects/capabilityHosts"
    )
    assert host["properties"] == {"capabilityHostKind": "Agents"}
    assert not any(
        resource["type"]
        in {
            "Microsoft.Authorization/roleAssignments",
            "Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments",
        }
        for resource in resources(managed["properties"]["template"])
    )
    foundation = module(document, "v2-foundation")["properties"]["template"]
    for name in ("v2-cosmos", "v2-search"):
        assert (
            module(foundation, name)["condition"]
            == "[not(parameters('useMicrosoftManagedAgentData'))]"
        )
    assert "useMicrosoftManagedAgentData" in module(document, "v2-agent-bindings")["condition"]
    account = module(foundation, "v2-foundry")["properties"]["parameters"]
    assert (
        foundation["parameters"]["foundryPublishingPublicNetworkAccess"]["defaultValue"]
        == "Disabled"
    )
    assert (
        account["publicNetworkAccess"]["value"]
        == "[parameters('foundryPublishingPublicNetworkAccess')]"
    )
    policy = module(document, "v2-foundry-publishing-policy")["properties"]["template"]
    assert policy["parameters"]["access"]["defaultValue"] == {}
    assert "'Enabled', 'Disabled'" in policy["outputs"]["publicNetworkAccess"]["value"]
    assert account["networkInjections"]["value"]["scenario"] == "agent"
    assert account["networkInjections"]["value"]["useMicrosoftManagedNetwork"] is False
    endpoint = next(
        resource
        for resource in resources(foundation)
        if resource.get("copy", {}).get("name") == "privateEndpoints"
    )
    assert "useMicrosoftManagedAgentData" in endpoint["condition"]
    assert "2" in endpoint["condition"] and "3" in endpoint["condition"]
    dns = next(
        resource
        for resource in resources(foundation)
        if resource.get("copy", {}).get("name") == "dnsZones"
    )
    assert "useMicrosoftManagedAgentData" in dns["condition"]
    assert "4" in dns["condition"] and "5" in dns["condition"]


def test_managed_default_keeps_app_storage_and_roundtrips_mode(tmp_path: Path) -> None:
    values = deploy_v2.load_infrastructure_parameters(infrastructure_parameter_file(tmp_path))
    assert values["useMicrosoftManagedAgentData"] is True
    assert values["cosmosAccountName"] == values["searchServiceName"] == ""
    saved = deploy_v2.make_tag_contract(values, "managed-foundation", "foundation")
    assert saved[deploy_v2.PREFIX + "storage"]
    assert saved[deploy_v2.PREFIX + "cosmos"] == saved[deploy_v2.PREFIX + "search"] == ""
    assert deploy_v2.decode_tag_options(saved)["useMicrosoftManagedAgentData"] is True
    assert deploy_v2.parse_tag_contract(saved, require_ready=False)


def test_foundry_publishing_policy_normalizes_small_ranges_and_exact_subnets() -> None:
    subnet = (
        f"/subscriptions/{SUBSCRIPTION}/resourceGroups/rg-client"
        "/providers/Microsoft.Network/virtualNetworks/vnet-client/subnets/publisher"
    )
    assert deploy_v2.normalize_foundry_publishing_access({}, SUBSCRIPTION) == {}
    assert deploy_v2.normalize_foundry_publishing_access(
        {"publicIpRanges": ["8.8.8.8/31", "1.1.1.1/32"], "subnetResourceIds": [subnet]},
        SUBSCRIPTION,
    ) == {
        "publicIpRanges": ["1.1.1.1", "8.8.8.8", "8.8.8.9"],
        "subnetResourceIds": [subnet.lower()],
    }


@pytest.mark.parametrize(
    "value",
    [
        None,
        {"publicIpRanges": ["8.8.8.8"]},
        {"publicIpRanges": [], "subnetResourceIds": []},
        {"publicIpRanges": "8.8.8.8", "subnetResourceIds": []},
        *(
            {"publicIpRanges": [address], "subnetResourceIds": []}
            for address in (
                "0.0.0.0/0",
                "10.0.0.0/8",
                "172.0.0.0/8",
                "100.0.0.0/8",
                "203.0.113.4",
                "224.1.1.1",
                "::1",
                "8.8.8.8/24",
                " 8.8.8.8",
            )
        ),
        {"publicIpRanges": ["8.8.8.8", "8.8.8.0/24"], "subnetResourceIds": []},
        {"publicIpRanges": [], "subnetResourceIds": ["/subscriptions/foreign"]},
    ],
)
def test_foundry_publishing_policy_rejects_unknown_or_unrestricted_sources(value: object) -> None:
    with pytest.raises(ValueError):
        deploy_v2.normalize_foundry_publishing_access(value, SUBSCRIPTION)


def test_foundry_publishing_policy_roundtrips_without_changing_legacy_options() -> None:
    cli = FakeV2Azure()
    assert "foundryPublishingAccess" not in deploy_v2.decode_tag_options(cli.tags)
    cli.use_managed_agent_data()
    values = {
        **cli.values,
        "foundryPublishingAccess": {
            "publicIpRanges": ["8.8.8.8/32"],
            "subnetResourceIds": [],
        },
    }
    saved = deploy_v2.make_tag_contract(values, "publishing", "foundation")
    assert deploy_v2.infrastructure_values(cli, saved)["foundryPublishingAccess"] == {
        "publicIpRanges": ["8.8.8.8"],
        "subnetResourceIds": [],
    }


def test_foundry_publishing_policy_completion_form_preserves_saved_access() -> None:
    cli = FakeV2Azure()
    cli.use_managed_agent_data()
    policy = {"publicIpRanges": ["8.8.8.8"], "subnetResourceIds": []}
    saved = deploy_v2.make_tag_contract(
        {**cli.values, "foundryPublishingAccess": policy}, "publishing", "foundation"
    )
    expression = ui_definition("infrastructure")["outputs"]["foundryPublishingAccess"]
    assert (
        _evaluate_ui_expression(
            expression,
            {"contract": {"resourceGroupApi": {"tags": saved}}},
            basics={"infrastructureStage": "complete"},
        )
        == policy
    )
    assert (
        _evaluate_ui_expression(
            expression,
            {"contract": {"resourceGroupApi": None}},
            basics={"infrastructureStage": "foundation"},
        )
        == {}
    )


@pytest.mark.parametrize("state", ["Enabled", "Disabled"])
def test_foundry_publishing_readback_accepts_only_exact_or_closed_policy(state: str) -> None:
    cli = FakeV2Azure()
    cli.use_managed_agent_data()
    values = {
        **cli.values,
        "foundryPublishingAccess": {
            "publicIpRanges": ["8.8.8.8"],
            "subnetResourceIds": [],
        },
    }
    account = cli.documents[cli.account_id]
    account["properties"].update(
        publicNetworkAccess=state,
        disableLocalAuth=True,
        networkAcls={"defaultAction": "Deny", "ipRules": [{"value": "8.8.8.8"}]},
        networkInjections=[
            {
                "subnetArmId": f"/subscriptions/{SUBSCRIPTION}/resourceGroups/{values['virtualNetworkResourceGroupName']}/providers/Microsoft.Network/virtualNetworks/{values['virtualNetworkName']}/subnets/{values['foundrySubnetName']}",
                "useMicrosoftManagedNetwork": False,
            }
        ],
    )
    assert deploy_v2._verify_foundry_publishing_access(cli, values, account)
    for action, access in (("Allow", state), ("Deny", "Enabled")):
        account["properties"]["publicNetworkAccess"] = access
        account["properties"]["networkAcls"] = {
            "defaultAction": action,
            "ipRules": [{"value": "1.1.1.1"}],
        }
        with pytest.raises(ValueError, match="Foundry publishing"):
            deploy_v2._verify_foundry_publishing_access(cli, values, account)
    assert not cli.operations


def test_foundry_publishing_preflight_rejects_shared_accounts_and_changed_saved_policy(
    tmp_path: Path,
) -> None:
    cli, values = infrastructure_cli(tmp_path)
    values["foundryPublishingAccess"] = {"publicIpRanges": ["8.8.8.8"], "subnetResourceIds": []}
    prepared = deploy_v2.prepare_infrastructure(cli, values, "foundation", "publishing")
    assert prepared["foundryPublishingAccess"] == values["foundryPublishingAccess"]
    cli.tags = deploy_v2.make_tag_contract(values, "publishing-foundation", "foundation")
    values["foundryPublishingAccess"]["publicIpRanges"] = ["1.1.1.1"]
    with pytest.raises(ValueError, match="publishing access is immutable"):
        deploy_v2.prepare_infrastructure(cli, values, "complete", "changed")
    values["foundryAccountMode"] = "existing"
    with pytest.raises(ValueError, match="only for new"):
        deploy_v2.prepare_infrastructure(cli, values, "foundation", "shared")
    assert not cli.operations


@pytest.mark.parametrize("reuse", [False, True])
@pytest.mark.parametrize(
    "registry", ["example.azurecr.io", "EXAMPLE.azurecr.io", "ghcr.io", "mcr.microsoft.com"]
)
def test_first_acr_application_install_uses_only_the_existing_workload_identity(
    reuse: bool, registry: str
) -> None:
    document = template("application")
    identity_name = "id-test-control-plane"
    expected = (
        {
            "registries": [
                {
                    "server": registry.lower(),
                    "identity": (
                        f"/subscriptions/{SUBSCRIPTION}/resourceGroups/rg-workload"
                        "/providers/Microsoft.ManagedIdentity/userAssignedIdentities/"
                        + identity_name
                    ),
                }
            ]
        }
        if not reuse and registry.lower().endswith(".azurecr.io")
        else {}
    )
    parameters = {
        "containerImage": registry + "/azbrief@sha256:" + "a" * 64,
        "reuseExistingApiKey": reuse,
        "preserveExistingSettings": False,
        "infrastructureTags": {"azbrief.v2.identity": identity_name},
        "appConfiguration": {},
        "jobConfiguration": {},
    }
    expressions = {"initialManagedRegistry": document["variables"]["initialManagedRegistry"]}
    for field, resource_type in (
        ("appConfiguration", "Microsoft.App/containerApps"),
        ("jobConfiguration", "Microsoft.App/jobs"),
    ):
        expression = (
            "union(variables('initialManagedRegistry'), if(variables('liveUpdate'), "
            f"listOutputsWithSecureValues('existingSettings', '2025-04-01').snapshot.{field}, "
            f"createObject()), parameters('{field}'))"
        )
        resource = next(row for row in resources(document) if row["type"] == resource_type)
        assert expression in resource["properties"]["configuration"]
        expressions[field] = "[" + expression + "]"
    for expression in expressions.values():
        assert (
            _evaluate_ui_expression(
                expression,
                {},
                parameters=parameters,
                variables=document["variables"],
                resource_group={"name": "rg-workload"},
                subscription={"subscriptionId": SUBSCRIPTION},
            )
            == expected
        )
    explicit = {
        "registries": [{"server": registry, "username": "pull-only", "passwordSecretRef": "pull"}]
    }
    parameters.update(appConfiguration=explicit, jobConfiguration=explicit)
    for field in ("appConfiguration", "jobConfiguration"):
        assert (
            _evaluate_ui_expression(
                expressions[field],
                {},
                parameters=parameters,
                variables=document["variables"],
                resource_group={"name": "rg-workload"},
                subscription={"subscriptionId": SUBSCRIPTION},
            )
            == explicit
        )


def test_portal_updates_snapshot_existing_state_without_a_resource_self_dependency() -> None:
    document = template("application")
    snapshot = module(document, "v2-application-readback")
    assert snapshot.get("dependsOn") == ["portalVerification"]
    assert snapshot["properties"]["parameters"]["readExistingSettings"]["value"] == (
        "[variables('liveUpdate')]"
    )
    child = snapshot["properties"]["template"]
    assert set(child["outputs"]) == {"snapshot"}
    assert child["outputs"]["snapshot"]["type"] == "secureObject"
    assert not list(resources(child))
    assert (
        _evaluate_ui_expression(
            child["outputs"]["snapshot"]["value"], {}, parameters={"readExistingSettings": False}
        )
        == {}
    )
    for resource_type in ("Microsoft.App/containerApps", "Microsoft.App/jobs"):
        target = next(row for row in resources(document) if row["type"] == resource_type)
        serialized = json.dumps(target["properties"])
        assert "listOutputsWithSecureValues('existingSettings', '2025-04-01')" in serialized
        assert "existingSettings" in target["dependsOn"]
        assert "reference('containerApp')" not in serialized
        assert "reference('schedulerJob')" not in serialized
        assert "listSecrets('containerApp'" not in serialized
        assert "listSecrets('schedulerJob'" not in serialized


def test_explicit_initial_recovery_preserves_failed_apps_key_and_creates_missing_job(
    capsys,
) -> None:
    cli = FakeV2Azure()
    cli.install()
    cli.tags.update(deploy_v2.APPLICATION_TAG_DEFAULTS)
    cli.documents[cli.app_id]["properties"]["provisioningState"] = "Failed"
    cli.collections[f"{cli.group_id}/providers/Microsoft.App/jobs"] = []
    app_secrets = copy.deepcopy(cli.secret_values[cli.app_id])
    with pytest.raises(ValueError, match="ownership"):
        deploy_v2.prepare_application(cli, IMAGE)
    prepared = deploy_v2.prepare_application(cli, IMAGE, recover_initial_install=True)
    key = next(secret for secret in app_secrets if secret["name"] == "orchestrator-api-key")
    assert prepared["reuseExistingApiKey"] is True
    assert prepared["existingSecrets"]["app"] == app_secrets
    assert prepared["existingSecrets"]["job"] == [key]
    assert prepared["appConfiguration"]["ingress"]["targetPort"] == 8000
    assert prepared["webAuthConfiguration"]["platform"]["enabled"] is True
    assert prepared["jobConfiguration"] == {}
    assert "apiKey" not in prepared
    assert cli.secret_values[cli.app_id] == app_secrets
    output = capsys.readouterr()
    assert key["value"] not in output.out + output.err


@pytest.mark.parametrize(
    "failure",
    [
        "completed",
        "missing-tags",
        "missing-app",
        "unreadable-secret",
        "no-key",
        "running",
        "foreign",
    ],
)
def test_explicit_initial_recovery_never_adopts_or_rotates_on_missing_evidence(
    failure: str,
) -> None:
    cli = FakeV2Azure()
    cli.install()
    cli.tags.update(deploy_v2.APPLICATION_TAG_DEFAULTS)
    cli.documents[cli.app_id]["properties"]["provisioningState"] = "Failed"
    cli.collections[f"{cli.group_id}/providers/Microsoft.App/jobs"] = []
    if failure == "completed":
        cli.tags[deploy_v2.PREFIX + "applicationState"] = "deployed"
        cli.tags[deploy_v2.PREFIX + "lastApplicationDeploymentAt"] = "2026-10-17T00:00:00Z"
    elif failure == "missing-tags":
        for name in deploy_v2.APPLICATION_TAG_KEYS:
            cli.tags.pop(name, None)
    elif failure == "missing-app":
        cli.collections[f"{cli.group_id}/providers/Microsoft.App/containerApps"] = []
    elif failure == "unreadable-secret":
        cli.denied.add(cli.app_id)
    elif failure == "no-key":
        cli.secret_values[cli.app_id] = []
    elif failure == "running":
        cli.documents[cli.app_id]["properties"]["provisioningState"] = "Updating"
    else:
        cli.documents[cli.app_id]["identity"]["userAssignedIdentities"] = {"foreign": {}}
    secrets = copy.deepcopy(cli.secret_values)
    with pytest.raises((RuntimeError, ValueError)):
        deploy_v2.prepare_application(cli, IMAGE, recover_initial_install=True)
    assert cli.secret_values == secrets
    assert not cli.operations


def test_legacy_byo_parameters_require_explicit_mode_false(tmp_path: Path) -> None:
    path = infrastructure_parameter_file(tmp_path)
    document = json.loads(path.read_text(encoding="utf-8"))
    document["parameters"]["useMicrosoftManagedAgentData"] = {"value": False}
    document["parameters"]["cosmosAccountName"] = {"value": "legacy-cosmos"}
    document["parameters"]["searchServiceName"] = {"value": "legacy-search"}
    path.write_text(json.dumps(document), encoding="utf-8")
    values = deploy_v2.load_infrastructure_parameters(path)
    assert values["useMicrosoftManagedAgentData"] is False
    assert values["cosmosAccountName"] and values["searchServiceName"]
    saved = deploy_v2.make_tag_contract(values, "legacy-foundation", "foundation")
    assert deploy_v2.decode_tag_options(saved)["useMicrosoftManagedAgentData"] is False
    assert deploy_v2.parse_tag_contract(saved, require_ready=False)


def test_portal_new_foundation_hides_byo_inputs_and_uses_managed_defaults() -> None:
    outputs = ui_definition("infrastructure")["outputs"]
    data = {
        "contract": {"resourceGroupApi": None},
        "options": {},
        "namingPrefix": "test",
        "namingSuffix": "managed",
    }
    for field, expected in (
        ("useMicrosoftManagedAgentData", True),
        ("cosmosAccountName", ""),
        ("searchServiceName", ""),
        ("searchSku", "basic"),
    ):
        assert (
            _evaluate_ui_expression(
                outputs[field],
                data,
                basics={"infrastructureStage": "foundation"},
                eager_conditionals=True,
            )
            == expected
        )
    assert ui_controls("infrastructure")["searchSku"]["visible"] is False
    preview = ui_controls("infrastructure")["resourceNamePreview"]["options"]["text"]
    assert "cosmosAccountName:" not in preview
    assert "searchServiceName:" not in preview


def test_new_network_plans_three_private_role_subnets(tmp_path: Path) -> None:
    values = deploy_v2.load_infrastructure_parameters(infrastructure_parameter_file(tmp_path))
    network = deploy_v2.planned_virtual_network(values)
    assert network["properties"]["addressSpace"]["addressPrefixes"] == ["10.240.0.0/16"]
    assert len(network["properties"]["subnets"]) == 3
    document = template("infrastructure")
    assert document["parameters"]["virtualNetworkMode"]["allowedValues"] == ["existing", "new"]
    assert (
        module(document, "v2-new-network")["condition"]
        == "[and(not(variables('complete')), equals(parameters('virtualNetworkMode'), 'new'))]"
    )
    assert any(
        "v2-new-network" in value for value in module(document, "v2-foundation")["dependsOn"]
    )


@pytest.mark.parametrize(
    "patch",
    [
        {"virtualNetworkAddressPrefix": "0.0.0.0/0"},
        {"virtualNetworkAddressPrefix": "2001:db8::/48"},
        {"containerAppsSubnetAddressPrefix": "10.240.0.0/27"},
        {"peSubnetAddressPrefix": "10.241.1.32/28"},
        {"containerAppsSubnetAddressPrefix": "10.240.1.0/28"},
    ],
)
def test_new_network_rejects_invalid_or_overlapping_ranges(
    tmp_path: Path, patch: dict[str, str]
) -> None:
    values = deploy_v2.load_infrastructure_parameters(infrastructure_parameter_file(tmp_path))
    with pytest.raises(ValueError):
        deploy_v2.planned_virtual_network({**values, **patch})


def test_managed_foundation_never_evaluates_omitted_store_references_or_diagnostics() -> None:
    foundation = module(template("infrastructure"), "v2-foundation")["properties"]["template"]
    declared = foundation["resources"]
    assert isinstance(declared, dict)
    for name in ("existingCosmos", "existingSearch", "cosmosDiagnostics", "searchDiagnostics"):
        assert declared[name]["condition"] == "[not(parameters('useMicrosoftManagedAgentData'))]"
    assert (
        declared["cosmosDiagnostics"]["scope"]
        == "[resourceId('Microsoft.DocumentDB/databaseAccounts', parameters('cosmosAccountName'))]"
    )
    assert (
        declared["searchDiagnostics"]["scope"]
        == "[resourceId('Microsoft.Search/searchServices', parameters('searchServiceName'))]"
    )


def test_model_discovery_uses_region_catalog_quota_and_provider_capacity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cli = FakeV2Azure()
    base = f"/subscriptions/{SUBSCRIPTION}/providers/Microsoft.CognitiveServices"
    for region, used in (("koreacentral", 90), ("eastus2", 10)):
        cli.collections[f"{base}/locations/{region}/models"] = [
            {
                "model": {
                    "format": "OpenAI",
                    "name": "gpt-5",
                    "version": "2025-08-07",
                    "capabilities": {"responses": "true", "agentsV2": "true"},
                    "skus": [{"name": "GlobalStandard", "usageName": "quota-gpt5"}],
                }
            }
        ]
        cli.collections[f"{base}/locations/{region}/usages"] = [
            {"name": {"value": "quota-gpt5"}, "limit": 100, "currentValue": used}
        ]
    original = cli.json
    reads: list[str] = []

    def json_read(*arguments: str) -> Any:
        if arguments[0] == "rest" and "modelCapacities" in arguments[arguments.index("--url") + 1]:
            reads.append(arguments[arguments.index("--url") + 1])
            return {
                "value": [
                    {
                        "location": region,
                        "properties": {"skuName": "GlobalStandard", "availableCapacity": capacity},
                    }
                    for region, capacity in (("koreacentral", 80), ("eastus2", 40))
                ]
            }
        return original(*arguments)

    monkeypatch.setattr(cli, "json", json_read)
    rows = deploy_v2.discover_model_options(cli, ["koreacentral", "eastus2"])
    assert [(row["region"], row["available"]) for row in rows] == [
        ("eastus2", 40),
        ("koreacentral", 10),
    ]
    assert len(reads) == 1
    assert not cli.operations


def test_portal_model_choices_do_not_override_new_infrastructure_location() -> None:
    outputs = ui_definition("infrastructure")["outputs"]
    data = {
        "contract": {"resourceGroupApi": None},
        "options": {
            "coreModelName": "gpt-5.1",
            "simpleModelName": "gpt-5-nano",
            "modelVersion": "2025-11-13",
            "simpleModelVersion": "2025-08-07",
            "modelCapacity": "100",
            "simpleModelCapacity": "50",
        },
    }
    expected = {
        **data["options"],
        "modelCapacity": 100,
        "simpleModelCapacity": 50,
        "location": "eastus2",
    }
    for name, value in expected.items():
        assert (
            _evaluate_ui_expression(
                outputs[name],
                data,
                network={"virtualNetworkMode": "new", "modelDeploymentRegion": "koreacentral"},
                basics={"infrastructureStage": "foundation"},
                selected_location="eastus2",
                eager_conditionals=True,
            )
            == value
        )
    controls = ui_controls("infrastructure")
    assert all(name in controls for name in data["options"])
    assert controls["coreModelName"]["constraints"]["regex"].startswith("^gpt-5")


def test_new_defaults_match_portal_cli_and_global_standard(tmp_path: Path) -> None:
    expected = {
        "location": "eastus",
        "coreModelName": "gpt-5.2",
        "simpleModelName": "gpt-5-mini",
        "modelVersion": "2025-12-11",
        "simpleModelVersion": "2025-08-07",
        "modelCapacity": 200,
        "simpleModelCapacity": 200,
    }
    schema = template("infrastructure")["parameters"]
    values = deploy_v2.load_infrastructure_parameters(infrastructure_parameter_file(tmp_path))
    controls = ui_controls("infrastructure")
    for field, value in expected.items():
        assert schema[field]["defaultValue"] == value
        assert values[field] == value
        if field != "location":
            assert str(controls[field]["defaultValue"]) == str(value)
            assert (
                _evaluate_ui_expression(
                    ui_definition("infrastructure")["outputs"][field],
                    {"contract": {"resourceGroupApi": None}, "options": {}},
                    basics={"infrastructureStage": "foundation"},
                    eager_conditionals=True,
                )
                == value
            )
    model_template = module(template("infrastructure"), "v2-required-models")["properties"][
        "template"
    ]
    deployments = [
        resource
        for resource in resources(model_template)
        if resource["type"] == "Microsoft.CognitiveServices/accounts/deployments"
    ]
    assert len(deployments) == 2
    assert all(resource["sku"]["name"] == "GlobalStandard" for resource in deployments)
    assert "modelDeploymentRegion" not in controls
    assert "modelRegionMismatchNotice" not in controls
    assert controls["virtualNetwork"]["options"]["filter"]["location"] == "all"
    assert ui_definition("infrastructure")["config"]["basics"]["location"]["visible"] == (
        "[equals(basics('infrastructureStage'), 'foundation')]"
    )
    assert (
        _evaluate_ui_expression(
            ui_definition("infrastructure")["outputs"]["location"],
            {"contract": {"resourceGroupApi": None}},
            network={"virtualNetworkMode": "new"},
            basics={"infrastructureStage": "foundation"},
            resource_group={"location": "koreacentral"},
            selected_location="koreacentral",
            eager_conditionals=True,
        )
        == "koreacentral"
    )
    assert all("location" not in resource for resource in deployments)


def test_new_defaults_do_not_replace_saved_legacy_models(tmp_path: Path) -> None:
    values = deploy_v2.load_infrastructure_parameters(infrastructure_parameter_file(tmp_path))
    values.update(
        location="koreacentral",
        coreModelName="gpt-5.6-terra",
        simpleModelName="gpt-5.6-luna",
        modelVersion="2026-07-09",
        simpleModelVersion="2026-07-09",
    )
    saved = deploy_v2.make_tag_contract(values, "legacy-foundation", state="foundation")
    options = deploy_v2.decode_tag_options(saved)
    options.pop("simpleModelVersion")
    encoded = json.dumps(options, separators=(",", ":"))
    saved.update(
        {
            key: encoded[index * 256 : (index + 1) * 256]
            for index, key in enumerate(deploy_v2.OPTION_TAG_KEYS)
        }
    )
    restored = deploy_v2.infrastructure_values(FakeV2Azure(), saved)
    for field in (
        "location",
        "coreModelName",
        "simpleModelName",
        "modelVersion",
        "simpleModelVersion",
    ):
        assert restored[field] == values[field]
        assert (
            _evaluate_ui_expression(
                ui_definition("infrastructure")["outputs"][field],
                {"contract": {"resourceGroupApi": {"tags": saved}}},
                network={},
                basics={"infrastructureStage": "complete"},
            )
            == values[field]
        )
    notice = _evaluate_ui_expression(
        ui_controls("infrastructure")["modelRegionNotice"]["options"]["text"],
        {"contract": {"resourceGroupApi": {"tags": saved}}},
        network={},
        basics={"infrastructureStage": "complete"},
        eager_conditionals=True,
    )
    assert "koreacentral" in notice
    assert "gpt-5.6-terra / 2026-07-09" in notice
    assert "gpt-5.6-luna / 2026-07-09" in notice
    assert "gpt-5.2" not in notice
    assert "--reselect-models" in notice


@pytest.mark.parametrize(
    "quota,provider_capacity,expected", [(400, 250, True), (300, 250, False), (400, 199, False)]
)
def test_independent_model_versions_share_quota_not_provider_capacity(
    monkeypatch: pytest.MonkeyPatch, quota: int, provider_capacity: int, expected: bool
) -> None:
    cli = FakeV2Azure()
    values = {
        **cli.values,
        "deployCapabilityHost": True,
        "provisionModels": True,
        "provisionEmail": False,
        "strictModelSelection": True,
        "coreModelName": "gpt-5",
        "simpleModelName": "gpt-5",
        "modelVersion": "2025-08-07",
        "simpleModelVersion": "2025-10-01",
        "modelCapacity": 200,
        "simpleModelCapacity": 200,
    }
    cli.collections[f"{cli.account_id}/deployments"] = []
    cli.collections[f"{cli.account_id}/models"] = [
        {
            "name": "gpt-5",
            "version": version,
            "capabilities": {"responses": "true", "agentsV2": "true"},
            "skus": [{"name": "GlobalStandard", "usageName": "shared-quota"}],
        }
        for version in (values["modelVersion"], values["simpleModelVersion"])
    ]
    cli.collections[
        f"/subscriptions/{SUBSCRIPTION}/providers/Microsoft.CognitiveServices/locations/{values['location']}/usages"
    ] = [{"name": {"value": "shared-quota"}, "limit": quota, "currentValue": 0}]

    def capacity_read(*arguments: str) -> Any:
        assert arguments[:3] == ("rest", "--method", "get")
        return {
            "value": [
                {
                    "location": values["location"],
                    "properties": {
                        "skuName": "GlobalStandard",
                        "availableCapacity": provider_capacity,
                    },
                }
            ]
        }

    monkeypatch.setattr(cli, "json", capacity_read)
    if expected:
        plan = deploy_v2.automatic_service_plan(cli, values, {})
        assert plan["useCoreForSimpleModel"] is False
    else:
        with pytest.raises(ValueError, match="Insufficient"):
            deploy_v2.automatic_service_plan(cli, values, {})
    assert not cli.operations


def test_infrastructure_excludes_application_installation() -> None:
    types = {resource["type"] for resource in resources(template("infrastructure"))}
    assert "Microsoft.App/containerApps" not in types
    assert "Microsoft.App/jobs" not in types
    assert "Microsoft.Insights/diagnosticSettings" in types
    assert "Microsoft.Authorization/roleAssignments" in types
    assert "Microsoft.Resources/tags" in types
    assert "Microsoft.CognitiveServices/accounts/projects" in types


def test_application_reads_rg_tags_and_only_writes_application_surfaces() -> None:
    document = template("application")
    assert document["parameters"]["infrastructureTags"]["defaultValue"] == "[resourceGroup().tags]"
    assert document["parameters"]["infrastructureSchema"]["allowedValues"] == ["2"]
    assert document["parameters"]["infrastructureState"]["allowedValues"] == ["ready"]
    assert document["parameters"]["infrastructureProfile"]["allowedValues"] == ["kt-private-v2"]
    assert {resource["type"] for resource in resources(document)} == {
        "Microsoft.App/containerApps",
        "Microsoft.App/containerApps/authConfigs",
        "Microsoft.App/jobs",
        "Microsoft.Resources/deployments",
        "Microsoft.Resources/tags",
    }


def test_application_deployment_record_waits_for_every_application_resource() -> None:
    document = template("application")
    record = next(
        resource
        for resource in resources(document)
        if resource["type"] == "Microsoft.Resources/tags"
    )
    assert record["name"] == "default"
    assert record["properties"]["tags"].startswith("[union(coalesce(resourceGroup().tags,")
    assert "'azbrief.v2.applicationState', 'deployed'" in record["properties"]["tags"]
    assert (
        "'azbrief.v2.lastApplicationDeploymentAt', parameters('applicationDeploymentTimestamp')"
        in record["properties"]["tags"]
    )
    dependencies = record["dependsOn"]
    assert document["languageVersion"] == "2.0"
    assert set(dependencies) == {"containerApp", "schedulerJob", "webAuth", "mcp"}
    assert document["resources"]["containerApp"]["type"] == "Microsoft.App/containerApps"
    assert document["resources"]["schedulerJob"]["type"] == "Microsoft.App/jobs"
    assert document["resources"]["webAuth"]["type"] == "Microsoft.App/containerApps/authConfigs"
    assert document["resources"]["mcp"]["name"] == "v2-managed-mcp-application"
    assert (
        document["parameters"]["applicationDeploymentTimestamp"]["defaultValue"]
        == "[utcNow('yyyy-MM-ddTHH:mm:ssZ')]"
    )
    assert document["outputs"]["application"]["value"]["applicationReady"] is False


def test_legacy_application_install_reserves_tag_room_before_writes() -> None:
    cli = FakeV2Azure()
    cli.tags.update({f"customer{index}": "keep" for index in range(49 - len(cli.tags))})
    with pytest.raises(ValueError, match="50-tag"):
        deploy_v2.prepare_application(cli, IMAGE)
    assert not cli.operations
    cli.tags.pop("customer0")
    assert deploy_v2.prepare_application(cli, IMAGE)["reuseExistingApiKey"] is False
    assert all(value == "keep" for key, value in cli.tags.items() if key.startswith("customer"))


def test_application_deployment_tag_pair_accepts_legacy_and_known_states() -> None:
    tags = tag_contract()
    assert deploy_v2.parse_tag_contract(tags) == tags
    tags.update(
        {
            deploy_v2.PREFIX + "applicationState": "not_deployed",
            deploy_v2.PREFIX + "lastApplicationDeploymentAt": "",
        }
    )
    assert deploy_v2.parse_tag_contract(tags) == tags
    tags.update(
        {
            deploy_v2.PREFIX + "applicationState": "deployed",
            deploy_v2.PREFIX + "lastApplicationDeploymentAt": "2026-10-13T07:15:30Z",
        }
    )
    assert deploy_v2.parse_tag_contract(tags) == tags


@pytest.mark.parametrize(
    "state,timestamp",
    [
        ("unknown", ""),
        ("not_deployed", "2026-10-13T07:15:30Z"),
        ("deployed", ""),
        ("deployed", "2026-10-13T07:15:30+09:00"),
        ("deployed", "2026-13-40T25:15:30Z"),
    ],
)
def test_application_deployment_tag_pair_rejects_corrupted_values(
    state: str, timestamp: str
) -> None:
    tags = tag_contract()
    tags.update(
        {
            deploy_v2.PREFIX + "applicationState": state,
            deploy_v2.PREFIX + "lastApplicationDeploymentAt": timestamp,
        }
    )
    with pytest.raises(ValueError):
        deploy_v2.parse_tag_contract(tags)


def test_application_deployment_tag_pair_is_complete_and_infrastructure_ready() -> None:
    tags = tag_contract()
    tags[deploy_v2.PREFIX + "applicationState"] = "deployed"
    with pytest.raises(ValueError, match="Incomplete"):
        deploy_v2.parse_tag_contract(tags)
    tags[deploy_v2.PREFIX + "lastApplicationDeploymentAt"] = "2026-10-13T07:15:30Z"
    tags[deploy_v2.PREFIX + "state"] = "foundation"
    with pytest.raises(ValueError, match="Invalid application"):
        deploy_v2.parse_tag_contract(tags, require_ready=False)


@pytest.mark.parametrize("mode", ["preflight", "validate", "what-if", "deploy"])
def test_preview_and_failed_application_preserve_previous_success_tags(
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    cli = FakeV2Azure(installed=True)
    cli.tags.update(
        {
            deploy_v2.PREFIX + "applicationState": "deployed",
            deploy_v2.PREFIX + "lastApplicationDeploymentAt": "2026-10-12T12:00:00Z",
            "costCenter": "customer-owned",
        }
    )
    previous = copy.deepcopy(cli.tags)
    if mode == "deploy":
        original = deploy_v2.deploy_template

        def fail_create(
            target: AzureCli, artifact: Path, parameters: dict[str, Any], operation: str, name: str
        ) -> dict[str, Any]:
            if operation == "create":
                raise RuntimeError("Application resource deployment failed")
            return original(target, artifact, parameters, operation, name)

        monkeypatch.setattr(deploy_v2, "deploy_template", fail_create)
        with pytest.raises(RuntimeError, match="resource deployment failed"):
            deploy_v2.run_application(cli, IMAGE, {}, mode, "failed-update")
    else:
        deploy_v2.run_application(cli, IMAGE, {}, mode, "preview-update")
    assert cli.tags == previous


def test_handoff_is_completed_after_control_plane_rbac() -> None:
    document = template("infrastructure")
    handoff = module(document, "v2-infrastructure-tags")
    assert handoff["condition"] == "[variables('complete')]"
    assert any("v2-control-plane-roles" in dependency for dependency in handoff["dependsOn"])
    assert any("v2-project-telemetry-role" in dependency for dependency in handoff["dependsOn"])
    tags_document = handoff["properties"]["template"]
    tags = tags_document["variables"]["contractTags"]
    assert len(deploy_v2.TAG_KEYS) == 31
    assert all(f"'{key}'" in tags for key in deploy_v2.TAG_KEYS)
    assert all(f"'{key}'" in tags for key in deploy_v2.APPLICATION_TAG_KEYS)
    assert "'not_deployed'" in tags
    assert "resourceGroup().tags" in tags
    assert handoff["properties"]["parameters"]["infrastructureState"] == (
        "[if(variables('manageAzureMcp'), createObject('value', 'mcp_pending'), "
        "createObject('value', 'ready'))]"
    )
    assert tags_document["parameters"]["infrastructureState"]["allowedValues"] == [
        "foundation",
        "mcp_pending",
        "ready",
    ]
    assert "range(0, 8)" in tags_document["variables"]["optionsTags"]
    assert "256" in tags_document["variables"]["optionsTags"]
    assert tags_document["resources"][0]["properties"]["tags"].startswith(
        "[union(coalesce(resourceGroup().tags,"
    )


def test_foundation_never_creates_the_project_before_account_host_readiness() -> None:
    document = template("infrastructure")
    foundation = module(document, "v2-foundation")
    assert foundation["condition"] == "[not(variables('complete'))]"
    nested = foundation["properties"]["template"]
    assert not any(
        resource["type"] == "Microsoft.CognitiveServices/accounts/projects"
        for resource in resources(nested)
    )
    workload = module(document, "v2-foundry-workload")
    assert workload["condition"] == "[variables('complete')]"
    project = next(
        resource
        for resource in resources(workload["properties"]["template"])
        if resource["type"] == "Microsoft.CognitiveServices/accounts/projects"
    )
    assert project["condition"] == "[not(parameters('reuseExistingFoundrySetup'))]"
    assert any("v2-required-models" in dependency for dependency in workload["dependsOn"])
    host = module(document, "v2-project-host")
    assert any("v2-agent-bindings" in dependency for dependency in host["dependsOn"])


def test_foundation_preserves_private_network_and_storage_contracts() -> None:
    foundation = module(template("infrastructure"), "v2-foundation")["properties"]["template"]
    environment = module(foundation, "v2-container-environment")["properties"]["parameters"]
    assert environment["internal"]["value"] is True
    assert environment["publicNetworkAccess"]["value"] == "Disabled"
    assert environment["appLogsConfiguration"]["value"]["destination"] == "log-analytics"
    storage = module(foundation, "v2-storage")["properties"]["parameters"]
    assert storage["allowSharedKeyAccess"]["value"] is False
    assert storage["allowBlobPublicAccess"]["value"] is False
    assert storage["publicNetworkAccess"]["value"] == "Disabled"
    assert storage["minimumTlsVersion"]["value"] == "TLS1_2"


def test_foundation_adds_only_environment_private_ingress_dns() -> None:
    foundation = module(template("infrastructure"), "v2-foundation")["properties"]["template"]
    dns = module(foundation, "v2-internal-ingress-dns")["properties"]["parameters"]
    assert "defaultDomain" in dns["name"]["value"]
    records = json.dumps(dns["a"]["value"])
    assert "staticIp" in records
    assert {record["name"] for record in dns["a"]["value"]} == {"*", "@"}
    links = json.dumps(dns["virtualNetworkLinks"]["value"])
    assert "vnetId" in links
    assert "false" in links.lower()
    assert dns["enableTelemetry"]["value"] is False


def test_compiled_foundry_and_monitoring_roles_keep_exact_resource_scopes() -> None:
    for document in (template("infrastructure"), template("runtime-roles")):
        for resource in resources(document):
            if resource["type"] != "Microsoft.Authorization/roleAssignments":
                continue
            definition = resource["properties"]["roleDefinitionId"]
            if "53ca6127-db72-4b80-b1b0-d745d6d5456d" in definition:
                assert "scope" in resource
                assert "Microsoft.CognitiveServices/accounts/projects" in resource.get(
                    "_scope_resource_type", resource["scope"]
                )
            if "3913510d-42f4-4e42-8a64-420c390055eb" in definition:
                assert "scope" in resource
                assert "Microsoft.Insights/components" in resource.get(
                    "_scope_resource_type", resource["scope"]
                )


def test_control_plane_blob_grants_and_foundation_diagnostics_keep_explicit_scopes() -> None:
    document = template("infrastructure")
    control_plane = module(document, "v2-control-plane-roles")["properties"]["template"]
    for resource in resources(control_plane):
        if resource["type"] != "Microsoft.Authorization/roleAssignments":
            continue
        if "ba92f5b4-2d11-453d-a403-e96b0029c9fe" in resource["properties"]["roleDefinitionId"]:
            assert "scope" in resource
            assert "Microsoft.Storage/storageAccounts/blobServices/containers" in resource.get(
                "_scope_resource_type", resource["scope"]
            )
    foundation = module(document, "v2-foundation")["properties"]["template"]
    declared = foundation["resources"]
    entries = declared.values() if isinstance(declared, dict) else declared
    diagnostics = [
        resource
        for resource in entries
        if resource["type"] == "Microsoft.Insights/diagnosticSettings"
    ]
    assert len(diagnostics) == 4
    assert all("scope" in resource for resource in diagnostics)


def test_app_and_job_share_image_and_secret_but_keep_distinct_commands() -> None:
    document = template("application")
    app = next(
        resource["properties"]["template"]
        for resource in resources(document)
        if resource["type"] == "Microsoft.App/containerApps"
    )
    job = next(
        resource["properties"]["template"]
        for resource in resources(document)
        if resource["type"] == "Microsoft.App/jobs"
    )
    assert "parameters('containerImage')" in app
    assert "parameters('containerImage')" in job
    assert "src.scheduler" in job
    assert "src.scheduler" not in app
    assert document["parameters"]["apiKey"]["type"] == "securestring"
    assert document["parameters"]["existingSecrets"]["type"] == "secureObject"
    assert "parameters('apiKey')" in json.dumps(document["resources"])
    assert "reuseExistingApiKey" in json.dumps(document["resources"])
    job_configuration = next(
        resource["properties"]["configuration"]
        for resource in resources(document)
        if resource["type"] == "Microsoft.App/jobs"
    )
    assert "manualTriggerConfig" in job_configuration
    assert "if(" in job_configuration


def test_foundation_saves_options_before_completion_and_app_stays_ready_only() -> None:
    document = template("infrastructure")
    foundation = module(document, "v2-foundation-tags")
    assert foundation["condition"] == "[not(variables('complete'))]"
    assert foundation["properties"]["parameters"]["infrastructureState"]["value"] == "foundation"
    assert "infrastructureOptions" in foundation["properties"]["parameters"]
    assert template("application")["parameters"]["infrastructureState"]["allowedValues"] == [
        "ready"
    ]


def test_managed_mcp_is_split_between_identity_and_application_without_auth_bypass() -> None:
    infrastructure = template("infrastructure")
    assert module(infrastructure, "v2-mcp-identity")
    authentication = template("mcp-authentication")
    assert module(authentication, "v2-mcp-authentication")
    assert module(authentication, "v2-mcp-project-permission")
    connection = next(
        resource
        for resource in resources(authentication)
        if resource.get("properties", {}).get("category") == "RemoteTool"
    )
    assert connection["apiVersion"] == "2025-10-01-preview"
    assert connection["properties"]["authType"] == "ProjectManagedIdentity"
    audience = connection["properties"]["audience"]
    assert audience.startswith("[format('api://{0}', ")
    assert "parameters('clientId')" in audience
    connection_module = module(authentication, "v2-mcp-project-connection")
    handoff = module(authentication, "v2-mcp-authentication-handoff")
    assert "v2-mcp-authentication" in (
        handoff["properties"]["parameters"]["authentication"]["value"]["clientId"]
    )
    assert "v2-mcp-project-permission" in json.dumps(handoff["dependsOn"])
    assert connection_module["properties"]["parameters"]["clientId"]["value"] == (
        "[parameters('authentication').clientId]"
    )
    assert connection["properties"]["target"] == "[parameters('serverUrl')]"
    assert (
        "azureMcpServerUrl" in connection_module["properties"]["parameters"]["serverUrl"]["value"]
    )
    assert any(
        "connection" in dependency
        for dependency in module(authentication, "v2-mcp-ready-tags")["dependsOn"]
    )
    app = module(template("application"), "v2-managed-mcp-application")
    assert (
        app["properties"]["parameters"]["installManagedMcp"]["value"]
        == "[not(parameters('reuseExistingApiKey'))]"
    )
    server = module(app["properties"]["template"], "v2-mcp-server-application")
    assert (
        server["properties"]["parameters"]["azureMcpImage"]["value"]
        == "mcr.microsoft.com/azure-sdk/azure-mcp:3.0.0-beta.38"
    )
    body = json.dumps(server["properties"]["template"])
    assert "--read-only" in body
    assert "AzureAd__ClientId" in body
    assert "DANGEROUSLY_DISABLE_AUTH" not in body
    assert "Microsoft.Authorization/roleAssignments" not in body


def test_shared_mcp_module_accepts_infrastructure_identity_and_keeps_legacy_default() -> None:
    source = (
        ROOT / "infra" / "azure-mcp-server" / "infra" / "modules" / "aca-infrastructure.bicep"
    ).read_text(encoding="utf-8")
    assert "param userAssignedIdentityResourceId string = ''" in source
    wrapper = module(template("application"), "v2-managed-mcp-application")
    server = module(wrapper["properties"]["template"], "v2-mcp-server-application")
    identity = server["properties"]["parameters"]["userAssignedIdentityResourceId"]["value"]
    assert "userAssignedIdentities" in identity
    child = server["properties"]["template"]
    assert child["parameters"]["userAssignedIdentityResourceId"]["defaultValue"] == ""
    container_app = next(
        resource
        for resource in resources(child)
        if resource["type"] == "Microsoft.App/containerApps"
    )
    assignment = json.dumps(container_app["identity"])
    assert "UserAssigned" in assignment
    assert "SystemAssigned" in assignment
    environment = json.dumps(container_app["properties"]["template"]["containers"][0]["env"])
    assert "AZURE_CLIENT_ID" in environment
    assert "userAssignedIdentityResourceId" in environment


def test_portal_infrastructure_does_not_execute_microsoft_graph() -> None:
    document = template("infrastructure")
    body = json.dumps(document)
    assert "Microsoft.Graph/" not in body
    assert "v2-mcp-authentication" not in body
    assert "v2-mcp-project-permission" not in body


def test_hosted_roles_only_complete_post_publication_permissions() -> None:
    document = template("runtime-roles")
    assert document["parameters"]["infrastructureTags"]["defaultValue"] == "[resourceGroup().tags]"
    assert {resource["type"] for resource in resources(document)} == {
        "Microsoft.Authorization/roleAssignments",
        "Microsoft.Resources/deployments",
    }
    evidence = module(document, "v2-hosted-evidence-reader")
    assert evidence["subscriptionId"] == "[subscription().subscriptionId]"
    assert "Microsoft.App/containerApps" not in json.dumps(document)
    assert "Microsoft.App/jobs" not in json.dumps(document)


def tag_contract() -> dict[str, str]:
    tags = {deploy_v2.PREFIX + suffix: "test-" + suffix for suffix in deploy_v2.TAG_FIELDS.values()}
    tags.update(
        {
            "azbrief.v2.schema": "2",
            "azbrief.v2.profile": deploy_v2.PROFILE,
            "azbrief.v2.state": "ready",
            "azbrief.v2.acaPeEnabled": "false",
            "azbrief.v2.location": "koreacentral",
            "azbrief.v2.storage": "sttestv2",
            "azbrief.v2.mcpUrl": "https://mcp.example.invalid/mcp",
        }
    )
    return tags


class FakeV2Azure(AzureCli):
    def __init__(self, installed: bool = False):
        super().__init__(SUBSCRIPTION, TENANT, "rg-v2-test")
        self.tags = tag_contract()
        self.values = deploy_v2.infrastructure_values(self, self.tags)
        self.documents: dict[str, dict[str, Any]] = {}
        self.collections: dict[str, list[dict[str, Any]]] = {}
        self.secret_values: dict[str, list[dict[str, str]]] = {}
        self.operations: list[str] = []
        self.requests: list[dict[str, Any]] = []
        self.inventory: list[dict[str, Any]] = []
        self.dns_zones: list[dict[str, Any]] = []
        self.denied: set[str] = set()
        self.environment_id = f"{self.group_id}/providers/Microsoft.App/managedEnvironments/{self.values['containerAppsEnvironmentName']}"
        self.identity_id = f"{self.group_id}/providers/Microsoft.ManagedIdentity/userAssignedIdentities/{self.values['controlPlaneIdentityName']}"
        self.account_id = f"{self.group_id}/providers/Microsoft.CognitiveServices/accounts/{self.values['foundryAccountName']}"
        self.project_id = f"{self.account_id}/projects/{self.values['projectName']}"
        self.app_id = f"{self.group_id}/providers/Microsoft.App/containerApps/{self.values['containerAppName']}"
        self.job_id = (
            f"{self.group_id}/providers/Microsoft.App/jobs/{self.values['schedulerJobName']}"
        )
        self.documents[self.environment_id] = self.resource(
            self.environment_id,
            {
                "provisioningState": "Succeeded",
                "publicNetworkAccess": "Disabled",
                "defaultDomain": "test.koreacentral.azurecontainerapps.io",
                "vnetConfiguration": {
                    "internal": True,
                    "infrastructureSubnetId": f"/subscriptions/{SUBSCRIPTION}/resourceGroups/{self.values['virtualNetworkResourceGroupName']}/providers/Microsoft.Network/virtualNetworks/{self.values['virtualNetworkName']}/subnets/{self.values['containerAppsSubnetName']}",
                },
            },
        )
        self.documents[self.identity_id] = self.resource(
            self.identity_id,
            {
                "clientId": "00000000-0000-0000-0000-000000000003",
                "principalId": "00000000-0000-0000-0000-000000000004",
            },
        )
        self.documents[self.account_id] = self.resource(
            self.account_id, {"publicNetworkAccess": "Disabled"}
        )
        self.collections[f"{self.account_id}/projects"] = [{"name": self.values["projectName"]}]
        self.collections[f"{self.project_id}/capabilityHosts"] = [
            {
                "name": "agents",
                "properties": {
                    "provisioningState": "Succeeded",
                    "storageConnections": ["agent-storage"],
                    "threadStorageConnections": ["agent-cosmos"],
                    "vectorStoreConnections": ["agent-search"],
                },
            }
        ]
        self.collections[f"{self.project_id}/connections"] = [
            self.connection(
                "agent-storage",
                "AzureStorageAccount",
                "Microsoft.Storage/storageAccounts",
                "storageAccountName",
                "blob.core.windows.net",
            ),
            self.connection(
                "agent-cosmos",
                "CosmosDb",
                "Microsoft.DocumentDB/databaseAccounts",
                "cosmosAccountName",
                "documents.azure.com",
            ),
            self.connection(
                "agent-search",
                "CognitiveSearch",
                "Microsoft.Search/searchServices",
                "searchServiceName",
                "search.windows.net",
            ),
        ]
        insights_id = f"{self.group_id}/providers/Microsoft.Insights/components/{self.values['applicationInsightsName']}"
        self.collections[f"{self.project_id}/connections"].append(
            {
                "name": "azbrief-app-insights",
                "properties": {
                    "category": "AppInsights",
                    "authType": "ProjectManagedIdentity",
                    "target": insights_id,
                    "metadata": {"ResourceId": insights_id},
                },
            }
        )
        self.collections[f"{self.group_id}/providers/Microsoft.App/containerApps"] = []
        self.collections[f"{self.group_id}/providers/Microsoft.App/jobs"] = []
        if installed:
            self.install()

    def resource(self, resource_id: str, properties: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": resource_id,
            "name": resource_id.split("/")[-1],
            "tags": {"deploymentProfile": deploy_v2.PROFILE, "costCenter": "keep"},
            "properties": properties,
        }

    def use_managed_agent_data(self) -> None:
        schema = template("infrastructure")["parameters"]
        options = {
            field: copy.deepcopy(schema[field]["defaultValue"]) for field in deploy_v2.OPTION_FIELDS
        }
        options["useMicrosoftManagedAgentData"] = True
        encoded = json.dumps(options, separators=(",", ":"))
        self.tags.update(
            {
                key: encoded[index * 256 : (index + 1) * 256]
                for index, key in enumerate(deploy_v2.OPTION_TAG_KEYS)
            }
        )
        self.tags[deploy_v2.PREFIX + "cosmos"] = ""
        self.tags[deploy_v2.PREFIX + "search"] = ""
        self.values = deploy_v2.infrastructure_values(self, self.tags)
        self.collections[f"{self.project_id}/capabilityHosts"][0]["properties"] = {
            "capabilityHostKind": "Agents",
            "provisioningState": "Succeeded",
        }
        self.collections[f"{self.project_id}/connections"] = [
            row
            for row in self.collections[f"{self.project_id}/connections"]
            if row["name"] == "azbrief-app-insights"
        ]

    def connection(
        self, name: str, category: str, resource_type: str, field: str, suffix: str
    ) -> dict[str, Any]:
        target_id = f"{self.group_id}/providers/{resource_type}/{self.values[field]}"
        return {
            "name": name,
            "properties": {
                "category": category,
                "authType": "AAD",
                "target": f"https://{self.values[field]}.{suffix}",
                "metadata": {"ResourceId": target_id},
            },
        }

    def install(self) -> None:
        for label, resource_id, collection in (
            ("app", self.app_id, "containerApps"),
            ("job", self.job_id, "jobs"),
        ):
            configuration = (
                {
                    "activeRevisionsMode": "Single",
                    "ingress": {
                        "targetPort": 8000,
                        "allowInsecure": False,
                        "fqdn": "test.example.invalid",
                    },
                }
                if label == "app"
                else {
                    "triggerType": "Schedule",
                    "replicaTimeout": 3600,
                    "replicaRetryLimit": 0,
                    "scheduleTriggerConfig": {
                        "cronExpression": "*/5 * * * *",
                        "parallelism": 1,
                        "replicaCompletionCount": 1,
                    },
                }
            )
            resource = self.resource(
                resource_id,
                {
                    "provisioningState": "Succeeded",
                    "environmentId": self.environment_id,
                    "workloadProfileName": "Consumption",
                    "configuration": configuration,
                    "template": {
                        "containers": [
                            {
                                "name": "azbrief" if label == "app" else "azbrief-scheduler",
                                "image": IMAGE,
                                "resources": {"cpu": 1, "memory": "2Gi"},
                                "env": [
                                    {"name": "API_KEY", "secretRef": "orchestrator-api-key"},
                                    {"name": "ADMIN_REQUIRE_AUTH", "value": "True"},
                                    {"name": "ARCHIVE_REQUIRE_AUTH", "value": "true"},
                                    {"name": "CUSTOM_SETTING", "value": "keep"},
                                ],
                            }
                        ],
                        "scale": {"minReplicas": 1, "maxReplicas": 3},
                    },
                },
            )
            resource["identity"] = {
                "type": "UserAssigned",
                "userAssignedIdentities": {self.identity_id: {}},
            }
            self.documents[resource_id] = resource
            self.collections[f"{self.group_id}/providers/Microsoft.App/{collection}"] = [resource]
            self.secret_values[resource_id] = [
                {"name": "orchestrator-api-key", "value": "same-api-key-do-not-rotate-1234567890"},
                {"name": "registry-token", "value": "same-registry-token"},
            ]
        client_id = "00000000-0000-0000-0000-000000000005"
        self.collections[f"{self.app_id}/authConfigs"] = [
            {
                "name": "current",
                "properties": {
                    "platform": {"enabled": True},
                    "httpSettings": {"requireHttps": True},
                    "globalValidation": {"unauthenticatedClientAction": "AllowAnonymous"},
                    "identityProviders": {
                        "azureActiveDirectory": {
                            "enabled": True,
                            "registration": {
                                "clientId": client_id,
                                "clientSecretSettingName": "registry-token",
                                "openIdIssuer": f"https://login.microsoftonline.com/{TENANT}/v2.0",
                            },
                            "validation": {"allowedAudiences": [client_id, f"api://{client_id}"]},
                        }
                    },
                    "login": {
                        "allowedExternalRedirectUrls": [
                            f"https://{self.values['containerAppName']}.test.koreacentral.azurecontainerapps.io"
                        ]
                    },
                },
            }
        ]

    def assert_context(self) -> None:
        return None

    def get(self, resource_id: str, api_version: str) -> dict[str, Any]:
        if resource_id in self.denied:
            raise RuntimeError("ARM read denied")
        return copy.deepcopy(self.documents[resource_id])

    def collection(self, resource_id: str, api_version: str) -> list[dict[str, Any]]:
        if resource_id in self.denied:
            raise RuntimeError("ARM collection denied")
        return copy.deepcopy(self.collections[resource_id])

    def json(self, *arguments: str) -> Any:
        if arguments[:2] == ("deployment", "group"):
            assert arguments[arguments.index("--mode") + 1] == "Incremental"
            path = Path(arguments[arguments.index("--parameters") + 1][1:])
            payload = json.loads(path.read_text(encoding="utf-8"))["parameters"]
            self.operations.append(arguments[2])
            self.requests.append(
                {
                    "template": Path(arguments[arguments.index("--template-file") + 1]).name,
                    "parameters": {key: entry["value"] for key, entry in payload.items()},
                    "temporary_path": str(path),
                }
            )
            return {"properties": {"provisioningState": "Succeeded"}}
        if arguments[:2] == ("group", "show"):
            return {"tags": copy.deepcopy(self.tags)}
        if arguments[:2] == ("resource", "list"):
            return copy.deepcopy(
                self.dns_zones if "--resource-type" in arguments else self.inventory
            )
        if arguments[0] == "rest":
            url = arguments[arguments.index("--url") + 1]
            resource_id = url.removeprefix(deploy_v2.MANAGEMENT).split("/listSecrets?")[0]
            if resource_id in self.denied:
                raise RuntimeError("Secret read denied")
            return {"value": copy.deepcopy(self.secret_values[resource_id])}
        raise AssertionError("Unexpected Azure operation")


class FakeAutomaticV2Azure(FakeV2Azure):
    def __init__(self) -> None:
        super().__init__()
        self.user = {"id": SUBSCRIPTION, "mail": "operator@example.com"}
        self.account_type = "user"
        self.applications: list[dict[str, Any]] = []
        self.principals: list[dict[str, Any]] = []
        self.owners: list[dict[str, Any]] = [{"id": SUBSCRIPTION}]
        self.directory_writes: list[str] = []
        self.paged = False

    def json(self, *arguments: str) -> Any:
        if arguments[:2] == ("account", "show"):
            return {
                "id": self.subscription,
                "tenantId": self.tenant,
                "user": {"type": self.account_type},
            }
        if arguments[:3] == ("ad", "signed-in-user", "show"):
            return copy.deepcopy(self.user)
        if arguments[0] == "rest" and arguments[arguments.index("--url") + 1].startswith(
            deploy_v2.GRAPH
        ):
            url = arguments[arguments.index("--url") + 1]
            path = url.removeprefix(deploy_v2.GRAPH).split("?")[0]
            method = arguments[arguments.index("--method") + 1]
            if method == "GET":
                rows = self.applications if path == "applications" else self.principals
                if path.endswith("/owners"):
                    rows = self.owners
                response = {"value": copy.deepcopy(rows)}
                if self.paged:
                    response["@odata.nextLink"] = "https://graph.microsoft.com/continued"
                return response
            self.directory_writes.append(path)
            payload = json.loads(
                Path(arguments[arguments.index("--body") + 1][1:]).read_text(encoding="utf-8")
            )
            if path == "applications":
                application = {
                    **payload,
                    "id": "00000000-0000-0000-0000-000000000006",
                    "appId": "00000000-0000-0000-0000-000000000007",
                    "passwordCredentials": [],
                }
                self.applications.append(application)
                return copy.deepcopy(application)
            if path == "servicePrincipals":
                principal = {
                    **payload,
                    "id": "00000000-0000-0000-0000-000000000008",
                    "appOwnerOrganizationId": self.tenant,
                    "servicePrincipalType": "Application",
                    "accountEnabled": True,
                }
                self.principals.append(principal)
                return copy.deepcopy(principal)
            if path.endswith("/addPassword"):
                self.applications[0]["passwordCredentials"] = [payload["passwordCredential"]]
                return {
                    "keyId": "00000000-0000-0000-0000-000000000009",
                    "secretText": "synthetic-web-credential-never-log",
                }
            raise AssertionError("Unexpected directory write")
        return super().json(*arguments)


class FakeMcpPreparation(FakeV2Azure):
    def __init__(self) -> None:
        super().__init__()
        self.tags[deploy_v2.PREFIX + "state"] = "mcp_pending"
        self.tags[deploy_v2.PREFIX + "mcpUrl"] = (
            f"https://{self.values['azureMcpContainerAppName']}.test.koreacentral.azurecontainerapps.io"
        )
        options = {
            "emailDataLocation": "Asia Pacific",
            "searchSku": "basic",
            "coreModelName": "gpt-5.6-terra",
            "simpleModelName": "gpt-5.6-luna",
            "modelVersion": "2026-07-09",
            "modelCapacity": 200,
            "simpleModelCapacity": 200,
            "existingPrivateDnsZoneIds": {},
            "tags": {"deploymentProfile": deploy_v2.PROFILE},
            "manageAzureMcp": True,
            "azureMcpClientId": "",
            "azureMcpServicePrincipalId": "",
            "azureMcpAppRoleId": "",
        }
        encoded = json.dumps(options, separators=(",", ":"))
        self.tags.update(
            {
                key: encoded[index * 256 : (index + 1) * 256]
                for index, key in enumerate(deploy_v2.OPTION_TAG_KEYS)
            }
        )
        self.values = deploy_v2.infrastructure_values(self, self.tags)
        mcp_identity = f"{self.group_id}/providers/Microsoft.ManagedIdentity/userAssignedIdentities/{self.values['azureMcpIdentityName']}"
        self.documents[mcp_identity] = self.resource(mcp_identity, {})
        self.core = {
            **self.values,
            "schemaVersion": 2,
            "profile": deploy_v2.PROFILE,
            "infrastructureComplete": True,
            "tenantId": self.tenant,
            "subscriptionId": self.subscription,
            "resourceGroup": self.group,
            "azureMcpEntraAppUniqueName": "azbrief-v2-mcp-abcdefghijklm",
        }
        self.apps: list[dict[str, Any]] = []
        self.core_state = "Succeeded"

    def json(self, *arguments: str) -> Any:
        if arguments[:3] == ("deployment", "group", "show"):
            return {"state": self.core_state, "infrastructure": copy.deepcopy(self.core)}
        if arguments[:3] == ("ad", "app", "list"):
            return copy.deepcopy(self.apps)
        return super().json(*arguments)


def test_managed_host_readback_and_app_preflight_require_no_byo_inventory() -> None:
    cli = FakeV2Azure()
    cli.use_managed_agent_data()
    parameters = deploy_v2.prepare_application(cli, IMAGE)
    assert parameters["infrastructureTags"][deploy_v2.PREFIX + "cosmos"] == ""
    assert parameters["infrastructureTags"][deploy_v2.PREFIX + "search"] == ""
    assert not cli.operations


@pytest.mark.parametrize(
    "field", ["storageConnections", "threadStorageConnections", "vectorStoreConnections"]
)
def test_managed_host_rejects_customer_connection_redirects(field: str) -> None:
    cli = FakeV2Azure()
    cli.use_managed_agent_data()
    cli.collections[f"{cli.project_id}/capabilityHosts"][0]["properties"][field] = [
        "customer-connection"
    ]
    with pytest.raises(ValueError, match="immutable"):
        deploy_v2.prepare_application(cli, IMAGE)
    assert not cli.operations


def test_managed_host_does_not_adopt_existing_byo_connection_names() -> None:
    cli = FakeV2Azure()
    cli.use_managed_agent_data()
    cli.collections[f"{cli.project_id}/connections"].append(
        {"name": "agent-storage", "properties": {}}
    )
    with pytest.raises(ValueError, match="BYO connections"):
        deploy_v2.prepare_application(cli, IMAGE)
    assert not cli.operations


def test_byo_host_cannot_be_read_as_a_managed_mode() -> None:
    cli = FakeV2Azure()
    cli.use_managed_agent_data()
    cli.tags[deploy_v2.PREFIX + "cosmos"] = "old-cosmos"
    with pytest.raises(ValueError, match="must not bind"):
        deploy_v2.prepare_application(cli, IMAGE)


def test_saved_byo_foundation_cannot_silently_switch_to_managed(tmp_path: Path) -> None:
    managed = deploy_v2.load_infrastructure_parameters(infrastructure_parameter_file(tmp_path))
    legacy = {
        **managed,
        "useMicrosoftManagedAgentData": False,
        "cosmosAccountName": "legacy-cosmos",
        "searchServiceName": "legacy-search",
    }
    cli = FakeV2Azure()
    cli.tags = deploy_v2.make_tag_contract(legacy, "legacy-foundation", state="foundation")
    with pytest.raises(ValueError, match="storage mode is immutable"):
        deploy_v2.prepare_infrastructure(cli, managed, "complete", "must-not-convert")
    assert not cli.operations


@pytest.mark.parametrize("managed", [True, False])
def test_portal_completes_only_the_saved_agent_data_mode(tmp_path: Path, managed: bool) -> None:
    values = deploy_v2.load_infrastructure_parameters(infrastructure_parameter_file(tmp_path))
    values["useMicrosoftManagedAgentData"] = managed
    if not managed:
        values.update(cosmosAccountName="old-cosmos", searchServiceName="old-search")
    saved = deploy_v2.make_tag_contract(values, "foundation", state="foundation")
    outputs = ui_definition("infrastructure")["outputs"]
    data = {"contract": {"resourceGroupApi": {"tags": saved}}, "names": {}, "options": {}}
    for field in ("useMicrosoftManagedAgentData", "cosmosAccountName", "searchServiceName"):
        actual = _evaluate_ui_expression(
            outputs[field],
            data,
            basics={"infrastructureStage": "complete"},
            eager_conditionals=True,
        )
        assert actual == values[field]


def test_managed_dns_completion_accepts_only_five_owned_default_zones() -> None:
    cli = FakeV2Azure()
    namespaces = [
        "privatelink.cognitiveservices.azure.com",
        "privatelink.openai.azure.com",
        "privatelink.services.ai.azure.com",
        "privatelink.blob.core.windows.net",
        "privatelink.koreacentral.azurecontainerapps.io",
    ]
    resolved = {
        name: f"{cli.group_id}/providers/Microsoft.Network/privateDnsZones/{name}"
        for name in namespaces
    }
    for resource_id in resolved.values():
        cli.documents[resource_id] = cli.resource(resource_id, {})
    deploy_v2._verify_saved_dns_bindings(cli, {}, resolved, use_microsoft_managed_agent_data=True)
    with pytest.raises(ValueError, match="incomplete"):
        deploy_v2._verify_saved_dns_bindings(
            cli,
            {},
            {**resolved, "privatelink.search.windows.net": "foreign"},
            use_microsoft_managed_agent_data=True,
        )
    assert not cli.operations


def test_mcp_preparation_uses_only_exact_pending_core_output() -> None:
    cli = FakeMcpPreparation()
    parameters = deploy_v2.prepare_mcp_authentication(cli)
    assert (
        parameters["infrastructure"]["azureMcpEntraAppUniqueName"]
        == cli.core["azureMcpEntraAppUniqueName"]
    )
    assert parameters["infrastructureOptions"]["manageAzureMcp"] is True
    assert not cli.operations


def test_compiled_infrastructure_handoff_contains_every_tag_binding() -> None:
    contract = template("infrastructure")["outputs"]["infrastructure"]["value"]
    assert set(deploy_v2.TAG_FIELDS) <= set(contract)


@pytest.mark.parametrize("redirect", [False, True])
def test_mcp_preparation_accepts_legacy_names_only_from_exact_arm_ids(redirect: bool) -> None:
    cli = FakeMcpPreparation()
    values = cli.values
    vnet_id = (
        f"/subscriptions/{cli.subscription}/resourceGroups/"
        f"{values['virtualNetworkResourceGroupName']}/providers/Microsoft.Network/"
        f"virtualNetworks/{values['virtualNetworkName']}"
    )
    cli.core.update(
        controlPlaneIdentityResourceId=(
            f"{cli.group_id}/providers/Microsoft.ManagedIdentity/userAssignedIdentities/"
            f"{values['controlPlaneIdentityName']}"
        ),
        virtualNetworkResourceId=vnet_id,
        privateEndpointSubnetId=f"{vnet_id}/subnets/{values['peSubnetName']}",
        foundrySubnetResourceId=f"{vnet_id}/subnets/{values['foundrySubnetName']}",
        infrastructureSubnetId=f"{vnet_id}/subnets/{values['containerAppsSubnetName']}",
    )
    missing = (
        "controlPlaneIdentityName",
        "virtualNetworkResourceGroupName",
        "virtualNetworkName",
        "peSubnetName",
        "foundrySubnetName",
        "containerAppsSubnetName",
    )
    for field in missing:
        cli.core.pop(field)
    if redirect:
        cli.core["controlPlaneIdentityResourceId"] += "-foreign"
        with pytest.raises(ValueError, match="binding changed"):
            deploy_v2.prepare_mcp_authentication(cli)
    else:
        result = deploy_v2.prepare_mcp_authentication(cli)
        assert all(result["infrastructure"][field] == values[field] for field in missing)
    assert not cli.operations


@pytest.mark.parametrize(
    "field,value",
    [
        ("resourceGroup", "other-rg"),
        ("azureMcpServerUrl", "https://foreign.example.invalid"),
        ("azureMcpEntraAppUniqueName", "unrelated-app"),
        ("infrastructureComplete", False),
    ],
)
def test_mcp_preparation_rejects_redirected_core_output(field: str, value: object) -> None:
    cli = FakeMcpPreparation()
    cli.core[field] = value
    with pytest.raises(ValueError):
        deploy_v2.prepare_mcp_authentication(cli)
    assert not cli.operations


def test_mcp_preparation_rejects_failed_core_and_foreign_directory_app() -> None:
    cli = FakeMcpPreparation()
    cli.core_state = "Failed"
    with pytest.raises(ValueError, match="successful exact core"):
        deploy_v2.prepare_mcp_authentication(cli)
    cli.core_state = "Succeeded"
    cli.apps = [{"uniqueName": "foreign-app", "displayName": "other"}]
    with pytest.raises(ValueError, match="different MCP"):
        deploy_v2.prepare_mcp_authentication(cli)
    assert not cli.operations


def test_saved_empty_dns_defaults_accept_only_owned_same_group_zones() -> None:
    cli = FakeV2Azure()
    namespaces = [
        "privatelink.cognitiveservices.azure.com",
        "privatelink.openai.azure.com",
        "privatelink.services.ai.azure.com",
        "privatelink.blob.core.windows.net",
        "privatelink.documents.azure.com",
        "privatelink.search.windows.net",
        "privatelink.koreacentral.azurecontainerapps.io",
    ]
    resolved = {
        namespace: f"{cli.group_id}/providers/Microsoft.Network/privateDnsZones/{namespace}"
        for namespace in namespaces
    }
    for resource_id in resolved.values():
        cli.documents[resource_id] = cli.resource(resource_id, {})
    deploy_v2._verify_saved_dns_bindings(cli, {}, resolved)
    altered = dict(resolved)
    altered[namespaces[0]] = resolved[namespaces[0]].replace(cli.group, "foreign-rg")
    with pytest.raises(ValueError, match="redirect"):
        deploy_v2._verify_saved_dns_bindings(cli, {}, altered)
    incomplete = dict(resolved)
    incomplete.pop(namespaces[0])
    with pytest.raises(ValueError, match="incomplete"):
        deploy_v2._verify_saved_dns_bindings(cli, {}, incomplete)
    cli.documents[resolved[namespaces[0]]]["tags"] = {"deploymentProfile": "foreign"}
    with pytest.raises(ValueError, match="foreign"):
        deploy_v2._verify_saved_dns_bindings(cli, {}, resolved)
    assert not cli.operations


def test_saved_explicit_dns_override_is_preserved_not_reinterpreted() -> None:
    cli = FakeV2Azure()
    saved = {
        f"zone{index}": f"/subscriptions/{SUBSCRIPTION}/resourceGroups/central/providers/Microsoft.Network/privateDnsZones/zone{index}"
        for index in range(7)
    }
    deploy_v2._verify_saved_dns_bindings(cli, saved, dict(saved))
    changed = dict(saved)
    changed["zone0"] = changed["zone0"].replace("central", "other")
    with pytest.raises(ValueError, match="redirect"):
        deploy_v2._verify_saved_dns_bindings(cli, saved, changed)
    assert not cli.operations


def test_deployer_defaults_use_directory_mail_and_object_id() -> None:
    user = {"id": SUBSCRIPTION, "mail": "operator@example.com", "userPrincipalName": "guest#EXT#"}
    environment = deploy_v2.deployer_environment_defaults(user)
    assert environment["EMAIL_RECIPIENT_ADDRESS"] == "operator@example.com"
    assert environment["ADMIN_ALLOWED_PRINCIPALS"] == SUBSCRIPTION
    assert environment["ARCHIVE_ALLOWED_PRINCIPALS"] == SUBSCRIPTION
    assert all(
        environment[name] == "true"
        for name in (
            "ADMIN_UI_ENABLED",
            "ARCHIVE_UI_ENABLED",
            "FEEDBACK_UI_ENABLED",
            "ADMIN_REQUIRE_AUTH",
            "ARCHIVE_REQUIRE_AUTH",
        )
    )
    assert "SCHEDULE_CRON_EXPRESSION" not in environment
    assert "SCHEDULE_DISPATCH_ENABLED" not in environment


@pytest.mark.parametrize(
    "user",
    [
        {},
        {"id": SUBSCRIPTION, "userPrincipalName": "operator@example.com"},
        {"id": SUBSCRIPTION, "mail": "guest#EXT#@example.com"},
        {"id": SUBSCRIPTION, "mail": "operator@example.com,other@example.com"},
        {"id": SUBSCRIPTION, "mail": "operator@example.com\r\nBcc:other@example.com"},
        {"id": "not-an-object-id", "mail": "operator@example.com"},
        {"id": "00000000-0000-0000-0000-000000000000", "mail": "operator@example.com"},
    ],
)
def test_deployer_defaults_reject_missing_or_invalid_directory_profile(
    user: dict[str, Any],
) -> None:
    with pytest.raises(ValueError):
        deploy_v2.deployer_environment_defaults(user)


def test_initial_application_uses_generated_key_and_tag_names() -> None:
    cli = FakeV2Azure()
    parameters = deploy_v2.prepare_application(cli, IMAGE)
    assert parameters["reuseExistingApiKey"] is False
    assert "apiKey" not in parameters
    assert parameters["infrastructureTags"] == cli.tags
    assert parameters["existingSecrets"] == {"app": [], "job": []}
    assert not cli.operations


def test_initial_deployer_defaults_require_auth_and_keep_explicit_settings() -> None:
    cli = FakeV2Azure()
    user = {"id": SUBSCRIPTION, "mail": "operator@example.com"}
    with pytest.raises(ValueError, match="complete Entra"):
        deploy_v2.prepare_application(cli, IMAGE, deployment_user=user)
    cli.install()
    auth = cli.collections[f"{cli.app_id}/authConfigs"][0]["properties"]
    cli.collections[f"{cli.group_id}/providers/Microsoft.App/containerApps"] = []
    cli.collections[f"{cli.group_id}/providers/Microsoft.App/jobs"] = []
    parameters = deploy_v2.prepare_application(
        cli,
        IMAGE,
        {
            "webAuthConfiguration": auth,
            "existingSecrets": {"app": [{"name": "registry-token", "value": "web-secret"}]},
            "appEnvironment": [{"name": "EMAIL_RECIPIENT_ADDRESS", "value": "custom@example.com"}],
        },
        deployment_user=user,
    )
    app = {entry["name"]: entry["value"] for entry in parameters["appEnvironment"]}
    job = {entry["name"]: entry["value"] for entry in parameters["jobEnvironment"]}
    assert app["EMAIL_RECIPIENT_ADDRESS"] == "custom@example.com"
    assert app["ADMIN_ALLOWED_PRINCIPALS"] == SUBSCRIPTION
    assert app["FEEDBACK_RECIPIENT_ADDRESS"] == "operator@example.com"
    assert job["EMAIL_RECIPIENT_ADDRESS"] == "custom@example.com"
    assert job["FEEDBACK_UI_ENABLED"] == "true"
    assert "ADMIN_ALLOWED_PRINCIPALS" not in job
    assert parameters["appTemplate"]["scale"]["minReplicas"] == 1
    assert "operator@example.com" not in json.dumps(parameters["infrastructureTags"])


def test_application_update_never_injects_deployer_defaults() -> None:
    cli = FakeV2Azure()
    cli.install()
    parameters = deploy_v2.prepare_application(cli, IMAGE, deployment_user={})
    environment = {entry["name"]: entry for entry in parameters["appEnvironment"]}
    assert "EMAIL_RECIPIENT_ADDRESS" not in environment
    assert "ADMIN_ALLOWED_PRINCIPALS" not in environment
    assert "ADMIN_UI_ENABLED" not in environment
    assert parameters["jobConfiguration"]["triggerType"] == "Schedule"


@pytest.mark.parametrize("mode", ["preflight", "validate", "what-if"])
def test_automatic_application_preview_never_writes_directory(mode: str) -> None:
    cli = FakeAutomaticV2Azure()
    deploy_v2.run_application(cli, IMAGE, {}, mode, "automatic-preview")
    assert not cli.directory_writes
    assert not cli.applications
    assert "create" not in cli.operations
    if cli.requests:
        parameters = cli.requests[0]["parameters"]
        assert parameters["webAuthConfiguration"]["platform"]["enabled"] is True
        assert parameters["existingSecrets"]["app"][0]["name"] == deploy_v2.WEB_SECRET_NAME


def test_automatic_application_deploy_prepares_login_without_leaking_credentials(
    capsys: pytest.CaptureFixture[str],
) -> None:
    cli = FakeAutomaticV2Azure()
    deploy_v2.run_application(cli, IMAGE, {}, "deploy", "automatic-install")
    assert cli.operations == ["validate", "what-if", "validate", "what-if", "create"]
    assert cli.directory_writes == [
        "applications",
        "servicePrincipals",
        "applications/00000000-0000-0000-0000-000000000006/addPassword",
    ]
    parameters = cli.requests[-1]["parameters"]
    auth = parameters["webAuthConfiguration"]["identityProviders"]["azureActiveDirectory"]
    assert auth["registration"]["clientId"] == cli.applications[0]["appId"]
    assert cli.applications[0]["requiredResourceAccess"] == []
    assert cli.applications[0]["owners@odata.bind"] == [
        deploy_v2.GRAPH + "directoryObjects/" + SUBSCRIPTION
    ]
    environment = {entry["name"]: entry["value"] for entry in parameters["appEnvironment"]}
    assert environment["ADMIN_ALLOWED_PRINCIPALS"] == SUBSCRIPTION
    assert environment["ARCHIVE_ALLOWED_PRINCIPALS"] == SUBSCRIPTION
    assert environment["EMAIL_RECIPIENT_ADDRESS"] == "operator@example.com"
    assert environment["FEEDBACK_UI_ENABLED"] == "true"
    assert "clientSecretSettingName" not in environment
    assert parameters["existingSecrets"]["app"] == [
        {
            "name": deploy_v2.WEB_SECRET_NAME,
            "value": "synthetic-web-credential-never-log",
        }
    ]
    assert cli.requests[-3]["parameters"] == parameters
    assert cli.requests[-2]["parameters"] == parameters
    assert "synthetic-web-credential" not in json.dumps(parameters["infrastructureTags"])
    assert "operator@example.com" not in json.dumps(parameters["infrastructureTags"])
    assert "synthetic-web-credential" not in capsys.readouterr().out
    initial = copy.deepcopy(parameters)
    cli.install()
    for label, resource_id in (("app", cli.app_id), ("job", cli.job_id)):
        resource = cli.documents[resource_id]
        container = resource["properties"]["template"]["containers"][0]
        environment_entries = {entry["name"]: entry for entry in container["env"]}
        environment_entries.update(
            {entry["name"]: entry for entry in initial[f"{label}Environment"]}
        )
        container["env"] = list(environment_entries.values())
    cli.secret_values[cli.app_id] += initial["existingSecrets"]["app"]
    cli.collections[f"{cli.app_id}/authConfigs"][0]["properties"] = initial["webAuthConfiguration"]
    cli.user = {}
    for revision in ("b", "c"):
        deploy_v2.run_application(
            cli,
            "ghcr.io/networkdog/azbrief@sha256:" + revision * 64,
            {},
            "deploy",
            "automatic-update",
        )
        updated = cli.requests[-1]["parameters"]
        assert updated["webAuthConfiguration"] == initial["webAuthConfiguration"]
        assert updated["jobConfiguration"]["triggerType"] == "Schedule"
        assert updated["reuseExistingApiKey"] is True
        assert {
            entry["name"]: entry["value"] for entry in updated["appEnvironment"] if "value" in entry
        }["EMAIL_RECIPIENT_ADDRESS"] == "operator@example.com"
    assert len(cli.directory_writes) == 3


@pytest.mark.parametrize("failure", ["no_mail", "service_principal", "paged", "foreign_app"])
def test_automatic_application_blocks_untrusted_setup_before_writes(failure: str) -> None:
    cli = FakeAutomaticV2Azure()
    if failure == "no_mail":
        cli.user.pop("mail")
    elif failure == "service_principal":
        cli.account_type = "servicePrincipal"
    elif failure == "paged":
        cli.paged = True
    else:
        cli.applications = [{"uniqueName": "foreign", "signInAudience": "AzureADMultipleOrgs"}]
    with pytest.raises(ValueError):
        deploy_v2.run_application(cli, IMAGE, {}, "deploy", "automatic-denied")
    assert not cli.operations
    assert not cli.directory_writes


def test_existing_web_credentials_are_never_rotated_after_interrupted_install() -> None:
    cli = FakeAutomaticV2Azure()
    deploy_v2.run_application(cli, IMAGE, {}, "deploy", "automatic-install")
    previous_writes = list(cli.directory_writes)
    with pytest.raises(ValueError, match="explicit recovery"):
        deploy_v2.run_application(cli, IMAGE, {}, "deploy", "automatic-retry")
    assert cli.directory_writes == previous_writes


@pytest.mark.parametrize(
    "failure", ["foreign_owner", "redirect", "credentials", "malformed_owners"]
)
def test_automatic_setup_does_not_adopt_modified_or_unowned_registration(failure: str) -> None:
    cli = FakeAutomaticV2Azure()
    deploy_v2.run_application(cli, IMAGE, {}, "deploy", "automatic-installed")
    cli.applications[0]["passwordCredentials"] = []
    if failure == "foreign_owner":
        cli.owners = [{"id": TENANT}]
    elif failure == "redirect":
        cli.applications[0]["web"]["redirectUris"] = ["https://foreign.example.invalid/callback"]
    elif failure == "credentials":
        cli.applications[0]["passwordCredentials"] = None
    else:
        cli.owners = ["malformed"]
    previous_writes = list(cli.directory_writes)
    previous_operations = list(cli.operations)
    with pytest.raises(ValueError):
        deploy_v2.run_application(cli, IMAGE, {}, "deploy", "automatic-rejected")
    assert cli.directory_writes == previous_writes
    assert cli.operations == previous_operations


def test_explicit_web_authentication_is_not_replaced_by_automatic_setup() -> None:
    cli = FakeAutomaticV2Azure()
    cli.install()
    auth = cli.collections[f"{cli.app_id}/authConfigs"][0]["properties"]
    cli.collections[f"{cli.group_id}/providers/Microsoft.App/containerApps"] = []
    cli.collections[f"{cli.group_id}/providers/Microsoft.App/jobs"] = []
    configuration = {
        "webAuthConfiguration": auth,
        "existingSecrets": {"app": [{"name": "registry-token", "value": "supplied-web-secret"}]},
    }
    deploy_v2.run_application(cli, IMAGE, configuration, "deploy", "explicit-authentication")
    assert not cli.directory_writes
    assert not cli.applications
    assert cli.requests[-1]["parameters"]["webAuthConfiguration"] == auth


def test_mcp_pending_core_can_only_be_read_for_authorized_preparation() -> None:
    cli = FakeV2Azure()
    cli.tags[deploy_v2.PREFIX + "state"] = "mcp_pending"
    with pytest.raises(ValueError, match="completed AzBrief v2"):
        deploy_v2.prepare_application(cli, IMAGE)
    tags, values = deploy_v2._read_infrastructure(cli, "mcp_pending")
    assert tags[deploy_v2.PREFIX + "state"] == "mcp_pending"
    assert values["controlPlaneIdentityResourceId"] == cli.identity_id
    assert not cli.operations


def test_mcp_preparation_failure_never_runs_core_or_promotes_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cli = FakeV2Azure()
    cli.tags[deploy_v2.PREFIX + "state"] = "mcp_pending"
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(deploy_v2, "prepare_mcp_authentication", lambda target: {})

    def deploy(
        target: AzureCli, artifact: Path, parameters: dict[str, Any], mode: str, name: str
    ) -> dict[str, Any]:
        calls.append((artifact.name, mode))
        if mode == "create":
            raise RuntimeError("Graph access denied")
        return {}

    monkeypatch.setattr(deploy_v2, "deploy_template", deploy)
    with pytest.raises(RuntimeError, match="Graph access denied"):
        deploy_v2.run_mcp_authentication(cli, "deploy", "test")
    assert calls == [
        ("mcp-authentication.json", mode) for mode in ("validate", "what-if", "create")
    ]
    assert cli.tags[deploy_v2.PREFIX + "state"] == "mcp_pending"
    assert not cli.operations


@pytest.mark.parametrize("wrapped", [False, True])
def test_application_update_accepts_only_explicit_missing_auth_config(
    monkeypatch: pytest.MonkeyPatch,
    wrapped: bool,
) -> None:
    cli = FakeV2Azure(installed=True)
    original_collection = cli.collection

    def collection(resource_id: str, api_version: str) -> list[dict[str, Any]]:
        if resource_id == f"{cli.app_id}/authConfigs":
            payload = '{"error":{"code":"AuthConfigNotFound"}}'
            if wrapped:
                payload = f"Not Found({payload})"
            raise RuntimeError("Azure CLI rest failed: ERROR: " + payload)
        return original_collection(resource_id, api_version)

    monkeypatch.setattr(cli, "collection", collection)
    parameters = deploy_v2.prepare_application(cli, IMAGE)
    assert parameters["reuseExistingApiKey"] is True
    assert parameters["webAuthConfiguration"] == {}
    assert parameters["existingSecrets"]["app"] == parameters["existingSecrets"]["job"]
    with pytest.raises(ValueError, match="complete Entra authentication"):
        deploy_v2.prepare_application(
            cli, IMAGE, {"appEnvironment": [{"name": "ADMIN_UI_ENABLED", "value": "true"}]}
        )
    assert not cli.operations


@pytest.mark.parametrize("code", ["AuthorizationFailed", "Forbidden", "ResourceNotFound"])
def test_application_auth_config_read_errors_still_block_updates(
    monkeypatch: pytest.MonkeyPatch, code: str
) -> None:
    cli = FakeV2Azure(installed=True)
    original_collection = cli.collection

    def collection(resource_id: str, api_version: str) -> list[dict[str, Any]]:
        if resource_id == f"{cli.app_id}/authConfigs":
            failure = {"error": {"code": code, "message": "AuthConfigNotFound in untrusted text"}}
            raise RuntimeError("Azure CLI rest failed: ERROR: " + json.dumps(failure))
        return original_collection(resource_id, api_version)

    monkeypatch.setattr(cli, "collection", collection)
    with pytest.raises(RuntimeError):
        deploy_v2.prepare_application(cli, IMAGE)
    assert not cli.operations


@pytest.mark.parametrize(
    "failure",
    [
        'Forbidden({"error":{"code":"AuthConfigNotFound"}})',
        "Not Found(invalid JSON)",
        "AuthConfigNotFound without structured evidence",
    ],
)
def test_application_auth_config_untrusted_wrappers_still_block_updates(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    cli = FakeV2Azure(installed=True)
    original_collection = cli.collection

    def collection(resource_id: str, api_version: str) -> list[dict[str, Any]]:
        if resource_id == f"{cli.app_id}/authConfigs":
            raise RuntimeError("Azure CLI rest failed: ERROR: " + failure)
        return original_collection(resource_id, api_version)

    monkeypatch.setattr(cli, "collection", collection)
    with pytest.raises(RuntimeError):
        deploy_v2.prepare_application(cli, IMAGE)
    assert not cli.operations


def ui_definition(name: str) -> dict[str, Any]:
    return json.loads((V2 / f"{name}.createUiDefinition.json").read_text(encoding="utf-8"))[
        "parameters"
    ]


def ui_controls(name: str) -> dict[str, dict[str, Any]]:
    return {
        element["name"]: element
        for step in ui_definition(name)["steps"]
        for element in step["elements"]
    }


def test_infrastructure_ui_keeps_customer_names_but_not_internal_settings() -> None:
    controls = ui_controls("infrastructure")
    for field in ("namingPrefix", "namingSuffix"):
        assert controls[field]["type"] == "Microsoft.Common.TextBox"
        assert controls[field]["constraints"]["required"] is True
    for field in (
        "foundryAccountName",
        "storageAccountName",
        "cosmosAccountName",
        "searchServiceName",
        "projectName",
        "containerAppName",
        "containerAppsEnvironmentName",
        "schedulerJobName",
        "controlPlaneIdentityName",
        "applicationInsightsName",
        "communicationServicesName",
        "emailServiceName",
        "azureMcpContainerAppName",
        "azureMcpIdentityName",
    ):
        assert field not in controls
        assert field in ui_definition("infrastructure")["outputs"]
    for field in (
        "modelDeploymentName",
        "simpleModelDeploymentName",
        "foundryHostedAgentName",
        "azureMcpServerUrl",
        "azureMcpProjectConnectionName",
    ):
        assert field not in controls
    for name in ("infrastructure", "application"):
        assert set(ui_definition(name)["outputs"]) <= set(template(name)["parameters"])


def default_option_tags() -> dict[str, str]:
    definitions = template("infrastructure")["parameters"]
    options = {
        name: copy.deepcopy(definitions[name]["defaultValue"]) for name in deploy_v2.OPTION_FIELDS
    }
    encoded = json.dumps(options, separators=(",", ":"))
    return {
        key: encoded[index * 256 : (index + 1) * 256]
        for index, key in enumerate(deploy_v2.OPTION_TAG_KEYS)
    }


@pytest.mark.parametrize("extra_tags,expected", [(0, True), (1, False)])
def test_portal_reserves_two_application_tags_within_group_budget(
    extra_tags: int, expected: bool
) -> None:
    tags = tag_contract()
    tags.update(default_option_tags())
    tags.update({f"customer{index}": "keep" for index in range(48 + extra_tags - len(tags))})
    data = {
        "application": {
            "deploymentAccepted": True,
            "resourceGroupApi": {"tags": tags},
            "containerAppsApi": {"value": []},
            "jobsApi": {"value": []},
        }
    }
    expression = ui_definition("application")["outputs"]["portalValidationPassed"]
    assert (
        _evaluate_ui_expression(expression, data, resource_group={"mode": "Existing"}) is expected
    )


def test_portal_does_not_use_application_metadata_as_inventory_evidence() -> None:
    tags = tag_contract()
    tags.update(default_option_tags())
    tags.update(
        {
            deploy_v2.PREFIX + "applicationState": "deployed",
            deploy_v2.PREFIX + "lastApplicationDeploymentAt": "2026-10-13T07:15:30Z",
        }
    )
    data = {
        "application": {
            "deploymentAccepted": True,
            "resourceGroupApi": {"tags": tags},
            "containerAppsApi": {"value": []},
            "jobsApi": {"value": []},
        }
    }
    expression = ui_definition("application")["outputs"]["portalValidationPassed"]
    assert _evaluate_ui_expression(expression, data, resource_group={"mode": "Existing"}) is True
    data["application"]["jobsApi"]["value"] = [
        {"name": tags[deploy_v2.PREFIX + "job"], "tags": {"deploymentProfile": deploy_v2.PROFILE}}
    ]
    assert _evaluate_ui_expression(expression, data, resource_group={"mode": "Existing"}) is False
    tags[deploy_v2.PREFIX + "lastApplicationDeploymentAt"] = ""
    assert _evaluate_ui_expression(expression, data, resource_group={"mode": "Existing"}) is False


@pytest.mark.parametrize(
    "state,valid", [("foundation", False), ("mcp_pending", False), ("ready", True), ("", False)]
)
def test_application_form_blocks_incomplete_infrastructure_before_arm_validation(
    state: str, valid: bool
) -> None:
    control = ui_controls("application")["containerImage"]
    validations = control["constraints"]["validations"]
    data = {"application": {"resourceGroupApi": {"tags": {deploy_v2.PREFIX + "state": state}}}}
    outcomes = [_evaluate_ui_expression(rule["isValid"], data) for rule in validations]
    assert all(outcomes) is valid
    if state == "foundation":
        assert "기존 기반 완성" in validations[0]["message"]
    if state == "mcp_pending":
        assert "MCP 인증 자동 준비" in validations[1]["message"]
        assert "--prepare-mcp-authentication" in (
            ui_controls("application")["mcpPendingNotice"]["options"]["text"]
        )
    assert template("application")["parameters"]["portalValidationPassed"]["allowedValues"] == [
        True
    ]


def test_portal_pending_mcp_notice_does_not_request_internal_identifiers() -> None:
    controls = ui_controls("application")
    notice = controls["mcpPendingNotice"]
    assert "mcp_pending" in notice["visible"]
    assert "--prepare-mcp-authentication" in notice["options"]["text"]
    assert "azureMcpClientId" not in controls
    tags = tag_contract()
    tags[deploy_v2.PREFIX + "state"] = "mcp_pending"
    expression = ui_definition("application")["outputs"]["portalValidationPassed"]
    result = _evaluate_ui_expression(
        expression,
        {
            "application": {
                "deploymentAccepted": True,
                "resourceGroupApi": {"tags": tags},
                "containerAppsApi": {"value": []},
                "jobsApi": {"value": []},
            }
        },
        resource_group={"mode": "Existing"},
    )
    assert result is False


def test_portal_foundation_notice_explains_blocked_application_without_bypass() -> None:
    notice = ui_controls("application")["foundationIncompleteNotice"]
    assert "foundation" in notice["visible"]
    assert "기존 기반 완성" in notice["options"]["text"]
    tags = tag_contract()
    tags[deploy_v2.PREFIX + "state"] = "foundation"
    expression = ui_definition("application")["outputs"]["portalValidationPassed"]
    data = {
        "application": {
            "deploymentAccepted": True,
            "resourceGroupApi": {"tags": tags},
            "containerAppsApi": {"value": []},
            "jobsApi": {"value": []},
        }
    }
    assert _evaluate_ui_expression(expression, data, resource_group={"mode": "Existing"}) is False


def test_portal_explains_cli_automatic_first_install_without_identity_inputs() -> None:
    controls = ui_controls("application")
    notice = controls["automaticSetupNotice"]
    assert "ready" in notice["visible"]
    assert "scripts.deploy_v2 application" in notice["options"]["text"]
    assert "Portal만으로 최초 설치하면 웹 UI는 꺼진 상태" in notice["options"]["text"]
    for field in ("deploymentUserMail", "deploymentUserObjectId", "webClientSecret"):
        assert field not in controls


def test_application_shares_verified_web_origins_with_job_delivery() -> None:
    declared = template("application")["resources"]
    entries = declared.values() if isinstance(declared, dict) else declared
    surfaces = [
        entry
        for entry in entries
        if entry["type"] in {"Microsoft.App/containerApps", "Microsoft.App/jobs"}
    ]
    assert len(surfaces) == 2
    origins = []
    for surface in surfaces:
        for name in ("ARCHIVE_BASE_URL", "FEEDBACK_BASE_URL"):
            body = surface["properties"]["template"]
            marker = f"createObject('name', '{name}', 'value', "
            start = body.index(marker) + len(marker)
            end = body.index(".defaultDomain)", start) + len(".defaultDomain)")
            origins.append(body[start:end])
    assert len(set(origins)) == 1
    assert origins[0].startswith("format('https://{0}.{1}', ")
    assert "reference('containerEnvironment').defaultDomain" in origins[0]
    assert "variables('environmentId')" in json.dumps(declared["containerEnvironment"])


def test_saved_customer_options_round_trip_and_application_rejects_foundation(
    tmp_path: Path,
) -> None:
    values = deploy_v2.load_infrastructure_parameters(infrastructure_parameter_file(tmp_path))
    values.update(
        searchSku="standard", emailDataLocation="Korea", modelCapacity=450, simpleModelCapacity=300
    )
    saved = deploy_v2.make_tag_contract(values, "customer-foundation", state="foundation")
    assert len(saved) == 41
    assert saved[deploy_v2.PREFIX + "applicationState"] == "not_deployed"
    assert saved[deploy_v2.PREFIX + "lastApplicationDeploymentAt"] == ""
    assert all(len(value) <= 256 for value in saved.values())
    options = deploy_v2.decode_tag_options(saved)
    assert options["searchSku"] == "standard"
    assert options["emailDataLocation"] == "Korea"
    assert options["modelCapacity"] == 450
    with pytest.raises(ValueError, match="completed"):
        deploy_v2.parse_tag_contract(saved)
    outputs = ui_definition("infrastructure")["outputs"]
    data = {"contract": {"resourceGroupApi": {"tags": saved}}}
    for field in (
        "searchSku",
        "emailDataLocation",
        "modelCapacity",
        "simpleModelCapacity",
        "modelVersion",
        "containerAppName",
        "foundryAccountName",
        "azureMcpIdentityName",
    ):
        actual = _evaluate_ui_expression(
            outputs[field], data, basics={"infrastructureStage": "complete"}
        )
        assert actual == values[field]


@pytest.mark.parametrize("state", ["foundation", "ready", "invalid"])
def test_application_ui_requires_ready_same_group_contract(state: str) -> None:
    expression = ui_definition("application")["outputs"]["portalValidationPassed"]
    tags = tag_contract()
    tags.update({"azbrief.v2.state": state, "azbrief.v2.options0": "{}"})
    assert _evaluate_ui_expression(
        expression,
        {
            "application": {
                "resourceGroupApi": {"tags": tags},
                "deploymentAccepted": True,
                "containerAppsApi": {"value": []},
                "jobsApi": {"value": []},
            }
        },
        resource_group={"mode": "Existing"},
    ) is (state == "ready")


@pytest.mark.parametrize(
    "response",
    [
        {},
        {"value": None},
        {"value": [], "error": {"code": "Denied"}},
        {"value": [], "nextLink": "https://management.azure.com/next"},
    ],
)
def test_application_ui_never_treats_failed_inventory_as_install(response: dict[str, Any]) -> None:
    expression = ui_definition("application")["outputs"]["portalValidationPassed"]
    tags = {**tag_contract(), "azbrief.v2.options0": "{}"}
    result = _evaluate_ui_expression(
        expression,
        {
            "application": {
                "resourceGroupApi": {"tags": tags},
                "deploymentAccepted": True,
                "containerAppsApi": response,
                "jobsApi": {"value": []},
            }
        },
        resource_group={"mode": "Existing"},
    )
    assert result is False


def test_option_tags_reject_partial_unknown_and_oversized_saved_inputs(tmp_path: Path) -> None:
    values = deploy_v2.load_infrastructure_parameters(infrastructure_parameter_file(tmp_path))
    saved = deploy_v2.make_tag_contract(values, "customer-foundation")
    saved.pop("azbrief.v2.options7")
    with pytest.raises(ValueError, match="Incomplete"):
        deploy_v2.decode_tag_options(saved)
    values["existingPrivateDnsZoneIds"] = {"unexpected": "a" * 2100}
    with pytest.raises(ValueError, match="2048"):
        deploy_v2.make_tag_contract(values, "customer-foundation")


def test_ui_uses_supported_control_schema_and_legacy_network_guards(tmp_path: Path) -> None:
    for name in ("infrastructure", "application"):
        definition = ui_definition(name)
        assert definition["config"]["isWizard"] is True
        assert definition["config"]["basics"]["resourceGroup"]["allowExisting"] is True
        assert all("visible" not in step for step in definition["steps"])
        for control in ui_controls(name).values():
            if control["type"] == "Microsoft.Common.CheckBox":
                assert "validations" not in control.get("constraints", {})
        assert "isValidJson(" not in json.dumps(definition)
        assert "listSecrets" not in json.dumps(definition)
    cli, values = infrastructure_cli(tmp_path)
    vnet_id = f"/subscriptions/{SUBSCRIPTION}/resourceGroups/{values['virtualNetworkResourceGroupName']}/providers/Microsoft.Network/virtualNetworks/{values['virtualNetworkName']}"
    network = {
        "virtualNetwork": {"id": vnet_id},
        "virtualNetworkApi": cli.documents[vnet_id],
        **values,
    }
    expression = ui_definition("infrastructure")["outputs"]["portalValidationPassed"]
    data = {
        "contract": {"contractAccepted": True, "resourceGroupApi": None},
        "names": {"namingPrefix": "kt", "namingSuffix": "dev"},
        "options": {},
    }
    assert (
        _evaluate_ui_expression(
            expression,
            data,
            network=network,
            resource_group={"mode": "New"},
            basics={"infrastructureStage": "foundation"},
            eager_conditionals=True,
        )
        is True
    )
    network["containerAppsSubnetName"] = network["foundrySubnetName"]
    assert (
        _evaluate_ui_expression(
            expression,
            data,
            network=network,
            resource_group={"mode": "New"},
            basics={"infrastructureStage": "foundation"},
        )
        is False
    )


def test_saved_foundation_names_and_choices_cannot_be_changed(tmp_path: Path) -> None:
    cli, values = infrastructure_cli(tmp_path)
    cli.tags = deploy_v2.make_tag_contract(values, "foundation", state="foundation")
    altered = {**values, "searchSku": "standard"}
    with pytest.raises(ValueError, match="saved foundation option"):
        deploy_v2.prepare_infrastructure(cli, altered, "complete", "v2")
    altered = {**values, "containerAppName": "another-customer-app"}
    with pytest.raises(ValueError, match="saved foundation option"):
        deploy_v2.prepare_infrastructure(cli, altered, "complete", "v2")
    assert not cli.operations


@pytest.mark.parametrize("existing", ["deployments", "projects"])
def test_model_reselection_never_changes_provisioned_foundation(
    tmp_path: Path, existing: str
) -> None:
    cli, values = infrastructure_cli(tmp_path)
    cli.tags = deploy_v2.make_tag_contract(values, "foundation", state="foundation")
    cli.collections[f"{cli.account_id}/deployments"] = []
    cli.collections[f"{cli.account_id}/projects"] = []
    cli.collections[f"{cli.account_id}/{existing}"] = [{"name": "must-retain"}]
    values.update(coreModelName="gpt-5.2", modelVersion="2025-12-11")
    with pytest.raises(ValueError, match="zero model deployments and zero projects"):
        deploy_v2.prepare_infrastructure(
            cli, values, "complete", "v2", allow_model_reselection=True
        )
    assert not cli.operations


def test_model_reselection_cannot_change_saved_region(tmp_path: Path) -> None:
    cli, values = infrastructure_cli(tmp_path)
    cli.tags = deploy_v2.make_tag_contract(values, "foundation", state="foundation")
    cli.collections[f"{cli.account_id}/deployments"] = []
    cli.collections[f"{cli.account_id}/projects"] = []
    values["location"] = "eastus2"
    with pytest.raises(ValueError, match="saved foundation option"):
        deploy_v2.prepare_infrastructure(
            cli, values, "complete", "v2", allow_model_reselection=True
        )
    assert not cli.operations


def test_independent_versions_roundtrip_and_legacy_version_falls_back(tmp_path: Path) -> None:
    values = deploy_v2.load_infrastructure_parameters(infrastructure_parameter_file(tmp_path))
    values.update(
        coreModelName="gpt-5.2",
        simpleModelName="gpt-5-mini",
        modelVersion="2025-12-11",
        simpleModelVersion="2025-08-07",
    )
    saved = deploy_v2.make_tag_contract(values, "foundation", state="foundation")
    restored = deploy_v2.infrastructure_values(FakeV2Azure(), saved)
    assert restored["simpleModelVersion"] == "2025-08-07"
    assert restored["modelVersion"] == "2025-12-11"
    options = deploy_v2.decode_tag_options(saved)
    options.pop("simpleModelVersion")
    encoded = json.dumps(options, separators=(",", ":"))
    saved.update(
        {
            key: encoded[index * 256 : (index + 1) * 256]
            for index, key in enumerate(deploy_v2.OPTION_TAG_KEYS)
        }
    )
    assert (
        deploy_v2.infrastructure_values(FakeV2Azure(), saved)["simpleModelVersion"] == "2025-12-11"
    )


@pytest.mark.parametrize(
    "api_region,picker_region,expected",
    [
        ("eastus", "eastus", True),
        ("koreacentral", "koreacentral", True),
        (None, None, False),
        (None, "eastus", True),
    ],
)
def test_existing_vnet_region_is_never_overridden_by_model_or_rg_inputs(
    tmp_path: Path, api_region: str | None, picker_region: str | None, expected: bool
) -> None:
    cli, values = infrastructure_cli(tmp_path)
    vnet_id = f"/subscriptions/{SUBSCRIPTION}/resourceGroups/{values['virtualNetworkResourceGroupName']}/providers/Microsoft.Network/virtualNetworks/{values['virtualNetworkName']}"
    api = copy.deepcopy(cli.documents[vnet_id])
    api.pop("location", None)
    picker: dict[str, Any] = {"id": vnet_id}
    if api_region is not None:
        api["location"] = api_region
    if picker_region is not None:
        picker["location"] = picker_region
    network = {
        **values,
        "modelDeploymentRegion": "eastus",
        "virtualNetwork": picker,
        "virtualNetworkApi": api,
    }
    data = {
        "contract": {"contractAccepted": True, "resourceGroupApi": None},
        "names": {"namingPrefix": "kt", "namingSuffix": "dev"},
        "options": {},
    }
    assert _evaluate_ui_expression(
        ui_definition("infrastructure")["outputs"]["location"],
        data,
        network=network,
        basics={"infrastructureStage": "foundation"},
        resource_group={"mode": "New", "location": "koreacentral"},
        selected_location="koreacentral",
        eager_conditionals=True,
    ) == (api_region or picker_region or "")
    assert (
        _evaluate_ui_expression(
            ui_definition("infrastructure")["outputs"]["portalValidationPassed"],
            data,
            network=network,
            basics={"infrastructureStage": "foundation"},
            resource_group={"mode": "New", "location": "koreacentral"},
            selected_location="koreacentral",
            eager_conditionals=True,
        )
        is expected
    )


def test_core_and_mcp_customer_names_never_merge_identities(tmp_path: Path) -> None:
    cli, values = infrastructure_cli(tmp_path)
    values["azureMcpIdentityName"] = values["controlPlaneIdentityName"]
    with pytest.raises(ValueError, match="distinct"):
        deploy_v2.prepare_infrastructure(cli, values, "foundation", "v2")
    assert not cli.operations


def test_infrastructure_outputs_are_safe_before_rg_and_vnet_load() -> None:
    definition = ui_definition("infrastructure")
    controls = ui_controls("infrastructure")
    data = {"contract": {"resourceGroupApi": None}, "names": {}, "options": {}}
    for step in definition["steps"]:
        values = data.setdefault(step["name"], {})
        for control in step["elements"]:
            if "defaultValue" in control:
                default = control["defaultValue"]
                choices = control.get("constraints", {}).get("allowedValues", [])
                if isinstance(choices, list):
                    default = next(
                        (choice["value"] for choice in choices if choice.get("label") == default),
                        default,
                    )
                values[control["name"]] = default
    for expression in definition["outputs"].values():
        _evaluate_ui_expression(
            expression,
            data,
            network={},
            resource_group={"mode": "New"},
            subscription={"subscriptionId": SUBSCRIPTION},
            basics={"infrastructureStage": "foundation"},
            eager_conditionals=True,
        )
    assert not any(name.endswith("Guard") for name in controls)
    assert template("infrastructure")["parameters"]["portalValidationPassed"]["allowedValues"] == [
        True
    ]


def test_infrastructure_initialization_fallback_never_authorizes_completion() -> None:
    expression = ui_definition("infrastructure")["outputs"]["portalValidationPassed"]
    result = _evaluate_ui_expression(
        expression,
        {"contract": {"resourceGroupApi": None}, "names": {}, "options": {}},
        network={},
        resource_group={"mode": "Existing"},
        basics={"infrastructureStage": "complete"},
        eager_conditionals=True,
    )
    assert result is False


def test_infrastructure_initial_output_normalization_is_idempotent() -> None:
    from scripts.build_v2_ui import normalize_ui

    document = json.loads(
        (V2 / "infrastructure.createUiDefinition.json").read_text(encoding="utf-8")
    )
    legacy = json.loads((ROOT / "infra/kt/createUiDefinition.json").read_text(encoding="utf-8"))
    validations = [
        validation
        for step in legacy["parameters"]["steps"]
        for control in step["elements"]
        for validation in control.get("constraints", {}).get("validations", [])
        if validation.get("isValid", "").startswith(
            (
                "[not(empty(filter(coalesce(steps('network')",
                "[and(not(equals(coalesce(steps('network')",
                "[not(or(equals(toLower(coalesce(steps('network')",
            )
        )
    ]
    assert normalize_ui(normalize_ui(document, validations), validations) == normalize_ui(
        document, validations
    )


def test_create_ui_tag_reads_use_dot_properties_without_changing_tag_values() -> None:
    from scripts.build_v2_ui import _portable_tag_access

    source = "[steps('contract').resourceGroupApi.tags['azbrief.v2.options0']]"
    payload = '{"tags":{"azbrief.v2.schema":"2"},"endpoint":"https://example.invalid"}'
    converted = _portable_tag_access(source)
    assert "tags['" not in converted
    assert converted.endswith(".azbrief_v2_options0]")
    assert (
        _evaluate_ui_expression(
            converted,
            {"contract": {"resourceGroupApi": {"tags": {"azbrief.v2.options0": payload}}}},
        )
        == payload
    )
    assert _evaluate_ui_expression(converted, {"contract": {"resourceGroupApi": None}}) is None


def test_generated_v2_forms_do_not_use_string_key_tag_indexes() -> None:
    for name in ("infrastructure", "application"):
        text = (V2 / f"{name}.createUiDefinition.json").read_text(encoding="utf-8")
        assert "tags['azbrief.v2." not in text
    expression = ui_definition("application")["outputs"]["portalValidationPassed"]
    assert (
        _evaluate_ui_expression(
            expression,
            {
                "application": {
                    "deploymentAccepted": True,
                    "containerAppsApi": {"value": []},
                    "jobsApi": {"value": []},
                    "resourceGroupApi": {
                        "tags": {
                            "azbrief.v2.schema": "2",
                            "azbrief.v2.profile": "kt-private-v2",
                            "azbrief.v2.state": "ready",
                            "azbrief.v2.options0": "{}",
                        }
                    },
                }
            },
            resource_group={"mode": "Existing"},
        )
        is True
    )


def test_v2_starts_with_basics_and_keeps_kt_name_only_subnet_choice_contract() -> None:
    legacy = json.loads((ROOT / "infra/kt/createUiDefinition.json").read_text(encoding="utf-8"))[
        "parameters"
    ]
    legacy_network = next(step for step in legacy["steps"] if step["name"] == "network")
    legacy_controls = {element["name"]: element for element in legacy_network["elements"]}
    for name in ("infrastructure", "application"):
        assert ui_definition(name)["config"]["isWizard"] is True
        assert list(ui_definition(name))[0] == "config"
    assert legacy["config"].get("isWizard", False) is False
    assert ui_definition("infrastructure")["basics"]
    assert (
        "steps('network').virtualNetwork.id"
        in ui_controls("infrastructure")["virtualNetworkApi"]["request"]["path"]
    )
    for name in ("peSubnetName", "foundrySubnetName", "containerAppsSubnetName"):
        assert (
            ui_controls("infrastructure")[name]["constraints"]
            == legacy_controls[name]["constraints"]
        )
        assert (
            ui_controls("infrastructure")[name]["defaultValue"]
            == legacy_controls[name]["defaultValue"]
        )
    expected_options = copy.deepcopy(legacy_controls["virtualNetwork"]["options"])
    expected_options["filter"]["location"] = "all"
    assert ui_controls("infrastructure")["virtualNetwork"]["options"] == expected_options


def test_vnet_discovery_uses_shared_bindings_or_the_direct_picker_dependency() -> None:
    from scripts.build_v2_ui import EFFECTIVE_VNET_ID

    control = ui_controls("infrastructure")["virtualNetworkApi"]
    assert "visible" not in control
    effective_id = EFFECTIVE_VNET_ID.replace("steps('names').", "steps('resources').")
    assert control["request"] == {
        "method": "GET",
        "path": f"[concat({effective_id}, '?api-version=2024-05-01')]",
    }
    assert not any(
        token in control["request"]["path"]
        for token in ("basics(", "subscription(", "virtualNetworkMode")
    )


def test_vnet_discovery_request_and_subnet_choices_follow_selection_changes() -> None:
    controls = ui_controls("infrastructure")
    network: dict[str, Any] = {"virtualNetworkMode": "existing", "virtualNetwork": None}
    documents = {
        f"/subscriptions/{SUBSCRIPTION}/resourceGroups/RG-Network-A/providers/Microsoft.Network/virtualNetworks/vnet-a": {
            "location": "koreacentral",
            "properties": {
                "subnets": [
                    {"name": "pe-Customer-A", "properties": {"addressPrefix": "10.20.0.0/28"}},
                    {"name": "Foundry-A", "properties": {}},
                    {"name": "Containers-A", "properties": {"addressPrefixes": None}},
                ]
            },
        },
        f"/subscriptions/{SUBSCRIPTION}/resourceGroups/rg-network-b/providers/Microsoft.Network/virtualNetworks/vnet-b": {
            "location": "eastus",
            "properties": {
                "subnets": [
                    {"name": "subnet-b", "properties": {"addressPrefix": "fd00::/64"}},
                    {"properties": {}},
                ]
            },
        },
    }
    for vnet_id, document in documents.items():
        network["virtualNetwork"] = {
            "id": vnet_id,
            "name": vnet_id.split("/")[-1],
            "location": document["location"],
        }
        request = _evaluate_ui_expression(
            controls["virtualNetworkApi"]["request"]["path"],
            {},
            network=network,
            basics={"infrastructureStage": "foundation"},
            subscription={"subscriptionId": SUBSCRIPTION},
            eager_conditionals=True,
        )
        assert request == f"{vnet_id}?api-version=2024-05-01"
        network["virtualNetworkApi"] = {"id": vnet_id, **document}
        expected = [{"label": controls["peSubnetName"]["defaultValue"], "value": ""}] + [
            {"label": subnet["name"], "value": subnet["name"]}
            for subnet in document["properties"]["subnets"]
            if subnet.get("name")
        ]
        for name in ("peSubnetName", "foundrySubnetName", "containerAppsSubnetName"):
            assert (
                _evaluate_ui_expression(
                    controls[name]["constraints"]["allowedValues"],
                    {},
                    network=network,
                    eager_conditionals=True,
                )
                == expected
            )


@pytest.mark.parametrize(
    "response,visible,message",
    [
        (None, True, "응답 없음"),
        ({"error": {"code": "Forbidden", "message": "private-error-body"}}, True, "Forbidden"),
        ({"id": "another-vnet", "properties": {"subnets": []}}, True, "another-vnet"),
        ({"id": f"/subscriptions/{SUBSCRIPTION}", "properties": {}}, True, "응답 ID"),
        ({"properties": {}}, True, "서브넷 목록이 없어"),
        ({"properties": {"subnets": None}}, True, "서브넷 목록이 없어"),
        ({"properties": {"subnets": []}}, True, "서브넷이 없습니다"),
        ({"properties": {"subnets": [{"name": "selected-subnet"}]}}, False, None),
    ],
)
def test_subnet_discovery_distinguishes_loading_failed_empty_and_ready_reads(
    response: dict[str, Any] | None, visible: bool, message: str | None
) -> None:
    controls = ui_controls("infrastructure")
    vnet_id = f"/subscriptions/{SUBSCRIPTION}/resourceGroups/rg-network/providers/Microsoft.Network/virtualNetworks/selected-vnet"
    document = copy.deepcopy(response)
    if document is not None and "id" not in document:
        document["id"] = vnet_id
    network: dict[str, Any] = {
        "virtualNetworkMode": "existing",
        "virtualNetwork": {"id": vnet_id},
        "virtualNetworkApi": document,
    }
    status = controls["subnetDiscoveryStatus"]
    assert (
        _evaluate_ui_expression(
            status["visible"],
            {"contract": {"resourceGroupApi": None}},
            network=network,
            resource_group={"mode": "New"},
            subscription={"subscriptionId": SUBSCRIPTION},
            basics={"infrastructureStage": "foundation"},
            eager_conditionals=True,
        )
        is visible
    )
    text = _evaluate_ui_expression(
        status["options"]["text"], {}, network=network, eager_conditionals=True
    )
    assert isinstance(text, str) and "private-error-body" not in text
    if message is not None:
        assert message in text
    for stage, mode, selection in (
        ("foundation", "new", {"id": vnet_id}),
        ("complete", "existing", {"id": vnet_id}),
        ("foundation", "existing", None),
    ):
        assert (
            _evaluate_ui_expression(
                status["visible"],
                {"contract": {"resourceGroupApi": None}},
                network={
                    **network,
                    "virtualNetworkMode": mode,
                    "virtualNetwork": selection,
                },
                resource_group={"mode": "New"},
                subscription={"subscriptionId": SUBSCRIPTION},
                basics={"infrastructureStage": stage},
                eager_conditionals=True,
            )
            is False
        )


@pytest.mark.parametrize("failure", ["denied", "stale", "unloaded"])
def test_subnet_discovery_failure_never_authorizes_a_stale_role_selection(
    tmp_path: Path, failure: str
) -> None:
    cli, values = infrastructure_cli(tmp_path)
    vnet_id = f"/subscriptions/{SUBSCRIPTION}/resourceGroups/{values['virtualNetworkResourceGroupName']}/providers/Microsoft.Network/virtualNetworks/{values['virtualNetworkName']}"
    document = copy.deepcopy(cli.documents[vnet_id])
    network = {
        **values,
        "virtualNetwork": {"id": vnet_id, "location": values["location"]},
        "virtualNetworkApi": document,
    }
    data = {
        "contract": {"contractAccepted": True, "resourceGroupApi": None},
        "names": {"namingPrefix": "kt", "namingSuffix": "dev"},
        "options": {},
    }
    guard = ui_definition("infrastructure")["outputs"]["portalValidationPassed"]
    assert (
        _evaluate_ui_expression(
            guard,
            data,
            network=network,
            basics={"infrastructureStage": "foundation"},
            resource_group={"mode": "New"},
            eager_conditionals=True,
        )
        is True
    )
    if failure == "denied":
        document["error"] = {"code": "Forbidden"}
    elif failure == "stale":
        document["id"] += "-previous"
    else:
        network["virtualNetworkApi"] = None
    assert (
        _evaluate_ui_expression(
            guard,
            data,
            network=network,
            basics={"infrastructureStage": "foundation"},
            resource_group={"mode": "New"},
            eager_conditionals=True,
        )
        is False
    )


@pytest.mark.parametrize("network_group", ["rg-customer-network", "RG-Customer-Network"])
def test_infrastructure_exports_vnet_rg_from_selected_arm_id(network_group: str) -> None:
    selected = {
        "id": f"/subscriptions/{SUBSCRIPTION}/resourceGroups/{network_group}/providers/Microsoft.Network/virtualNetworks/vnet-customer",
        "name": "vnet-customer",
        "location": "koreacentral",
    }
    expression = ui_definition("infrastructure")["outputs"]["virtualNetworkResourceGroupName"]
    actual = _evaluate_ui_expression(
        expression,
        {"contract": {"resourceGroupApi": None}},
        network={"virtualNetwork": selected},
        basics={"infrastructureStage": "foundation"},
    )
    assert actual == network_group


def test_completion_reuses_saved_vnet_rg_without_a_picker_value() -> None:
    expression = ui_definition("infrastructure")["outputs"]["virtualNetworkResourceGroupName"]
    actual = _evaluate_ui_expression(
        expression,
        {"contract": {"resourceGroupApi": {"tags": {"azbrief.v2.vnetRg": "rg-approved-network"}}}},
        network={},
        basics={"infrastructureStage": "complete"},
        resource_group={"name": "rg-application", "mode": "Existing"},
        eager_conditionals=True,
    )
    assert actual == "rg-approved-network"


def test_generated_forms_remove_editable_validation_rows_but_require_review_success() -> None:
    for name in ("infrastructure", "application"):
        controls = ui_controls(name)
        assert not any(control_name.endswith("Guard") for control_name in controls)
        assert not any(control.get("defaultValue") == "검사" for control in controls.values())
        assert "portalValidationPassed" in ui_definition(name)["outputs"]
        assert template(name)["parameters"]["portalValidationPassed"]["allowedValues"] == [True]


@pytest.mark.parametrize(("prefix", "suffix"), [("kt", "prd"), ("abcdefghij", "0123456789")])
def test_common_name_inputs_generate_unique_rule_compliant_resource_names(
    prefix: str, suffix: str
) -> None:
    from scripts.build_v2_ui import RESOURCE_NAME_PARTS

    outputs = ui_definition("infrastructure")["outputs"]
    data = {"contract": {"resourceGroupApi": None}, "namingPrefix": prefix, "namingSuffix": suffix}
    names = {
        name: _evaluate_ui_expression(
            outputs[name], data, network={}, basics={"infrastructureStage": "foundation"}
        )
        for name in RESOURCE_NAME_PARTS
    }
    assert names["containerAppName"] == f"ca-{prefix}-{suffix}"
    assert names["storageAccountName"] == f"st{prefix}{suffix}"
    assert names["cosmosAccountName"] == names["searchServiceName"] == ""
    provisioned_names = {name: value for name, value in names.items() if value}
    assert len(set(provisioned_names.values())) == len(provisioned_names)
    assert re.fullmatch(r"[a-z0-9]{3,24}", names["storageAccountName"])
    for name in ("containerAppName", "schedulerJobName", "azureMcpContainerAppName"):
        assert len(names[name]) <= 32
        assert re.fullmatch(r"[a-z][a-z0-9-]+[a-z0-9]", names[name])


def test_common_name_inputs_are_saved_and_reused_on_completion(tmp_path: Path) -> None:
    values = deploy_v2.load_infrastructure_parameters(infrastructure_parameter_file(tmp_path))
    values.update(namingPrefix="kt", namingSuffix="prd")
    saved = deploy_v2.make_tag_contract(values, "foundation", state="foundation")
    assert deploy_v2.decode_tag_options(saved)["namingPrefix"] == "kt"
    assert deploy_v2.decode_tag_options(saved)["namingSuffix"] == "prd"
    data = {"contract": {"resourceGroupApi": {"tags": saved}}, "names": {}}
    for name in ("namingPrefix", "namingSuffix"):
        actual = _evaluate_ui_expression(
            ui_definition("infrastructure")["outputs"][name],
            data,
            basics={"infrastructureStage": "complete"},
            eager_conditionals=True,
        )
        assert actual == values[name]


def test_cli_can_resolve_all_customer_resource_names_from_one_pair(tmp_path: Path) -> None:
    from scripts.build_v2_ui import RESOURCE_NAME_PARTS

    path = infrastructure_parameter_file(tmp_path)
    document = json.loads(path.read_text(encoding="utf-8"))
    for name in RESOURCE_NAME_PARTS:
        document["parameters"].pop(name, None)
    document["parameters"].update(namingPrefix={"value": "kt"}, namingSuffix={"value": "prd"})
    path.write_text(json.dumps(document), encoding="utf-8")
    values = deploy_v2.load_infrastructure_parameters(path)
    assert values["foundryAccountName"] == "ai-kt-prd"
    assert values["storageAccountName"] == "stktprd"
    assert values["azureMcpContainerAppName"] == "ca-kt-mcp-prd"


def test_application_update_retains_keys_auth_environment_and_schedule() -> None:
    cli = FakeV2Azure(installed=True)
    parameters = deploy_v2.prepare_application(cli, IMAGE.replace("a" * 64, "b" * 64))
    assert parameters["reuseExistingApiKey"] is True
    assert "apiKey" not in parameters
    assert parameters["existingSecrets"]["app"] == cli.secret_values[cli.app_id]
    assert parameters["existingSecrets"]["job"] == cli.secret_values[cli.job_id]
    assert parameters["webAuthConfiguration"]["platform"]["enabled"] is True
    assert parameters["jobConfiguration"]["triggerType"] == "Schedule"
    assert (
        parameters["jobConfiguration"]["scheduleTriggerConfig"]["cronExpression"] == "*/5 * * * *"
    )
    assert parameters["tags"]["costCenter"] == "keep"
    assert parameters["appTemplate"]["scale"]["maxReplicas"] == 3
    environment = {entry["name"]: entry for entry in parameters["appEnvironment"]}
    assert environment["CUSTOM_SETTING"]["value"] == "keep"
    assert environment["ADMIN_REQUIRE_AUTH"]["value"] == "true"
    assert not cli.operations


@pytest.mark.parametrize("failure", ["denied", "missing", "different"])
def test_secret_failure_never_rotates_api_keys(failure: str) -> None:
    cli = FakeV2Azure(installed=True)
    if failure == "denied":
        cli.denied.add(cli.app_id)
    elif failure == "missing":
        cli.secret_values[cli.app_id] = []
    else:
        cli.secret_values[cli.job_id][0]["value"] = "different-api-key-not-approved"
    with pytest.raises((ValueError, RuntimeError)):
        deploy_v2.prepare_application(cli, IMAGE)
    assert not cli.operations


@pytest.mark.parametrize("internal", [False, 1, "true", None])
def test_application_requires_typed_private_environment(internal: object) -> None:
    cli = FakeV2Azure()
    cli.documents[cli.environment_id]["properties"]["vnetConfiguration"]["internal"] = internal
    with pytest.raises(ValueError, match="Environment"):
        deploy_v2.prepare_application(cli, IMAGE)
    assert not cli.operations


def test_contract_and_runtime_redirection_fail_before_writes() -> None:
    cli = FakeV2Azure(installed=True)
    with pytest.raises(ValueError, match="redirect"):
        deploy_v2.prepare_application(
            cli, IMAGE, {"appEnvironment": [{"name": "AZURE_SUBSCRIPTION_ID", "value": TENANT}]}
        )
    cli.tags["azbrief.v2.state"] = "pending"
    with pytest.raises(ValueError, match="completed"):
        deploy_v2.prepare_application(cli, IMAGE)
    assert not cli.operations


def test_ui_activation_requires_authentication_and_allow_lists() -> None:
    cli = FakeV2Azure()
    with pytest.raises(ValueError, match="Entra"):
        deploy_v2.prepare_application(
            cli, IMAGE, {"appEnvironment": [{"name": "ADMIN_UI_ENABLED", "value": "true"}]}
        )
    cli = FakeV2Azure(installed=True)
    with pytest.raises(ValueError, match="allow-list"):
        deploy_v2.prepare_application(
            cli, IMAGE, {"appEnvironment": [{"name": "ADMIN_UI_ENABLED", "value": "true"}]}
        )
    parameters = deploy_v2.prepare_application(
        cli,
        IMAGE,
        {
            "appEnvironment": [
                {"name": "ADMIN_UI_ENABLED", "value": "true"},
                {"name": "ADMIN_ALLOWED_PRINCIPALS", "value": TENANT},
            ]
        },
    )
    assert parameters["appTemplate"]["scale"]["minReplicas"] == 1
    assert not cli.operations


def test_auth_and_schedule_downgrades_are_blocked() -> None:
    cli = FakeV2Azure(installed=True)
    with pytest.raises(ValueError, match="downgrade"):
        deploy_v2.prepare_application(
            cli, IMAGE, {"webAuthConfiguration": {"platform": {"enabled": False}}}
        )
    with pytest.raises(ValueError, match="scheduler"):
        deploy_v2.prepare_application(cli, IMAGE, {"jobConfiguration": {"triggerType": "Manual"}})
    with pytest.raises(ValueError, match="authentication"):
        deploy_v2.prepare_application(
            cli, IMAGE, {"appEnvironment": [{"name": "ADMIN_REQUIRE_AUTH", "value": "false"}]}
        )
    assert not cli.operations


def test_initial_install_and_two_updates_only_submit_application_template(
    capsys: pytest.CaptureFixture[str],
) -> None:
    cli = FakeV2Azure()
    deploy_v2.run_application(cli, IMAGE, {}, "deploy", "v2-install", automatic_setup=False)
    assert cli.operations == ["validate", "what-if", "create"]
    assert cli.requests[-1]["parameters"]["reuseExistingApiKey"] is False
    cli.install()
    secret_snapshot = copy.deepcopy(cli.secret_values)
    for digest in ("b", "c"):
        image = IMAGE.replace("a" * 64, digest * 64)
        deploy_v2.run_application(cli, image, {}, "deploy", "v2-update-" + digest)
        submitted = cli.requests[-1]["parameters"]
        assert submitted["containerImage"] == image
        assert submitted["reuseExistingApiKey"] is True
        assert submitted["existingSecrets"]["app"] == secret_snapshot[cli.app_id]
        assert submitted["existingSecrets"]["job"] == secret_snapshot[cli.job_id]
        assert submitted["jobConfiguration"]["triggerType"] == "Schedule"
        assert submitted["webAuthConfiguration"]["platform"]["enabled"] is True
    assert cli.operations == ["validate", "what-if", "create"] * 3
    assert {request["template"] for request in cli.requests} == {"application.json"}
    assert all(not Path(request["temporary_path"]).exists() for request in cli.requests)
    output = capsys.readouterr()
    assert "same-api-key" not in output.out + output.err
    assert "same-registry-token" not in output.out + output.err


@pytest.mark.parametrize("change", ["schema", "unknown", "value-length", "tag-count", "key-case"])
def test_invalid_tag_contracts_are_rejected(change: str) -> None:
    tags = tag_contract()
    if change == "schema":
        tags["azbrief.v2.schema"] = "1"
    elif change == "unknown":
        tags["azbrief.v2.password"] = "never-store-secrets"
    elif change == "value-length":
        tags["azbrief.v2.app"] = "a" * 257
    elif change == "tag-count":
        tags.update({f"other-{index}": "keep" for index in range(22)})
    else:
        tags["AZBRIEF.V2.SCHEMA"] = tags.pop("azbrief.v2.schema")
    with pytest.raises(ValueError):
        deploy_v2.parse_tag_contract(tags)


def infrastructure_parameter_file(tmp_path: Path) -> Path:
    values = deploy_v2.infrastructure_values(FakeV2Azure(), tag_contract())
    schema = template("infrastructure")["parameters"]
    supplied = {
        name: {"value": values[name]}
        for name, definition in schema.items()
        if (
            "defaultValue" not in definition
            or name in {"peSubnetName", "foundrySubnetName", "containerAppsSubnetName"}
        )
        and name not in deploy_v2.INTERNAL_PARAMETERS
    }
    path = tmp_path / "infrastructure.parameters.json"
    path.write_text(json.dumps({"parameters": supplied}), encoding="utf-8")
    return path


def test_infrastructure_parameter_defaults_are_resolved_not_literal_arm(tmp_path: Path) -> None:
    values = deploy_v2.load_infrastructure_parameters(infrastructure_parameter_file(tmp_path))
    assert values["schedulerJobName"] == "job-" + values["containerAppName"]
    assert values["controlPlaneIdentityName"] == "id-" + values["containerAppName"]
    assert values["tags"]["deploymentProfile"] == deploy_v2.PROFILE
    assert not any(isinstance(value, str) and value.startswith("[") for value in values.values())
    assert set(values).isdisjoint(deploy_v2.INTERNAL_PARAMETERS)


def test_completed_infrastructure_cannot_be_redeployed(tmp_path: Path) -> None:
    cli = FakeV2Azure()
    values = deploy_v2.load_infrastructure_parameters(infrastructure_parameter_file(tmp_path))
    with pytest.raises(ValueError, match="application deployment"):
        deploy_v2.prepare_infrastructure(cli, values, "foundation", "v2")
    assert not cli.operations


def infrastructure_cli(tmp_path: Path) -> tuple[FakeV2Azure, dict[str, Any]]:
    cli = FakeV2Azure()
    cli.tags = {"costCenter": "keep"}
    values = deploy_v2.load_infrastructure_parameters(infrastructure_parameter_file(tmp_path))
    vnet_id = f"/subscriptions/{SUBSCRIPTION}/resourceGroups/{values['virtualNetworkResourceGroupName']}/providers/Microsoft.Network/virtualNetworks/{values['virtualNetworkName']}"
    cli.documents[vnet_id] = {
        "id": vnet_id,
        "location": values["location"],
        "properties": {
            "provisioningState": "Succeeded",
            "addressSpace": {"addressPrefixes": ["10.60.0.0/16"]},
            "subnets": [
                {
                    "name": values["peSubnetName"],
                    "properties": {"addressPrefix": "10.60.2.0/28", "delegations": []},
                },
                {
                    "name": values["foundrySubnetName"],
                    "properties": {
                        "addressPrefix": "10.60.0.0/24",
                        "delegations": [
                            {"properties": {"serviceName": "Microsoft.App/environments"}}
                        ],
                    },
                },
                {
                    "name": values["containerAppsSubnetName"],
                    "properties": {
                        "addressPrefix": "10.60.1.0/27",
                        "delegations": [
                            {"properties": {"serviceName": "Microsoft.App/environments"}}
                        ],
                    },
                },
            ],
        },
    }
    return cli, values


def test_infrastructure_preflight_uses_existing_network_and_no_application(tmp_path: Path) -> None:
    cli, values = infrastructure_cli(tmp_path)
    prepared = deploy_v2.prepare_infrastructure(cli, values, "foundation", "v2")
    assert prepared["infrastructureStage"] == "foundation"
    assert prepared["existingPrivateDnsZoneIds"] == {}
    assert prepared["containerAppName"] == values["containerAppName"]
    assert not any(key.startswith("create") for key in prepared)
    assert not cli.operations


def test_new_vnet_preflight_uses_plan_without_reading_missing_target(tmp_path: Path) -> None:
    cli, values = infrastructure_cli(tmp_path)
    values.update(virtualNetworkMode="new", virtualNetworkResourceGroupName=cli.group)
    vnet_id = (
        f"{cli.group_id}/providers/Microsoft.Network/virtualNetworks/{values['virtualNetworkName']}"
    )
    cli.collections[f"{cli.group_id}/providers/Microsoft.Network/virtualNetworks"] = []
    cli.denied.add(vnet_id)
    prepared = deploy_v2.prepare_infrastructure(cli, values, "foundation", "new-test")
    assert prepared["virtualNetworkMode"] == "new"
    assert prepared["virtualNetworkResourceGroupName"] == cli.group
    assert not cli.operations
    saved = deploy_v2.make_tag_contract(values, "new-foundation", "foundation")
    options = deploy_v2.decode_tag_options(saved)
    assert options["virtualNetworkMode"] == "new"
    assert options["virtualNetworkAddressPrefix"] == "10.240.0.0/16"


def test_new_vnet_preflight_never_overwrites_existing_or_foreign_group(tmp_path: Path) -> None:
    cli, values = infrastructure_cli(tmp_path)
    values["virtualNetworkMode"] = "new"
    with pytest.raises(ValueError, match="explicit target"):
        deploy_v2.prepare_infrastructure(cli, values, "foundation", "new-test")
    values["virtualNetworkResourceGroupName"] = cli.group
    vnet_id = (
        f"{cli.group_id}/providers/Microsoft.Network/virtualNetworks/{values['virtualNetworkName']}"
    )
    cli.collections[f"{cli.group_id}/providers/Microsoft.Network/virtualNetworks"] = [
        {"id": vnet_id}
    ]
    with pytest.raises(ValueError, match="overwrite an existing VNet"):
        deploy_v2.prepare_infrastructure(cli, values, "foundation", "new-test")
    assert not cli.operations


@pytest.mark.parametrize("change", ["virtualNetworkMode", "virtualNetworkAddressPrefix"])
def test_saved_new_vnet_mode_and_prefixes_are_immutable(tmp_path: Path, change: str) -> None:
    cli, values = infrastructure_cli(tmp_path)
    values.update(virtualNetworkMode="new", virtualNetworkResourceGroupName=cli.group)
    cli.tags = deploy_v2.make_tag_contract(values, "new-foundation", "foundation")
    values[change] = "existing" if change == "virtualNetworkMode" else "10.241.0.0/16"
    with pytest.raises(ValueError, match="immutable"):
        deploy_v2.prepare_infrastructure(cli, values, "complete", "must-not-switch")
    assert not cli.operations


def test_portal_new_vnet_outputs_are_target_group_and_safe_nonoverlapping_ranges() -> None:
    outputs = ui_definition("infrastructure")["outputs"]
    network = {
        "virtualNetworkMode": "new",
        "newVirtualNetworkName": "vnet-new-test",
        "newNetworkRange": "172.28",
        "newPeSubnetName": "pe",
        "newFoundrySubnetName": "agents",
        "newContainerAppsSubnetName": "apps",
    }
    data = {"contract": {"resourceGroupApi": None}}
    expected = {
        "virtualNetworkMode": "new",
        "virtualNetworkResourceGroupName": "rg-new-test",
        "virtualNetworkName": "vnet-new-test",
        "virtualNetworkAddressPrefix": "172.28.0.0/16",
        "foundrySubnetAddressPrefix": "172.28.0.0/24",
        "containerAppsSubnetAddressPrefix": "172.28.1.0/27",
        "peSubnetAddressPrefix": "172.28.1.32/28",
        "peSubnetName": "pe",
        "foundrySubnetName": "agents",
        "containerAppsSubnetName": "apps",
    }
    for field, value in expected.items():
        assert (
            _evaluate_ui_expression(
                outputs[field],
                data,
                network=network,
                basics={"infrastructureStage": "foundation"},
                resource_group={"name": "rg-new-test", "mode": "New"},
                eager_conditionals=True,
            )
            == value
        )


def test_infrastructure_rejects_tag_overflow_and_resource_collision_before_write(
    tmp_path: Path,
) -> None:
    cli, values = infrastructure_cli(tmp_path)
    cli.tags = {f"existing-{index}": "keep" for index in range(22)}
    with pytest.raises(ValueError, match="50-tag"):
        deploy_v2.prepare_infrastructure(cli, values, "foundation", "v2")
    cli.tags = {}
    resource_id = (
        f"{cli.group_id}/providers/Microsoft.Storage/storageAccounts/{values['storageAccountName']}"
    )
    cli.inventory = [cli.resource(resource_id, {})]
    with pytest.raises(ValueError, match="targets already exist"):
        deploy_v2.prepare_infrastructure(cli, values, "foundation", "v2")
    assert not cli.operations


def test_infrastructure_stage_order_and_preview_gates(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str]] = []
    cli = FakeV2Azure()
    values = {"foundryAccountName": "test-foundry", "projectName": "test-project"}

    def prepare(
        cli: AzureCli,
        values: dict[str, Any],
        stage: str,
        name: str,
        *,
        allow_model_reselection: bool = False,
    ) -> dict[str, Any]:
        assert allow_model_reselection is False
        calls.append(("preflight", stage))
        return {"infrastructureStage": stage}

    def deploy(
        cli: AzureCli, path: Path, parameters: dict[str, Any], mode: str, name: str
    ) -> dict[str, Any]:
        assert path.name == "infrastructure.json"
        calls.append((mode, parameters["infrastructureStage"]))
        return {"properties": {"provisioningState": "Succeeded"}}

    def wait(cli: AzureCli, scope: str, expected_name: str | None = None) -> None:
        calls.append(("wait", "project" if expected_name else "account"))

    monkeypatch.setattr(deploy_v2, "prepare_infrastructure", prepare)
    monkeypatch.setattr(deploy_v2, "deploy_template", deploy)
    monkeypatch.setattr(deploy_v2, "wait_for_host", wait)
    monkeypatch.setattr(deploy_v2, "read_infrastructure", lambda cli: ({}, {}))
    deploy_v2.run_infrastructure(cli, values, "deploy", "v2")
    assert calls == [
        ("preflight", "foundation"),
        ("validate", "foundation"),
        ("preflight", "foundation"),
        ("what-if", "foundation"),
        ("preflight", "foundation"),
        ("create", "foundation"),
        ("wait", "account"),
        ("preflight", "complete"),
        ("validate", "complete"),
        ("preflight", "complete"),
        ("what-if", "complete"),
        ("preflight", "complete"),
        ("create", "complete"),
        ("wait", "project"),
    ]


def test_existing_unlinked_dns_zone_is_never_overwritten(tmp_path: Path) -> None:
    cli, values = infrastructure_cli(tmp_path)
    zone_id = f"{cli.group_id}/providers/Microsoft.Network/privateDnsZones/privatelink.blob.core.windows.net"
    cli.dns_zones = [{"id": zone_id, "name": "privatelink.blob.core.windows.net"}]
    cli.collections[f"{zone_id}/virtualNetworkLinks"] = []
    with pytest.raises(ValueError, match="never overwrite"):
        deploy_v2.prepare_infrastructure(cli, values, "foundation", "v2")
    assert not cli.operations


@pytest.mark.parametrize(
    "entry",
    [
        {"name": "SERVICE_TOKEN", "value": "never-inline-this-token"},
        {"name": "SERVICE_TOKEN", "secretRef": "missing-secret"},
    ],
)
def test_credentials_require_known_secret_references(entry: dict[str, str]) -> None:
    cli = FakeV2Azure(installed=True)
    with pytest.raises(ValueError, match="secretRef"):
        deploy_v2.prepare_application(cli, IMAGE, {"appEnvironment": [entry]})
    assert not cli.operations


def test_linked_canonical_dns_is_reused_without_touching_other_same_name_zone(
    tmp_path: Path,
) -> None:
    cli, values = infrastructure_cli(tmp_path)
    name = "privatelink.blob.core.windows.net"
    unlinked_id = f"{cli.group_id}/providers/Microsoft.Network/privateDnsZones/{name}"
    canonical_id = f"/subscriptions/{SUBSCRIPTION}/resourceGroups/rg-central-dns/providers/Microsoft.Network/privateDnsZones/{name}"
    vnet_id = f"/subscriptions/{SUBSCRIPTION}/resourceGroups/{values['virtualNetworkResourceGroupName']}/providers/Microsoft.Network/virtualNetworks/{values['virtualNetworkName']}"
    cli.dns_zones = [{"id": unlinked_id, "name": name}, {"id": canonical_id, "name": name}]
    cli.collections[f"{unlinked_id}/virtualNetworkLinks"] = []
    cli.collections[f"{canonical_id}/virtualNetworkLinks"] = [
        {
            "properties": {
                "virtualNetwork": {"id": vnet_id},
                "registrationEnabled": False,
                "provisioningState": "Succeeded",
            }
        }
    ]
    prepared = deploy_v2.prepare_infrastructure(cli, values, "foundation", "v2")
    assert prepared["existingPrivateDnsZoneIds"] == {name: canonical_id}
    assert not cli.operations
