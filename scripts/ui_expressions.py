"""Specialize Portal forms and keep deployment validation outside the input hot path."""

from __future__ import annotations

import ast
import json
import re
from typing import Any

_LOGICAL = {"ui_and": "and", "ui_or": "or", "ui_not": "not", "ui_if": "if"}


def _literal(node: ast.expr) -> tuple[bool, Any]:
    if isinstance(node, ast.Constant):
        return True, node.value
    if isinstance(node, ast.Name) and node.id in {"true", "false", "null"}:
        return True, {"true": True, "false": False, "null": None}[node.id]
    return False, None


def _row_fields(node: ast.expr) -> dict[str, ast.expr] | None:
    if not (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "parse"
        and len(node.args) == 1
        and isinstance(node.args[0], ast.Call)
        and isinstance(node.args[0].func, ast.Name)
        and node.args[0].func.id == "concat"
    ):
        return None
    slots, parts = {}, []
    for index, argument in enumerate(node.args[0].args):
        if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
            parts.append(argument.value)
        else:
            key = f"__binding_{index}__"
            slots[key] = argument
            parts.append(json.dumps(key))
    try:
        value = json.loads("".join(parts))
    except json.JSONDecodeError:
        return None
    if not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], dict):
        return None
    if not all(isinstance(item, str) and item in slots for item in value[0].values()):
        return None
    return {key: slots[item] for key, item in value[0].items()}


class _UnavailableControls(ast.NodeTransformer):
    def __init__(self, controls: set[tuple[str, str]]) -> None:
        self.controls = controls

    def visit_Attribute(self, node: ast.Attribute) -> ast.expr:
        node = self.generic_visit(node)
        if (
            isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Name)
            and node.value.func.id == "steps"
            and len(node.value.args) == 1
            and isinstance(node.value.args[0], ast.Constant)
            and (node.value.args[0].value, node.attr) in self.controls
        ):
            return ast.Constant(None)
        known, value = _literal(node.value)
        return ast.Constant(None) if known and value is None else node


class _PhaseCompiler(ast.NodeTransformer):
    def __init__(self, stage: str) -> None:
        self.stage = stage

    def visit_Call(self, node: ast.Call) -> ast.expr:
        if not isinstance(node.func, ast.Name):
            raise ValueError("Portal functions must be named")
        function = node.func.id
        if (
            function == "basics"
            and len(node.args) == 1
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value == "infrastructureStage"
        ):
            return ast.Constant(self.stage)
        if function == "ui_if":
            condition = self.visit(node.args[0])
            known, value = _literal(condition)
            if known and type(value) is bool:
                return self.visit(node.args[1] if value else node.args[2])
        node = self.generic_visit(node)
        arguments = [_literal(argument) for argument in node.args]
        if function in {"ui_and", "ui_or"}:
            terminal = function == "ui_or"
            if any(
                known and type(value) is bool and value is terminal for known, value in arguments
            ):
                return ast.Constant(terminal)
            node.args = [
                argument
                for argument, (known, value) in zip(node.args, arguments)
                if not (known and type(value) is bool and value is not terminal)
            ]
            if not node.args:
                return ast.Constant(not terminal)
            if len(node.args) == 1:
                return node.args[0]
        if all(known for known, _ in arguments):
            values = [value for _, value in arguments]
            if function == "equals":
                return ast.Constant(type(values[0]) is type(values[1]) and values[0] == values[1])
            if function == "ui_not" and type(values[0]) is bool:
                return ast.Constant(not values[0])
            if function == "coalesce":
                return ast.Constant(next((value for value in values if value is not None), None))
        if (
            function == "first"
            and len(node.args) == 1
            and isinstance(node.args[0], ast.Call)
            and isinstance(node.args[0].func, ast.Name)
            and node.args[0].func.id == "map"
            and len(node.args[0].args) == 2
            and isinstance(node.args[0].args[1], ast.Lambda)
        ):
            mapping = node.args[0]
            predicate = mapping.args[1]
            fields = _row_fields(mapping.args[0])
            parameter = predicate.args.args[0].arg
            if fields is not None:
                parents = {
                    child: parent
                    for parent in ast.walk(predicate.body)
                    for child in ast.iter_child_nodes(parent)
                }
                used: set[str] = set()
                for child in ast.walk(predicate.body):
                    if isinstance(child, ast.Name) and child.id == parameter:
                        parent = parents.get(child)
                        if not isinstance(parent, ast.Attribute):
                            return node
                        used.add(parent.attr)
                if not used:
                    return predicate.body
                if used <= fields.keys() and used != fields.keys():
                    parts: list[ast.expr] = [ast.Constant("[{")]
                    for index, key in enumerate(key for key in fields if key in used):
                        parts.extend(
                            (
                                ast.Constant(("" if index == 0 else ",") + json.dumps(key) + ":"),
                                fields[key],
                            )
                        )
                    parts.append(ast.Constant("}]"))
                    mapping.args[0].args[0].args = parts
        return node


