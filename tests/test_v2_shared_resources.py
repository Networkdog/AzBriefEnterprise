"""Cross-RG resource ownership and routing contracts for v2 deployments."""

import copy
import json
from itertools import product
from pathlib import Path
from typing import Any

import pytest

from scripts import deploy_v2
from scripts.deploy_kt import validate_existing_host
from src.admin.readiness import AdminReadinessCollector
from tests.test_admin_readiness import _settings
from tests.test_deployment_v2 import (
    IMAGE,
    SUBSCRIPTION,
    FakeV2Azure,
    infrastructure_cli,
    module,
    resources,
    template,
)


def _id(group: str, resource_type: str, name: str) -> str:
    return f"/subscriptions/{SUBSCRIPTION}/resourceGroups/{group}/providers/{resource_type}/{name}"


def shared_fixture(
    tmp_path: Path, modes: tuple[str, ...] = tuple(deploy_v2.SHARED_RESOURCES)
) -> tuple[FakeV2Azure, dict[str, Any]]:
    cli, values = infrastructure_cli(tmp_path)
    vnet_id = _id(
        values["virtualNetworkResourceGroupName"],
        "Microsoft.Network/virtualNetworks",
        values["virtualNetworkName"],
    )

    def document(resource_id: str, **properties: Any) -> dict[str, Any]:
        result = cli.resource(resource_id, {"provisioningState": "Succeeded", **properties})
        result["tags"] = {"platformOwner": "preserve"}
        return result

    def private_endpoint(target: dict[str, Any], group: str) -> None:
        endpoint_id = _id("rg-shared-network", "Microsoft.Network/privateEndpoints", "pe-" + group)
        target["properties"]["privateEndpointConnections"] = [
            {
                "properties": {
                    "privateEndpoint": {"id": endpoint_id},
                    "privateLinkServiceConnectionState": {"status": "Approved"},
                }
            }
        ]
        cli.documents[endpoint_id] = document(
            endpoint_id,
            subnet={"id": f"{vnet_id}/subnets/{values['peSubnetName']}"},
            privateLinkServiceConnections=[
                {"properties": {"privateLinkServiceId": target["id"], "groupIds": [group]}}
            ],
        )

    def zone(name: str) -> str:
        zone_id = _id("rg-shared-dns", "Microsoft.Network/privateDnsZones", name)
        cli.dns_zones.append({"id": zone_id, "name": name})
        cli.documents[zone_id] = document(zone_id)
        cli.collections[f"{zone_id}/virtualNetworkLinks"] = [
            {
                "properties": {
                    "virtualNetwork": {"id": vnet_id},
                    "registrationEnabled": False,
                    "provisioningState": "Succeeded",
                }
            }
        ]
        return zone_id

    targets = {
        "foundryAccountMode": ("rg-shared-ai", "shared-foundry"),
        "storageAccountMode": ("rg-shared-data", "stsharedv2"),
        "containerAppsEnvironmentMode": ("rg-shared-compute", "shared-environment"),
        "communicationServicesMode": ("rg-shared-email", "shared-acs"),
    }
    for mode in modes:
        name, existing, _, resource_type, _ = deploy_v2.SHARED_RESOURCES[mode]
        group, leaf = targets[mode]
        resource_id = _id(group, resource_type, leaf)
        values.update({mode: "existing", name: leaf, existing: resource_id})
        cli.documents[resource_id] = document(resource_id, publicNetworkAccess="Disabled")
    if "foundryAccountMode" in modes:
        account = cli.documents[values["existingFoundryAccountResourceId"]]
        account.update(kind="AIServices", location=values["location"])
        account["properties"].update(
            allowProjectManagement=True,
            disableLocalAuth=True,
            customSubDomainName="shared-foundry-endpoint",
            networkInjections=[
                {
                    "scenario": "agent",
                    "useMicrosoftManagedNetwork": False,
                    "subnetArmId": f"{vnet_id}/subnets/{values['foundrySubnetName']}",
                }
            ],
        )
        private_endpoint(account, "account")
        cli.collections[f"{account['id']}/capabilityHosts"] = [
            {"properties": {"provisioningState": "Succeeded"}}
        ]
        cli.collections[f"{account['id']}/projects"] = [{"name": "unrelated-customer-project"}]
        cli.documents[vnet_id]["properties"]["subnets"][1]["properties"][
            "serviceAssociationLinks"
        ] = [{"id": "retained"}]
    if "storageAccountMode" in modes:
        storage = cli.documents[values["existingStorageAccountResourceId"]]
        storage["kind"] = "StorageV2"
        storage["properties"].update(
            allowSharedKeyAccess=False,
            allowBlobPublicAccess=False,
            supportsHttpsTrafficOnly=True,
            minimumTlsVersion="TLS1_2",
        )
        private_endpoint(storage, "blob")
        cli.collections[f"{storage['id']}/blobServices/default/containers"] = []
        values.update(
            stateContainerName="azbrief-state-" + values["containerAppName"].lower(),
            archiveContainerName="azbrief-archive-" + values["containerAppName"].lower(),
        )
    if "foundryAccountMode" in modes or "storageAccountMode" in modes:
        for name in (
            "privatelink.cognitiveservices.azure.com",
            "privatelink.openai.azure.com",
            "privatelink.services.ai.azure.com",
            "privatelink.blob.core.windows.net",
        ):
            zone(name)
    if "containerAppsEnvironmentMode" in modes:
        environment = cli.documents[values["existingContainerAppsEnvironmentResourceId"]]
        environment["location"] = values["location"]
        customer_id = "00000000-0000-0000-0000-000000000090"
        environment["properties"].update(
            defaultDomain="shared.eastus.azurecontainerapps.io",
            staticIp="10.60.1.5",
            vnetConfiguration={
                "internal": True,
                "infrastructureSubnetId": f"{vnet_id}/subnets/{values['containerAppsSubnetName']}",
            },
            workloadProfiles=[{"name": "Consumption", "workloadProfileType": "Consumption"}],
            appLogsConfiguration={
                "destination": "log-analytics",
                "logAnalyticsConfiguration": {"customerId": customer_id},
            },
        )
        workspace_id = _id(
            "rg-shared-monitoring", "Microsoft.OperationalInsights/workspaces", "shared-workspace"
        )
        cli.documents[workspace_id] = document(workspace_id, customerId=customer_id)
        cli.collections[
            f"/subscriptions/{SUBSCRIPTION}/providers/Microsoft.OperationalInsights/workspaces"
        ] = [cli.documents[workspace_id]]
        domain_id = zone(environment["properties"]["defaultDomain"])
        for name in ("*", "@"):
            cli.documents[f"{domain_id}/A/{name}"] = {
                "properties": {"aRecords": [{"ipv4Address": "10.60.1.5"}]}
            }
        cli.documents[vnet_id]["properties"]["subnets"][2]["properties"][
            "serviceAssociationLinks"
        ] = [{"id": "retained"}]
    if "communicationServicesMode" in modes:
        domain_id = (
            _id("rg-shared-domains", "Microsoft.Communication/emailServices", "shared-email")
            + "/domains/AzureManagedDomain"
        )
        communication = cli.documents[values["existingCommunicationServicesResourceId"]]
        communication["properties"].update(
            hostName="shared.communication.azure.com", linkedDomains=[domain_id]
        )
        cli.documents[domain_id] = document(
            domain_id, domainManagement="AzureManaged", fromSenderDomain="shared.azurecomm.net"
        )
    return cli, values


