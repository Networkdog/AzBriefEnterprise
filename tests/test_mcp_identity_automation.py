"""Authorized MCP setup tests. No Azure/Graph requests or permission grants are performed."""

import base64
import copy
import io
import json
from pathlib import Path
from subprocess import CompletedProcess
from typing import Any
from urllib.error import HTTPError
from uuid import UUID

import pytest

from scripts import build_v2_ui, deploy_v2
from scripts import provision_mcp_identity as setup
from tests.test_deployment_v2 import (
    SUBSCRIPTION,
    FakeMcpPreparation,
    infrastructure_cli,
    module,
    resources,
    template,
)
from tests.test_portal_policy import policy_checks
from tests.test_v2_shared_ui import controls, definition, evaluate, foundation, output

TENANT = "00000000-0000-0000-0000-000000000010"
INSTALLER = "00000000-0000-0000-0000-000000000011"
PROJECT = "00000000-0000-0000-0000-000000000012"
CLIENT = "00000000-0000-0000-0000-000000000013"
OBJECT = "00000000-0000-0000-0000-000000000014"
PRINCIPAL = "00000000-0000-0000-0000-000000000015"
ROLE = "00000000-0000-0000-0000-000000000016"
SCOPE = "00000000-0000-0000-0000-000000000017"
CONTROL = "00000000-0000-0000-0000-000000000018"
MCP = "00000000-0000-0000-0000-000000000019"


def configuration() -> dict[str, str]:
    return {
        "tenantId": TENANT,
        "installerPrincipalId": INSTALLER,
        "projectPrincipalId": PROJECT,
        "controlPlanePrincipalId": CONTROL,
        "mcpPrincipalId": MCP,
        "workloadResourceGroupId": (f"/subscriptions/{SUBSCRIPTION}/resourceGroups/rg-workload"),
        "uniqueName": "azbrief-v2-mcp-abcdefghijklm",
        "appRoleId": ROLE,
        "scopeId": SCOPE,
    }


def automation() -> dict[str, str]:
    prefix = f"/subscriptions/{SUBSCRIPTION}/resourceGroups/rg-setup/providers/"
    return {
        "identityResourceId": prefix + "Microsoft.ManagedIdentity/userAssignedIdentities/setup",
        "subnetResourceId": prefix + "Microsoft.Network/virtualNetworks/setup/subnets/aci",
        "storageAccountResourceId": prefix + "Microsoft.Storage/storageAccounts/setupexecution",
    }


