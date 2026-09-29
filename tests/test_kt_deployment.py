"""Offline contracts for the KT private infrastructure and existing-network preflight."""

import copy
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from scripts import deploy_kt as kt

SUBSCRIPTION = "11111111-1111-1111-1111-111111111111"
TENANT = "22222222-2222-2222-2222-222222222222"
GROUP = f"/subscriptions/{SUBSCRIPTION}/resourceGroups/rg-kt-test"
VNET = f"/subscriptions/{SUBSCRIPTION}/resourceGroups/rg-network/providers/Microsoft.Network/virtualNetworks/vnet-kt"


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
    for module, flag, name in (
        ("peSubnet", "createPESubnet", "PESubnet"),
        ("foundrySubnet", "createFoundrySubnet", "FoundrySubnet"),
        ("containerAppsSubnet", "createContainerAppsSubnet", "ContainerAppsSubnet"),
    ):
        definition = template["resources"][module]
        assert definition["condition"] == f"[parameters('{flag}')]"
        assert template["parameters"][flag]["defaultValue"] is False
        assert _params(template, module)["name"] == name
        assert definition["resourceGroup"] == "[parameters('virtualNetworkResourceGroupName')]"
        if module != "peSubnet":
            assert _params(template, module)["delegation"] == "Microsoft.App/environments"
    assert "peSubnet" in template["resources"]["foundrySubnet"]["dependsOn"]
    assert "foundrySubnet" in template["resources"]["containerAppsSubnet"]["dependsOn"]


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
    assert all(plan.create for plan in plans)
    assert vnet == original


def test_existing_subnets_preserve_nsg_routes_policies_and_larger_sizes(values: dict, vnet: dict):
    vnet["properties"]["subnets"] = [
        _subnet("PESubnet", "10.70.1.0/24"),
        _subnet("FoundrySubnet", "10.70.2.0/24", True),
        _subnet("ContainerAppsSubnet", "10.70.3.0/24", True),
    ]
    for _, parameter, _, _ in kt.SUBNETS:
        values[parameter] = ""
    original = copy.deepcopy(vnet)
    plans = kt.plan_subnets(values, vnet)
    assert not any(plan.create for plan in plans)
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
    vnet["properties"]["subnets"] = [_subnet("FoundrySubnet", "10.70.0.32/27")]
    with pytest.raises(ValueError, match="incompatible delegation"):
        kt.plan_subnets(values, vnet)
    vnet["properties"]["subnets"][0] = _subnet("FoundrySubnet", "10.70.0.128/27", True)
    with pytest.raises(ValueError, match="will not be resized"):
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
                            {"scenario": "agent", "subnetArmId": f"{VNET}/subnets/FoundrySubnet"}
                        ],
                        "vnetConfiguration": {
                            "infrastructureSubnetId": f"{VNET}/subnets/ContainerAppsSubnet"
                        },
                    }
                }
            if resource_id.endswith(f"/privateEndpoints/{spec['name']}"):
                return {
                    "properties": {
                        "subnet": {"id": f"{VNET}/subnets/PESubnet"},
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
            for subnet_name, prefix, create_flag, _ in kt.SUBNETS:
                if values[create_flag]:
                    self.vnet["properties"]["subnets"].append(
                        _subnet(subnet_name, values[prefix], subnet_name != "PESubnet")
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
    assert all(cli.deployments[0][row[2]] for row in kt.SUBNETS)
    assert not any(cli.deployments[1][row[2]] for row in kt.SUBNETS)
    assert len(cli.vnet["properties"]["subnets"]) == 3
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
    busy = _subnet("FoundrySubnet", values["foundrySubnetAddressPrefix"], True)
    busy["properties"]["serviceAssociationLinks"] = [{"name": "other-account"}]
    cli.vnet["properties"]["subnets"].append(busy)
    with pytest.raises(ValueError, match="occupied"):
        kt.run(cli, values, "deploy")
    assert not cli.deployments


def test_existing_pe_subnet_requires_enough_actual_free_ips(values: dict, vnet: dict):
    cli = FakeAzure(values, vnet)
    cli.vnet["properties"]["subnets"].append(_subnet("PESubnet", values["peSubnetAddressPrefix"]))
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