def _render(node: ast.expr) -> str:
    if isinstance(node, ast.Constant):
        if isinstance(node.value, str):
            return "'" + node.value.replace("\\", "\\\\").replace("'", "\\'") + "'"
        if node.value is None:
            return "null"
        if type(node.value) is bool:
            return str(node.value).lower()
        return str(node.value)
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        return "-" + _render(node.operand)
    if isinstance(node, ast.Attribute):
        return _render(node.value) + "." + node.attr
    if isinstance(node, ast.Subscript):
        return _render(node.value) + "[" + _render(node.slice) + "]"
    if isinstance(node, ast.Lambda) and len(node.args.args) == 1:
        return f"({node.args.args[0].arg}) => {_render(node.body)}"
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        return (
            _LOGICAL.get(node.func.id, node.func.id)
            + "("
            + ", ".join(_render(argument) for argument in node.args)
            + ")"
        )
    raise ValueError(f"Unsupported Portal expression: {type(node).__name__}")


def parse_expression(expression: str) -> ast.expr:
    """Parse function syntax without rewriting quoted JSON or display text."""
    source = expression[1:-1]
    parts = []
    start = index = 0

    def functions(value: str) -> str:
        value = re.sub(r"\((\w+)\)\s*=>", r"lambda \1:", value)
        value = re.sub(r"\b(and|or|not|if)\(", r"ui_\1(", value)
        return re.sub(r"\blambda\(", "arm_lambda(", value)

    while index < len(source):
        if source[index] != "'":
            index += 1
            continue
        parts.append(functions(source[start:index]))
        start = index
        index += 1
        while index < len(source):
            if source[index] == "\\":
                index += 2
            elif source[index] == "'":
                index += 1
                break
            else:
                index += 1
        parts.append(source[start:index])
        start = index
    parts.append(functions(source[start:]))
    return ast.parse("".join(parts), mode="eval").body


def _binding_fallback(node: ast.expr) -> ast.Constant | None:
    known, value = _literal(node)
    if known and isinstance(value, (str, bool)):
        return ast.Constant(False if type(value) is bool else "")
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        if node.func.id in {
            "ui_and",
            "ui_or",
            "ui_not",
            "equals",
            "empty",
            "contains",
            "startsWith",
            "endsWith",
            "greater",
            "greaterOrEquals",
            "less",
            "lessOrEquals",
        }:
            return ast.Constant(False)
        if node.func.id in {"concat", "string", "toLower", "toUpper", "replace", "trim"}:
            return ast.Constant("")
        if node.func.id == "coalesce":
            for argument in reversed(node.args):
                fallback = _binding_fallback(argument)
                if fallback is not None:
                    return fallback
        if node.func.id == "ui_if":
            left, right = _binding_fallback(node.args[1]), _binding_fallback(node.args[2])
            if left is not None and right is not None and type(left.value) is type(right.value):
                return left
    return None


class _FalsyBindings(ast.NodeTransformer):
    """Preserve false/empty results from known one-row native bindings."""

    def visit_Call(self, node: ast.Call) -> ast.expr:
        node = self.generic_visit(node)
        if (
            isinstance(node.func, ast.Name)
            and node.func.id == "first"
            and len(node.args) == 1
            and isinstance(node.args[0], ast.Call)
            and isinstance(node.args[0].func, ast.Name)
            and node.args[0].func.id == "map"
            and isinstance(node.args[0].args[1], ast.Lambda)
            and _row_fields(node.args[0].args[0]) is not None
        ):
            fallback = _binding_fallback(node.args[0].args[1].body)
            if fallback is not None:
                return ast.Call(
                    func=ast.Name(id="coalesce", ctx=ast.Load()), args=[node, fallback], keywords=[]
                )
        return node