class FakeGraph(setup.Graph):
    def __init__(self):
        self.application: dict[str, Any] | None = None
        self.principal: dict[str, Any] | None = None
        self.assignments: list[dict[str, Any]] = []
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []
        self.owners = [{"id": INSTALLER}]

    def request(
        self, method: str, path: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        self.calls.append((method, path, copy.deepcopy(payload)))
        if path.startswith("applications?") and method == "GET":
            return {"value": [copy.deepcopy(self.application)] if self.application else []}
        if path == "applications" and method == "POST":
            assert payload is not None
            self.application = {
                **copy.deepcopy(payload),
                "id": OBJECT,
                "appId": CLIENT,
                "passwordCredentials": [],
                "keyCredentials": [],
                "identifierUris": [],
            }
            self.application.pop("owners@odata.bind")
            return copy.deepcopy(self.application)
        if path == f"applications/{OBJECT}/owners?$select=id":
            return {"value": self.owners}
        if path == f"applications/{OBJECT}" and method == "PATCH":
            assert self.application is not None and payload is not None
            assert "uniqueName" not in payload
            self.application.update(copy.deepcopy(payload))
            return {}
        if path == f"applications/{OBJECT}" and method == "GET":
            assert self.application is not None
            return copy.deepcopy(self.application)
        if path.startswith("servicePrincipals?") and method == "GET":
            return {"value": [copy.deepcopy(self.principal)] if self.principal else []}
        if path == "servicePrincipals" and method == "POST":
            assert payload == {"appId": CLIENT}
            self.principal = {
                "id": PRINCIPAL,
                "appId": CLIENT,
                "appOwnerOrganizationId": TENANT,
                "servicePrincipalType": "Application",
                "accountEnabled": True,
            }
            return copy.deepcopy(self.principal)
        if path.startswith(f"servicePrincipals/{PRINCIPAL}/appRoleAssignedTo?"):
            return {"value": copy.deepcopy(self.assignments)}
        if path == f"servicePrincipals/{PRINCIPAL}/appRoleAssignedTo" and method == "POST":
            assert payload is not None
            self.assignments.append(copy.deepcopy(payload))
            return {"id": "confirmed"}
        raise AssertionError(f"Unexpected directory operation: {method} {path}")


def test_creates_only_exact_mcp_objects_and_reuses_them_without_new_grants():
    graph = FakeGraph()
    expected = {"clientId": CLIENT, "servicePrincipalId": PRINCIPAL, "appRoleId": ROLE}
    assert setup.provision(graph, configuration()) == expected
    count = len(graph.calls)
    assert setup.provision(graph, configuration()) == expected
    assert not any(method == "POST" for method, _, _ in graph.calls[count:])
    assert graph.assignments == [
        {"principalId": PROJECT, "resourceId": PRINCIPAL, "appRoleId": ROLE}
    ]
    assert graph.application is not None
    assert graph.application["identifierUris"] == ["api://" + CLIENT]
    assert graph.application["requiredResourceAccess"] == []
    assert graph.application["api"]["preAuthorizedApplications"] == [
        {"appId": setup.VSCODE_CLIENT_ID, "delegatedPermissionIds": [SCOPE]}
    ]
    assert not any("addPassword" in path or "directoryRoles" in path for _, path, _ in graph.calls)


@pytest.mark.parametrize(
    "change", ["owner", "unique", "role", "scope", "audience", "credentials", "api-permission"]
)
def test_never_adopts_or_overwrites_foreign_directory_state(change: str):
    graph = FakeGraph()
    setup.provision(graph, configuration())
    assert graph.application is not None
    if change == "owner":
        graph.owners = [{"id": CONTROL}]
    elif change == "unique":
        graph.application["uniqueName"] = "foreign"
    elif change == "role":
        graph.application["appRoles"][0]["value"] = "Other.Permission"
    elif change == "scope":
        graph.application["api"]["oauth2PermissionScopes"] = []
    elif change == "audience":
        graph.application["identifierUris"] = ["api://foreign"]
    elif change == "credentials":
        graph.application["passwordCredentials"] = [{"keyId": CONTROL}]
    else:
        graph.application["requiredResourceAccess"] = [{"resourceAppId": CONTROL}]
    graph.calls.clear()
    with pytest.raises(setup.McpIdentityError):
        setup.provision(graph, configuration())
    assert not any(method != "GET" for method, _, _ in graph.calls)


@pytest.mark.parametrize("identity", [PROJECT, CONTROL, MCP])
def test_runtime_and_project_identities_cannot_become_the_installer(identity: str):
    graph = FakeGraph()
    with pytest.raises(setup.McpIdentityError, match="distinct"):
        setup.provision(graph, {**configuration(), "installerPrincipalId": identity})
    assert not graph.calls


@pytest.mark.parametrize(
    "response",
    [{}, {"value": None}, {"value": [], "@odata.nextLink": "private"}, {"value": [None]}],
)
def test_missing_or_paged_inventory_is_not_confirmed_absence(response):
    with pytest.raises(setup.McpIdentityError):
        setup.rows(response)


def test_graph_failures_are_bounded_and_never_expose_private_error_bodies(monkeypatch):
    graph = setup.Graph("do-not-log-this-token")
    attempts = []

    def forbidden(*_args, **_kwargs):
        attempts.append(1)
        raise HTTPError(setup.GRAPH, 403, "private details", {}, io.BytesIO(b"private-body"))

    monkeypatch.setattr(graph.opener, "open", forbidden)
    with pytest.raises(setup.McpIdentityError, match="HTTP 403") as error:
        graph.request("GET", "applications")
    assert len(attempts) == 1
    assert "private" not in str(error.value) and "token" not in str(error.value)
    with pytest.raises(setup.McpIdentityError, match="redirect"):
        setup.NoRedirects().redirect_request(None, None, 302, "", {}, "https://other.invalid")


def test_graph_retry_exhaustion_has_no_sleep_after_the_final_attempt(monkeypatch):
    graph = setup.Graph("synthetic")
    sleeps = []

    def throttled(*_args, **_kwargs):
        raise HTTPError(setup.GRAPH, 429, "", {"Retry-After": "2"}, io.BytesIO())

    monkeypatch.setattr(graph.opener, "open", throttled)
    monkeypatch.setattr(setup.time, "sleep", sleeps.append)
    with pytest.raises(setup.McpIdentityError, match="HTTP 429"):
        graph.request("GET", "applications")
    assert sleeps == [2, 2, 2]


@pytest.mark.parametrize(
    "missing",
    [
        "Application.ReadWrite.OwnedBy",
        "Application.Read.All",
        "AppRoleAssignment.ReadWrite.All",
        "identity",
        "tenant",
        "none",
    ],
)
def test_entrypoint_requires_preapproved_permissions_and_keeps_outputs_nonsecret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, missing: str
):
    roles = [
        "Application.ReadWrite.OwnedBy",
        "Application.Read.All",
        "AppRoleAssignment.ReadWrite.All",
    ]
    claims = {
        "oid": CONTROL if missing == "identity" else INSTALLER,
        "tid": CONTROL if missing == "tenant" else TENANT,
        "roles": [role for role in roles if role != missing],
    }
    body = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    token = "synthetic." + body + ".signature"
    graph = FakeGraph()
    monkeypatch.setenv("AZBRIEF_MCP_SETUP", json.dumps(configuration()))
    output_path = tmp_path / "outputs.json"
    monkeypatch.setenv("AZ_SCRIPTS_OUTPUT_PATH", str(output_path))
    monkeypatch.setattr(
        setup.subprocess, "run", lambda *_args, **_kwargs: CompletedProcess([], 0, token, "")
    )
    monkeypatch.setattr(setup, "Graph", lambda _token: graph)
    if missing != "none":
        with pytest.raises(setup.McpIdentityError, match="preapproved"):
            setup.main()
        assert not output_path.exists() and not graph.calls
    else:
        setup.main()
        result = json.loads(output_path.read_text(encoding="utf-8"))
        assert set(result) == {"clientId", "servicePrincipalId", "appRoleId"}
        assert all(UUID(value).int for value in result.values())
        assert token not in output_path.read_text(encoding="utf-8")