@pytest.mark.parametrize("enabled", list(product((False, True), repeat=4)))
def test_all_sixteen_new_existing_combinations_resolve_exact_ids(
    tmp_path: Path, enabled: tuple[bool, ...]
) -> None:
    modes = tuple(mode for mode, selected in zip(deploy_v2.SHARED_RESOURCES, enabled) if selected)
    cli, values = shared_fixture(tmp_path, modes)
    before = copy.deepcopy(cli.documents)
    prepared = deploy_v2.prepare_infrastructure(cli, values, "foundation", "shared")
    assert set(prepared) <= set(template("infrastructure")["parameters"])
    bindings = deploy_v2.resource_bindings(cli, prepared)
    saved = deploy_v2.make_tag_contract({**prepared, **bindings}, "shared-foundation", "foundation")
    decoded = deploy_v2.infrastructure_values(cli, saved)
    assert deploy_v2.decode_resource_bindings(saved) == bindings
    for mode, (name, existing, field, _, _) in deploy_v2.SHARED_RESOURCES.items():
        assert decoded[mode] == prepared[mode]
        assert decoded[name] == prepared[name]
        assert decoded[field] == bindings[field]
        if mode in modes:
            assert decoded[existing] == values[existing]
            assert f"/resourceGroups/{cli.group}/" not in decoded[field]
    assert all(len(value) <= 256 for value in saved.values())
    assert len(saved) <= 49
    assert cli.documents == before
    assert not cli.operations


