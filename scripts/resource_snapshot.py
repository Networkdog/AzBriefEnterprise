"""Local-only Azure Resource Graph snapshot replay support.

This module lives under ``scripts/`` so neither the Hosted Agent package nor
the Container Apps image includes it. It reads azsnapshot NDJSON exports and
implements the bounded Resource Graph KQL subset used by AzBrief's analysis
tools without acquiring Azure credentials.
"""

from __future__ import annotations

import asyncio
import copy
import json
import math
import os
import re
import tempfile
from collections import Counter, OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable, Iterator, Optional


class SnapshotError(ValueError):
    """Raised when a snapshot or offline query is invalid."""


@dataclass(frozen=True)
class SnapshotMetadata:
    """Non-secret metadata used to identify one azsnapshot export."""

    version: str
    started_utc: str
    finished_utc: str
    subscription_count: int
    table_count: int
    warning_count: int
    error_count: int


def _casefold_key(mapping: dict[str, Any], key: str) -> Any:
    if key in mapping:
        return mapping[key]
    target = key.casefold()
    for candidate, value in mapping.items():
        if str(candidate).casefold() == target:
            return value
    return None


def _path_tokens(path: str) -> list[Any]:
    tokens: list[Any] = []
    position = 0
    path = path.strip()
    identifier = re.compile(r"[A-Za-z_@][A-Za-z0-9_@]*")
    while position < len(path):
        if path[position] == ".":
            position += 1
            continue
        if path[position] == "[":
            end = _matching_delimiter(path, position, "[", "]")
            raw = path[position + 1 : end].strip()
            if re.fullmatch(r"-?\d+", raw):
                tokens.append(int(raw))
            else:
                tokens.append(_parse_string(raw))
            position = end + 1
            continue
        match = identifier.match(path, position)
        if match is None:
            raise SnapshotError(f"InvalidQuery: unsupported field path {path!r}")
        tokens.append(match.group(0))
        position = match.end()
    return tokens


def _get_path(row: Any, path: str) -> Any:
    current = row
    for token in _path_tokens(path):
        if isinstance(token, int):
            if not isinstance(current, list):
                return None
            index = token if token >= 0 else len(current) + token
            if index < 0 or index >= len(current):
                return None
            current = current[index]
        elif isinstance(current, dict):
            current = _casefold_key(current, token)
        else:
            return None
    return current


def _matching_delimiter(text: str, start: int, opening: str, closing: str) -> int:
    depth = 0
    quote = ""
    position = start
    while position < len(text):
        char = text[position]
        if quote:
            if char == quote:
                if position + 1 < len(text) and text[position + 1] == quote:
                    position += 2
                    continue
                quote = ""
        elif char in ("'", '"'):
            quote = char
        elif char == opening:
            depth += 1
        elif char == closing:
            depth -= 1
            if depth == 0:
                return position
        position += 1
    raise SnapshotError(f"InvalidQuery: unclosed {opening!r} in {text!r}")


def _is_boundary(text: str, position: int) -> bool:
    return position < 0 or position >= len(text) or not (
        text[position].isalnum() or text[position] == "_"
    )


def _split_top_level(text: str, delimiter: str, *, keyword: bool = False) -> list[str]:
    parts: list[str] = []
    start = 0
    position = 0
    quote = ""
    round_depth = 0
    square_depth = 0
    lowered = text.casefold()
    needle = delimiter.casefold()
    while position < len(text):
        char = text[position]
        if quote:
            if char == quote:
                if position + 1 < len(text) and text[position + 1] == quote:
                    position += 2
                    continue
                quote = ""
            position += 1
            continue
        if char in ("'", '"'):
            quote = char
            position += 1
            continue
        if char == "(":
            round_depth += 1
        elif char == ")":
            round_depth -= 1
        elif char == "[":
            square_depth += 1
        elif char == "]":
            square_depth -= 1
        elif round_depth == 0 and square_depth == 0 and lowered.startswith(needle, position):
            before_ok = not keyword or _is_boundary(text, position - 1)
            after = position + len(delimiter)
            after_ok = not keyword or _is_boundary(text, after)
            if before_ok and after_ok:
                parts.append(text[start:position].strip())
                start = after
                position = after
                continue
        position += 1
    parts.append(text[start:].strip())
    return parts


def _strip_outer_parentheses(text: str) -> str:
    value = text.strip()
    while value.startswith("("):
        try:
            end = _matching_delimiter(value, 0, "(", ")")
        except SnapshotError:
            break
        if end != len(value) - 1:
            break
        value = value[1:-1].strip()
    return value


def _parse_string(value: str) -> str:
    value = value.strip()
    if len(value) < 2 or value[0] not in ("'", '"') or value[-1] != value[0]:
        raise SnapshotError(f"InvalidQuery: expected a quoted string, got {value!r}")
    quote = value[0]
    return value[1:-1].replace(quote * 2, quote)


def _split_assignment(text: str) -> Optional[tuple[str, str]]:
    position = 0
    quote = ""
    round_depth = 0
    square_depth = 0
    while position < len(text):
        char = text[position]
        if quote:
            if char == quote:
                if position + 1 < len(text) and text[position + 1] == quote:
                    position += 2
                    continue
                quote = ""
        elif char in ("'", '"'):
            quote = char
        elif char == "(":
            round_depth += 1
        elif char == ")":
            round_depth -= 1
        elif char == "[":
            square_depth += 1
        elif char == "]":
            square_depth -= 1
        elif char == "=" and round_depth == 0 and square_depth == 0:
            previous = text[position - 1] if position else ""
            following = text[position + 1] if position + 1 < len(text) else ""
            if previous not in ("!", "<", ">", "=") and following not in ("=", "~"):
                name = text[:position].strip()
                expression = text[position + 1 :].strip()
                if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
                    return name, expression
        position += 1
    return None


