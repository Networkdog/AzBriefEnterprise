"""Offline contracts for infrastructure-once and repeatable application deployment."""

import copy
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from scripts import deploy_v2
from scripts.deploy_kt import AzureCli
from tests.test_kt_deployment import _evaluate_ui_expression

ROOT = Path(__file__).resolve().parents[1]
V2 = ROOT / "infra" / "v2"
SUBSCRIPTION = "00000000-0000-0000-0000-000000000001"
TENANT = "00000000-0000-0000-0000-000000000002"
IMAGE = "ghcr.io/example/azbrief@sha256:" + "a" * 64


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
    declared = document["resources"]
    entries = declared.values() if isinstance(declared, dict) else declared
    return next(resource for resource in entries if resource["name"] == name)


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
    }


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
    assert handoff["properties"]["parameters"]["infrastructureState"]["value"] == "ready"
    assert tags_document["parameters"]["infrastructureState"]["allowedValues"] == [
        "foundation",
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
    project = next(
        resource
        for resource in document["resources"]
        if resource["type"] == "Microsoft.CognitiveServices/accounts/projects"
    )
    assert project["condition"] == "[and(variables('complete'), not(variables('reuseFoundry')))]"
    assert any("v2-required-models" in dependency for dependency in project["dependsOn"])
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


def test_app_and_job_share_image_and_secret_but_keep_distinct_commands() -> None:
    document = template("application")
    app = next(
        resource["properties"]["template"]
        for resource in document["resources"]
        if resource["type"] == "Microsoft.App/containerApps"
    )
    job = next(
        resource["properties"]["template"]
        for resource in document["resources"]
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
        for resource in document["resources"]
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
    assert module(infrastructure, "v2-mcp-authentication")
    assert module(infrastructure, "v2-mcp-project-permission")
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


def test_initial_application_uses_generated_key_and_tag_names() -> None:
    cli = FakeV2Azure()
    parameters = deploy_v2.prepare_application(cli, IMAGE)
    assert parameters["reuseExistingApiKey"] is False
    assert "apiKey" not in parameters
    assert parameters["infrastructureTags"] == cli.tags
    assert parameters["existingSecrets"] == {"app": [], "job": []}
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
        assert controls[field]["type"] == "Microsoft.Common.TextBox"
        assert controls[field]["constraints"]["required"] is True
    for field in (
        "modelDeploymentName",
        "simpleModelDeploymentName",
        "coreModelName",
        "simpleModelName",
        "modelVersion",
        "modelCapacity",
        "simpleModelCapacity",
        "foundryHostedAgentName",
        "azureMcpServerUrl",
        "azureMcpProjectConnectionName",
    ):
        assert field not in controls
    for name in ("infrastructure", "application"):
        assert set(ui_definition(name)["outputs"]) <= set(template(name)["parameters"])


def test_saved_customer_options_round_trip_and_application_rejects_foundation(
    tmp_path: Path,
) -> None:
    values = deploy_v2.load_infrastructure_parameters(infrastructure_parameter_file(tmp_path))
    values.update(
        searchSku="standard", emailDataLocation="Korea", modelCapacity=450, simpleModelCapacity=300
    )
    saved = deploy_v2.make_tag_contract(values, "customer-foundation", state="foundation")
    assert len(saved) == 39
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
    controls = ui_controls("application")
    expression = controls["deploymentGuard"]["constraints"]["validations"][1]["isValid"]
    tags = tag_contract()
    tags.update({"azbrief.v2.state": state, "azbrief.v2.options0": "{}"})
    assert _evaluate_ui_expression(
        expression,
        {"application": {"resourceGroupApi": {"tags": tags}}},
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
    expression = ui_controls("application")["deploymentGuard"]["constraints"]["validations"][2][
        "isValid"
    ]
    result = _evaluate_ui_expression(
        expression, {"application": {"containerAppsApi": response, "jobsApi": {"value": []}}}
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
    validations = ui_controls("infrastructure")["networkGuard"]["constraints"]["validations"]
    assert len(validations) == 5
    for validation in validations:
        assert (
            _evaluate_ui_expression(
                validation["isValid"], {}, network=network, eager_conditionals=True
            )
            is True
        )
    network["containerAppsSubnetName"] = network["foundrySubnetName"]
    assert _evaluate_ui_expression(validations[-1]["isValid"], {}, network=network) is False


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


def test_core_and_mcp_customer_names_never_merge_identities(tmp_path: Path) -> None:
    cli, values = infrastructure_cli(tmp_path)
    expression = ui_controls("infrastructure")["namesGuard"]["constraints"]["validations"][0][
        "isValid"
    ]
    assert _evaluate_ui_expression(expression, values) is True
    values["azureMcpIdentityName"] = values["controlPlaneIdentityName"]
    assert _evaluate_ui_expression(expression, values) is False
    with pytest.raises(ValueError, match="distinct"):
        deploy_v2.prepare_infrastructure(cli, values, "foundation", "v2")
    assert not cli.operations


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
    deploy_v2.run_application(cli, IMAGE, {}, "deploy", "v2-install")
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
        if "defaultValue" not in definition and name not in deploy_v2.INTERNAL_PARAMETERS
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

    def prepare(cli: AzureCli, values: dict[str, Any], stage: str, name: str) -> dict[str, Any]:
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
