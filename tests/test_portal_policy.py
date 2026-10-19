"""Contracts for moving Portal preflight out of the reactive input graph."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import pytest

from scripts import build_v2_ui
from scripts.portal_policy import _Evidence, evidence_schema
from tests.test_kt_deployment import _evaluate_ui_expression
from tests.test_v2_shared_ui import (
    GROUP,
    SUBSCRIPTION,
    UiState,
    complete,
    definition,
    evaluate,
    foundation,
    model_deployment,
    save_tags,
    select_existing,
)

ROOT = Path(__file__).resolve().parents[1]


def observations(state: UiState, view: str = "foundation") -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name in ("portalEvidence", "portalResourceEvidence"):
        part = _evaluate_ui_expression(
            definition(view)["outputs"][name],
            {**state.values, "resources": state.values},
            network=state.network,
            resource_group=state.group,
            subscription={"subscriptionId": SUBSCRIPTION},
            selected_location=state.selected_location,
            strict_null_members=True,
            eager_conditionals=True,
        )
        assert isinstance(part, dict)
        for key, value in part.items():
            if key in result and isinstance(value, dict):
                result[key].update(value)
            else:
                result[key] = value
    return result


def policy_checks(
    state: UiState,
    *,
    surface: str = "infrastructure",
    include_outputs: bool = False,
    evidence: dict[str, Any] | None = None,
    inputs: dict[str, Any] | None = None,
    current_tags: dict[str, str] | None = None,
) -> dict[str, bool]:
    policy = json.loads(
        (ROOT / "infra" / "v2" / f"{surface}.portal-policy.json").read_text(encoding="utf-8")
    )
    if inputs is None:
        inputs = {
            name: (
                evaluate(value, state, False)
                if isinstance(value, str) and value.startswith("[")
                else value
            )
            for name, value in definition(surface)["outputs"].items()
            if name not in {"portalValidationPassed", "reuseCoreModel", "reuseSimpleModel"}
        }
    view = (
        "application.input"
        if surface == "application"
        else "completion" if state.stage == "complete" else "foundation"
    )
    step = "application" if surface == "application" else "contract"
    if current_tags is None:
        current_tags = (state.values[step].get("resourceGroupApi") or {}).get("tags") or {}
    parameters = {"evidence": evidence or observations(state, view), "inputs": inputs}
    functions = {
        item["namespace"] + "." + name: body
        for item in policy["functions"]
        for name, body in item["members"].items()
    }
    group = {
        **state.group,
        "id": f"/subscriptions/{SUBSCRIPTION}/resourceGroups/{GROUP}",
    }
    if current_tags:
        group["tags"] = current_tags
    entries = policy["resources"][0]["properties"]["parameters"]
    if include_outputs:
        entries = {
            **entries,
            **{name: {"value": value["value"]} for name, value in policy["outputs"].items()},
        }
    checked: dict[str, bool] = {}
    for name, value in entries.items():
        result = _evaluate_ui_expression(
            value["value"],
            {},
            parameters=parameters,
            variables=policy["variables"],
            resource_group=group,
            subscription={"subscriptionId": SUBSCRIPTION},
            custom_functions=functions,
            strict_object_members=True,
        )
        assert type(result) is bool
        checked[name] = result
    return checked


def test_untagged_existing_arm_group_can_omit_the_tags_property() -> None:
    state = foundation()
    state.group["mode"] = "Existing"
    state.values["contract"]["resourceGroupApi"] = {
        "id": f"/subscriptions/{SUBSCRIPTION}/resourceGroups/{GROUP}"
    }
    checks = policy_checks(state)
    assert all(checks.values()), checks


@pytest.mark.parametrize("value", [False, True, 0, 200, "200", "true", "한글 'quoted'", None])
def test_evidence_atoms_preserve_types_and_missing_values(value: object) -> None:
    source = json.dumps({"value": value}, ensure_ascii=False, separators=(",", ":"))
    source = source.replace("\\", "\\\\").replace("'", "\\'")
    expression = "[" + _Evidence().atom(f"parse('{source}')", "value") + "]"
    encoded = json.loads(_evaluate_ui_expression(expression, {}, strict_null_members=True))
    decoded = json.loads(base64.b64decode(encoded))
    assert decoded == ([] if value is None else [value])
    if value is not None:
        assert type(decoded[0]) is type(value)


def test_control_values_are_not_serialized_as_step_namespaces() -> None:
    expression = definition("foundation")["outputs"]["portalEvidence"]
    assert "string(coalesce(steps('resources')," not in expression
    state = foundation()
    state.values["storageAccountMode"] = "existing"
    encoded = observations(state)
    assert json.loads(base64.b64decode(encoded["resources"]["storageAccountMode"])) == ["existing"]
    resources = definition("foundation")["outputs"]["portalResourceEvidence"]
    assert "storageAccountMode" not in resources
    assert len(expression) < 20_000
    assert len(resources) < 140_000


def test_evidence_projection_does_not_copy_secrets_errors_or_continuation_urls() -> None:
    state = foundation()
    select_existing(state, "storageAccount")
    response = state.values["existingStorageAccountApi"]
    response["keys"] = ["private-key-do-not-copy"]
    response["properties"]["secret"] = "private-secret-do-not-copy"
    response["error"] = {"message": "private-error-body-do-not-copy"}
    response["nextLink"] = "https://example.invalid/?sig=private-signed-token"
    encoded = observations(state)
    projection = encoded["resources"]["existingStorageAccountApi"]
    assert "keys" not in projection and "secret" not in projection["properties"]
    assert json.loads(base64.b64decode(projection["error"])) == ["present"]
    assert json.loads(base64.b64decode(projection["nextLink"])) == ["present"]


@pytest.mark.parametrize("shared", [None, *build_v2_ui.SHARED_RESOURCES])
def test_server_policy_accepts_the_existing_valid_resource_combinations(shared: str | None) -> None:
    state = foundation()
    if shared:
        select_existing(state, shared)
    assert evaluate(definition()["outputs"]["portalValidationPassed"], state, False) is True
    checks = policy_checks(state, include_outputs=True)
    assert checks.pop("reuseCoreModel") is False
    assert checks.pop("reuseSimpleModel") is False
    assert all(checks.values()), checks


@pytest.mark.parametrize("shared", [None, *build_v2_ui.SHARED_RESOURCES])
def test_completion_server_policy_preserves_saved_choices_and_model_reuse(
    shared: str | None,
) -> None:
    state = foundation()
    if shared:
        select_existing(state, shared)
    if shared == "foundryAccount":
        state.values["sharedFoundryDeploymentsApi"]["value"] = [
            model_deployment(state, role) for role in ("Core", "Simple")
        ]
    state = complete(state)
    state.values["contract"]["resourceGroupApi"][
        "id"
    ] = f"/subscriptions/{SUBSCRIPTION}/resourceGroups/{GROUP}"
    assert evaluate(definition()["outputs"]["portalValidationPassed"], state, False) is True
    checks = policy_checks(state, include_outputs=True)
    assert checks.pop("reuseCoreModel") is (shared == "foundryAccount")
    assert checks.pop("reuseSimpleModel") is (shared == "foundryAccount")
    assert all(checks.values()), checks


@pytest.mark.parametrize("role", ["Core", "Simple"])
def test_completion_server_policy_never_reuses_a_mismatching_model(role: str) -> None:
    state = foundation()
    select_existing(state, "foundryAccount")
    rows = [model_deployment(state, item) for item in ("Core", "Simple")]
    rows[0 if role == "Core" else 1]["properties"]["model"]["version"] = "different-version"
    state.values["sharedFoundryDeploymentsApi"]["value"] = rows
    state = complete(state)
    state.values["contract"]["resourceGroupApi"][
        "id"
    ] = f"/subscriptions/{SUBSCRIPTION}/resourceGroups/{GROUP}"
    assert evaluate(definition()["outputs"]["portalValidationPassed"], state, False) is False
    checks = policy_checks(state, include_outputs=True)
    assert checks["deploymentPolicy"] is False
    assert checks[f"reuse{role}Model"] is False
    other = "Simple" if role == "Core" else "Core"
    assert checks[f"reuse{other}Model"] is True


@pytest.mark.parametrize(
    "status,response,expected",
    [
        ("ready", {"value": []}, True),
        ("foundation", {"value": []}, False),
        ("mcp_pending", {"value": []}, False),
        ("", {"value": []}, False),
        ("ready", {}, False),
        ("ready", {"value": None}, False),
        ("ready", {"value": [], "error": {"code": "Denied"}}, False),
        ("ready", {"value": [], "nextLink": "https://example.invalid/?sig=private"}, False),
    ],
)
def test_application_server_policy_preserves_readiness_and_failed_inventory(
    status: str, response: dict[str, Any], expected: bool
) -> None:
    tags = {**save_tags(foundation()), "azbrief.v2.state": status}
    state = UiState(
        values={
            "application": {
                "resourceGroupApi": {
                    "id": f"/subscriptions/{SUBSCRIPTION}/resourceGroups/{GROUP}",
                    "tags": tags,
                },
                "deploymentAccepted": True,
                "containerImage": "ghcr.io/example/azbrief@sha256:" + "a" * 64,
                "containerAppsApi": response,
                "jobsApi": {"value": []},
            }
        },
        network={},
        group={"mode": "Existing", "name": GROUP, "location": "eastus"},
    )
    assert (
        evaluate(definition("application")["outputs"]["portalValidationPassed"], state, False)
        is expected
    )
    checks = policy_checks(state, surface="application")
    assert checks["deploymentPolicy"] is expected
    assert checks["parametersMatch"] is True
    assert checks["tagBindingsMatch"] is True


def test_server_policy_rejects_unsafe_subnets_and_parameter_substitution() -> None:
    state = foundation()
    state.network["containerAppsSubnetName"] = state.network["foundrySubnetName"]
    checks = policy_checks(state)
    assert checks["deploymentPolicy"] is False
    assert checks["parametersMatch"] is True
    state = foundation()
    inputs = {
        name: evaluate(value, state, False)
        for name, value in definition()["outputs"].items()
        if name not in {"portalValidationPassed", "reuseCoreModel", "reuseSimpleModel"}
    }
    inputs["foundryAccountName"] = "redirected-account"
    assert policy_checks(state, inputs=inputs)["parametersMatch"] is False


def test_server_policy_checks_exact_version_and_current_tag_freshness() -> None:
    state = foundation()
    evidence = observations(state)
    evidence["policyHash"] = "stale-policy"
    checks = policy_checks(
        state, evidence=evidence, current_tags={"azbrief.v2.state": "foundation"}
    )
    assert checks["policyVersion"] is False
    assert checks["freshContract"] is False


def test_encoded_schema_has_only_declared_nonsecret_resource_fields() -> None:
    schema = evidence_schema(definition(), build_v2_ui.PORTAL_TAG_KEYS)
    storage = schema["resources"]["existingStorageAccountApi"]
    assert "allowBlobPublicAccess" in storage["properties"]
    assert "hostName" in schema["resources"]["existingCommunicationServicesApi"]["properties"]
    assert (
        "destination"
        in schema["resources"]["existingContainerAppsEnvironmentApi"]["properties"][
            "appLogsConfiguration"
        ]
    )


@pytest.mark.parametrize("name", ["infrastructure", "application"])
def test_generated_arm_policy_has_bounded_expressions_and_true_only_checks(name: str) -> None:
    policy = json.loads(
        (ROOT / "infra" / "v2" / f"{name}.portal-policy.json").read_text(encoding="utf-8")
    )

    def visit(value: object) -> None:
        if isinstance(value, str) and value.startswith("["):
            assert len(value) < 24576
        elif isinstance(value, dict):
            for item in value.values():
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(policy)
    checks = policy["resources"][0]["properties"]["template"]["parameters"]
    assert checks and all(rule["allowedValues"] == [True] for rule in checks.values())
    assert len(policy["variables"]) < 256


def test_every_resource_write_waits_for_portal_verification() -> None:
    for name in ("infrastructure", "application"):
        template = json.loads((ROOT / "infra" / "v2" / f"{name}.json").read_text(encoding="utf-8"))
        resources = template["resources"]
        resources = list(resources.values()) if isinstance(resources, dict) else resources
        verification = next(item for item in resources if item["name"] == "v2-portal-verification")
        assert (
            verification["condition"] == "[equals(parameters('portalVerificationMode'), 'portal')]"
        )
        assert template["parameters"]["portalValidationPassed"]["allowedValues"] == [True]
        if name == "infrastructure":
            barrier = next(
                item for item in resources if item["name"] == "v2-network-binding-validation"
            )
            assert any("v2-portal-verification" in entry for entry in barrier["dependsOn"])
        else:
            for resource in resources:
                if resource["name"] in {
                    "v2-application-readback",
                    "v2-managed-mcp-application",
                    "[parameters('infrastructureTags')['azbrief.v2.app']]",
                    "[parameters('infrastructureTags')['azbrief.v2.job']]",
                }:
                    assert any(
                        "v2-portal-verification" in entry or entry == "portalVerification"
                        for entry in resource["dependsOn"]
                    )


def test_application_keeps_native_readiness_messages_and_binds_actual_target_tags() -> None:
    image = next(
        element
        for step in definition("application.input")["steps"]
        for element in step["elements"]
        if element["name"] == "containerImage"
    )
    messages = [rule["message"] for rule in image["constraints"]["validations"]]
    assert len(messages) == 3
    assert any("foundation" in message for message in messages)
    assert any("MCP" in message for message in messages)
    assert any("ready" in message for message in messages)
    policy = json.loads(
        (ROOT / "infra" / "v2" / "application.portal-policy.json").read_text(encoding="utf-8")
    )
    expression = policy["resources"][0]["properties"]["parameters"]["tagBindingsMatch"]["value"]
    assert (
        _evaluate_ui_expression(
            expression,
            {},
            parameters={"inputs": {"infrastructureTags": {"azbrief.v2.app": "other"}}},
            resource_group={"tags": {"azbrief.v2.app": "actual"}},
        )
        is False
    )
