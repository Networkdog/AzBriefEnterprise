"""Shared display normalization for email and immutable Archive reports."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

CAPABILITY_CATEGORIES = ("new_feature", "new_service", "region_expansion", "preview", "sdk_tooling")
IMPACT_FIELDS = ("cost_impact", "security_impact", "performance_impact", "operational_impact")
ANNOTATION_COMMON_WORDS = frozenset(
    "a an and are as at azure be by for from how in is it learn microsoft of on or the to "
    "use using with document documentation guide this that your you 문서 확인 방법 설명 합니다".split()
)
_EMPTY_IMPACT_VALUES = frozenset(
    {"", "none", "n/a", "not applicable", "해당 없음", "없음", "該当なし", "なし"}
)


def normalize_analysis_narrative(value: str) -> str:
    """Remove outline prefixes from prose paragraphs while retaining lists and code."""
    parts = re.split(r"(\n[ \t]*\n)", (value or "").replace("\r\n", "\n"))
    in_code = False
    numbered: dict[int, re.Match[str]] = {}
    for index in range(0, len(parts), 2):
        block = parts[index]
        fences = re.findall(r"(?m)^\s*(?:```|~~~)", block)
        protected = in_code or bool(fences)
        if len(fences) % 2:
            in_code = not in_code
        if protected:
            continue
        first = re.match(r"\A(\d{1,3})[.)][ \t]+", block)
        markers = re.findall(r"(?m)^\d{1,3}[.)][ \t]+", block)
        nested = re.search(r"(?m)^[ \t]+(?:\d+[.)]|[-*+])[ \t]+", block)
        if first and len(markers) == 1 and not nested:
            numbered[index] = first
    for index, marker in numbered.items():
        number = int(marker.group(1))
        before = numbered.get(index - 2)
        after = numbered.get(index + 2)
        if (before and int(before.group(1)) == number - 1) or (
            after and int(after.group(1)) == number + 1
        ):
            continue
        parts[index] = parts[index][marker.end() :]
    return "".join(parts)


def normalize_impact_content(details: Any, summary: Any = "") -> tuple[dict[str, str], str]:
    """Resolve populated dimensions or a legacy prose summary without leaking JSON."""

    def dimensions(value: Any) -> dict[str, str]:
        populated = {}
        for key in IMPACT_FIELDS:
            item = value.get(key) if isinstance(value, Mapping) else getattr(value, key, None)
            if isinstance(item, str) and item.strip().casefold() not in _EMPTY_IMPACT_VALUES:
                populated[key] = item.strip()
        return populated

    populated = dimensions(details)
    if populated:
        return populated, ""
    if isinstance(summary, Mapping):
        return dimensions(summary), ""
    if not isinstance(summary, str) or summary.strip().casefold() in _EMPTY_IMPACT_VALUES:
        return {}, ""
    text = summary.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*\n(.*?)\n```", text, re.DOTALL | re.IGNORECASE)
    candidate = fenced.group(1).strip() if fenced else text
    try:
        parsed = json.loads(candidate)
    except (ValueError, TypeError):
        return {}, text
    if isinstance(parsed, (dict, list)) or parsed is None:
        return dimensions(parsed), ""
    return {}, text


def report_presentation(result: Mapping[str, Any]) -> dict[str, Any]:
    """Build display values without modifying the original report contract or data."""
    display: dict[str, Any] = {
        "impact_in_overview": result.get("update_category") in CAPABILITY_CATEGORIES,
        "annotation_common_words": sorted(ANNOTATION_COMMON_WORDS),
    }
    display["relevance_reason"] = normalize_analysis_narrative(result.get("relevance_reason") or "")
    details, summary = normalize_impact_content(
        result.get("impact_details"), result.get("impact_summary", "")
    )
    display["impact_details"] = details
    display["impact_summary"] = summary
    return display