def _find_operator(text: str, operator: str) -> Optional[int]:
    lowered = text.casefold()
    needle = operator.casefold()
    position = 0
    quote = ""
    round_depth = 0
    square_depth = 0
    while position <= len(text) - len(operator):
        char = text[position]
        if quote:
            if char == quote:
                if position + 1 < len(text) and text[position + 1] == quote:
                    position += 2
                    continue
                quote = ""
            position += 1
            continue
        if char in ("'", '"'):
            quote = char
        elif char == "(":
            round_depth += 1
        elif char == ")":
            round_depth -= 1
        elif char == "[":
            square_depth += 1
        elif char == "]":
            square_depth -= 1
        elif round_depth == 0 and square_depth == 0 and lowered.startswith(needle, position):
            wordish = operator[0].isalnum() or operator[-1].isalnum()
            before_ok = not wordish or _is_boundary(text, position - 1)
            after_ok = not wordish or _is_boundary(text, position + len(operator))
            if before_ok and after_ok:
                return position
        position += 1
    return None


def _parse_call(text: str) -> Optional[tuple[str, list[str], str]]:
    match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*\(", text)
    if match is None:
        return None
    opening = text.find("(", match.start())
    closing = _matching_delimiter(text, opening, "(", ")")
    arguments = _split_top_level(text[opening + 1 : closing], ",")
    if len(arguments) == 1 and not arguments[0]:
        arguments = []
    return match.group(1).casefold(), arguments, text[closing + 1 :].strip()


def _json_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value)


def _to_number(value: Any) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


def _eval_function(name: str, arguments: list[str], row: dict[str, Any]) -> Any:
    values = [_eval_value(argument, row) for argument in arguments]
    if name == "tostring":
        return _json_text(values[0] if values else None)
    if name == "tolower":
        return _json_text(values[0] if values else None).lower()
    if name == "toupper":
        return _json_text(values[0] if values else None).upper()
    if name in ("toint", "tolong"):
        number = _to_number(values[0] if values else None)
        return int(number) if number is not None else None
    if name in ("todouble", "toreal"):
        return _to_number(values[0] if values else None)
    if name == "tobool":
        value = values[0] if values else None
        if isinstance(value, bool):
            return value
        return str(value).casefold() == "true" if value is not None else None
    if name == "todatetime":
        value = values[0] if values else None
        if isinstance(value, (int, float)):
            seconds = float(value) / 1000 if abs(float(value)) > 10_000_000_000 else float(value)
            return datetime.fromtimestamp(seconds, tz=timezone.utc)
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return value
        return value
    if name == "array_length":
        return len(values[0]) if values and isinstance(values[0], list) else 0
    if name == "bag_keys":
        return list(values[0]) if values and isinstance(values[0], dict) else []
    if name == "split":
        source = _json_text(values[0] if values else None)
        separator = _json_text(values[1] if len(values) > 1 else "")
        return source.split(separator)
    if name == "coalesce":
        return next((value for value in values if value not in (None, "")), None)
    if name == "strcat":
        return "".join(_json_text(value) for value in values)
    if name == "iff":
        if len(arguments) != 3:
            raise SnapshotError("InvalidQuery: iff() requires three arguments")
        return _eval_value(arguments[1], row) if _eval_expression(arguments[0], row) else _eval_value(arguments[2], row)
    if name == "case":
        if len(arguments) < 3:
            raise SnapshotError("InvalidQuery: case() requires condition/value pairs and a default")
        for index in range(0, len(arguments) - 1, 2):
            if _eval_expression(arguments[index], row):
                return _eval_value(arguments[index + 1], row)
        return _eval_value(arguments[-1], row)
    if name in ("isnull", "isnotnull", "isempty", "isnotempty"):
        value = values[0] if values else None
        if name == "isnull":
            return value is None
        if name == "isnotnull":
            return value is not None
        if name == "isempty":
            return value is None or value == "" or value == [] or value == {}
        return value is not None and value != "" and value != [] and value != {}
    if name == "has_any":
        source = _json_text(values[0] if values else None).casefold()
        candidates = values[1] if len(values) > 1 else []
        if not isinstance(candidates, list):
            candidates = values[1:]
        return any(_json_text(candidate).casefold() in source for candidate in candidates)
    if name == "dynamic":
        if not arguments:
            return None
        raw = arguments[0].strip()
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise SnapshotError("InvalidQuery: dynamic() requires valid JSON") from exc
    raise SnapshotError(f"InvalidQuery: unsupported offline KQL function '{name}'")


def _apply_postfix(value: Any, postfix: str) -> Any:
    remaining = postfix.strip()
    while remaining:
        if not remaining.startswith("["):
            raise SnapshotError(f"InvalidQuery: unsupported expression suffix {remaining!r}")
        end = _matching_delimiter(remaining, 0, "[", "]")
        raw = remaining[1:end].strip()
        key: Any = int(raw) if re.fullmatch(r"-?\d+", raw) else _parse_string(raw)
        if isinstance(key, int) and isinstance(value, list):
            index = key if key >= 0 else len(value) + key
            value = value[index] if 0 <= index < len(value) else None
        elif isinstance(value, dict):
            value = _casefold_key(value, str(key))
        else:
            value = None
        remaining = remaining[end + 1 :].strip()
    return value


def _eval_value(expression: str, row: dict[str, Any]) -> Any:
    text = _strip_outer_parentheses(expression)
    if not text:
        return None
    lowered = text.casefold()
    if text[0] in ("'", '"') and text[-1] == text[0]:
        return _parse_string(text)
    if lowered in ("true", "false"):
        return lowered == "true"
    if lowered in ("null", "dynamic(null)"):
        return None
    if re.fullmatch(r"-?\d+", text):
        return int(text)
    if re.fullmatch(r"-?(?:\d+\.\d*|\d*\.\d+)", text):
        return float(text)
    call = _parse_call(text)
    if call is not None:
        name, arguments, postfix = call
        return _apply_postfix(_eval_function(name, arguments, row), postfix)
    if text.startswith("(") and text.endswith(")"):
        return [_eval_value(item, row) for item in _split_top_level(text[1:-1], ",")]
    if re.fullmatch(
        r"[A-Za-z_@][A-Za-z0-9_@]*(?:\.[A-Za-z_@][A-Za-z0-9_@]*|\[(?:-?\d+|'[^']*'|\"[^\"]*\")\])*",
        text,
    ):
        return _get_path(row, text)
    raise SnapshotError(f"InvalidQuery: unsupported offline KQL expression {text!r}")


def _equal(left: Any, right: Any, *, insensitive: bool) -> bool:
    if insensitive:
        return _json_text(left).casefold() == _json_text(right).casefold()
    return left == right