def test_automatic_module_preserves_private_setup_and_runtime_permission_boundaries():
    document = template("mcp-authentication-automatic")
    declared = list(resources(document))
    script = next(row for row in declared if row["type"] == "Microsoft.Resources/deploymentScripts")
    assert script["properties"]["cleanupPreference"] == "Always"
    assert "subnetIds" in script["properties"]["containerSettings"]
    assert set(script["properties"]["storageAccountSettings"]) == {"storageAccountName"}
    assert "storageAccountKey" not in json.dumps(document)
    assert "v2-mcp-automation-prerequisites" in json.dumps(script["dependsOn"])
    assert not any(
        "roleAssignments" in row["type"] or row["type"].startswith("Microsoft.Graph/")
        for row in declared
    )
    handoff = module(document, "v2-mcp-ready-tags")
    assert handoff["properties"]["parameters"]["infrastructureState"]["value"] == "ready"
    parent = template("infrastructure")
    automatic = module(parent, "v2-mcp-automatic-authentication")
    assert "mcpAutomation" in automatic["condition"]
    assert "v2-infrastructure-tags" in json.dumps(automatic["dependsOn"])
    assert "v2-infrastructure-tags" in json.dumps(parent)
    assert "Microsoft.Graph/" not in json.dumps(parent)


def test_setup_ids_are_nonsecret_and_old_tag_contracts_remain_valid(tmp_path: Path):
    cli, values = infrastructure_cli(tmp_path)
    original = deploy_v2.make_tag_contract(values, "baseline", "foundation")
    assert "mcpAutomation" not in deploy_v2.decode_tag_options(original)
    values["mcpAutomation"] = automation()
    saved = deploy_v2.make_tag_contract(values, "automatic", "foundation")
    assert deploy_v2.decode_tag_options(saved)["mcpAutomation"] == automation()
    for field in automation():
        changed = {**automation(), field: automation()[field] + "?redirect=true"}
        with pytest.raises(ValueError):
            deploy_v2.validate_mcp_automation(changed, SUBSCRIPTION)
    with pytest.raises(ValueError, match="exactly"):
        deploy_v2.validate_mcp_automation({**automation(), "clientSecret": "never"})
    assert not cli.operations


