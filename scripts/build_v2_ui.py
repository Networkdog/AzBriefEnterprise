"""Normalize v2 Portal forms and reuse the tested KT network validation contract."""

from __future__ import annotations

import argparse
import copy
import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
_TAG_INDEX = re.compile(
    r"(steps\('(?:contract|application)'\)\.resourceGroupApi\.tags)\['(azbrief\.v2\.[A-Za-z0-9]+)'\]"
)
RESOURCE_NAME_PARTS = {
    "foundryAccountName": ("ai-", "-"),
    "storageAccountName": ("st", ""),
    "cosmosAccountName": ("cosmos-", "-"),
    "searchServiceName": ("srch-", "-"),
    "projectName": ("", "-"),
    "containerAppsEnvironmentName": ("cae-", "-"),
    "containerAppName": ("ca-", "-"),
    "schedulerJobName": ("job-", "-"),
    "controlPlaneIdentityName": ("id-", "-"),
    "logAnalyticsWorkspaceName": ("log-", "-"),
    "applicationInsightsName": ("appi-", "-"),
    "communicationServicesName": ("acs-", "-"),
    "emailServiceName": ("email-", "-"),
    "azureMcpContainerAppName": ("ca-", "-mcp-"),
    "azureMcpIdentityName": ("id-", "-mcp-"),
}


def derive_resource_names(prefix: str, suffix: str) -> dict[str, str]:
    if not re.fullmatch(r"[a-z][a-z0-9]{0,9}", prefix) or not re.fullmatch(
        r"[a-z0-9]{1,10}", suffix
    ):
        raise ValueError(
            "Common resource names require a lowercase 1-10 character prefix and suffix"
        )
    return {
        name: f"{start}{prefix}{separator}{suffix}"
        for name, (start, separator) in RESOURCE_NAME_PARTS.items()
    }


def _replace_name_reads(value: Any, expressions: dict[str, str]) -> Any:
    if isinstance(value, dict):
        return {key: _replace_name_reads(item, expressions) for key, item in value.items()}
    if isinstance(value, list):
        return [_replace_name_reads(item, expressions) for item in value]
    if not isinstance(value, str) or not value.startswith("["):
        return value
    for name, expression in expressions.items():
        value = value.replace(f"steps('names').{name}", expression)
    return value


def _group_resource_names(parameters: dict[str, Any]) -> None:
    step = next((step for step in parameters["steps"] if step["name"] == "names"), None)
    if step is None:
        return
    prefix = "coalesce(steps('names').namingPrefix, '')"
    suffix = "coalesce(steps('names').namingSuffix, '')"
    expressions = {
        name: f"concat('{start}', {prefix}, '{separator}', {suffix})"
        for name, (start, separator) in RESOURCE_NAME_PARTS.items()
    }
    retained = [
        element
        for element in step["elements"]
        if element["name"] not in RESOURCE_NAME_PARTS
        and element["name"] not in {"namingPrefix", "namingSuffix", "resourceNamePreview"}
    ]
    step["elements"] = [
        {
            "name": "namingPrefix",
            "type": "Microsoft.Common.TextBox",
            "label": "공통 접두사",
            "defaultValue": "azbrief",
            "toolTip": "고객 명명 규칙의 공통 부분입니다. 서비스 구분자는 자동으로 붙습니다.",
            "visible": "[equals(basics('infrastructureStage'), 'foundation')]",
            "constraints": {
                "required": True,
                "regex": "^[a-z][a-z0-9]{0,9}$",
                "validationMessage": "소문자로 시작하는 영문 소문자·숫자 1~10자를 입력하십시오.",
            },
        },
        {
            "name": "namingSuffix",
            "type": "Microsoft.Common.TextBox",
            "label": "공통 접미사",
            "defaultValue": "v2",
            "toolTip": "환경·업무·설치 구분자입니다. 동일 RG의 기존 이름과 충돌하지 않도록 선택하십시오.",
            "visible": "[equals(basics('infrastructureStage'), 'foundation')]",
            "constraints": {
                "required": True,
                "regex": "^[a-z0-9]{1,10}$",
                "validationMessage": "영문 소문자·숫자 1~10자를 입력하십시오.",
            },
        },
        {
            "name": "resourceNamePreview",
            "type": "Microsoft.Common.InfoBox",
            "visible": "[equals(basics('infrastructureStage'), 'foundation')]",
            "options": {
                "icon": "Info",
                "text": "[concat("
                + ", ".join(
                    f"'{name}: ', {expression}"
                    + (", ' | '" if index < len(expressions) - 1 else "")
                    for index, (name, expression) in enumerate(expressions.items())
                )
                + ")]",
            },
        },
        *retained,
    ]
    updated = _replace_name_reads(parameters, expressions)
    parameters.clear()
    parameters.update(updated)
    saved_options = parameters["outputs"]["emailDataLocation"].split(
        ", steps('options').emailDataLocation)"
    )[0]
    for name in ("namingPrefix", "namingSuffix"):
        parameters["outputs"][name] = (
            saved_options.replace(".emailDataLocation", f".{name}")
            + f", coalesce(steps('names').{name}, ''))]"
        )


def _remove_validation_surfaces(parameters: dict[str, Any]) -> None:
    validations: list[str] = []
    existing = parameters["outputs"].get("portalValidationPassed")
    if isinstance(existing, str):
        for step in parameters["steps"]:
            step["elements"] = [
                element for element in step["elements"] if not element["name"].endswith("Guard")
            ]
        return
    for step in parameters["steps"]:
        retained = []
        for element in step["elements"]:
            if not element["name"].endswith("Guard"):
                retained.append(element)
                continue
            rules = element.get("constraints", {}).get("validations", [])
            expressions = [rule["isValid"][1:-1] for rule in rules if "isValid" in rule]
            if not expressions:
                continue
            combined = (
                expressions[0] if len(expressions) == 1 else "and(" + ", ".join(expressions) + ")"
            )
            visibility = element.get("visible")
            if isinstance(visibility, str):
                combined = f"if({visibility[1:-1]}, {combined}, true)"
            if combined not in validations:
                validations.append(combined)
        step["elements"] = retained
    if validations:
        parameters["outputs"]["portalValidationPassed"] = (
            "["
            + (validations[0] if len(validations) == 1 else "and(" + ", ".join(validations) + ")")
            + "]"
        )


def _portable_tag_access(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _portable_tag_access(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_portable_tag_access(item) for item in value]
    if not isinstance(value, str) or not value.startswith("["):
        return value

    def replace(match: re.Match[str]) -> str:
        tags, key = match.groups()
        property_name = key.replace(".", "_")
        return (
            f"parse(replace(string(coalesce({tags}, parse('{{}}'))), "
            f"'\"{key}\":', '\"{property_name}\":')).{property_name}"
        )

    return _TAG_INDEX.sub(replace, value)


def _safe_initial_outputs(parameters: dict[str, Any]) -> None:
    vnet_resource_group = (
        "first(skip(split(concat(coalesce(steps('network').virtualNetwork.id, ''), "
        "'/////'), '/'), 4))"
    )
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
            expression = expression.replace(
                "steps('network').virtualNetwork.resourceGroup", vnet_resource_group
            )
            parameters["outputs"][name] = expression


def normalize_ui(
    document: dict[str, Any], network_validations: list[dict[str, Any]]
) -> dict[str, Any]:
    result = copy.deepcopy(document)
    parameters = result["parameters"]
    config = parameters.setdefault("config", {})
    config.pop("isWizard", None)
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
    _group_resource_names(parameters)
    _remove_validation_surfaces(parameters)
    return _portable_tag_access(result)


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