def _compare(left: Any, right: Any, operator: str) -> bool:
    insensitive = operator.endswith("~") or operator in (
        "contains",
        "!contains",
        "has",
        "!has",
        "startswith",
        "endswith",
    )
    normalized = operator.rstrip("~").casefold()
    if normalized in ("==", "="):
        return _equal(left, right, insensitive=insensitive)
    if normalized in ("!=", "!"):
        return not _equal(left, right, insensitive=insensitive)
    if normalized in ("in", "!in"):
        candidates = right if isinstance(right, list) else [right]
        found = any(_equal(left, candidate, insensitive=insensitive) for candidate in candidates)
        return not found if normalized == "!in" else found
    left_text = _json_text(left)
    right_text = _json_text(right)
    if insensitive:
        left_text = left_text.casefold()
        right_text = right_text.casefold()
    if normalized in ("contains", "!contains"):
        found = right_text in left_text
        return not found if normalized == "!contains" else found
    if normalized in ("has", "!has"):
        tokens = re.findall(r"[A-Za-z0-9_]+", left_text)
        found = right_text in tokens
        return not found if normalized == "!has" else found
    if normalized == "startswith":
        return left_text.startswith(right_text)
    if normalized == "endswith":
        return left_text.endswith(right_text)
    if normalized == "matches regex":
        try:
            return re.search(right_text, left_text) is not None
        except re.error as exc:
            raise SnapshotError(f"InvalidQuery: invalid regex {right_text!r}") from exc
    left_number = _to_number(left)
    right_number = _to_number(right)
    comparable_left: Any = left_number if left_number is not None and right_number is not None else left_text
    comparable_right: Any = right_number if left_number is not None and right_number is not None else right_text
    if normalized == ">":
        return comparable_left > comparable_right
    if normalized == "<":
        return comparable_left < comparable_right
    if normalized == ">=":
        return comparable_left >= comparable_right
    if normalized == "<=":
        return comparable_left <= comparable_right
    raise SnapshotError(f"InvalidQuery: unsupported comparison operator {operator!r}")


_COMPARISON_OPERATORS = (
    "matches regex",
    "!contains",
    "startswith",
    "endswith",
    "contains",
    "!in~",
    "in~",
    "!in",
    "in",
    "!has",
    "has",
    "!=",
    "!~",
    "==",
    "=~",
    ">=",
    "<=",
    ">",
    "<",
)


def _eval_expression(expression: str, row: dict[str, Any]) -> bool:
    text = _strip_outer_parentheses(expression)
    or_parts = _split_top_level(text, "or", keyword=True)
    if len(or_parts) > 1:
        return any(_eval_expression(part, row) for part in or_parts)
    and_parts = _split_top_level(text, "and", keyword=True)
    if len(and_parts) > 1:
        return all(_eval_expression(part, row) for part in and_parts)
    if text.casefold().startswith("not "):
        return not _eval_expression(text[4:], row)
    for operator in _COMPARISON_OPERATORS:
        position = _find_operator(text, operator)
        if position is None:
            continue
        left = _eval_value(text[:position], row)
        right_text = text[position + len(operator) :].strip()
        if operator.casefold().rstrip("~") in ("in", "!in"):
            inner = _strip_outer_parentheses(right_text)
            right = [_eval_value(item, row) for item in _split_top_level(inner, ",")]
        else:
            right = _eval_value(right_text, row)
        return _compare(left, right, operator.casefold())
    return bool(_eval_value(text, row))


def _field_name(expression: str) -> str:
    text = expression.strip()
    match = re.search(r"(?:^|\.)([A-Za-z_@][A-Za-z0-9_@]*)\s*$", text)
    return match.group(1) if match else text


def _extend_rows(rows: Iterable[dict[str, Any]], specification: str) -> Iterator[dict[str, Any]]:
    assignments = []
    for item in _split_top_level(specification, ","):
        assignment = _split_assignment(item)
        if assignment is None:
            raise SnapshotError(f"InvalidQuery: extend item requires alias = expression: {item!r}")
        assignments.append(assignment)
    for source in rows:
        row = dict(source)
        for name, expression in assignments:
            row[name] = _eval_value(expression, row)
        yield row


def _project_rows(rows: Iterable[dict[str, Any]], specification: str) -> Iterator[dict[str, Any]]:
    projections = []
    for item in _split_top_level(specification, ","):
        assignment = _split_assignment(item)
        projections.append(assignment or (_field_name(item), item))
    for row in rows:
        yield {name: _eval_value(expression, row) for name, expression in projections}


def _where_rows(rows: Iterable[dict[str, Any]], expression: str) -> Iterator[dict[str, Any]]:
    for row in rows:
        if _eval_expression(expression, row):
            yield row


def _mv_expand_rows(rows: Iterable[dict[str, Any]], specification: str) -> Iterator[dict[str, Any]]:
    text = re.sub(r"\s+to\s+typeof\s*\([^)]*\)\s*$", "", specification, flags=re.I)
    assignment = _split_assignment(text)
    name, expression = assignment or (_field_name(text), text)
    for source in rows:
        value = _eval_value(expression, source)
        if not isinstance(value, list):
            continue
        for item in value:
            row = dict(source)
            row[name] = item
            yield row