def test_foundation_automation_is_explicit_and_completion_restores_only_saved_setup():
    state = foundation()
    assert output("mcpAutomation", state) == {}
    state.values["options"]["mcpSetupEnabled"] = True
    assert output("portalValidationPassed", state) is False
    selected = controls()
    assert evaluate(selected["mcpSetupIdentity"]["visible"], state) is True
    assert selected["mcpSetupAccepted"]["constraints"]["required"] is True
    for name in ("mcpSetupIdentity", "mcpSetupStorage", "mcpSetupNetwork"):
        assert selected[name]["type"] == "Microsoft.Solutions.ResourceSelector"
    assert definition("application")["outputs"]["portalValidationPassed"]


def test_complete_automation_selection_outputs_exact_ids_and_required_consent():
    state = foundation()
    config = automation()
    vnet_id = config["subnetResourceId"].rsplit("/subnets/", 1)[0]
    state.values["options"].update(
        {
            "mcpSetupEnabled": True,
            "mcpSetupAccepted": True,
            "mcpSetupIdentity": {"id": config["identityResourceId"]},
            "mcpSetupStorage": {"id": config["storageAccountResourceId"]},
            "mcpSetupNetwork": {"id": vnet_id},
            "mcpSetupSubnet": "aci",
            "mcpSetupNetworkApi": {
                "id": vnet_id,
                "name": "setup",
                "type": "Microsoft.Network/virtualNetworks",
                "properties": {
                    "provisioningState": "Succeeded",
                    "subnets": [
                        {
                            "id": config["subnetResourceId"],
                            "name": "aci",
                            "properties": {
                                "delegations": [
                                    {
                                        "properties": {
                                            "serviceName": "Microsoft.ContainerInstance/containerGroups"
                                        }
                                    }
                                ]
                            },
                        }
                    ],
                },
            },
        }
    )
    assert output("mcpAutomation", state) == config
    assert output("portalValidationPassed", state) is True
    checks = policy_checks(state)
    assert all(checks.values()), checks
    state.values["options"]["mcpSetupAccepted"] = False
    assert output("portalValidationPassed", state) is False
    assert not all(policy_checks(state).values())


def add_setup_documents(cli, values):
    config = automation()
    identity = cli.resource(
        config["identityResourceId"],
        {
            "tenantId": cli.tenant,
            "principalId": INSTALLER,
            "clientId": CLIENT,
        },
    )
    identity["type"] = "Microsoft.ManagedIdentity/userAssignedIdentities"
    cli.documents[identity["id"]] = identity
    subnet = cli.resource(
        config["subnetResourceId"],
        {
            "provisioningState": "Succeeded",
            "delegations": [
                {"properties": {"serviceName": "Microsoft.ContainerInstance/containerGroups"}}
            ],
        },
    )
    cli.documents[subnet["id"]] = subnet
    vnet_id = subnet["id"].rsplit("/subnets/", 1)[0]
    network = cli.resource(vnet_id, {"provisioningState": "Succeeded", "subnets": [subnet]})
    network["location"] = values["location"]
    cli.documents[vnet_id] = network
    storage = cli.resource(
        config["storageAccountResourceId"],
        {
            "provisioningState": "Succeeded",
            "publicNetworkAccess": "Disabled",
        },
    )
    storage.update(kind="StorageV2", location=values["location"])
    cli.documents[storage["id"]] = storage
    values["mcpAutomation"] = config
    return copy.deepcopy(config)