@pytest.mark.parametrize("mode", list(deploy_v2.SHARED_RESOURCES))
def test_shared_resource_ids_reject_wrong_subscription_type_and_redirect(
    tmp_path: Path, mode: str
) -> None:
    cli, values = shared_fixture(tmp_path, (mode,))
    _, existing, _, _, _ = deploy_v2.SHARED_RESOURCES[mode]
    original = values[existing]
    for bad in (
        original.replace(SUBSCRIPTION, "00000000-0000-0000-0000-000000000099"),
        original + "?api-version=2025-01-01",
        original.replace("/providers/", "/providers/Unexpected/"),
    ):
        with pytest.raises(ValueError):
            deploy_v2.prepare_infrastructure(cli, {**values, existing: bad}, "foundation", "bad")
    cli.documents[original]["id"] = original.replace("rg-shared-", "rg-redirected-")
    with pytest.raises(ValueError, match="redirected"):
        deploy_v2.prepare_infrastructure(cli, values, "foundation", "bad")
    assert not cli.operations


@pytest.mark.parametrize("mode", ["foundryAccountMode", "containerAppsEnvironmentMode"])
def test_shared_network_owners_keep_their_exact_vnet_region_and_private_configuration(
    tmp_path: Path, mode: str
) -> None:
    cli, values = shared_fixture(tmp_path, (mode,))
    _, field, _, _, _ = deploy_v2.SHARED_RESOURCES[mode]
    selected = cli.documents[values[field]]
    selected["location"] = "koreacentral"
    with pytest.raises(ValueError, match="same-region"):
        deploy_v2.prepare_infrastructure(cli, values, "foundation", "bad-region")
    selected["location"] = values["location"]
    selected["properties"]["publicNetworkAccess"] = "Enabled"
    with pytest.raises(ValueError):
        deploy_v2.prepare_infrastructure(cli, values, "foundation", "public")
    selected["properties"]["publicNetworkAccess"] = "Disabled"
    with pytest.raises(ValueError, match="existing VNet"):
        deploy_v2.prepare_infrastructure(
            cli, {**values, "virtualNetworkMode": "new"}, "foundation", "moved"
        )


def test_shared_environment_reuses_only_its_actual_workspace_and_internal_dns(
    tmp_path: Path,
) -> None:
    cli, values = shared_fixture(tmp_path, ("containerAppsEnvironmentMode",))
    prepared = deploy_v2.prepare_infrastructure(cli, values, "foundation", "shared")
    workspace_id = prepared["existingLogAnalyticsWorkspaceResourceId"]
    assert "/resourceGroups/rg-shared-monitoring/" in workspace_id
    assert prepared["logAnalyticsWorkspaceName"] == "shared-workspace"
    cli.documents[workspace_id]["properties"]["customerId"] = "different-workspace"
    with pytest.raises(ValueError, match="workspace"):
        deploy_v2.prepare_infrastructure(cli, prepared, "foundation", "bad")
    cli.documents[workspace_id]["properties"]["customerId"] = "00000000-0000-0000-0000-000000000090"
    zone_id = next(
        row["id"] for row in cli.dns_zones if row["name"] == "shared.eastus.azurecontainerapps.io"
    )
    cli.documents[f"{zone_id}/A/*"]["properties"]["aRecords"] = [{"ipv4Address": "10.60.1.6"}]
    with pytest.raises(ValueError, match="VIP"):
        deploy_v2.prepare_infrastructure(cli, prepared, "foundation", "bad")
    assert not cli.operations