class _SafeSelections(ast.NodeTransformer):
    def __init__(self, context_fields: set[str] | None = None) -> None:
        self.context_fields = context_fields or set()

    def _member(self, value: ast.expr, key: str, default: Any) -> ast.expr:
        value = "coalesce(" + _render(value) + ", parse('{}'))"
        nullable = default is None
        if nullable:
            default = {"__azbrief_unavailable_member": True}
        fallback = json.dumps([{key: default}], separators=(",", ":"))
        null_field = json.dumps(key) + ":null"
        default_field = json.dumps(key) + ":" + json.dumps(default, separators=(",", ":"))
        rows = "parse(concat('[{\"object\":', string(" + value + "), '}]'))"
        selected = (
            "if(contains(member.object, " + _render(ast.Constant(key)) + "), "
            "parse(concat('[', replace(string(member.object), "
            + _render(ast.Constant(null_field))
            + ", "
            + _render(ast.Constant(default_field))
            + "), ']')), parse("
            + _render(ast.Constant(fallback))
            + "))"
        )
        result = "first(map(" + rows + ", (member) => first(" + selected + ")))." + key
        if nullable:
            values = (
                "map("
                + rows
                + ", (member) => map("
                + selected
                + ", (presentMember) => presentMember."
                + key
                + "))"
            )
            missing = _render(ast.Constant(json.dumps([[default]], separators=(",", ":"))))
            result = f"if(equals(string({values}), {missing}), parse('null'), {result})"
        return parse_expression("[" + result + "]")

    def visit_Call(self, node: ast.Call) -> ast.expr:
        if (
            isinstance(node.func, ast.Name)
            and node.func.id == "coalesce"
            and len(node.args) == 2
            and isinstance(node.args[0], ast.Attribute)
        ):
            known, default = _literal(node.args[1])
            if not known and (
                isinstance(node.args[1], ast.Call)
                and isinstance(node.args[1].func, ast.Name)
                and node.args[1].func.id == "parse"
                and len(node.args[1].args) == 1
                and isinstance(node.args[1].args[0], ast.Constant)
                and isinstance(node.args[1].args[0].value, str)
            ):
                default = json.loads(node.args[1].args[0].value)
                known = True
            if known and default is not None:
                member = node.args[0]
                if (
                    isinstance(member.value, ast.Call)
                    and isinstance(member.value.func, ast.Name)
                    and member.value.func.id in {"steps", "resourceGroup", "subscription"}
                ):
                    return self.generic_visit(node)
                return self._member(self.visit(member.value), member.attr, default)
        if isinstance(node.func, ast.Name) and node.func.id == "coalesce":
            arguments = []
            for argument in node.args:
                if isinstance(argument, ast.Attribute) and not (
                    isinstance(argument.value, ast.Call)
                    and isinstance(argument.value.func, ast.Name)
                    and argument.value.func.id in {"steps", "resourceGroup", "subscription"}
                ):
                    arguments.append(self._member(self.visit(argument.value), argument.attr, None))
                else:
                    arguments.append(self.visit(argument))
            node.args = arguments
            return node
        return self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> ast.expr:
        node = self.generic_visit(node)
        contextual = (
            isinstance(node.value, ast.Attribute) and node.value.attr in {"options", "resources"}
        ) or node.attr in self.context_fields
        intrinsic = (
            isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Name)
            and node.value.func.id in {"steps", "resourceGroup", "subscription"}
        )
        if node.attr.startswith("azbrief_v2_") or (contextual and not intrinsic):
            return self._member(node.value, node.attr, None)
        if node.attr in {"id", "subnetArmId"}:
            return self._member(node.value, node.attr, "")
        if node.attr == "tags":
            return self._member(node.value, node.attr, {})
        return node


def compile_expression(
    expression: str,
    stage: str,
    unavailable_controls: set[tuple[str, str]] | None = None,
    context_fields: set[str] | None = None,
) -> str | bool:
    """Prune unreachable phase branches while preserving every active predicate."""
    if stage not in {"foundation", "complete"}:
        raise ValueError("Choose an explicit infrastructure phase")
    node = parse_expression(expression)
    if unavailable_controls:
        node = _UnavailableControls(unavailable_controls).visit(node)
    for _ in range(4):
        before = ast.dump(node)
        node = _PhaseCompiler(stage).visit(node)
        if ast.dump(node) == before:
            break
    known, value = _literal(node)
    if known:
        if isinstance(value, (str, bool)):
            return value
        return "[parse('" + json.dumps(value, separators=(",", ":")) + "')]"
    return "[" + _render(_SafeSelections(context_fields).visit(_FalsyBindings().visit(node))) + "]"


def safe_member(expression: str, key: str, default: Any) -> str:
    """Read an optional native member without exception-driven fallback."""
    return (
        "[" + _render(_SafeSelections()._member(parse_expression(expression), key, default)) + "]"
    )