@pytest.mark.parametrize(
    "failure",
    [
        "none",
        "tenant",
        "redirect",
        "shared-app-storage",
        "delegation",
        "region",
        "public",
        "denied",
    ],
)
def test_private_setup_preflight_reads_exact_resources_and_never_grants_permissions(
    tmp_path: Path, failure: str
):
    cli, values = infrastructure_cli(tmp_path)
    config = add_setup_documents(cli, values)
    if failure == "tenant":
        cli.documents[config["identityResourceId"]]["properties"]["tenantId"] = TENANT
    elif failure == "redirect":
        cli.documents[config["identityResourceId"]]["id"] += "-foreign"
    elif failure == "shared-app-storage":
        values["mcpAutomation"]["storageAccountResourceId"] = (
            cli.group_id
            + "/providers/Microsoft.Storage/storageAccounts/"
            + values["storageAccountName"]
        )
        cli.documents[values["mcpAutomation"]["storageAccountResourceId"]] = {
            **cli.documents[config["storageAccountResourceId"]],
            "id": values["mcpAutomation"]["storageAccountResourceId"],
        }
    elif failure == "delegation":
        cli.documents[config["subnetResourceId"]]["properties"]["delegations"] = []
    elif failure == "region":
        cli.documents[config["storageAccountResourceId"]]["location"] = "differentregion"
    elif failure == "public":
        cli.documents[config["storageAccountResourceId"]]["properties"][
            "publicNetworkAccess"
        ] = "Enabled"
    elif failure == "denied":
        cli.denied.add(config["identityResourceId"])
    if failure == "none":
        assert deploy_v2._verify_mcp_automation(cli, values) == config
    else:
        with pytest.raises((RuntimeError, ValueError)):
            deploy_v2._verify_mcp_automation(cli, values)
    assert not cli.operations


def test_automatic_retry_uses_saved_executor_without_the_cli_users_graph_permissions():
    cli = FakeMcpPreparation()
    config = add_setup_documents(cli, cli.values)
    options = {**deploy_v2.decode_tag_options(cli.tags), "mcpAutomation": config}
    encoded = json.dumps(options, separators=(",", ":"))
    cli.tags.update(
        {
            key: encoded[index * 256 : (index + 1) * 256]
            for index, key in enumerate(deploy_v2.OPTION_TAG_KEYS)
        }
    )
    identity_id = (
        f"{cli.group_id}/providers/Microsoft.ManagedIdentity/userAssignedIdentities/"
        + cli.values["azureMcpIdentityName"]
    )
    cli.documents[identity_id]["properties"].update(principalId=MCP)
    original = cli.json

    def no_delegated_graph(*arguments):
        if arguments[:2] == ("ad", "app"):
            raise AssertionError("The CLI user must not supply the executor's Graph authorization.")
        return original(*arguments)

    cli.json = no_delegated_graph
    result = deploy_v2.prepare_mcp_authentication(cli)
    assert result["automation"] == config
    assert UUID(result["mcpPrincipalId"]).int
    assert not cli.operations


def test_existing_pending_install_has_a_portal_continuation_without_core_replay():
    form = definition("mcp-authentication")
    assert set(form["outputs"]) == {"automation", "portalValidationPassed"}
    assert not form["basics"]
    serialized = json.dumps(form)
    assert "infrastructureStage" not in serialized
    assert "mcpSetupEnabled" not in serialized
    assert controls("application")["mcpPendingNotice"]["options"]["uri"] == (
        build_v2_ui.MCP_AUTOMATION_PORTAL_URL
    )
    document = template("mcp-authentication-portal")
    assert document["parameters"]["portalValidationPassed"]["allowedValues"] == [True]
    declared = list(resources(document))
    assert not any(
        row["type"]
        in {
            "Microsoft.App/containerApps",
            "Microsoft.App/jobs",
            "Microsoft.CognitiveServices/accounts/deployments",
            "Microsoft.CognitiveServices/accounts/projects",
            "Microsoft.Authorization/roleAssignments",
        }
        for row in declared
    )
    executor = module(document, "v2-mcp-authorized-portal-setup")
    assert "v2-mcp-exact-pending-core" in json.dumps(executor["dependsOn"])
    proof = module(document, "v2-mcp-exact-pending-core")
    assert proof["properties"]["template"]["parameters"]["approved"]["allowedValues"] == [True]
    validation = json.dumps(proof["properties"]["parameters"])
    assert "mcp_pending" in validation and "Succeeded" in validation
    assert "uniqueString(resourceGroup().id)" in validation