def test_shared_storage_never_adopts_fixed_or_existing_customer_containers(tmp_path: Path) -> None:
    cli, values = shared_fixture(tmp_path, ("storageAccountMode",))
    resource_id = values["existingStorageAccountResourceId"]
    with pytest.raises(ValueError, match="workload-specific"):
        deploy_v2.prepare_infrastructure(
            cli, {**values, "stateContainerName": "azbrief-state"}, "foundation", "bad"
        )
    cli.collections[f"{resource_id}/blobServices/default/containers"] = [
        {"name": values["stateContainerName"], "properties": {"publicAccess": "None"}}
    ]
    with pytest.raises(ValueError, match="adopt"):
        deploy_v2.prepare_infrastructure(cli, values, "foundation", "bad")
    assert not cli.operations


def test_existing_acs_uses_a_linked_domain_without_rewriting_links_or_keys(tmp_path: Path) -> None:
    cli, values = shared_fixture(tmp_path, ("communicationServicesMode",))
    prepared = deploy_v2.prepare_infrastructure(cli, values, "foundation", "shared")
    assert "/resourceGroups/rg-shared-domains/" in prepared["existingEmailDomainResourceId"]
    assert prepared["emailServiceName"] == "shared-email"
    wrong = prepared["existingEmailDomainResourceId"].replace("shared-email", "another-email")
    with pytest.raises(ValueError, match="already linked"):
        deploy_v2.prepare_infrastructure(
            cli, {**prepared, "existingEmailDomainResourceId": wrong}, "foundation", "bad"
        )
    assert not cli.operations


def test_existing_foundry_allows_other_projects_but_refuses_another_workloads_project(
    tmp_path: Path,
) -> None:
    cli, values = shared_fixture(tmp_path, ("foundryAccountMode",))
    account_id = values["existingFoundryAccountResourceId"]
    assert validate_existing_host(cli, account_id, values) == (False, False)
    project_id = f"{account_id}/projects/{values['projectName']}"
    cli.collections[f"{account_id}/projects"].append({"name": values["projectName"]})
    cli.documents[project_id] = cli.resource(project_id, {"provisioningState": "Succeeded"})
    with pytest.raises(ValueError, match="another workload"):
        deploy_v2._owned_project(cli, values, account_id)
    cli.documents[project_id]["tags"]["azbriefWorkloadResourceGroup"] = cli.group_id
    deploy_v2._owned_project(cli, values, account_id)


def test_resource_handoff_rejects_truncated_chunks_and_redirected_names(tmp_path: Path) -> None:
    cli, values = shared_fixture(tmp_path)
    prepared = deploy_v2.prepare_infrastructure(cli, values, "foundation", "shared")
    bindings = deploy_v2.resource_bindings(cli, prepared)
    tags = deploy_v2.make_tag_contract({**prepared, **bindings}, "shared", "foundation")
    incomplete = {
        key: value for key, value in tags.items() if key != deploy_v2.RESOURCE_TAG_KEYS[1]
    }
    with pytest.raises(ValueError, match="chunks"):
        deploy_v2.parse_tag_contract(incomplete, require_ready=False)
    tags["azbrief.v2.foundry"] = "different-foundry"
    with pytest.raises(ValueError, match="saved name"):
        deploy_v2.infrastructure_values(cli, tags)
    cli.tags.update({f"customer-{index}": "keep" for index in range(50 - len(cli.tags))})
    with pytest.raises(ValueError, match="50-tag"):
        deploy_v2.prepare_infrastructure(cli, values, "foundation", "overflow")
    assert not cli.operations