def _hashable(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _summarize(rows: Iterable[dict[str, Any]], specification: str) -> list[dict[str, Any]]:
    by_parts = _split_top_level(specification, "by", keyword=True)
    if len(by_parts) > 2:
        raise SnapshotError("InvalidQuery: summarize supports a single by clause")
    aggregate_text = by_parts[0]
    group_specs = []
    if len(by_parts) == 2:
        for item in _split_top_level(by_parts[1], ","):
            assignment = _split_assignment(item)
            group_specs.append(assignment or (_field_name(item), item))

    aggregate_specs = []
    for item in _split_top_level(aggregate_text, ","):
        assignment = _split_assignment(item)
        output_name, expression = assignment or ("", item)
        call = _parse_call(expression)
        if call is None or call[2]:
            raise SnapshotError(f"InvalidQuery: unsupported summarize expression {item!r}")
        function, arguments, _ = call
        if not output_name:
            output_name = "count_" if function == "count" else f"{function}_"
        if function not in ("count", "countif", "dcount", "sum", "min", "max", "avg"):
            raise SnapshotError(f"InvalidQuery: unsupported summarize function '{function}'")
        aggregate_specs.append((output_name, function, arguments))

    groups: OrderedDict[tuple[Any, ...], dict[str, Any]] = OrderedDict()
    for row in rows:
        raw_group = tuple(_eval_value(expression, row) for _, expression in group_specs)
        key = tuple(_hashable(value) for value in raw_group)
        state = groups.setdefault(
            key,
            {
                "group": raw_group,
                "values": {name: [] for name, _, _ in aggregate_specs},
                "count": 0,
            },
        )
        state["count"] += 1
        for name, function, arguments in aggregate_specs:
            if function == "count":
                continue
            if function == "countif":
                state["values"][name].append(
                    bool(arguments and _eval_expression(arguments[0], row))
                )
            else:
                value = _eval_value(arguments[0], row) if arguments else None
                state["values"][name].append(value)

    if not groups and not group_specs:
        groups[()] = {
            "group": (),
            "values": {name: [] for name, _, _ in aggregate_specs},
            "count": 0,
        }

    output = []
    for state in groups.values():
        result = {
            name: value for (name, _), value in zip(group_specs, state["group"])
        }
        for name, function, _ in aggregate_specs:
            values = state["values"][name]
            if function == "count":
                result[name] = state["count"]
            elif function == "countif":
                result[name] = sum(1 for value in values if value)
            elif function == "dcount":
                result[name] = len({_hashable(value) for value in values if value is not None})
            else:
                numeric = [number for value in values if (number := _to_number(value)) is not None]
                if function == "sum":
                    result[name] = sum(numeric)
                elif function == "min":
                    result[name] = min(values) if values else None
                elif function == "max":
                    result[name] = max(values) if values else None
                elif function == "avg":
                    result[name] = sum(numeric) / len(numeric) if numeric else None
        output.append(result)
    return output


def _sort_value(value: Any) -> tuple[bool, Any]:
    if value is None:
        return True, ""
    if isinstance(value, datetime):
        return False, value.timestamp()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return False, value
    return False, _json_text(value).casefold()


def _order_rows(rows: Iterable[dict[str, Any]], specification: str) -> list[dict[str, Any]]:
    ordered = list(rows)
    keys = []
    for item in _split_top_level(specification, ","):
        match = re.match(
            r"^(.*?)(?:\s+(asc|desc))?(?:\s+nulls\s+(?:first|last))?$",
            item.strip(),
            re.I,
        )
        if match is None:
            raise SnapshotError(f"InvalidQuery: invalid order by item {item!r}")
        keys.append((match.group(1).strip(), (match.group(2) or "asc").casefold()))
    for expression, direction in reversed(keys):
        ordered.sort(
            key=lambda row, expr=expression: _sort_value(_eval_value(expr, row)),
            reverse=direction == "desc",
        )
    return ordered


class SnapshotKqlEngine:
    """Execute a safe, bounded Resource Graph KQL subset over NDJSON tables."""

    def __init__(self, root: Path, *, max_result_rows: int = 5000) -> None:
        self.root = root
        self.max_result_rows = max_result_rows
        self.tables = {
            path.stem.casefold(): path for path in sorted(root.glob("*.ndjson")) if path.is_file()
        }

    def _iter_rows(
        self,
        table: str,
        subscriptions: Optional[list[str]] = None,
    ) -> Iterator[dict[str, Any]]:
        path = self.tables.get(table.casefold())
        if path is None:
            raise SnapshotError(
                f"InvalidQuery: table '{table}' is not present in this snapshot. "
                f"Available tables: {', '.join(sorted(self.tables))}"
            )
        allowed = {value.casefold() for value in subscriptions or []}
        with path.open("r", encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, start=1):
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise SnapshotError(
                        f"Invalid snapshot JSON in {path.name} at line {line_number}"
                    ) from exc
                if not isinstance(row, dict):
                    continue
                candidates = [row]
                if table.casefold() == "advisorresources":
                    wrapped = _get_path(row, "properties.value")
                    if isinstance(wrapped, list):
                        candidates = []
                        for item in wrapped:
                            if not isinstance(item, dict):
                                continue
                            candidate = dict(item)
                            candidate.setdefault("subscriptionId", row.get("subscriptionId"))
                            candidate.setdefault("tenantId", row.get("tenantId"))
                            candidates.append(candidate)
                for candidate in candidates:
                    subscription_id = candidate.get("subscriptionId")
                    if allowed and (
                        not isinstance(subscription_id, str)
                        or subscription_id.casefold() not in allowed
                    ):
                        continue
                    yield candidate

    def execute(
        self,
        query: str,
        subscriptions: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        cleaned = re.sub(r"(?m)^\s*//.*$", "", query).strip().rstrip(";")
        if re.search(r"\b(?:join|union|let|datatable|toscalar|render)\b", cleaned, re.I):
            raise SnapshotError(
                "InvalidQuery: offline snapshot mode does not support join, union, let, "
                "datatable, toscalar, or render"
            )
        clauses = _split_top_level(cleaned, "|")
        if not clauses or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", clauses[0]):
            raise SnapshotError("InvalidQuery: query must start with an exported table name")
        rows: Iterable[dict[str, Any]] = self._iter_rows(clauses[0], subscriptions)

        for clause in clauses[1:]:
            clause = clause.strip()
            if not clause:
                continue
            lowered = clause.casefold()
            if lowered.startswith("where "):
                rows = _where_rows(rows, clause[6:].strip())
            elif lowered.startswith("extend "):
                rows = _extend_rows(rows, clause[7:].strip())
            elif lowered.startswith("project-away "):
                fields = [item.strip() for item in _split_top_level(clause[13:], ",")]
                source = rows
                rows = (
                    {key: value for key, value in row.items() if key not in fields}
                    for row in source
                )
            elif lowered.startswith("project-rename "):
                renames = []
                for item in _split_top_level(clause[15:], ","):
                    assignment = _split_assignment(item)
                    if assignment is None:
                        raise SnapshotError(
                            f"InvalidQuery: project-rename requires new = old: {item!r}"
                        )
                    renames.append(assignment)
                source = rows

                def _rename(iterator: Iterable[dict[str, Any]]) -> Iterator[dict[str, Any]]:
                    for source_row in iterator:
                        row = dict(source_row)
                        for new_name, old_name in renames:
                            row[new_name] = _eval_value(old_name, row)
                            row.pop(old_name, None)
                        yield row

                rows = _rename(source)
            elif lowered.startswith("project "):
                rows = _project_rows(rows, clause[8:].strip())
            elif lowered.startswith("summarize "):
                rows = _summarize(rows, clause[10:].strip())
            elif lowered.startswith("order by "):
                rows = _order_rows(rows, clause[9:].strip())
            elif lowered.startswith("sort by "):
                rows = _order_rows(rows, clause[8:].strip())
            elif lowered.startswith("take ") or lowered.startswith("limit "):
                match = re.fullmatch(r"(?:take|limit)\s+(\d+)", clause, re.I)
                if match is None:
                    raise SnapshotError(f"InvalidQuery: invalid row limit {clause!r}")
                limit = int(match.group(1))
                source = rows
                rows = (row for index, row in enumerate(source) if index < limit)
            elif lowered.startswith("top "):
                match = re.match(r"^top\s+(\d+)\s+by\s+(.+)$", clause, re.I | re.S)
                if match is None:
                    raise SnapshotError(f"InvalidQuery: invalid top clause {clause!r}")
                rows = _order_rows(rows, match.group(2))[: int(match.group(1))]
            elif lowered.startswith("distinct "):
                fields = [item.strip() for item in _split_top_level(clause[9:], ",")]
                seen = set()
                distinct_rows = []
                for row in rows:
                    projected = {field: _eval_value(field, row) for field in fields}
                    key = tuple(_hashable(value) for value in projected.values())
                    if key not in seen:
                        seen.add(key)
                        distinct_rows.append(projected)
                rows = distinct_rows
            elif lowered.startswith("mv-expand "):
                rows = _mv_expand_rows(rows, clause[10:].strip())
            elif lowered in ("count", "count()"):
                rows = [{"Count": sum(1 for _ in rows)}]
            else:
                raise SnapshotError(
                    f"InvalidQuery: unsupported offline KQL operator in clause {clause!r}"
                )

        data = []
        total_records = 0
        for row in rows:
            total_records += 1
            if len(data) < self.max_result_rows:
                data.append(row)
        return {
            "data": data,
            "count": len(data),
            "total_records": total_records,
            "result_truncated": total_records > len(data),
            "source": "offline_ndjson_snapshot",
        }


class SnapshotResourceGraphService:
    """ResourceGraphService-compatible adapter backed only by NDJSON files."""

    def __init__(self, snapshot_dir: str | Path, *, max_result_rows: int = 5000) -> None:
        self.root = Path(snapshot_dir).expanduser().resolve()
        manifest_path = self.root / "manifest.json"
        if not manifest_path.is_file():
            raise SnapshotError(f"Snapshot manifest not found: {manifest_path}")
        try:
            self.manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SnapshotError(f"Invalid snapshot manifest: {manifest_path}") from exc
        if self.manifest.get("tool") != "azsnapshot":
            raise SnapshotError("Snapshot manifest tool must be 'azsnapshot'")
        version = str(self.manifest.get("version", ""))
        if not version.startswith("1."):
            raise SnapshotError(f"Unsupported azsnapshot version: {version or '(missing)'}")
        self.engine = SnapshotKqlEngine(self.root, max_result_rows=max_result_rows)
        if "resources" not in self.engine.tables or "resourcecontainers" not in self.engine.tables:
            raise SnapshotError("Snapshot must include resources.ndjson and resourcecontainers.ndjson")
        subscriptions = self.manifest.get("subscriptions") or []
        self._subscription_name_map = {
            str(item.get("subscriptionId")): str(item.get("displayName"))
            for item in subscriptions
            if isinstance(item, dict) and item.get("subscriptionId") and item.get("displayName")
        }
        self._query_cache: OrderedDict[str, dict[str, Any]] = OrderedDict()

    @property
    def metadata(self) -> SnapshotMetadata:
        return SnapshotMetadata(
            version=str(self.manifest.get("version", "")),
            started_utc=str(self.manifest.get("startedUtc", "")),
            finished_utc=str(self.manifest.get("finishedUtc", "")),
            subscription_count=len(self.manifest.get("subscriptions") or []),
            table_count=len(self.engine.tables),
            warning_count=len(self.manifest.get("warnings") or []),
            error_count=len(self.manifest.get("errors") or []),
        )

    @property
    def table_names(self) -> tuple[str, ...]:
        return tuple(sorted(self.engine.tables))

    def prompt_context(self) -> str:
        captured = self.metadata.finished_utc or self.metadata.started_utc or "unknown time"
        return (
            "LOCAL TEST MODE: tenant evidence comes only from an immutable azsnapshot NDJSON "
            f"export captured at {captured}. Treat every resource, health, policy, and Advisor "
            "claim as historical at that timestamp, never as current state. Do not request or "
            "claim live Azure MCP, ARM, Cost Management, Billing, Activity Log, or Log Analytics "
            "evidence. Use only the snapshot-backed tools and make unavailable evidence an "
            "explicit gap. Available exported tables: "
            + ", ".join(self.table_names)
            + "."
        )

    async def query_resources(
        self,
        query: str,
        subscriptions: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        key = json.dumps([query.strip(), sorted(subscriptions or [])], ensure_ascii=True)
        cached = self._query_cache.get(key)
        if cached is not None:
            self._query_cache.move_to_end(key)
            return copy.deepcopy(cached)
        result = await asyncio.to_thread(self.engine.execute, query, subscriptions)
        try:
            encoded_size = len(json.dumps(result, ensure_ascii=False, default=str))
        except (TypeError, ValueError):
            encoded_size = math.inf
        if encoded_size <= 4_000_000:
            self._query_cache[key] = copy.deepcopy(result)
            self._query_cache.move_to_end(key)
            while len(self._query_cache) > 24:
                self._query_cache.popitem(last=False)
        return result

    async def get_resource_types_summary(self) -> dict[str, Any]:
        return await self.query_resources(
            "Resources | summarize count() by type | order by count_ desc"
        )

    async def find_related_resources(self, service_keywords: list[str]) -> dict[str, Any]:
        from src.services.resource_graph import ResourceGraphQueryBuilder

        return await self.query_resources(
            ResourceGraphQueryBuilder.find_related_resources(service_keywords)
        )

    def get_subscription_name(self, subscription_id: str) -> str:
        return self._subscription_name_map.get(subscription_id, subscription_id)

    def get_subscription_name_map(self) -> dict[str, str]:
        return dict(self._subscription_name_map)

    def enrich_subscription_names(self, data: list[dict[str, Any]]) -> list[dict[str, Any]]:
        for row in data:
            subscription_id = row.get("subscriptionId")
            if isinstance(subscription_id, str):
                row["subscriptionName"] = self.get_subscription_name(subscription_id)
        return data

    def validate(self, *, full: bool = False) -> dict[str, Any]:
        expected = self.manifest.get("counts") or {}
        missing = [name for name in expected if name.casefold() not in self.engine.tables]
        malformed: dict[str, int] = {}
        mismatched: dict[str, dict[str, int]] = {}
        checked: dict[str, int] = {}
        if full:
            for name, path in self.engine.tables.items():
                logical_count = 0
                bad = 0
                with path.open("r", encoding="utf-8") as stream:
                    for line in stream:
                        if not line.strip():
                            continue
                        try:
                            row = json.loads(line)
                        except json.JSONDecodeError:
                            bad += 1
                            continue
                        if name == "advisorresources" and isinstance(
                            _get_path(row, "properties.value"), list
                        ):
                            logical_count += len(_get_path(row, "properties.value"))
                        else:
                            logical_count += 1
                checked[name] = logical_count
                if bad:
                    malformed[name] = bad
                expected_count = expected.get(name)
                if isinstance(expected_count, int) and logical_count != expected_count:
                    mismatched[name] = {
                        "expected": expected_count,
                        "actual": logical_count,
                    }
        else:
            for name, path in self.engine.tables.items():
                try:
                    with path.open("r", encoding="utf-8") as stream:
                        first = next((line for line in stream if line.strip()), "")
                    if first:
                        json.loads(first)
                        checked[name] = 1
                except (OSError, json.JSONDecodeError):
                    malformed[name] = 1
        return {
            "ok": not missing and not malformed and not mismatched and self.metadata.error_count == 0,
            "mode": "full" if full else "quick",
            "metadata": self.metadata,
            "checked": checked,
            "missing_tables": missing,
            "malformed_tables": malformed,
            "count_mismatches": mismatched,
        }

    async def get_policy_compliance_summary(self, resource_type: str = "") -> str:
        """Aggregate the exported per-subscription Policy Insights summaries."""
        path = self.engine.tables.get("policy_compliance_summary")
        if path is None:
            return "Snapshot gap: policy_compliance_summary.ndjson was not exported."

        def _aggregate() -> str:
            subscriptions = 0
            non_compliant_resources = 0
            non_compliant_policies = 0
            compliance_states: Counter[str] = Counter()
            with path.open("r", encoding="utf-8") as stream:
                for line_number, line in enumerate(stream, start=1):
                    if not line.strip():
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError as exc:
                        raise SnapshotError(
                            f"Invalid snapshot JSON in {path.name} at line {line_number}"
                        ) from exc
                    subscriptions += 1
                    results = row.get("results") if isinstance(row, dict) else None
                    if not isinstance(results, dict):
                        continue
                    non_compliant_resources += int(results.get("nonCompliantResources") or 0)
                    non_compliant_policies += int(results.get("nonCompliantPolicies") or 0)
                    for detail in results.get("resourceDetails") or []:
                        if not isinstance(detail, dict):
                            continue
                        state = str(detail.get("complianceState") or "Unknown")
                        compliance_states[state] += int(detail.get("count") or 0)
            lines = [
                "## Policy compliance snapshot",
                f"- Subscriptions represented: {subscriptions}",
                f"- Non-compliant resources: {non_compliant_resources}",
                f"- Non-compliant policies: {non_compliant_policies}",
            ]
            for state, count in sorted(compliance_states.items()):
                lines.append(f"- Resource state {state}: {count}")
            if resource_type:
                lines.append(
                    "- Gap: the exported policy summary is subscription-level and cannot "
                    f"isolate resource type {resource_type}."
                )
            return "\n".join(lines)

        return await asyncio.to_thread(_aggregate)


SNAPSHOT_RESOURCE_GRAPH_TOOLS = frozenset(
    {
        "query_azure_resources",
        "get_resource_type_summary",
        "find_related_resources",
        "get_service_resource_details",
        "get_security_posture",
        "explore_resource_schema",
        "get_resource_configurations",
        "get_resource_dependencies",
        "get_service_health",
    }
)

SNAPSHOT_DOCUMENTATION_TOOLS = frozenset(
    {
        "search_resource_graph_docs",
        "search_azure_docs",
        "get_service_documentation",
        "search_update_related_docs",
        "query_tool_result",
    }
)

SNAPSHOT_GAP_TOOL_NAMES = frozenset(
    {
        "get_cost_by_resource_type",
        "get_cost_by_service",
        "list_billing_accounts",
        "list_billing_profiles",
        "query_log_analytics",
        "get_recent_errors",
        "get_activity_log_summary",
        "call_azure_rest_api",
    }
)


def _escape_kql_literal(value: str) -> str:
    return str(value).replace("'", "''")


def build_snapshot_tools(service: SnapshotResourceGraphService) -> list[Any]:
    """Build a no-credential tool registry for the local snapshot harness."""
    from langchain_core.tools import BaseTool
    from pydantic import BaseModel, ConfigDict, PrivateAttr

    from src.agent import tools as tools_module

    class SnapshotToolInput(BaseModel):
        model_config = ConfigDict(extra="allow")

    class SnapshotTool(BaseTool):
        name: str
        description: str
        args_schema: type[BaseModel] = SnapshotToolInput
        _handler: Callable[..., Awaitable[str]] = PrivateAttr()

        def __init__(
            self,
            *,
            name: str,
            description: str,
            handler: Callable[..., Awaitable[str]],
        ) -> None:
            super().__init__(name=name, description=description)
            self._handler = handler

        def _run(self, **kwargs: Any) -> str:
            raise NotImplementedError("Use async version")

        async def _arun(self, **kwargs: Any) -> str:
            return await self._handler(**kwargs)

    original_tools = tools_module.get_all_tools()
    selected = []
    for tool in original_tools:
        if tool.name in SNAPSHOT_RESOURCE_GRAPH_TOOLS:
            tool._service = service
            selected.append(tool)
        elif tool.name in SNAPSHOT_DOCUMENTATION_TOOLS:
            selected.append(tool)

    async def _resource_health(**kwargs: Any) -> str:
        resource_type = str(kwargs.get("resource_type") or "").strip()
        query = """
        HealthResources
        | where type =~ 'microsoft.resourcehealth/availabilitystatuses'
        """
        if resource_type:
            query += (
                "\n| where properties.targetResourceType =~ "
                f"'{_escape_kql_literal(resource_type)}'"
            )
        query += """
        | extend availabilityState = tostring(properties.availabilityState)
        | extend previousAvailabilityState = tostring(properties.previousAvailabilityState)
        | extend targetResourceId = tostring(properties.targetResourceId)
        | extend targetResourceType = tostring(properties.targetResourceType)
        | project name, subscriptionId, resourceGroup, availabilityState,
                  previousAvailabilityState, targetResourceId, targetResourceType
        | order by availabilityState asc, name asc
        | limit 200
        """
        result = await service.query_resources(query)
        return tools_module.format_rg_result(result, "Resource Health snapshot")

    async def _service_health_events(**kwargs: Any) -> str:
        service_name = str(kwargs.get("service_name") or "").strip()
        query = """
        ServiceHealthResources
        | where type =~ 'microsoft.resourcehealth/events'
        | extend eventType = tostring(properties.EventType)
        | extend status = tostring(properties.Status)
        | extend title = tostring(properties.Title)
        | extend summary = tostring(properties.Summary)
        | extend impact = tostring(properties.Impact)
        | extend impactStartTime = todatetime(properties.ImpactStartTime)
        """
        if service_name:
            query += f"\n| where impact contains '{_escape_kql_literal(service_name)}'"
        query += """
        | project name, eventType, status, title, summary, impactStartTime, impact
        | order by impactStartTime desc
        | limit 100
        """
        result = await service.query_resources(query)
        return tools_module.format_rg_result(result, "Service Health snapshot")

    async def _advisor(**kwargs: Any) -> str:
        category = str(kwargs.get("category") or "").strip()
        impact = str(kwargs.get("impact") or "").strip()
        query = """
        AdvisorResources
        | extend category = tostring(properties.category)
        | extend impact = tostring(properties.impact)
        | extend impactedType = tostring(properties.impactedType)
        | extend impactedValue = tostring(properties.impactedValue)
        | extend problem = tostring(properties.shortDescription.problem)
        | extend solution = tostring(properties.shortDescription.solution)
        """
        if category:
            query += f"\n| where category =~ '{_escape_kql_literal(category)}'"
        if impact:
            query += f"\n| where impact =~ '{_escape_kql_literal(impact)}'"
        query += """
        | project id, subscriptionId, category, impact, impactedType, impactedValue,
                  problem, solution
        | order by impact asc, impactedType asc
        | limit 100
        """
        result = await service.query_resources(query)
        return tools_module.format_rg_result(result, "Advisor snapshot")

    async def _policy(**kwargs: Any) -> str:
        return await service.get_policy_compliance_summary(
            resource_type=str(kwargs.get("resource_type") or "")
        )

    async def _region_gap(**kwargs: Any) -> str:
        namespace = str(kwargs.get("provider_namespace") or "unknown")
        resource_type = str(kwargs.get("resource_type") or "")
        subject = f"{namespace}/{resource_type}".rstrip("/")
        return (
            "Offline snapshot gap: resource_providers.ndjson records provider registration and "
            f"resource-type names for {subject}, but this export does not preserve per-region "
            "location metadata. Region deployability and feature rollout were not queried live."
        )

    async def _not_exported_factory(tool_name: str, **kwargs: Any) -> str:
        del kwargs
        return (
            f"Offline snapshot gap: {tool_name} requires live or time-series Azure data that this "
            "azsnapshot export does not contain. No live Azure call was made."
        )

    selected.extend(
        [
            SnapshotTool(
                name="get_resource_health",
                description="Reads historical Resource Health rows from healthresources.ndjson.",
                handler=_resource_health,
            ),
            SnapshotTool(
                name="get_service_health_events",
                description=(
                    "Reads historical Service Health events from servicehealthresources.ndjson."
                ),
                handler=_service_health_events,
            ),
            SnapshotTool(
                name="get_advisor_recommendations",
                description="Reads historical Advisor rows from advisorresources.ndjson.",
                handler=_advisor,
            ),
            SnapshotTool(
                name="get_policy_compliance",
                description=(
                    "Aggregates historical subscription-level Policy Insights summaries from "
                    "policy_compliance_summary.ndjson."
                ),
                handler=_policy,
            ),
            SnapshotTool(
                name="get_service_region_availability",
                description=(
                    "Returns an explicit snapshot gap because the export lacks provider location "
                    "metadata and never calls ARM."
                ),
                handler=_region_gap,
            ),
        ]
    )
    for tool_name in sorted(SNAPSHOT_GAP_TOOL_NAMES):
        selected.append(
            SnapshotTool(
                name=tool_name,
                description=(
                    "Returns an explicit offline snapshot gap. This tool never calls Azure."
                ),
                handler=lambda _name=tool_name, **kwargs: _not_exported_factory(
                    _name, **kwargs
                ),
            )
        )
    return selected


def build_snapshot_specialist_node(
    settings: Any,
    tools: list[Any],
    service: SnapshotResourceGraphService,
) -> Callable[[dict[str, Any]], Awaitable[dict[str, str]]]:
    """Run only the Resource Graph Prompt Agent against snapshot-backed tools."""
    from src.agent import foundry_backend

    roster = {
        spec.role: spec for spec in settings.get_foundry_specialist_agents()
    }
    resource_graph_agent = roster.get("resource_graph")
    if resource_graph_agent is None:
        raise SnapshotError("Snapshot analysis requires the Resource Graph Prompt Agent")
    local_tools = foundry_backend.select_specialist_tools("resource_graph", tools)

    async def snapshot_specialist_node(state: dict[str, Any]) -> dict[str, str]:
        update_context = state.get("update_context", "")
        trace_id = state.get("trace_id", "")
        prompt_context = f"{update_context}\n\n## Offline snapshot contract\n{service.prompt_context()}"
        prompt = foundry_backend.SPECIALIST_PROMPTS["resource_graph"].format(
            update_context=prompt_context
        )
        try:
            invocation = foundry_backend._coerce_invocation(
                await foundry_backend._invoke_foundry_agent(
                    settings.foundry_project_endpoint,
                    resource_graph_agent.name,
                    prompt,
                    settings.foundry_agent_timeout_s,
                    local_tools=local_tools,
                    trace_id=trace_id,
                    task_id="specialist:resource_graph:snapshot",
                )
            )
            parsed, validation_error = foundry_backend._parse_specialist_result(
                "resource_graph", invocation.text
            )
            if parsed is None:
                gap = f"Resource Graph specialist returned invalid output: {validation_error}"
                parsed = foundry_backend.SpecialistEvidence(
                    role="resource_graph",
                    status="partial",
                    claims=(),
                    gaps=(gap,),
                )
        except Exception as exc:
            parsed = foundry_backend.SpecialistEvidence(
                role="resource_graph",
                status="partial",
                claims=(),
                gaps=(f"Resource Graph snapshot specialist failed: {type(exc).__name__}",),
            )
        findings = foundry_backend._render_findings([parsed])
        merged = (
            f"{update_context}\n\n{foundry_backend.SPECIALIST_CONTEXT_HEADER}\n{findings}"
        )
        return {"update_context": merged}

    return snapshot_specialist_node


class SnapshotRuntime:
    """Process-local patch boundary for snapshot report generation."""

    def __init__(self, service: SnapshotResourceGraphService) -> None:
        self.service = service
        self._temp_dir: Optional[tempfile.TemporaryDirectory[str]] = None
        self._restorations: list[tuple[Any, str, Any]] = []
        self._old_data_dir: Optional[str] = None
        self.settings: Any = None
        self.analyzer_class: Any = None

    def _replace(self, owner: Any, name: str, value: Any) -> None:
        self._restorations.append((owner, name, getattr(owner, name)))
        setattr(owner, name, value)

    def __enter__(self) -> "SnapshotRuntime":
        self._temp_dir = tempfile.TemporaryDirectory(prefix="azbrief-snapshot-")
        temp_path = Path(self._temp_dir.name)
        self._old_data_dir = os.environ.get("AZBRIEF_DATA_DIR")
        os.environ["AZBRIEF_DATA_DIR"] = str(temp_path)

        from src.agent import analyzer as analyzer_module
        from src.agent import foundry_backend, history, kql_knowledge, pattern_memory
        from src.config import get_settings

        original_kql_cache = kql_knowledge._cache
        original_kql_path = kql_knowledge._cache_path
        self._restorations.extend(
            [
                (kql_knowledge, "_cache", original_kql_cache),
                (kql_knowledge, "_cache_path", original_kql_path),
                (history, "_DATA_DIR", history._DATA_DIR),
                (history, "_HISTORY_FILE", history._HISTORY_FILE),
                (history, "_RETIREMENT_FILE", history._RETIREMENT_FILE),
                (pattern_memory, "_DATA_DIR", pattern_memory._DATA_DIR),
                (pattern_memory, "_PATTERN_FILE", pattern_memory._PATTERN_FILE),
            ]
        )
        kql_knowledge._cache = {"schemas": {}, "queries": {}, "failed_queries": []}
        kql_knowledge._cache_path = temp_path / "kql_knowledge.json"
        history._DATA_DIR = temp_path
        history._HISTORY_FILE = temp_path / "analysis_results.jsonl"
        history._RETIREMENT_FILE = temp_path / "retirement_tracker.json"
        pattern_memory._DATA_DIR = temp_path
        pattern_memory._PATTERN_FILE = temp_path / "analysis_patterns.json"

        base_settings = get_settings()
        suffix = "\n\n".join(
            part
            for part in (base_settings.custom_system_prompt or "", self.service.prompt_context())
            if part
        )
        self.settings = base_settings.model_copy(
            update={
                "custom_system_prompt": suffix,
                "max_concurrent_analyses": 1,
            }
        )

        snapshot_tools = build_snapshot_tools(self.service)
        self._replace(analyzer_module, "get_all_tools", lambda: snapshot_tools)
        self._replace(
            foundry_backend,
            "build_specialist_collaboration_node",
            lambda settings, tools=None: build_snapshot_specialist_node(
                settings, tools or [], self.service
            ),
        )

        base_analyzer = analyzer_module.AzureUpdateAnalyzer
        runtime = self

        class SnapshotAzureUpdateAnalyzer(base_analyzer):
            """AzureUpdateAnalyzer wired to one immutable local snapshot."""

            def __init__(self, max_iterations: int = 5, settings: Any = None) -> None:
                super().__init__(
                    max_iterations=max_iterations,
                    settings=settings or runtime.settings,
                )

            def _inject_enrichment_tasks(self, plan: Any, state: Any) -> Any:
                plan = super()._inject_enrichment_tasks(plan, state)
                available = {tool.name for tool in self.tools}
                plan.tasks = [task for task in plan.tasks if task.tool_name in available]
                return plan

        SnapshotAzureUpdateAnalyzer.__name__ = "SnapshotAzureUpdateAnalyzer"
        SnapshotAzureUpdateAnalyzer.__qualname__ = "SnapshotAzureUpdateAnalyzer"
        self.analyzer_class = SnapshotAzureUpdateAnalyzer
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        for owner, name, value in reversed(self._restorations):
            setattr(owner, name, value)
        self._restorations.clear()
        if self._old_data_dir is None:
            os.environ.pop("AZBRIEF_DATA_DIR", None)
        else:
            os.environ["AZBRIEF_DATA_DIR"] = self._old_data_dir
        if self._temp_dir is not None:
            self._temp_dir.cleanup()
            self._temp_dir = None