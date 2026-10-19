"""Behavioral contracts for phase-specific Portal expression evaluation."""

import ast
import json
from pathlib import Path

import pytest

from scripts.ui_expressions import compile_expression, parse_expression
from tests.test_kt_deployment import _evaluate_ui_expression
from tests.test_v2_shared_ui import _outer_lambda_captures

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("stage", ["foundation", "complete"])
def test_phase_compilation_removes_only_unreachable_branches(stage: str) -> None:
    expression = (
        "[if(equals(basics('infrastructureStage'), 'foundation'), "
        "and(equals(1, true), true), and(true, not(false)))]"
    )
    compiled = compile_expression(expression, stage)
    assert compiled is (stage == "complete")


@pytest.mark.parametrize("value", ["{}", '{"id":null}', '{"id":"actual-id"}'])
def test_safe_selection_preserves_nulls_and_real_identity_without_native_errors(value: str) -> None:
    expression = compile_expression(f"[parse('{value}').id]", "foundation")
    assert isinstance(expression, str)
    assert _evaluate_ui_expression(
        expression, {}, strict_null_members=True, eager_conditionals=True
    ) == (json.loads(value).get("id") or "")


def test_phase_compiler_does_not_rewrite_function_text_inside_strings() -> None:
    source = "[concat('if(equals(basics', 'constant')]"
    compiled = compile_expression(source, "foundation")
    assert isinstance(compiled, str)
    assert _evaluate_ui_expression(compiled, {}) == "if(equals(basicsconstant"


@pytest.mark.parametrize("name,stage", [("foundation", "foundation"), ("completion", "complete")])
def test_specialized_forms_keep_guards_and_do_not_capture_outer_lambda_values(
    name: str, stage: str
) -> None:
    data = json.loads(
        (ROOT / "infra" / "v2" / f"{name}.createUiDefinition.json").read_text(encoding="utf-8")
    )["parameters"]
    assert all(element["name"] != "infrastructureStage" for element in data["basics"])
    assert data["outputs"]["infrastructureStage"] == stage
    assert "verification" not in {step["name"] for step in data["steps"]}
    guard = data["outputs"]["portalValidationPassed"]
    assert guard == "[equals(steps('contract').contractAccepted, true)]"
    assert data["outputs"]["portalVerificationMode"] == "portal"
    assert "portalEvidence" in data["outputs"]
    assert not _outer_lambda_captures(guard)
    navigation = next(element for element in data["basics"] if element["name"] == "phaseNavigation")
    target = "completion" if stage == "foundation" else "foundation"
    assert f"{target}.createUiDefinition.json" in navigation["options"]["uri"]
    assert "infrastructure.json" in navigation["options"]["uri"]


def test_completion_contains_no_foundation_inputs() -> None:
    data = json.loads(
        (ROOT / "infra" / "v2" / "completion.createUiDefinition.json").read_text(encoding="utf-8")
    )["parameters"]
    inputs = [
        element["name"]
        for step in data["steps"]
        for element in step["elements"]
        if element["type"]
        not in {
            "Microsoft.Common.InfoBox",
            "Microsoft.Solutions.ArmApiControl",
        }
    ]
    assert set(inputs) == {"contractAccepted"}
    assert data["config"]["basics"]["location"]["visible"] is False


@pytest.mark.parametrize("stage", ["foundation", "complete"])
def test_phase_literal_is_an_actual_output_value_not_an_unrecognized_expression(stage: str) -> None:
    assert compile_expression("[basics('infrastructureStage')]", stage) == stage


def test_removed_phase_controls_remain_unknown_without_native_missing_control_references() -> None:
    compiled = compile_expression(
        "[coalesce(steps('resources').existingFoundryAccount.id, '')]",
        "complete",
        {("resources", "existingFoundryAccount")},
    )
    assert compiled == ""


@pytest.mark.parametrize("value", [False, True, 0, "", "actual"])
def test_member_projection_preserves_falsy_values_without_scalar_first(value: object) -> None:
    literal = json.dumps({"value": value}, separators=(",", ":"))
    compiled = compile_expression(f"[coalesce(parse('{literal}').value, 'fallback')]", "complete")
    result = _evaluate_ui_expression(compiled, {}, native_empty_array_elements=True)
    assert result == value
    assert type(result) is type(value)


@pytest.mark.parametrize("simple", [None, "", "simple-version"])
def test_multi_value_coalesce_keeps_null_and_empty_version_semantics(simple: str | None) -> None:
    values = {"core": "core-version"}
    if simple is not None:
        values["simple"] = simple
    literal = json.dumps(values, separators=(",", ":"))
    compiled = compile_expression(
        f"[coalesce(parse('{literal}').simple, parse('{literal}').core, '')]", "complete"
    )
    assert _evaluate_ui_expression(compiled, {}, strict_null_members=True) == (
        "core-version" if simple is None else simple
    )


@pytest.mark.parametrize(
    "body,expected",
    [
        ("equals(row.value.text, 'different')", False),
        ("coalesce(row.value.text, '')", ""),
    ],
)
def test_single_row_bindings_preserve_native_false_and_empty_results(
    body: str, expected: str | bool
) -> None:
    source = (
        "[first(map(parse(concat('[{\"value\":', string(parse('{\"text\":\"\"}')), '}]')), "
        "(row) => " + body + "))]"
    )
    compiled = compile_expression(source, "complete")
    assert (
        _evaluate_ui_expression(
            compiled, {}, native_empty_array_elements=True, native_falsy_first=True
        )
        == expected
    )


@pytest.mark.parametrize("name", ["foundation", "completion", "application.input"])
def test_generated_input_views_reference_only_declared_controls(name: str) -> None:
    data = json.loads(
        (ROOT / "infra" / "v2" / f"{name}.createUiDefinition.json").read_text(encoding="utf-8")
    )["parameters"]
    available = {
        (step["name"], element["name"]) for step in data["steps"] for element in step["elements"]
    }

    def visit(value: object) -> None:
        if isinstance(value, dict):
            for item in value.values():
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)
        elif isinstance(value, str) and value.startswith("["):
            for node in ast.walk(parse_expression(value)):
                if (
                    isinstance(node, ast.Attribute)
                    and isinstance(node.value, ast.Call)
                    and isinstance(node.value.func, ast.Name)
                    and node.value.func.id == "steps"
                ):
                    assert (node.value.args[0].value, node.attr) in available

    visit(data)