def test_compiled_shared_modes_never_put_parent_accounts_environments_or_diagnostics() -> None:
    infrastructure = template("infrastructure")
    foundation = module(infrastructure, "v2-foundation")["properties"]["template"]
    for name, parameter in (
        ("v2-foundry", "foundryAccountMode"),
        ("v2-storage", "storageAccountMode"),
        ("v2-container-environment", "containerAppsEnvironmentMode"),
        ("v2-email-services", "communicationServicesMode"),
        ("v2-internal-ingress-dns", "containerAppsEnvironmentMode"),
    ):
        assert (
            module(foundation, name)["condition"] == f"[equals(parameters('{parameter}'), 'new')]"
        )
    for row in resources(foundation):
        if (
            row["type"] == "Microsoft.Insights/diagnosticSettings"
            and row.get("name") == "azbrief-v2"
        ):
            if "existingAccount" in str(row.get("scope")):
                assert "foundryAccountMode" in row["condition"]
    for name, resource_id in (
        ("v2-required-models", "accountId"),
        ("v2-foundry-workload", "accountId"),
        ("v2-shared-storage-workload", "storageId"),
        ("v2-shared-environment-reader", "environmentId"),
        ("v2-shared-communication-access", "communicationId"),
    ):
        child = module(infrastructure, name)
        assert resource_id in child["resourceGroup"]
    workload = module(infrastructure, "v2-shared-storage-workload")["properties"]["template"]
    writes = [
        row for row in resources(workload) if row["type"] != "Microsoft.Resources/deployments"
    ]
    assert {row["type"] for row in writes} == {
        "Microsoft.Storage/storageAccounts/blobServices/containers",
        "Microsoft.Authorization/roleAssignments",
    }
    role = next(row for row in writes if row["type"] == "Microsoft.Authorization/roleAssignments")
    assert "containers" in str(role.get("_scope_resource_type", role["scope"]))
    assert "conditionVersion" not in role["properties"]
    assert "Microsoft.Storage/storageAccounts'" not in str(role["scope"])
    models = [
        row
        for row in resources(module(infrastructure, "v2-required-models")["properties"]["template"])
        if row["type"] == "Microsoft.CognitiveServices/accounts/deployments"
    ]
    assert models and all(row["sku"]["name"] == "GlobalStandard" for row in models)


def test_application_mcp_and_hosted_roles_use_the_saved_external_resource_ids() -> None:
    application = template("application")
    body = json.dumps(application)
    for field in (
        "foundryAccountResourceId",
        "containerAppsEnvironmentResourceId",
        "storageAccountResourceId",
        "communicationServicesResourceId",
        "emailDomainResourceId",
    ):
        assert field in body
    mcp = module(application, "v2-mcp-server-application")
    assert (
        "containerAppsEnvironmentResourceId"
        in mcp["properties"]["parameters"]["containerAppEnvironmentId"]["value"]
    )
    authentication = template("mcp-authentication")
    permission = module(authentication, "v2-mcp-project-permission")
    assert (
        "foundryProjectResourceId"
        in permission["properties"]["parameters"]["foundryProjectResourceId"]["value"]
    )
    connection = module(authentication, "v2-mcp-project-connection")
    assert "accountId" in connection["resourceGroup"]
    hosted = module(template("runtime-roles"), "v2-hosted-project-user")
    assert "accountId" in hosted["resourceGroup"]
    assert "53ca6127-db72-4b80-b1b0-d745d6d5456d" in json.dumps(hosted["properties"]["template"])


def test_shared_scope_deployment_names_are_isolated_per_workload_or_project() -> None:
    infrastructure = template("infrastructure")
    for name in (
        "v2-required-models",
        "v2-foundry-workload",
        "v2-shared-storage-workload",
        "v2-shared-storage-reader",
        "v2-shared-environment-reader",
        "v2-shared-communication-access",
    ):
        assert "workloadKey" in module(infrastructure, name)["name"]
    project = module(infrastructure, "v2-foundry-workload")["properties"]["template"]
    for name in ("v2-managed-project-host", "v2-shared-foundry-access"):
        assert "projectKey" in module(project, name)["name"]
    connection = module(template("mcp-authentication"), "v2-mcp-project-connection")
    assert "uniqueString" in connection["name"]
    assert "accountId" in connection["name"]


def test_shared_email_role_definition_belongs_to_the_workload_identity() -> None:
    access = module(template("infrastructure"), "v2-shared-communication-access")["properties"][
        "template"
    ]
    definition = next(
        row for row in resources(access) if row["type"] == "Microsoft.Authorization/roleDefinitions"
    )
    identifier = access.get("variables", {}).get("senderRoleId", definition["name"])
    assert "parameters('principalId')" in identifier
    assert "parameters('principalId')" in definition["properties"]["roleName"]
    assignment = next(
        row for row in resources(access) if row["type"] == "Microsoft.Authorization/roleAssignments"
    )
    assert "scope" in assignment
    assert "subscriptionResourceId(" in assignment["properties"]["roleDefinitionId"]


