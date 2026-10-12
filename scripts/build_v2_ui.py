"""Normalize v2 Portal forms and reuse the tested KT network validation contract."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def _safe_initial_outputs(parameters: dict[str, Any]) -> None:
    tag_prefix = "steps('contract').resourceGroupApi.tags"
    chunks = [f"{tag_prefix}['azbrief.v2.options{index}']" for index in range(8)]
    original = "parse(concat(" + ", ".join(chunks) + "))"
    combined = "concat(" + ", ".join(f"coalesce({chunk}, '')" for chunk in chunks) + ")"
    guarded = f"parse(if(empty({combined}), '{{}}', {combined}))"
    for name, expression in parameters["outputs"].items():
        if isinstance(expression, str):
            expression = expression.replace(original, guarded)
            expression = expression.replace(
                "parse(steps('network').existingPrivateDnsZoneIds)",
                "parse(if(empty(coalesce(steps('network').existingPrivateDnsZoneIds, '')), '{}', coalesce(steps('network').existingPrivateDnsZoneIds, '{}')))",
            )
            parameters["outputs"][name] = expression


def normalize_ui(
    document: dict[str, Any], network_validations: list[dict[str, Any]]
) -> dict[str, Any]:
    result = copy.deepcopy(document)
    parameters = result["parameters"]
    config = parameters.setdefault("config", {})
    config["isWizard"] = True
    config.setdefault("basics", {}).setdefault("resourceGroup", {})["allowExisting"] = True
    _safe_initial_outputs(parameters)
    for step in parameters["steps"]:
        visibility = step.pop("visible", None)
        elements = step["elements"]
        for element in list(elements):
            if visibility is not None:
                element["visible"] = visibility
            constraints = element.get("constraints", {})
            if element["type"] == "Microsoft.Common.CheckBox" and "validations" in constraints:
                guard_name = element["name"].replace("Accepted", "Guard")
                validations = constraints.pop("validations")
                elements.append(
                    {
                        "name": guard_name,
                        "type": "Microsoft.Common.TextBox",
                        "label": "배포 검증 (값 변경 불필요)",
                        "defaultValue": "검사",
                        "constraints": {"required": True, "validations": validations},
                    }
                )
                constraints["validationMessage"] = "배포 범위와 사전검사를 확인하십시오."
            if element["name"] == "existingPrivateDnsZoneIds":
                element["constraints"] = {
                    "required": True,
                    "regex": r"^\s*\{[\s\S]*\}\s*$",
                    "validationMessage": "검증한 zone-name → ARM ID JSON 객체를 입력하십시오.",
                }
        if step["name"] == "network":
            elements[:] = [element for element in elements if element["name"] != "networkGuard"]
            elements.append(
                {
                    "name": "networkGuard",
                    "type": "Microsoft.Common.TextBox",
                    "label": "서브넷 검증 (값 변경 불필요)",
                    "defaultValue": "검사",
                    "visible": "[equals(basics('infrastructureStage'), 'foundation')]",
                    "constraints": {"required": True, "validations": network_validations},
                }
            )
        if step["name"] == "names":
            elements[:] = [element for element in elements if element["name"] != "namesGuard"]
            elements.append(
                {
                    "name": "namesGuard",
                    "type": "Microsoft.Common.TextBox",
                    "label": "이름 분리 검증 (값 변경 불필요)",
                    "defaultValue": "검사",
                    "visible": "[equals(basics('infrastructureStage'), 'foundation')]",
                    "constraints": {
                        "required": True,
                        "validations": [
                            {
                                "isValid": "[and(not(equals(toLower(coalesce(steps('names').containerAppName, '')), toLower(coalesce(steps('names').azureMcpContainerAppName, '')))), not(equals(toLower(coalesce(steps('names').controlPlaneIdentityName, '')), toLower(coalesce(steps('names').azureMcpIdentityName, '')))))]",
                                "message": "Core App/MCP App 이름과 두 UAMI 이름은 각각 달라야 합니다. identity 권한을 합치지 않습니다.",
                            }
                        ],
                    },
                }
            )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    legacy = json.loads((ROOT / "infra/kt/createUiDefinition.json").read_text(encoding="utf-8"))
    validations = [
        validation
        for step in legacy["parameters"]["steps"]
        for element in step["elements"]
        for validation in element.get("constraints", {}).get("validations", [])
        if validation.get("isValid", "").startswith(
            (
                "[not(empty(filter(coalesce(steps('network')",
                "[and(not(equals(coalesce(steps('network')",
                "[not(or(equals(toLower(coalesce(steps('network')",
            )
        )
    ]
    if len(validations) != 5:
        raise ValueError("The five tested KT subnet/VNet validation rules must be retained")
    for name in ("infrastructure", "application"):
        path = ROOT / "infra/v2" / f"{name}.createUiDefinition.json"
        original = path.read_text(encoding="utf-8")
        normalized = normalize_ui(json.loads(original), validations)
        content = json.dumps(normalized, ensure_ascii=False, indent=2) + "\n"
        if args.check:
            if original != content:
                raise ValueError(f"Normalize {path.name} with python -m scripts.build_v2_ui")
        else:
            path.write_text(content, encoding="utf-8", newline="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