def test_admin_readiness_uses_cross_group_ids_without_changing_legacy_names() -> None:
    account = _id("rg-shared-ai", "Microsoft.CognitiveServices/accounts", "shared-foundry")
    environment = _id(
        "rg-shared-compute", "Microsoft.App/managedEnvironments", "shared-environment"
    )
    storage = _id("rg-shared-data", "Microsoft.Storage/storageAccounts", "stsharedv2")
    collector = AdminReadinessCollector(
        settings=_settings(
            azure_subscription_id=SUBSCRIPTION,
            admin_readiness_foundry_account=account,
            admin_readiness_container_environments=json.dumps([environment]),
            admin_readiness_support_resources=json.dumps(
                [
                    {
                        "id": "state",
                        "name": "stsharedv2",
                        "resource_id": storage,
                        "resource_type": "Microsoft.Storage/storageAccounts",
                        "api_version": "2023-05-01",
                    }
                ]
            ),
        )
    )
    requests = collector._arm_requests()
    assert requests["foundry_account"][0] == account
    assert requests["foundry_project"][0] == f"{account}/projects/project"
    assert requests[f"environment:{environment}"][0] == environment
    assert requests["support:state"][0] == storage
    assert "/resourceGroups/rg-azbrief/" in requests["app:app"][0]
    with pytest.raises(ValueError, match="typed resource ID"):
        collector._resource_id(environment, "Microsoft.Storage/storageAccounts")
    with pytest.raises(ValueError, match="typed resource ID"):
        collector._resource_id(
            account.replace(SUBSCRIPTION, "another-subscription"),
            "Microsoft.CognitiveServices/accounts",
        )


def test_shared_storage_runtime_overrides_cannot_redirect_to_legacy_containers(
    tmp_path: Path,
) -> None:
    cli, values = shared_fixture(tmp_path, ("storageAccountMode",))
    values.update(
        tenantId=cli.tenant,
        subscriptionId=cli.subscription,
        controlPlaneClientId="00000000-0000-0000-0000-000000000004",
    )
    environment = {
        "ARCHIVE_BLOB_CONTAINER_URL": {
            "name": "ARCHIVE_BLOB_CONTAINER_URL",
            "value": f"https://{values['storageAccountName']}.blob.core.windows.net/{values['archiveContainerName']}",
        }
    }
    deploy_v2._validate_runtime(environment, values)
    environment["ARCHIVE_BLOB_CONTAINER_URL"][
        "value"
    ] = f"https://{values['storageAccountName']}.blob.core.windows.net/azbrief-archive"
    with pytest.raises(ValueError, match="infrastructure-bound"):
        deploy_v2._validate_runtime(environment, values)


def test_cross_group_application_installs_and_updates_without_replaying_shared_resources(
    tmp_path: Path,
) -> None:
    cli, values = shared_fixture(tmp_path)
    prepared = deploy_v2.prepare_infrastructure(cli, values, "foundation", "shared")
    bindings = deploy_v2.resource_bindings(cli, prepared)
    cli.tags = {
        "costCenter": "keep",
        **deploy_v2.make_tag_contract({**prepared, **bindings}, "shared-complete"),
    }
    cli.values = deploy_v2.infrastructure_values(cli, cli.tags)
    cli.account_id = bindings["foundryAccountResourceId"]
    cli.environment_id = bindings["containerAppsEnvironmentResourceId"]
    cli.project_id = f"{cli.account_id}/projects/{values['projectName']}"
    cli.identity_id = _id(
        cli.group,
        "Microsoft.ManagedIdentity/userAssignedIdentities",
        prepared["controlPlaneIdentityName"],
    )
    cli.documents[cli.identity_id] = cli.resource(
        cli.identity_id,
        {
            "clientId": "00000000-0000-0000-0000-000000000003",
            "principalId": "00000000-0000-0000-0000-000000000004",
        },
    )
    cli.documents[cli.project_id] = cli.resource(cli.project_id, {"provisioningState": "Succeeded"})
    cli.documents[cli.project_id]["tags"]["azbriefWorkloadResourceGroup"] = cli.group_id
    cli.collections[f"{cli.account_id}/projects"].append({"name": values["projectName"]})
    cli.collections[f"{cli.project_id}/capabilityHosts"] = [
        {
            "name": "agents",
            "properties": {"provisioningState": "Succeeded", "capabilityHostKind": "Agents"},
        }
    ]
    insights_id = _id(
        cli.group, "Microsoft.Insights/components", prepared["applicationInsightsName"]
    )
    cli.collections[f"{cli.project_id}/connections"] = [
        {
            "name": "azbrief-app-insights",
            "properties": {
                "category": "AppInsights",
                "authType": "ProjectManagedIdentity",
                "target": insights_id,
                "metadata": {"ResourceId": insights_id},
            },
        }
    ]
    cli.collections[f"{bindings['storageAccountResourceId']}/blobServices/default/containers"] = [
        {"name": bindings[field], "properties": {"publicAccess": "None"}}
        for field in ("stateContainerName", "archiveContainerName")
    ]
    cli.app_id = _id(cli.group, "Microsoft.App/containerApps", prepared["containerAppName"])
    cli.job_id = _id(cli.group, "Microsoft.App/jobs", prepared["schedulerJobName"])
    _, resolved = deploy_v2.read_infrastructure(cli)
    assert resolved["environmentResourceId"] == bindings["containerAppsEnvironmentResourceId"]
    assert (
        resolved["foundryProjectEndpoint"]
        == f"https://shared-foundry-endpoint.services.ai.azure.com/api/projects/{values['projectName']}"
    )
    snapshot = copy.deepcopy(cli.documents)
    deploy_v2.run_application(cli, IMAGE, {}, "deploy", "shared-app", automatic_setup=False)
    assert cli.requests[-1]["parameters"]["reuseExistingApiKey"] is False
    assert (
        deploy_v2.decode_resource_bindings(cli.requests[-1]["parameters"]["infrastructureTags"])
        == bindings
    )
    assert cli.documents == snapshot
    cli.values.update(resolved)
    cli.install()
    cli.collections[f"{cli.app_id}/authConfigs"][0]["properties"]["login"][
        "allowedExternalRedirectUrls"
    ] = [f"https://{prepared['containerAppName']}.{resolved['environmentDefaultDomain']}"]
    previous_secrets = copy.deepcopy(cli.secret_values)
    for digest in ("b", "c"):
        deploy_v2.run_application(
            cli,
            IMAGE.replace("a" * 64, digest * 64),
            {},
            "deploy",
            "shared-update",
            automatic_setup=False,
        )
        submitted = cli.requests[-1]["parameters"]
        assert submitted["reuseExistingApiKey"] is True
        assert submitted["existingSecrets"]["app"] == previous_secrets[cli.app_id]
        assert submitted["existingSecrets"]["job"] == previous_secrets[cli.job_id]
    assert cli.operations == ["validate", "what-if", "create"] * 3
    assert {row["template"] for row in cli.requests} == {"application.json"}
    assert cli.tags["costCenter"] == "keep"


def test_existing_foundry_model_checks_use_the_selected_account_not_workload_rg(
    tmp_path: Path,
) -> None:
    cli, values = shared_fixture(tmp_path, ("foundryAccountMode",))
    account_id = values["existingFoundryAccountResourceId"]
    cli.collections[f"{account_id}/deployments"] = [
        {
            "name": values[alias],
            "sku": {"name": "GlobalStandard", "capacity": 200},
            "properties": {
                "provisioningState": "Succeeded",
                "model": {"format": "OpenAI", "name": values[model], "version": values[version]},
            },
        }
        for alias, model, version in (
            ("modelDeploymentName", "coreModelName", "modelVersion"),
            ("simpleModelDeploymentName", "simpleModelName", "simpleModelVersion"),
        )
    ]
    parameters = {
        **values,
        "foundryAccountResourceId": account_id,
        "deployCapabilityHost": True,
        "provisionModels": True,
        "provisionEmail": False,
        "strictModelSelection": True,
    }
    planned = deploy_v2.automatic_service_plan(cli, parameters, {})
    assert planned["reuseCoreModel"] is planned["reuseSimpleModel"] is True
    cli.collections[f"{account_id}/deployments"][0]["sku"]["capacity"] = 100
    with pytest.raises(ValueError, match="no allocation"):
        deploy_v2.automatic_service_plan(cli, parameters, {})
    cli.collections[f"{account_id}/deployments"][0]["sku"]["capacity"] = 200
    cli.collections[f"{account_id}/deployments"][0]["sku"]["name"] = "GlobalProvisionedManaged"
    with pytest.raises(ValueError, match="no replacement"):
        deploy_v2.automatic_service_plan(cli, parameters, {})
    assert not cli.operations


@pytest.mark.parametrize("mode", list(deploy_v2.SHARED_RESOURCES))
def test_denied_shared_resource_read_never_becomes_a_new_resource(
    tmp_path: Path, mode: str
) -> None:
    cli, values = shared_fixture(tmp_path, (mode,))
    cli.denied.add(values[deploy_v2.SHARED_RESOURCES[mode][1]])
    with pytest.raises(RuntimeError, match="denied"):
        deploy_v2.prepare_infrastructure(cli, values, "foundation", "denied")
    assert not cli.operations


def test_shared_resource_names_are_derived_from_ids_without_duplicate_manual_inputs(
    tmp_path: Path,
) -> None:
    from tests.test_deployment_v2 import infrastructure_parameter_file

    path = infrastructure_parameter_file(tmp_path)
    document = json.loads(path.read_text(encoding="utf-8"))
    parameters = document["parameters"]
    for mode, (name, existing, _, resource_type, _) in deploy_v2.SHARED_RESOURCES.items():
        parameters[mode] = {"value": "existing"}
        parameters[existing] = {
            "value": _id(
                "rg-shared",
                resource_type,
                "shared-name" if name != "storageAccountName" else "stsharedv2",
            )
        }
        parameters.pop(name, None)
    path.write_text(json.dumps(document), encoding="utf-8")
    loaded = deploy_v2.load_infrastructure_parameters(path)
    assert loaded["foundryAccountName"] == "shared-name"
    assert loaded["storageAccountName"] == "stsharedv2"
    assert (
        loaded["containerAppsEnvironmentName"]
        == loaded["communicationServicesName"]
        == "shared-name"
    )


def test_shared_completion_reuses_models_and_does_not_require_parent_ownership(
    tmp_path: Path,
) -> None:
    cli, values = shared_fixture(tmp_path)
    foundation = deploy_v2.prepare_infrastructure(cli, values, "foundation", "shared")
    bindings = deploy_v2.resource_bindings(cli, foundation)
    cli.tags = deploy_v2.make_tag_contract(
        {**foundation, **bindings}, "shared-foundation", "foundation"
    )
    for field, resource_type in (
        ("controlPlaneIdentityName", "Microsoft.ManagedIdentity/userAssignedIdentities"),
        ("applicationInsightsName", "Microsoft.Insights/components"),
    ):
        resource_id = _id(cli.group, resource_type, foundation[field])
        resource = cli.resource(resource_id, {"provisioningState": "Succeeded"})
        cli.documents[resource_id] = resource
        cli.inventory.append(resource)
    cli.collections[f"{bindings['storageAccountResourceId']}/blobServices/default/containers"] = [
        {"name": bindings[field], "properties": {"publicAccess": "None"}}
        for field in ("stateContainerName", "archiveContainerName")
    ]
    cli.collections[f"{bindings['foundryAccountResourceId']}/deployments"] = [
        {
            "name": foundation[alias],
            "sku": {"name": "GlobalStandard", "capacity": 200},
            "properties": {
                "provisioningState": "Succeeded",
                "model": {
                    "format": "OpenAI",
                    "name": foundation[model],
                    "version": foundation[version],
                },
            },
        }
        for alias, model, version in (
            ("modelDeploymentName", "coreModelName", "modelVersion"),
            ("simpleModelDeploymentName", "simpleModelName", "simpleModelVersion"),
        )
    ]
    result = deploy_v2.prepare_infrastructure(cli, foundation, "complete", "shared")
    assert result["reuseCoreModel"] is result["reuseSimpleModel"] is True
    assert result["useCoreForSimpleModel"] is False
    assert result["reuseExistingFoundrySetup"] is False
    assert result["reuseExistingTelemetryConnection"] is False
    assert deploy_v2.resource_bindings(cli, result) == bindings
    storage_rows = cli.collections[
        f"{bindings['storageAccountResourceId']}/blobServices/default/containers"
    ]
    storage_rows.append(
        {"name": "another-private-container", "properties": {"publicAccess": "None"}}
    )
    with pytest.raises(ValueError, match="saved infrastructure resource ID or container"):
        deploy_v2.prepare_infrastructure(
            cli,
            {**foundation, "stateContainerName": "another-private-container"},
            "complete",
            "redirected",
        )
    assert not cli.operations
