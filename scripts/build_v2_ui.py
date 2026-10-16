"""Normalize v2 Portal forms and reuse the tested KT network validation contract."""

from __future__ import annotations

import argparse
import copy
import json
import re
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parents[1]
INFRASTRUCTURE_REGION = (
    "if(equals(coalesce(steps('network').virtualNetworkMode, 'existing'), 'new'), "
    "toLower(coalesce(location(), '')), "
    "toLower(coalesce(steps('network').virtualNetworkApi.location, "
    "steps('network').virtualNetwork.location, '')))"
)
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
SHARED_RESOURCES = {
    "foundryAccount": (
        "Foundry 계정",
        "Microsoft.CognitiveServices/accounts",
        "2025-06-01",
        "foundry",
    ),
    "storageAccount": (
        "Storage 계정",
        "Microsoft.Storage/storageAccounts",
        "2023-05-01",
        "storage",
    ),
    "containerAppsEnvironment": (
        "Container Apps Environment",
        "Microsoft.App/managedEnvironments",
        "2026-01-01",
        "environment",
    ),
    "communicationServices": (
        "Communication Services (ACS)",
        "Microsoft.Communication/communicationServices",
        "2023-04-01",
        "acs",
    ),
}
RESOURCE_BINDING_KEYS = (
    "foundryAccountResourceId",
    "storageAccountResourceId",
    "containerAppsEnvironmentResourceId",
    "communicationServicesResourceId",
    "logAnalyticsWorkspaceResourceId",
    "emailDomainResourceId",
    "stateContainerName",
    "archiveContainerName",
)
V2_METADATA_TAG_SUFFIXES = (
    "schema",
    "profile",
    "state",
    "acaPeEnabled",
    "deployment",
    "location",
    "environment",
    "app",
    "job",
    "identity",
    "storage",
    "cosmos",
    "search",
    "workspace",
    "insights",
    "acs",
    "email",
    "foundry",
    "project",
    "hosted",
    "coreModel",
    "simpleModel",
    "vnetRg",
    "vnet",
    "peSubnet",
    "foundrySubnet",
    "appSubnet",
    "mcpUrl",
    "mcpConnection",
    "mcpApp",
    "mcpIdentity",
    "applicationState",
    "lastApplicationDeploymentAt",
    *(f"options{index}" for index in range(8)),
)


def _all(*expressions: str) -> str:
    return expressions[0] if len(expressions) == 1 else "and(" + ", ".join(expressions) + ")"


def _saved_chunks(step: str, kind: str, tags_alias: str = "") -> tuple[str, str, str]:
    tags = tags_alias or f"coalesce(steps('{step}').resourceGroupApi.tags, parse('{{}}'))"
    values = [
        (
            f"coalesce({tags_alias}.azbrief_v2_{kind}{i}, '')"
            if tags_alias
            else f"coalesce(steps('{step}').resourceGroupApi.tags['azbrief.v2.{kind}{i}'], '')"
        )
        for i in range(8)
    ]
    joined = "concat(" + ", ".join(values) + ")"
    key_prefix = "azbrief_v2_" if tags_alias else "azbrief.v2."
    present = [f"contains({tags}, '{key_prefix}{kind}{i}')" for i in range(8)]
    any_present = "or(" + ", ".join(present) + ")"
    contiguous = _all(
        present[0],
        *(
            f"or(not({present[i]}), "
            + _all(
                f"not(empty({values[i]}))",
                f"lessOrEquals(length({values[i]}), 256)",
                present[i - 1] if i else "true",
            )
            + ")"
            for i in range(8)
        ),
    )
    valid = (
        _all(
            contiguous,
            f"startsWith({joined}, '{{')",
            f"equals(last(split({joined}, '}}')), '')",
        )
        if kind == "resources"
        else f"not(empty({joined}))"
    )
    value = f"if({valid}, {joined}, '{{}}')"
    return f"parse({value})", any_present, contiguous


def _id_predicate(resource_id: str, resource_type: str) -> str:
    namespace, *types = resource_type.lower().split("/")
    segments = f"split(toLower(coalesce({resource_id}, '')), '/')"

    def part(count: int) -> str:
        return f"last(take({segments}, {count}))"

    return _all(
        f"equals(length({segments}), {7 + 2 * len(types)})",
        f"equals({part(1)}, '')",
        f"equals({part(2)}, 'subscriptions')",
        f"equals({part(3)}, toLower(subscription().subscriptionId))",
        f"equals({part(4)}, 'resourcegroups')",
        f"not(empty({part(5)}))",
        f"equals({part(6)}, 'providers')",
        f"equals({part(7)}, '{namespace}')",
        *(
            _all(
                f"equals({part(8 + 2 * index)}, '{name}')",
                f"not(empty({part(9 + 2 * index)}))",
            )
            for index, name in enumerate(types)
        ),
        f"not(contains({resource_id}, '#'))",
    )


def _valid_resource_id(resource_id: str, resource_type: str, api: str = "") -> str:
    predicates = [_id_predicate("rid", resource_type)]
    if api:
        predicates.extend(
            (
                f"empty({api}.error)",
                f"empty({api}.nextLink)",
                f"equals(toLower(coalesce({api}.id, '')), toLower(rid))",
                f"equals(toLower(coalesce({api}.type, '')), '{resource_type.lower()}')",
                f"equals(toLower(last(split(coalesce({api}.name, ''), '/'))), toLower(last(split(rid, '/'))))",
                f"equals({api}.properties.provisioningState, 'Succeeded')",
            )
        )
    rows = f"filter(split(coalesce({resource_id}, ''), '?'), (rid) => not(empty(rid)))"
    return _all(
        f"not(contains(coalesce({resource_id}, ''), '?'))",
        f"equals(length(filter({rows}, (rid) => {_all(*predicates)})), 1)",
    )


def _valid_foundry_subdomain(value: str) -> str:
    normalized = f"toLower(coalesce({value}, ''))"
    remaining = normalized
    for character in "abcdefghijklmnopqrstuvwxyz0123456789-":
        remaining = f"replace({remaining}, '{character}', '')"
    return _all(
        f"not(empty({normalized}))",
        f"lessOrEquals(length({normalized}), 64)",
        f"not(startsWith({normalized}, '-'))",
        f"empty({remaining})",
    )


def _valid_blob_container(value: str) -> str:
    name = f"coalesce({value}, '')"
    remainder = name
    for character in "abcdefghijklmnopqrstuvwxyz0123456789-":
        remainder = f"replace({remainder}, '{character}', '')"
    return _all(
        f"greaterOrEquals(length({name}), 3)",
        f"lessOrEquals(length({name}), 63)",
        f"not(startsWith({name}, '-'))",
        f"not(empty(last(split({name}, '-'))))",
        f"not(contains({name}, '--'))",
        f"empty({remainder})",
    )


def _complete_inventory(api: str) -> str:
    return _all(
        f"not(equals(coalesce({api}.value, false), false))",
        f"empty({api}.error)",
        f"empty({api}.nextLink)",
    )


def _child_inventory(api: str, parent_id: str, resource_type: str) -> str:
    child_type = resource_type.split("/")[-1]
    valid_child = _all(
        _id_predicate("coalesce(item.id, '')", resource_type),
        f"startsWith(toLower(coalesce(item.id, '')), concat(toLower({parent_id}), '/{child_type.lower()}/'))",
        f"equals(toLower(coalesce(item.type, '')), '{resource_type.lower()}')",
        f"equals(toLower(last(split(coalesce(item.name, ''), '/'))), toLower(last(split(coalesce(item.id, ''), '/'))))",
    )
    return _all(
        _complete_inventory(api),
        f"empty(filter(coalesce({api}.value, parse('[]')), (item) => not({valid_child})))",
    )


def _integer_at_least(value: str, minimum: str) -> str:
    representation = f"string(coalesce({value}, -1))"
    remainder = representation
    for digit in "0123456789":
        remainder = f"replace({remainder}, '{digit}', '')"
    parsed = f"int(if(empty({remainder}), {representation}, '0'))"
    return _all(
        f"equals({value}, {parsed})",
        f"greaterOrEquals({parsed}, {minimum})",
        f"greater({minimum}, 0)",
    )


def _binding_validation(
    step: str, tags_alias: str = "", options_alias: str = "", resources_alias: str = ""
) -> str:
    saved, present, contiguous = _saved_chunks(step, "resources", tags_alias)
    options, _, _ = _saved_chunks(step, "options", tags_alias)
    saved = resources_alias or saved
    options = options_alias or options
    tags = tags_alias or f"steps('{step}').resourceGroupApi.tags"
    keys = _all(
        "equals(length(binding), 8)",
        *(f"contains(binding, '{key}')" for key in RESOURCE_BINDING_KEYS),
    )
    id_rules: list[str] = []
    for name, (_, resource_type, _, tag) in SHARED_RESOURCES.items():
        resource_id = f"coalesce(binding.{name}ResourceId, '')"
        mode = f"coalesce(opts.{name}Mode, 'new')"
        id_rules.extend(
            (
                _valid_resource_id(resource_id, resource_type),
                f"equals(last(split({resource_id}, '/')), coalesce({tags}['azbrief.v2.{tag}'], ''))",
                f"if(equals({mode}, 'new'), equals(toLower(last(take(split({resource_id}, '/'), 5))), toLower(coalesce(resourceGroup().name, ''))), true)",
            )
        )
    for name, resource_type, tag in (
        ("logAnalyticsWorkspace", "Microsoft.OperationalInsights/workspaces", "workspace"),
        ("emailDomain", "Microsoft.Communication/emailServices/domains", "email"),
    ):
        resource_id = f"coalesce(binding.{name}ResourceId, '')"
        leaf = (
            f"last(take(split({resource_id}, '/'), 9))"
            if name == "emailDomain"
            else f"last(split({resource_id}, '/'))"
        )
        owner = "communicationServices" if name == "emailDomain" else "containerAppsEnvironment"
        id_rules.extend(
            (
                _valid_resource_id(resource_id, resource_type),
                f"equals({leaf}, coalesce({tags}['azbrief.v2.{tag}'], ''))",
                f"if(equals(coalesce(opts.{owner}Mode, 'new'), 'new'), equals(toLower(last(take(split({resource_id}, '/'), 5))), toLower(coalesce(resourceGroup().name, ''))), true)",
            )
        )
    bindings = f"parse(concat('[', string({saved}), ']'))"
    shared_containers = _all(
        _valid_blob_container("binding.stateContainerName"),
        _valid_blob_container("binding.archiveContainerName"),
        "not(equals(binding.stateContainerName, binding.archiveContainerName))",
        *(
            f"not(contains(parse('[\"azbrief-state\",\"azbrief-archive\"]'), coalesce(binding.{name}, '')))"
            for name in ("stateContainerName", "archiveContainerName")
        ),
    )
    new_containers = _all(
        "equals(binding.stateContainerName, 'azbrief-state')",
        "equals(binding.archiveContainerName, 'azbrief-archive')",
    )
    valid_binding = _all(
        contiguous,
        f"equals(length(filter({bindings}, (binding) => "
        + _all(
            keys,
            *id_rules,
            f"if(equals(coalesce(opts.storageAccountMode, 'new'), 'existing'), {shared_containers}, {new_containers})",
        )
        + ")), 1)",
    )
    valid_options = _all(
        *(
            f"contains(parse('[\"new\",\"existing\"]'), coalesce(opts.{name}Mode, 'new'))"
            for name in SHARED_RESOURCES
        ),
        f"if({present}, {valid_binding}, "
        + _all(*(f"equals(coalesce(opts.{name}Mode, 'new'), 'new')" for name in SHARED_RESOURCES))
        + ")",
    )
    return (
        f"equals(length(filter(parse(concat('[', string({options}), ']')), "
        f"(opts) => {valid_options})), 1)"
    )


def _shared_context(expression: str, step: str = "contract", include_saved: bool = True) -> str:
    """Bind tag projections once using documented map/first expressions."""
    expression = re.sub(
        r"(?<![\w.])tags\['azbrief\.v2\.([A-Za-z0-9]+)'\]",
        r"tags.azbrief_v2_\1",
        expression,
    )
    expression = re.sub(
        r"contains\(tags,\s*'azbrief\.v2\.([A-Za-z0-9]+)'\)",
        r"contains(tags, 'azbrief_v2_\1')",
        expression,
    )
    projection = f"string(coalesce(steps('{step}').resourceGroupApi.tags, parse('{{}}')))"
    for suffix in (*V2_METADATA_TAG_SUFFIXES, *(f"resources{i}" for i in range(8))):
        projection = (
            f"""replace({projection}, '"azbrief_v2_{suffix}":', '"azbrief/v2/{suffix}":')"""
        )
        projection = (
            f"""replace({projection}, '"azbrief.v2.{suffix}":', '"azbrief_v2_{suffix}":')"""
        )
    if include_saved:
        options, _, _ = _saved_chunks(step, "options", "tags")
        resources, _, _ = _saved_chunks(step, "resources", "tags")
        expression = (
            "first(map(parse(concat('[{\"options\":', "
            f"string({options}), ',\"resources\":', string({resources}), '}}]')), "
            f"(saved) => {expression}))"
        )
    return f"first(map(parse(concat('[', {projection}, ']')), (tags) => {expression}))"


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
    displayed_expressions = {
        name: expression
        for name, expression in expressions.items()
        if name not in {"cosmosAccountName", "searchServiceName"}
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
                    + (", ' | '" if index < len(displayed_expressions) - 1 else "")
                    for index, (name, expression) in enumerate(displayed_expressions.items())
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


def _without_guard_extension(expression: str, marker: str) -> str:
    if marker not in expression:
        return expression
    if expression.startswith("and(") and expression.endswith(")"):
        body = expression[4:-1]
        depth = 0
        quoted = False
        start = 0
        arguments = []
        for index, character in enumerate(body):
            if character == "'":
                quoted = not quoted
            elif not quoted:
                if character == "(":
                    depth += 1
                elif character == ")":
                    depth -= 1
                elif character == "," and depth == 0:
                    arguments.append(body[start:index].strip())
                    start = index + 1
        arguments.append(body[start:].strip())
        retained = [
            result
            for argument in arguments
            if (result := _without_guard_extension(argument, marker))
        ]
        if len(retained) == 1:
            return retained[0]
        return "and(" + ", ".join(retained) + ")" if retained else ""
    return ""


def _extend_guard(parameters: dict[str, Any], marker: str, condition: str) -> None:
    guard = _without_guard_extension(parameters["outputs"]["portalValidationPassed"][1:-1], marker)
    if not guard:
        raise ValueError("The base Portal validation must remain after replacing an extension")
    guard = "[" + guard + "]"
    parameters["outputs"]["portalValidationPassed"] = f"[and({guard[1:-1]}, {condition})]"


def _foundation_tag_budget(
    existing: dict[str, str], ids: dict[str, str], workspace_id: str, domain_id: str
) -> str:
    def generated(name: str) -> str:
        prefix, separator = RESOURCE_NAME_PARTS[name]
        return (
            f"concat('{prefix}', coalesce(steps('names').namingPrefix, ''), "
            f"'{separator}', coalesce(steps('names').namingSuffix, ''))"
        )

    # 태그 길이 계산에서만 구독 GUID의 고정 36자를 사용하며 리소스 ID로 출력하지 않습니다.
    base = (
        "concat('/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/', "
        "coalesce(resourceGroup().name, ''), '/providers/')"
    )
    planned = {
        name
        + "ResourceId": (
            f"if({existing[name]}, {ids[name]}, "
            f"concat({base}, '{resource_type}/', {generated(name + 'Name')}))"
        )
        for name, (_, resource_type, _, _) in SHARED_RESOURCES.items()
    }
    planned["logAnalyticsWorkspaceResourceId"] = (
        f"if({existing['containerAppsEnvironment']}, {workspace_id}, "
        f"concat({base}, 'Microsoft.OperationalInsights/workspaces/', {generated('logAnalyticsWorkspaceName')}))"
    )
    planned["emailDomainResourceId"] = (
        f"if({existing['communicationServices']}, {domain_id}, "
        f"concat({base}, 'Microsoft.Communication/emailServices/', {generated('emailServiceName')}, '/domains/AzureManagedDomain'))"
    )
    for name, prefix in (
        ("stateContainerName", "azbrief-state"),
        ("archiveContainerName", "azbrief-archive"),
    ):
        planned[name] = (
            f"if({existing['storageAccount']}, concat('{prefix}-', {generated('containerAppName')}), '{prefix}')"
        )
    text = (
        "concat("
        + ", ".join(
            [
                f"""'{{"{RESOURCE_BINDING_KEYS[0]}":"', {planned[RESOURCE_BINDING_KEYS[0]]}""",
                *(f"""'","{key}":"', {planned[key]}""" for key in RESOURCE_BINDING_KEYS[1:]),
                """'"}'""",
            ]
        )
        + ")"
    )
    tags = (
        "if(equals(resourceGroup().mode, 'Existing'), "
        "coalesce(steps('contract').resourceGroupApi.tags, parse('{}')), parse('{}'))"
    )
    available = "50"
    for key in V2_METADATA_TAG_SUFFIXES:
        available = f"sub({available}, if(contains({tags}, 'azbrief.v2.{key}'), 0, 1))"
    count = (
        "length(filter(parse('[0,256,512,768,1024,1280,1536,1792]'), "
        f"(offset) => greater(length({text}), offset)))"
    )
    return _all(
        f"lessOrEquals(length({text}), 2048)",
        f"lessOrEquals(length({tags}), sub({available}, {count}))",
    )


def _shared_resource_controls(parameters: dict[str, Any]) -> None:
    step = next((step for step in parameters["steps"] if step["name"] == "names"), None)
    if step is None:
        return
    foundation = "equals(basics('infrastructureStage'), 'foundation')"
    _, saved_present, _ = _saved_chunks("contract", "resources", "tags")
    saved = "saved.resources"
    options = "saved.options"
    tags = "tags"
    modes = {
        name: f"if({foundation}, coalesce(steps('names').{name}Mode, 'new'), coalesce({options}.{name}Mode, 'new'))"
        for name in SHARED_RESOURCES
    }
    existing = {name: f"equals({mode}, 'existing')" for name, mode in modes.items()}
    ids = {
        name: f"if({foundation}, coalesce(steps('names').existing{name[0].upper() + name[1:]}.id, ''), coalesce({saved}.{name}ResourceId, ''))"
        for name in SHARED_RESOURCES
    }
    workspace_api = "steps('names').existingLogAnalyticsWorkspaceApi"
    environment_api = "steps('names').existingContainerAppsEnvironmentApi"
    acs_api = "steps('names').existingCommunicationServicesApi"
    customer_id = (
        f"coalesce({environment_api}.properties.appLogsConfiguration."
        "logAnalyticsConfiguration.customerId, '')"
    )
    inventory = "steps('names').logAnalyticsWorkspacesApi"
    workspace_matches = (
        f"filter(coalesce({inventory}.value, parse('[]')), (workspace) => "
        + _all(
            f"not(empty({customer_id}))",
            f"equals(toLower(coalesce(workspace.properties.customerId, '')), toLower({customer_id}))",
        )
        + ")"
    )
    unique_workspace = _all(
        f"not(equals(coalesce({inventory}.value, false), false))",
        f"empty({inventory}.error)",
        f"empty({inventory}.nextLink)",
        f"equals(length({workspace_matches}), 1)",
    )
    matched_workspace = (
        f"first(if(empty({workspace_matches}), parse('[{{}}]'), {workspace_matches}))"
    )
    explicit_workspace = "coalesce(steps('names').existingLogAnalyticsWorkspace.id, '')"
    workspace_id = (
        f"if({foundation}, if(not(empty({explicit_workspace})), {explicit_workspace}, "
        f"if({unique_workspace}, coalesce({matched_workspace}.id, ''), '')), "
        f"coalesce({saved}.logAnalyticsWorkspaceResourceId, ''))"
    )
    domain_id = (
        f"if({foundation}, coalesce(steps('names').existingEmailDomain, ''), "
        f"coalesce({saved}.emailDomainResourceId, ''))"
    )

    def email_parent(resource_id: str) -> str:
        parts = f"split({resource_id}, '/')"
        return (
            f"if(empty({resource_id}), '', concat('/subscriptions/', last(take({parts}, 3)), '/resourceGroups/', "
            f"last(take({parts}, 5)), '/providers/Microsoft.Communication/emailServices/', "
            f"last(take({parts}, 9))))"
        )

    email_service_id = email_parent(domain_id)
    region = (
        f"if({foundation}, {INFRASTRUCTURE_REGION}, "
        f"coalesce({tags}['azbrief.v2.location'], ''))"
    )
    vnet = (
        f"if({foundation}, coalesce(steps('network').virtualNetwork.id, ''), "
        f"concat('/subscriptions/', last(take(split(coalesce({saved}.foundryAccountResourceId, ''), '/'), 3)), "
        f"'/resourceGroups/', coalesce({tags}['azbrief.v2.vnetRg'], ''), "
        f"'/providers/Microsoft.Network/virtualNetworks/', coalesce({tags}['azbrief.v2.vnet'], '')))"
    )
    subnet_ids = {
        name: (
            f"concat({vnet}, '/subnets/', if({foundation}, "
            f"coalesce(steps('network').{control}, ''), coalesce({tags}['azbrief.v2.{tag}'], '')))"
        )
        for name, control, tag in (
            ("foundryAccount", "foundrySubnetName", "foundrySubnet"),
            ("containerAppsEnvironment", "containerAppsSubnetName", "appSubnet"),
        )
    }
    subnet_readbacks = {
        name: (
            f"if({foundation}, equals(length(filter("
            "coalesce(steps('network').virtualNetworkApi.properties.subnets, parse('[]')), "
            "(subnet) => "
            + _all(
                f"equals(toLower(coalesce(subnet.id, '')), toLower({subnet_ids[name]}))",
                f"equals(toLower(coalesce(subnet.name, '')), toLower(coalesce(steps('network').{control}, '')))",
            )
            + ")), 1), true)"
        )
        for name, control in (
            ("foundryAccount", "foundrySubnetName"),
            ("containerAppsEnvironment", "containerAppsSubnetName"),
        )
    }
    network_mode = (
        f"if({foundation}, coalesce(steps('network').virtualNetworkMode, 'existing'), "
        f"coalesce({options}.virtualNetworkMode, 'existing'))"
    )
    new_network_compatible = (
        f"or(equals({network_mode}, 'existing'), "
        f"and(not({existing['foundryAccount']}), not({existing['containerAppsEnvironment']})))"
    )
    shared_names = {
        "sharedResourceNotice",
        "savedResourceBindings",
        "invalidSavedResourceBindings",
        "logAnalyticsWorkspacesApi",
        "existingLogAnalyticsWorkspace",
        "existingLogAnalyticsWorkspaceApi",
        "existingEmailDomain",
        "existingEmailDomainApi",
        "existingEmailServiceApi",
        "sharedFoundryHostsApi",
        "sharedFoundryDeploymentsApi",
        "sharedFoundryProjectsApi",
    } | {
        control
        for name in SHARED_RESOURCES
        for control in (
            name + "Mode",
            "existing" + name[0].upper() + name[1:],
            "existing" + name[0].upper() + name[1:] + "Api",
        )
    }
    step["label"] = "리소스 선택·이름"
    step["elements"] = [
        element for element in step["elements"] if element["name"] not in shared_names
    ]
    selectors: list[dict[str, Any]] = [
        {
            "name": "sharedResourceNotice",
            "type": "Microsoft.Common.InfoBox",
            "visible": f"[{foundation}]",
            "options": {
                "icon": "Info",
                "text": (
                    "네 서비스를 각각 신규 생성하거나 같은 구독의 기존 리소스에서 선택합니다. 다른 RG도 "
                    "선택할 수 있으며 ID·시크릿을 직접 입력하지 않습니다. 기존 Foundry와 Environment는 "
                    "모델과 같은 리전의 기존 VNet·정확한 전용 서브넷에 이미 연결돼 있어야 합니다. 신규 "
                    "VNet과 함께 재사용할 수 없으며 임의로 전환하지 않습니다. Storage·ACS의 리전은 "
                    "달라도 됩니다. 읽기 실패·미완료·공용 구성·다른 리소스로의 응답은 차단합니다. "
                    "공유 Storage에는 이 App 전용 상태·Archive 컨테이너만 만듭니다. 공유 Environment의 "
                    "실제 로그 workspace를 재사용하며 새 workspace로 대체하지 않습니다. 기존 ACS에 "
                    "이미 연결된 준비된 도메인과 DoNotReply 발신자를 사용합니다. 공유 리소스의 DNS·"
                    "Private Endpoint는 새로 만들지 않습니다. 같은 VNet의 기존 승인된 연결·DNS와 "
                    "Foundry 계정 Host 준비는 승인된 읽기 전용 CLI 사전검사로 먼저 확인하십시오. "
                    "공유 Foundry의 준비된 계정 Host와 전체 모델·프로젝트 목록을 읽습니다. 모델 별칭은 "
                    "정확한 모델·버전·GlobalStandard·필요 용량이 일치할 때만 그대로 재사용하며, 없을 "
                    "때만 생성합니다. 같은 이름의 프로젝트가 이미 있으면 Portal에서 재설정하지 "
                    "않습니다. 승인된 CLI에서 소유권·프로젝트 Host·텔레메트리의 명시적 재사용을 검증하십시오."
                ),
            },
        }
    ]
    readbacks: list[dict[str, Any]] = []

    def api_control(
        name: str, target: str, resource_type: str, version: str, active: str
    ) -> dict[str, Any]:
        return {
            "name": name,
            "type": "Microsoft.Solutions.ArmApiControl",
            "request": {
                "method": "GET",
                "path": (
                    f"[if(and({active}, {_valid_resource_id(target, resource_type)}), "
                    f"concat({target}, '?api-version={version}'), "
                    "concat('/subscriptions/', subscription().subscriptionId, '?api-version=2022-12-01'))]"
                ),
            },
        }

    for control, collection, version in (
        ("sharedFoundryHostsApi", "capabilityHosts", "2025-04-01-preview"),
        ("sharedFoundryDeploymentsApi", "deployments", "2025-06-01"),
        ("sharedFoundryProjectsApi", "projects", "2025-06-01"),
    ):
        readbacks.append(
            {
                "name": control,
                "type": "Microsoft.Solutions.ArmApiControl",
                "request": {
                    "method": "GET",
                    "path": (
                        f"[if(and({existing['foundryAccount']}, "
                        f"{_valid_resource_id(ids['foundryAccount'], 'Microsoft.CognitiveServices/accounts')}), "
                        f"concat({ids['foundryAccount']}, '/{collection}?api-version={version}'), "
                        "concat('/subscriptions/', subscription().subscriptionId, '?api-version=2022-12-01'))]"
                    ),
                },
            }
        )

    for name, (label, resource_type, version, tag) in SHARED_RESOURCES.items():
        selector = "existing" + name[0].upper() + name[1:]
        selectors.extend(
            (
                {
                    "name": name + "Mode",
                    "type": "Microsoft.Common.DropDown",
                    "label": label + " 배포 방식",
                    "defaultValue": "신규 생성",
                    "visible": f"[{foundation}]",
                    "constraints": {
                        "required": True,
                        "allowedValues": [
                            {"label": "신규 생성", "value": "new"},
                            {"label": "기존 리소스 사용", "value": "existing"},
                        ],
                    },
                },
                {
                    "name": selector,
                    "type": "Microsoft.Solutions.ResourceSelector",
                    "label": "기존 " + label,
                    "resourceType": resource_type,
                    "options": {"filter": {"subscription": "onBasics", "location": "all"}},
                    "visible": f"[and({foundation}, {existing[name]})]",
                },
            )
        )
        readbacks.append(
            api_control(selector + "Api", ids[name], resource_type, version, existing[name])
        )
        parameters["outputs"][name + "Mode"] = f"[{modes[name]}]"
        parameters["outputs"][selector + "ResourceId"] = f"[if({existing[name]}, {ids[name]}, '')]"
        prefix, separator = RESOURCE_NAME_PARTS[name + "Name"]
        generated = (
            f"concat('{prefix}', coalesce(steps('names').namingPrefix, ''), "
            f"'{separator}', coalesce(steps('names').namingSuffix, ''))"
        )
        parameters["outputs"][name + "Name"] = (
            f"[if({foundation}, if({existing[name]}, last(split({ids[name]}, '/')), {generated}), "
            f"if({saved_present}, last(split(coalesce({saved}.{name}ResourceId, ''), '/')), "
            f"coalesce({tags}['azbrief.v2.{tag}'], '')))]"
        )
    selectors.extend(
        (
            {
                "name": "existingLogAnalyticsWorkspace",
                "type": "Microsoft.Solutions.ResourceSelector",
                "label": "Environment 로그 workspace (자동 확인 실패 시 선택)",
                "resourceType": "Microsoft.OperationalInsights/workspaces",
                "options": {"filter": {"subscription": "onBasics", "location": "all"}},
                "visible": f"[and({foundation}, {existing['containerAppsEnvironment']})]",
            },
            {
                "name": "existingEmailDomain",
                "type": "Microsoft.Common.DropDown",
                "label": "ACS에 이미 연결된 메일 도메인",
                "defaultValue": "도메인을 선택하십시오.",
                "filter": True,
                "visible": f"[and({foundation}, {existing['communicationServices']})]",
                "constraints": {
                    "required": True,
                    "allowedValues": (
                        '[parse(concat(\'[{"label":"도메인을 선택하십시오.","value":""}\', '
                        "replace(replace(replace(string(map("
                        f"coalesce({acs_api}.properties.linkedDomains, parse('[]')), "
                        '(domain) => parse(concat(\'{"label":"\', domain, '
                        "'\",\"value\":\"', domain, '\"}')))), '[]', ''), '[', ','), ']', ''), ']'))]"
                    ),
                },
            },
        )
    )
    readbacks.extend(
        (
            {
                "name": "logAnalyticsWorkspacesApi",
                "type": "Microsoft.Solutions.ArmApiControl",
                "request": {
                    "method": "GET",
                    "path": (
                        f"[if(and({foundation}, {existing['containerAppsEnvironment']}), "
                        "concat('/subscriptions/', subscription().subscriptionId, "
                        "'/providers/Microsoft.OperationalInsights/workspaces?api-version=2025-02-01'), "
                        "concat('/subscriptions/', subscription().subscriptionId, '?api-version=2022-12-01'))]"
                    ),
                },
            },
            api_control(
                "existingLogAnalyticsWorkspaceApi",
                workspace_id,
                "Microsoft.OperationalInsights/workspaces",
                "2025-02-01",
                existing["containerAppsEnvironment"],
            ),
            api_control(
                "existingEmailDomainApi",
                domain_id,
                "Microsoft.Communication/emailServices/domains",
                "2023-04-01",
                existing["communicationServices"],
            ),
            api_control(
                "existingEmailServiceApi",
                email_service_id,
                "Microsoft.Communication/emailServices",
                "2023-04-01",
                existing["communicationServices"],
            ),
        )
    )
    parameters["outputs"][
        "existingLogAnalyticsWorkspaceResourceId"
    ] = f"[if({existing['containerAppsEnvironment']}, {workspace_id}, '')]"
    parameters["outputs"][
        "existingEmailDomainResourceId"
    ] = f"[if({existing['communicationServices']}, {domain_id}, '')]"
    for name, target, owner, tag in (
        (
            "logAnalyticsWorkspaceName",
            workspace_id,
            "containerAppsEnvironment",
            "workspace",
        ),
        ("emailServiceName", email_service_id, "communicationServices", "email"),
    ):
        prefix, separator = RESOURCE_NAME_PARTS[name]
        binding_target = (
            email_parent(f"coalesce({saved}.emailDomainResourceId, '')")
            if name == "emailServiceName"
            else f"coalesce({saved}.logAnalyticsWorkspaceResourceId, '')"
        )
        parameters["outputs"][name] = (
            f"[if({foundation}, if({existing[owner]}, last(split({target}, '/')), "
            f"concat('{prefix}', coalesce(steps('names').namingPrefix, ''), '{separator}', coalesce(steps('names').namingSuffix, ''))), "
            f"if({saved_present}, last(split({binding_target}, '/')), coalesce({tags}['azbrief.v2.{tag}'], '')))]"
        )
    hosts_api = "steps('names').sharedFoundryHostsApi"
    deployments_api = "steps('names').sharedFoundryDeploymentsApi"
    projects_api = "steps('names').sharedFoundryProjectsApi"
    inventory_rules = [
        _child_inventory(
            api, ids["foundryAccount"], f"Microsoft.CognitiveServices/accounts/{child}"
        )
        for api, child in (
            (hosts_api, "capabilityHosts"),
            (deployments_api, "deployments"),
            (projects_api, "projects"),
        )
    ]
    hosts_ready = _all(
        f"not(empty(coalesce({hosts_api}.value, parse('[]'))))",
        f"empty(filter(coalesce({hosts_api}.value, parse('[]')), "
        "(host) => not(equals(host.properties.provisioningState, 'Succeeded'))))",
    )
    project_name = (
        f"if({foundation}, concat(coalesce(steps('names').namingPrefix, ''), '-', "
        f"coalesce(steps('names').namingSuffix, '')), coalesce({tags}['azbrief.v2.project'], ''))"
    )
    project_absent = (
        f"empty(filter(coalesce({projects_api}.value, parse('[]')), (project) => "
        f"equals(toLower(last(split(coalesce(project.name, ''), '/'))), toLower({project_name}))))"
    )
    core_version = (
        f"if({foundation}, coalesce(steps('options').modelVersion, '2025-12-11'), "
        f"coalesce({options}.modelVersion, ''))"
    )
    active_accounts = (
        f"filter(split({ids['foundryAccount']}, '?'), (account) => "
        f"and({existing['foundryAccount']}, not(empty(account))))"
    )
    model_rules = []
    for role, alias_tag, alias_default, model_field, default_model, capacity_field in (
        ("Core", "coreModel", "gpt-5-terra", "coreModelName", "gpt-5.2", "modelCapacity"),
        (
            "Simple",
            "simpleModel",
            "gpt-5-luna",
            "simpleModelName",
            "gpt-5-mini",
            "simpleModelCapacity",
        ),
    ):
        alias = (
            f"if({foundation}, '{alias_default}', coalesce({tags}['azbrief.v2.{alias_tag}'], ''))"
        )
        model = (
            f"if({foundation}, coalesce(steps('options').{model_field}, '{default_model}'), "
            f"coalesce({options}.{model_field}, ''))"
        )
        simple_version = (
            f"if({foundation}, coalesce(steps('options').simpleModelVersion, '2025-08-07'), "
            f"coalesce({options}.simpleModelVersion, ''))"
        )
        version = (
            core_version
            if role == "Core"
            else f"if(empty({simple_version}), {core_version}, {simple_version})"
        )
        capacity = (
            f"if({foundation}, int(coalesce(steps('options').{capacity_field}, '200')), "
            f"coalesce({options}.{capacity_field}, 0))"
        )
        matches = (
            f"filter(coalesce({deployments_api}.value, parse('[]')), (deployment) => "
            f"equals(toLower(last(split(coalesce(deployment.name, ''), '/'))), toLower({alias})))"
        )
        compatible = _all(
            "equals(deployment.properties.provisioningState, 'Succeeded')",
            "equals(deployment.properties.model.format, 'OpenAI')",
            f"equals(deployment.properties.model.name, {model})",
            f"equals(deployment.properties.model.version, {version})",
            "equals(deployment.sku.name, 'GlobalStandard')",
            _integer_at_least("deployment.sku.capacity", capacity),
        )
        exact_match = f"equals(length(filter({matches}, (deployment) => {compatible})), 1)"
        model_rules.append(
            _all(
                f"not(empty({alias}))",
                f"not(empty({model}))",
                f"startsWith({model}, 'gpt-5')",
                f"not(empty({version}))",
                f"lessOrEquals(length({matches}), 1)",
                f"empty(filter({matches}, (deployment) => not({compatible})))",
            )
        )
        parameters["outputs"][f"reuse{role}Model"] = (
            f"[equals(length(filter({active_accounts}, (account) => "
            f"and({inventory_rules[1]}, equals(length({matches}), 1), {exact_match}))), 1)]"
        )
    valid_inventory = _all(
        *inventory_rules,
        hosts_ready,
        f"not(empty({project_name}))",
        project_absent,
        *model_rules,
    )
    shared_foundry_inventory = _all(
        f"equals(length({active_accounts}), 1)",
        f"empty(filter({active_accounts}, (account) => not({valid_inventory})))",
    )
    foundry_api = "steps('names').existingFoundryAccountApi"
    foundry = _all(
        subnet_readbacks["foundryAccount"],
        f"equals(toLower(coalesce({foundry_api}.kind, '')), 'aiservices')",
        f"equals({foundry_api}.properties.allowProjectManagement, true)",
        f"equals({foundry_api}.properties.disableLocalAuth, true)",
        _valid_foundry_subdomain(f"{foundry_api}.properties.customSubDomainName"),
        f"equals(toLower(coalesce({foundry_api}.properties.publicNetworkAccess, '')), 'disabled')",
        f"equals(toLower(coalesce({foundry_api}.location, '')), toLower({region}))",
        f"not(empty(coalesce({foundry_api}.location, '')))",
        f"equals(length(coalesce({foundry_api}.properties.networkInjections, parse('[]'))), 1)",
        f"equals(length(filter(coalesce({foundry_api}.properties.networkInjections, parse('[]')), "
        "(injection) => "
        + _all(
            "equals(injection.scenario, 'agent')",
            "equals(injection.useMicrosoftManagedNetwork, false)",
            f"equals(toLower(coalesce(injection.subnetArmId, '')), toLower({subnet_ids['foundryAccount']}))",
        )
        + ")), 1)",
        shared_foundry_inventory,
    )
    storage_api = "steps('names').existingStorageAccountApi"
    storage = _all(
        f'contains(parse(\'["storagev2","blobstorage","blockblobstorage"]\'), toLower(coalesce({storage_api}.kind, \'\')))',
        f"equals(toLower(coalesce({storage_api}.properties.publicNetworkAccess, '')), 'disabled')",
        f"equals({storage_api}.properties.allowBlobPublicAccess, false)",
        f"equals({storage_api}.properties.allowSharedKeyAccess, false)",
        f"equals({storage_api}.properties.supportsHttpsTrafficOnly, true)",
        f"equals({storage_api}.properties.minimumTlsVersion, 'TLS1_2')",
    )
    environment = _all(
        subnet_readbacks["containerAppsEnvironment"],
        f"if({foundation}, not(equals(steps('network').deployContainerAppsPrivateEndpoint, true)), not(equals(toLower(coalesce({tags}['azbrief.v2.acaPeEnabled'], '')), 'true')))",
        f"equals({environment_api}.properties.vnetConfiguration.internal, true)",
        f"equals(toLower(coalesce({environment_api}.properties.publicNetworkAccess, '')), 'disabled')",
        f"equals(toLower(coalesce({environment_api}.location, '')), toLower({region}))",
        f"not(empty(coalesce({environment_api}.location, '')))",
        f"equals(toLower(coalesce({environment_api}.properties.vnetConfiguration.infrastructureSubnetId, '')), toLower({subnet_ids['containerAppsEnvironment']}))",
        f"not(empty(coalesce({environment_api}.properties.defaultDomain, '')))",
        f"not(empty(coalesce({environment_api}.properties.staticIp, '')))",
        f"equals(length(filter(coalesce({environment_api}.properties.workloadProfiles, parse('[]')), "
        "(profile) => and(equals(profile.name, 'Consumption'), equals(profile.workloadProfileType, 'Consumption')))), 1)",
        f"equals({environment_api}.properties.appLogsConfiguration.destination, 'log-analytics')",
        f"not(empty({customer_id}))",
    )
    linked_domain = (
        f"contains(map(coalesce({acs_api}.properties.linkedDomains, parse('[]')), "
        f"(domain) => toLower(domain)), toLower({domain_id}))"
    )
    email_api = "steps('names').existingEmailServiceApi"
    domain_api = "steps('names').existingEmailDomainApi"
    communication = _all(
        f"not(empty(coalesce({acs_api}.properties.hostName, '')))",
        f"equals(last(split(toLower(coalesce({acs_api}.properties.hostName, '')), '.communication.azure.com')), '')",
        f"not(empty(coalesce({acs_api}.properties.dataLocation, '')))",
        linked_domain,
        _valid_resource_id(domain_id, "Microsoft.Communication/emailServices/domains", domain_api),
        _valid_resource_id(email_service_id, "Microsoft.Communication/emailServices", email_api),
        f"equals({email_api}.properties.dataLocation, {acs_api}.properties.dataLocation)",
        f"not(empty(coalesce({domain_api}.properties.fromSenderDomain, '')))",
        f"contains(parse('[\"AzureManaged\",\"CustomerManaged\"]'), coalesce({domain_api}.properties.domainManagement, ''))",
        f"if(equals({domain_api}.properties.domainManagement, 'CustomerManaged'), "
        + _all(
            *(
                f"equals({domain_api}.properties.verificationStates.{kind}.status, 'Verified')"
                for kind in ("Domain", "SPF", "DKIM", "DKIM2")
            )
        )
        + ", true)",
    )
    workspace = _all(
        _valid_resource_id(workspace_id, "Microsoft.OperationalInsights/workspaces", workspace_api),
        f"not(empty(coalesce({workspace_api}.properties.customerId, '')))",
        f"equals(toLower(coalesce({workspace_api}.properties.customerId, '')), toLower({customer_id}))",
        f"if({foundation}, or(not(empty({explicit_workspace})), {unique_workspace}), true)",
        f"if(and({foundation}, {unique_workspace}, not(empty({explicit_workspace}))), "
        f"equals(toLower({explicit_workspace}), toLower(coalesce({matched_workspace}.id, ''))), true)",
    )
    rules = [
        {
            "isValid": f"[{new_network_compatible}]",
            "message": "기존 Foundry·Environment는 신규 VNet과 함께 사용할 수 없습니다. 기존 VNet과 각 서비스에 이미 연결된 정확한 전용 서브넷을 선택하십시오.",
        },
        *(
            {
                "isValid": "[if("
                + existing[name]
                + ", "
                + _all(
                    _valid_resource_id(
                        ids[name],
                        SHARED_RESOURCES[name][1],
                        "steps('names').existing" + name[0].upper() + name[1:] + "Api",
                    ),
                    condition,
                )
                + ", true)]",
                "message": message,
            }
            for name, condition, message in (
                (
                    "foundryAccount",
                    foundry,
                    "기존 Foundry의 같은 구독·정확한 ID/유형·Succeeded·프로젝트 지원·Entra 전용·실제 customSubDomainName·사설 네트워크·준비된 계정 Host와 전체 모델/프로젝트 목록을 확인하십시오. 기존 별칭의 모델·버전·GlobalStandard·용량이 다르거나 미완료·중복이면 차단합니다. 같은 이름의 프로젝트가 이미 있으면 다른 워크로드를 덮어쓰지 말고 승인된 CLI로 소유권·Host·텔레메트리의 명시적 재사용을 검증하십시오.",
                ),
                (
                    "storageAccount",
                    storage,
                    "기존 Storage의 같은 구독·정확한 ID/유형·Succeeded·Blob 지원 종류·공용/공유 키 접근 차단·HTTPS/TLS 1.2 구성을 확인할 수 없습니다.",
                ),
                (
                    "containerAppsEnvironment",
                    _all(environment, workspace),
                    "기존 Environment의 같은 구독·정확한 ID/유형·Succeeded·내부/PNA 차단·모델/VNet 리전·전용 서브넷·로그 workspace를 확인할 수 없습니다. 기존 Environment에 새 Private Endpoint를 요청할 수 없습니다. workspace 목록은 전체 페이지와 유일한 customerId 일치가 필요하며, 불완전하면 정확한 기존 workspace를 선택하십시오. 새 workspace로 대체하지 않습니다.",
                ),
                (
                    "communicationServices",
                    communication,
                    "기존 ACS의 같은 구독·정확한 ID/유형·Succeeded·이미 연결된 준비된 도메인과 같은 데이터 위치의 Email Service를 확인하십시오. ID를 직접 입력하거나 다른 도메인으로 대체하지 않습니다.",
                ),
            )
        ),
    ]
    for name, subnet_control in (
        ("foundryAccount", "foundrySubnetName"),
        ("containerAppsEnvironment", "containerAppsSubnetName"),
    ):
        owner_api = "steps('names').existing" + name[0].upper() + name[1:] + "Api"
        exact_binding = (
            f"equals(length(filter(coalesce({owner_api}.properties.networkInjections, parse('[]')), "
            f"(injection) => and(equals(injection.scenario, 'agent'), "
            f"equals(toLower(coalesce(injection.subnetArmId, '')), toLower({subnet_ids[name]}))))), 1)"
            if name == "foundryAccount"
            else f"equals(toLower(coalesce({owner_api}.properties.vnetConfiguration.infrastructureSubnetId, '')), toLower({subnet_ids[name]}))"
        )
        verified_owner = _all(
            existing[name],
            _valid_resource_id(ids[name], SHARED_RESOURCES[name][1], owner_api),
            subnet_readbacks[name],
            exact_binding,
        )
        occupancy = []
        empty_occupancy = []
        # RP 관리 NIC의 ID 위치 대신 검증된 서비스의 정확한 서브넷 바인딩을 신뢰합니다.
        for field in (
            "ipConfigurations",
            "privateEndpoints",
            "serviceAssociationLinks",
            "resourceNavigationLinks",
        ):
            belongs = _all(
                *(
                    f"or(empty(coalesce(link.properties.{owner_field}, '')), "
                    f"equals(toLower(coalesce(link.properties.{owner_field}, '')), toLower({ids[name]})))"
                    for owner_field in ("link", "linkedResourceId", "resourceId")
                ),
            )
            empty_occupancy.append(f"empty(coalesce(subnet.properties.{field}, parse('[]')))")
            occupancy.append(
                f"empty(filter(coalesce(subnet.properties.{field}, parse('[]')), "
                f"(link) => not({belongs})))"
            )
        allowed_subnet = f"or({_all(*empty_occupancy)}, {_all(verified_owner, *occupancy)})"
        occupied = (
            "empty(filter(coalesce(steps('network').virtualNetworkApi.properties.subnets, parse('[]')), "
            f"(subnet) => and(equals(toLower(coalesce(subnet.name, '')), "
            f"toLower(coalesce(steps('network').{subnet_control}, ''))), "
            f"not({allowed_subnet}))))"
        )
        rules.append(
            {
                "isValid": f"[if(and({foundation}, equals({network_mode}, 'existing')), {occupied}, true)]",
                "message": "검증된 기존 서비스의 정확한 서브넷에 속한 RP 관리 NIC는 허용합니다. 새 서비스가 점유 서브넷을 공유하거나 명시된 다른 소유자를 재사용할 수 없습니다.",
            }
        )
    mode_rules = _all(
        *(f'contains(parse(\'["new","existing"]\'), {mode})' for mode in modes.values())
    )
    rules.append(
        {
            "isValid": f"[{mode_rules}]",
            "message": "네 서비스의 배포 방식은 신규 생성 또는 기존 리소스 사용이어야 합니다.",
        }
    )
    rules.append(
        {
            "isValid": f"[if({foundation}, {_foundation_tag_budget(existing, ids, workspace_id, domain_id)}, true)]",
            "message": "기존 고객 태그와 v2 기본 태그·실제 리소스 바인딩 청크를 합해 RG의 50개 한도를 지켜야 합니다. 바인딩은 최대 2,048자·청크당 256자이며 임의의 고정 청크 수로 계산하지 않습니다.",
        }
    )
    binding_rule = _binding_validation("contract", "tags", options, saved)
    combined = _all(
        *(rule["isValid"][1:-1] for rule in rules),
        f"if({foundation}, not({saved_present}), {binding_rule})",
    )
    _extend_guard(parameters, "existingFoundryAccountApi", _shared_context(combined))
    for element in step["elements"]:
        if element["name"] == "namingPrefix":
            element["constraints"]["validations"] = rules
        if element["name"] == "resourceNamePreview":
            preview = []
            for name, (prefix, separator) in RESOURCE_NAME_PARTS.items():
                if name in {"cosmosAccountName", "searchServiceName"}:
                    continue
                value = (
                    f"concat('{name}: {prefix}', coalesce(steps('names').namingPrefix, ''), "
                    f"'{separator}', coalesce(steps('names').namingSuffix, ''), ' | ')"
                )
                owner = (
                    "containerAppsEnvironment"
                    if name == "logAnalyticsWorkspaceName"
                    else "communicationServices" if name == "emailServiceName" else name[:-4]
                )
                preview.append(
                    f"if({existing[owner]}, '', {value})" if owner in existing else value
                )
            element["options"]["text"] = "[concat(" + ", ".join(preview) + ")]"
    completion_items = [
        f"'{label}: ', {modes[name]}, ' | ', coalesce({saved}.{name}ResourceId, '이전 이름 태그'), ' | '"
        for name, (label, _, _, _) in SHARED_RESOURCES.items()
    ]
    selectors.extend(
        (
            {
                "name": "savedResourceBindings",
                "type": "Microsoft.Common.InfoBox",
                "visible": f"[not({foundation})]",
                "options": {
                    "icon": "Info",
                    "text": "[concat('저장된 리소스 (재선택하지 않음): ', "
                    + ", ".join(completion_items)
                    + f", 'workspace: ', coalesce({saved}.logAnalyticsWorkspaceResourceId, '이전 이름 태그'), "
                    + f"' | email domain: ', coalesce({saved}.emailDomainResourceId, '이전 이름 태그'))]",
                },
            },
            {
                "name": "invalidSavedResourceBindings",
                "type": "Microsoft.Common.InfoBox",
                "visible": f"[and(not({foundation}), not({binding_rule}))]",
                "options": {
                    "icon": "Warning",
                    "text": "저장된 리소스 모드·ID·태그 청크가 누락되거나 일치하지 않아 완료를 차단합니다. 정확한 원래 RG의 바인딩을 진단하십시오. 리소스를 재선택하거나 상태 태그를 강제로 바꾸지 마십시오.",
                },
            },
        )
    )
    step["elements"].extend([*selectors, *readbacks])
    network_step = next(step for step in parameters["steps"] if step["name"] == "network")
    for element in network_step["elements"]:
        if element["name"] == "newVirtualNetworkName":
            element["constraints"]["validations"] = [rules[0]]
    options_step = next(step for step in parameters["steps"] if step["name"] == "options")
    for element in options_step["elements"]:
        if element["name"] == "emailDataLocation":
            element["visible"] = f"[and({foundation}, not({existing['communicationServices']}))]"

    def scope_expressions(value: Any) -> Any:
        if isinstance(value, dict):
            return {key: scope_expressions(item) for key, item in value.items()}
        if isinstance(value, list):
            return [scope_expressions(item) for item in value]
        if (
            isinstance(value, str)
            and value.startswith("[")
            and ("saved." in value or re.search(r"(?<![\w.])tags(?:\.|\[|,)", value))
        ):
            return "[" + _shared_context(value[1:-1]) + "]"
        return value

    for current in (step, network_step, options_step):
        current["elements"] = scope_expressions(current["elements"])
    for name, value in parameters["outputs"].items():
        if name != "portalValidationPassed":
            parameters["outputs"][name] = scope_expressions(value)


def _managed_agent_data_outputs(parameters: dict[str, Any]) -> None:
    if not any(step["name"] == "contract" for step in parameters["steps"]):
        return
    chunks = [
        f"coalesce(steps('contract').resourceGroupApi.tags['azbrief.v2.options{index}'], '')"
        for index in range(8)
    ]
    combined = "concat(" + ", ".join(chunks) + ")"
    options = f"parse(if(empty({combined}), '{{}}', {combined}))"
    managed = f"if(equals(basics('infrastructureStage'), 'foundation'), true, coalesce({options}.useMicrosoftManagedAgentData, false))"
    if "useMicrosoftManagedAgentData" not in parameters["outputs"]:
        for name in ("cosmosAccountName", "searchServiceName"):
            existing = parameters["outputs"][name]
            parameters["outputs"][name] = f"[if({managed}, '', {existing[1:-1]})]"
        parameters["outputs"]["useMicrosoftManagedAgentData"] = f"[{managed}]"
    parameters["outputs"][
        "searchSku"
    ] = f"[if(equals(basics('infrastructureStage'), 'complete'), coalesce({options}.searchSku, 'basic'), 'basic')]"
    for step in parameters["steps"]:
        for element in step["elements"]:
            if element["name"] == "searchSku":
                element["visible"] = False


def _network_mode_controls(
    parameters: dict[str, Any], network_validations: list[dict[str, Any]]
) -> None:
    step = next((step for step in parameters["steps"] if step["name"] == "network"), None)
    if step is None:
        return
    foundation = "equals(basics('infrastructureStage'), 'foundation')"
    mode = "coalesce(steps('network').virtualNetworkMode, 'existing')"
    new = f"and({foundation}, equals({mode}, 'new'))"
    existing = f"and({foundation}, equals({mode}, 'existing'))"
    step["label"] = "네트워크 선택"
    new_names = {
        "virtualNetworkMode",
        "newVirtualNetworkName",
        "newNetworkRange",
        "newPeSubnetName",
        "newFoundrySubnetName",
        "newContainerAppsSubnetName",
        "subnetDiscoveryStatus",
    }
    step["elements"] = [element for element in step["elements"] if element["name"] not in new_names]
    step["elements"].insert(
        0,
        {
            "name": "virtualNetworkMode",
            "type": "Microsoft.Common.DropDown",
            "label": "VNet 배포 방식",
            "defaultValue": "기존 VNet 사용",
            "visible": f"[{foundation}]",
            "constraints": {
                "required": True,
                "allowedValues": [
                    {"label": "기존 VNet 사용", "value": "existing"},
                    {"label": "신규 VNet 생성", "value": "new"},
                ],
            },
        },
    )
    for name, label, default in (
        ("newVirtualNetworkName", "신규 VNet 이름", "vnet-azbrief-v2"),
        ("newPeSubnetName", "Private Endpoint 서브넷 이름", "private-endpoints"),
        ("newFoundrySubnetName", "Foundry 전용 서브넷 이름", "foundry-agents"),
        ("newContainerAppsSubnetName", "Container Apps 전용 서브넷 이름", "container-apps"),
    ):
        control: dict[str, Any] = {
            "name": name,
            "type": "Microsoft.Common.TextBox",
            "label": label,
            "defaultValue": default,
            "visible": f"[{new}]",
            "constraints": {
                "required": True,
                "regex": "^[A-Za-z][A-Za-z0-9_-]{0,62}$",
                "validationMessage": "영문자로 시작하는 영문·숫자·하이픈·밑줄 1~63자를 입력하십시오.",
            },
        }
        if name != "newVirtualNetworkName":
            others = {"newPeSubnetName", "newFoundrySubnetName", "newContainerAppsSubnetName"} - {
                name
            }
            control["constraints"]["validations"] = [
                {
                    "isValid": "[and("
                    + ", ".join(
                        f"not(equals(toLower(coalesce(steps('network').{name}, '')), toLower(coalesce(steps('network').{other}, ''))))"
                        for other in sorted(others)
                    )
                    + ")]",
                    "message": "세 역할의 서브넷 이름은 서로 달라야 합니다.",
                }
            ]
        step["elements"].append(control)
    step["elements"].append(
        {
            "name": "newNetworkRange",
            "type": "Microsoft.Common.DropDown",
            "label": "신규 VNet 주소 범위",
            "defaultValue": "10.240.0.0/16",
            "visible": f"[{new}]",
            "toolTip": "세 서브넷은 /24, /27, /28로 자동 분리합니다. 기존·연결 예정 네트워크와 겹치지 않는 범위를 선택하십시오. 임의 CIDR은 보호된 CLI 매개변수와 검증을 사용합니다.",
            "constraints": {
                "required": True,
                "allowedValues": [
                    {"label": value + ".0.0/16", "value": value}
                    for value in ("10.240", "10.241", "172.28")
                ],
            },
        }
    )
    for element in step["elements"]:
        if element["name"] in {
            "virtualNetwork",
            "peSubnetName",
            "foundrySubnetName",
            "containerAppsSubnetName",
        }:
            element["visible"] = f"[{existing}]"
        if element["name"] == "virtualNetworkApi":
            element.pop("visible", None)
            # 정상 KT 폼의 직접 선택 의존성을 유지하고 구독 응답으로 대체하지 않습니다.
            element["request"][
                "path"
            ] = "[concat(steps('network').virtualNetwork.id, '?api-version=2024-05-01')]"
    response = "steps('network').virtualNetworkApi"
    selected_id = "coalesce(steps('network').virtualNetwork.id, '')"
    same_vnet = f"equals(toLower(coalesce({response}.id, '')), toLower({selected_id}))"
    has_subnets = f"not(equals(coalesce({response}.properties.subnets, false), false))"
    error = f"not(empty({response}.error))"
    status = {
        "name": "subnetDiscoveryStatus",
        "type": "Microsoft.Common.InfoBox",
        "visible": f"[and({existing}, not(empty({selected_id})), or({error}, not({same_vnet}), not({has_subnets}), empty({response}.properties.subnets)))]",
        "options": {
            "icon": "Warning",
            "text": (
                f"[if({error}, concat('서브넷 조회 실패: ', coalesce({response}.error.code, 'ARMReadFailed'), "
                "'. 선택한 VNet의 읽기 권한(Microsoft.Network/virtualNetworks/read)과 구독을 확인하십시오. 권한을 자동 변경하지 않습니다.'), "
                f"if(not({same_vnet}), concat('선택한 VNet의 조회 응답이 확인되지 않았습니다. 선택 ID: ', {selected_id}, "
                f"' · 응답 ID: ', if(empty(coalesce({response}.id, '')), '응답 없음', coalesce({response}.id, '')), "
                "'. 이 상태는 리전 불일치나 서브넷 없음으로 판단하지 않습니다. 현재 탭을 다시 열어도 지속되면 이 ID와 Portal 진단을 확인하십시오. 이전 응답으로 배포할 수 없습니다.'), "
                f"if(not({has_subnets}), 'VNet 응답에 서브넷 목록이 없어 선택을 진행할 수 없습니다. VNet 조회 응답을 확인하십시오.', "
                "'선택한 VNet에는 서브넷이 없습니다. 기존 서브넷이 있는 VNet을 선택하거나 별도로 승인된 네트워크 준비를 완료하십시오.')))]"
            ),
        },
    }
    api_index = next(
        index
        for index, element in enumerate(step["elements"])
        if element["name"] == "virtualNetworkApi"
    )
    step["elements"].insert(api_index + 1, status)
    outputs = parameters["outputs"]
    chunks = [
        f"coalesce(steps('contract').resourceGroupApi.tags['azbrief.v2.options{index}'], '')"
        for index in range(8)
    ]
    combined = "concat(" + ", ".join(chunks) + ")"
    saved = f"parse(if(empty({combined}), '{{}}', {combined}))"
    base = "coalesce(steps('network').newNetworkRange, '10.240')"
    if "virtualNetworkMode" not in outputs:
        for name, new_value in {
            "virtualNetworkResourceGroupName": "resourceGroup().name",
            "virtualNetworkName": "steps('network').newVirtualNetworkName",
            "peSubnetName": "steps('network').newPeSubnetName",
            "foundrySubnetName": "steps('network').newFoundrySubnetName",
            "containerAppsSubnetName": "steps('network').newContainerAppsSubnetName",
        }.items():
            outputs[name] = f"[if({new}, {new_value}, {outputs[name][1:-1]})]"
        outputs["virtualNetworkMode"] = (
            f"[if({foundation}, {mode}, coalesce({saved}.virtualNetworkMode, 'existing'))]"
        )
        for name, suffix, default in (
            ("virtualNetworkAddressPrefix", ".0.0/16", "10.240.0.0/16"),
            ("foundrySubnetAddressPrefix", ".0.0/24", "10.240.0.0/24"),
            ("containerAppsSubnetAddressPrefix", ".1.0/27", "10.240.1.0/27"),
            ("peSubnetAddressPrefix", ".1.32/28", "10.240.1.32/28"),
        ):
            outputs[name] = (
                f"[if({foundation}, concat({base}, '{suffix}'), coalesce({saved}.{name}, '{default}'))]"
            )
    guard = outputs["portalValidationPassed"]
    if "newVirtualNetworkName" not in guard:
        for validation in network_validations:
            original = validation["isValid"][1:-1]
            guard = guard.replace(original, f"if({new}, true, {original})")
        guard = f"[and({guard[1:-1]}, if({new}, and(not(empty(coalesce(steps('network').newVirtualNetworkName, ''))), not(empty(coalesce(steps('network').newPeSubnetName, ''))), not(equals(toLower(coalesce(steps('network').newFoundrySubnetName, '')), toLower(coalesce(steps('network').newContainerAppsSubnetName, ''))))), true))]"
    outputs["portalValidationPassed"] = guard


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


def _application_deployment_tag_guard(parameters: dict[str, Any]) -> None:
    if not any(step["name"] == "application" for step in parameters["steps"]):
        return
    existing = parameters["outputs"]["portalValidationPassed"]
    if "azbrief_v2_applicationState" in existing or "azbrief.v2.applicationState" in existing:
        return
    tags = "coalesce(steps('application').resourceGroupApi.tags, parse('{}'))"
    state_key = "azbrief.v2.applicationState"
    timestamp_key = "azbrief.v2.lastApplicationDeploymentAt"
    has_state = f"contains({tags}, '{state_key}')"
    has_timestamp = f"contains({tags}, '{timestamp_key}')"
    state = f"coalesce(steps('application').resourceGroupApi.tags['{state_key}'], '')"
    timestamp = f"coalesce(steps('application').resourceGroupApi.tags['{timestamp_key}'], '')"
    budget = f"lessOrEquals(length({tags}), sub(sub(50, if({has_state}, 0, 1)), if({has_timestamp}, 0, 1)))"
    pair = (
        f"or(and(not({has_state}), not({has_timestamp})), "
        f"and({has_state}, {has_timestamp}, or(and(equals({state}, 'not_deployed'), empty({timestamp})), "
        f"and(equals({state}, 'deployed'), equals(length({timestamp}), 20), equals(last(split({timestamp}, 'Z')), '')))))"
    )
    parameters["outputs"]["portalValidationPassed"] = f"[and({existing[1:-1]}, {budget}, {pair})]"


def _application_shared_binding_guard(parameters: dict[str, Any]) -> None:
    step = next((step for step in parameters["steps"] if step["name"] == "application"), None)
    if step is None:
        return
    condition = _shared_context(
        _binding_validation("application", "tags"),
        "application",
        include_saved=False,
    )
    _extend_guard(parameters, "binding.stateContainerName", condition)
    image = next(element for element in step["elements"] if element["name"] == "containerImage")
    image["constraints"]["validations"].append(
        {
            "isValid": f"[{condition}]",
            "message": "저장된 리소스 바인딩이 불완전하거나 다른 구독·이름을 가리킵니다. 원래 v2 인프라의 모드·ID·연속 태그 청크를 진단하십시오. 앱 설치에서 공유 리소스를 다시 선택하지 않습니다.",
        }
    )


def _separate_region_inputs(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _separate_region_inputs(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_separate_region_inputs(item) for item in value]
    if not isinstance(value, str) or not value.startswith("["):
        return value
    foundation = "equals(basics('infrastructureStage'), 'foundation')"
    new_network = "equals(coalesce(steps('network').virtualNetworkMode, 'existing'), 'new')"
    old_region = "coalesce(steps('network').modelDeploymentRegion, 'eastus')"
    vnet_region = "toLower(coalesce(steps('network').virtualNetworkApi.location, steps('network').virtualNetwork.location, ''))"
    old_guard = (
        f"if(and({foundation}, not({new_network})), "
        f"and(not(empty({vnet_region})), equals({vnet_region}, {old_region})), true)"
    )
    new_guard = f"if({foundation}, not(empty({INFRASTRUCTURE_REGION})), true)"
    return value.replace(old_guard, new_guard).replace(old_region, INFRASTRUCTURE_REGION)


def _model_selection_controls(parameters: dict[str, Any]) -> None:
    step = next((step for step in parameters["steps"] if step["name"] == "options"), None)
    if step is None:
        return
    location_config = parameters["config"]["basics"]["location"]
    location_config["label"] = "신규 인프라 배포 리전"
    location_config["visible"] = True
    location_config["resourceTypes"] = [
        "Microsoft.CognitiveServices/accounts",
        "Microsoft.App/managedEnvironments",
    ]
    fields = {
        "coreModelName": ("Core 모델 이름 (GPT-5 계열)", "gpt-5.2"),
        "simpleModelName": ("Simple 모델 이름 (GPT-5 계열)", "gpt-5-mini"),
        "modelVersion": ("Core 모델 버전", "2025-12-11"),
        "simpleModelVersion": ("Simple 모델 버전", "2025-08-07"),
    }
    names = set(fields) | {"modelCapacity", "simpleModelCapacity", "modelRegionNotice"}
    step["elements"] = [element for element in step["elements"] if element["name"] not in names]
    foundation = "equals(basics('infrastructureStage'), 'foundation')"
    region = INFRASTRUCTURE_REGION
    network_step = next(step for step in parameters["steps"] if step["name"] == "network")
    network_step["elements"] = [
        element
        for element in network_step["elements"]
        if element["name"]
        not in {"modelDeploymentRegion", "modelRegionMismatchNotice", "infrastructureRegionNotice"}
    ]
    network_step["elements"].insert(
        0,
        {
            "name": "infrastructureRegionNotice",
            "type": "Microsoft.Common.InfoBox",
            "visible": f"[{foundation}]",
            "options": {
                "icon": "Info",
                "text": "기존 VNet은 실제 조회된 리전을 그대로 사용합니다. 신규 VNet만 기본 사항의 '신규 인프라 배포 리전'에 생성합니다. 이 설정은 Global Standard의 모델 추론 지역 선택이 아니며, 모델 이름·버전·용량이나 후보 조회 지역을 바꿔도 VNet·Environment의 리전은 변경되지 않습니다.",
            },
        },
    )
    for element in network_step["elements"]:
        if element["name"] == "virtualNetwork":
            element["options"]["filter"]["location"] = "all"
    guard = parameters["outputs"]["portalValidationPassed"]
    region_guard = f"if({foundation}, not(empty({region})), true)"
    if region_guard not in guard:
        parameters["outputs"]["portalValidationPassed"] = f"[and({guard[1:-1]}, {region_guard})]"
    chunks = [
        f"coalesce(steps('contract').resourceGroupApi.tags['azbrief.v2.options{index}'], '')"
        for index in range(8)
    ]
    combined = "concat(" + ", ".join(chunks) + ")"
    saved = f"parse(if(empty({combined}), '{{}}', {combined}))"
    actual_model_region = (
        "if(equals(coalesce(steps('names').foundryAccountMode, 'new'), 'existing'), "
        "toLower(coalesce(steps('names').existingFoundryAccountApi.location, '')), "
        f"{region})"
    )
    new_profile_notice = (
        f"concat('GlobalStandard 모델·quota 리전: ', "
        f"if(empty({actual_model_region}), '미확인', {actual_model_region}), "
        "'. 모델 배포의 관리·할당량 리전은 부모 Foundry 계정에서 결정되며 별도 location 속성은 없습니다. "
        "추론 처리 위치는 Microsoft가 전역으로 라우팅하므로 지역을 고정하지 않습니다. "
        "다른 리전 모델은 별도 모델 리소스와 연결이 필요하며 이 템플릿이 자동 생성하지 않습니다. "
        "Core gpt-5.2 / Simple gpt-5-mini, GlobalStandard 각 200단위가 기본이며 PTU는 사용하지 않습니다. "
        "CLI --list-model-options --model-regions는 읽기 전용 후보 조회이며 인프라 리전을 변경하지 않습니다.')"
    )
    completed_profile_notice = (
        f"concat('저장된 배포: 리전 ', coalesce(steps('contract').resourceGroupApi.tags['azbrief.v2.location'], '미확인'), "
        f"', Core ', coalesce({saved}.coreModelName, '미확인'), ' / ', coalesce({saved}.modelVersion, '미확인'), "
        f"', Simple ', coalesce({saved}.simpleModelName, '미확인'), ' / ', coalesce({saved}.simpleModelVersion, {saved}.modelVersion, '미확인'), "
        "'. 기존 기반 완성은 이 값을 재사용하며 신규 기본값으로 변경하지 않습니다. "
        "모델 이름·버전·용량은 기반 준비에서 선택하며 모델 관리 리전은 부모 계정에서 결정됩니다. "
        "GlobalStandard의 추론은 전역 라우팅되며 VNet 리전을 설정하지 않습니다. 저장된 모델의 quota가 부족하면 "
        "모델·프로젝트가 모두 없는지 확인한 후 승인된 CLI --stage complete --reselect-models로 "
        "모델만 명시적으로 재선택하십시오. 리전과 기존 자원은 바꾸지 않습니다. PTU는 사용하지 않습니다.')"
    )
    step["elements"].append(
        {
            "name": "modelRegionNotice",
            "type": "Microsoft.Common.InfoBox",
            "options": {
                "icon": "Warning",
                "text": f"[if({foundation}, {new_profile_notice}, {completed_profile_notice})]",
            },
        }
    )
    for name, (label, default) in fields.items():
        step["elements"].append(
            {
                "name": name,
                "type": "Microsoft.Common.TextBox",
                "label": label,
                "defaultValue": default,
                "visible": f"[{foundation}]",
                "constraints": {
                    "required": True,
                    "regex": (
                        "^gpt-5[A-Za-z0-9._-]{0,80}$"
                        if name.endswith("Name")
                        else "^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"
                    ),
                    "validationMessage": "후보 조회에서 확인한 정확한 모델 이름과 버전을 입력하십시오.",
                },
            }
        )
        persisted = (
            f"coalesce({saved}.simpleModelVersion, {saved}.modelVersion, '{default}')"
            if name == "simpleModelVersion"
            else f"{saved}.{name}"
        )
        parameters["outputs"][
            name
        ] = f"[if({foundation}, coalesce(steps('options').{name}, '{default}'), {persisted})]"
    for name, label in (
        ("modelCapacity", "Core 용량 단위"),
        ("simpleModelCapacity", "Simple 용량 단위"),
    ):
        step["elements"].append(
            {
                "name": name,
                "type": "Microsoft.Common.DropDown",
                "label": label,
                "defaultValue": "200",
                "visible": f"[{foundation}]",
                "constraints": {
                    "required": True,
                    "allowedValues": [
                        {"label": str(value), "value": str(value)}
                        for value in (1, 10, 50, 100, 200, 500, 1000)
                    ],
                },
                "toolTip": "GlobalStandard의 처리량 설정이며 PTU 구매가 아닙니다. 선택 모델의 quota·공급 용량·부하를 검증하십시오. 배포 수락이나 런타임 처리량을 보장하지 않습니다.",
            }
        )
        parameters["outputs"][
            name
        ] = f"[if({foundation}, int(coalesce(steps('options').{name}, '200')), {saved}.{name})]"
    for element in step["elements"]:
        if element["name"] == "automaticSettings":
            element["options"][
                "text"
            ] = "모델 논리 배포 별칭·Hosted 이름·MCP 연결 이름은 자동 설정합니다. 실제 모델 이름·버전·용량은 명시 선택하며 부모 Foundry 리전의 카탈로그·quota·공급 용량을 검증합니다. 이 모델 설정이 VNet 리전을 변경하지 않습니다."
    complete_location = (
        "coalesce(steps('contract').resourceGroupApi.tags['azbrief.v2.location'], '')"
    )
    parameters["outputs"]["location"] = f"[if({foundation}, {region}, {complete_location})]"


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
    parameters = _separate_region_inputs(result["parameters"])
    result["parameters"] = parameters
    if any(step["name"] == "contract" for step in parameters["steps"]):
        existing_guard = parameters["outputs"].get("portalValidationPassed")
        if isinstance(existing_guard, str):
            retained = _without_guard_extension(existing_guard[1:-1], "existingFoundryAccountApi")
            if not retained:
                raise ValueError("The infrastructure Portal base validation cannot be removed")
            parameters["outputs"]["portalValidationPassed"] = "[" + retained + "]"
    config = parameters.setdefault("config", {})
    config["isWizard"] = False
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
    _managed_agent_data_outputs(parameters)
    _remove_validation_surfaces(parameters)
    _network_mode_controls(parameters, network_validations)
    _model_selection_controls(parameters)
    _shared_resource_controls(parameters)
    _application_deployment_tag_guard(parameters)
    for step in parameters["steps"]:
        for element in step["elements"]:
            if element["name"] == "stageNotice":
                element["options"]["text"] = (
                    "인프라 내부 단계는 기반 준비 → 계정 host 준비 확인 → 기존 기반 완성입니다. "
                    "Portal은 Microsoft Graph를 호출하지 않습니다. 코어 완성 후 mcp_pending 상태에서 "
                    "권한이 확인된 CLI의 --prepare-mcp-authentication을 1회 실행하면 MCP 인증·연결을 "
                    "자동 준비하고 ready로 전환합니다. 내부 ID를 수동 입력하거나 인증을 해제하지 않습니다."
                )
        if step["name"] == "application":
            for element in step["elements"]:
                if element["name"] == "containerImage":
                    element["constraints"]["validations"] = [
                        {
                            "isValid": "[not(equals(coalesce(steps('application').resourceGroupApi.tags['azbrief.v2.state'], ''), 'foundation'))]",
                            "message": "현재 RG는 기반 준비(foundation)만 완료됐습니다. 인프라 배포에서 '기존 기반 완성'을 같은 RG로 실행한 뒤 MCP 인증 준비까지 완료하십시오. 앱 배포를 먼저 진행할 수 없습니다.",
                        },
                        {
                            "isValid": "[not(equals(coalesce(steps('application').resourceGroupApi.tags['azbrief.v2.state'], ''), 'mcp_pending'))]",
                            "message": "코어는 완료됐지만 MCP 인증 준비가 남았습니다. 같은 RG에서 승인된 CLI의 --prepare-mcp-authentication --mode deploy를 완료한 뒤 앱 배포를 다시 여십시오.",
                        },
                        {
                            "isValid": "[equals(coalesce(steps('application').resourceGroupApi.tags['azbrief.v2.state'], ''), 'ready')]",
                            "message": "완료된 v2 인프라의 같은 기존 RG를 선택하십시오. azbrief.v2.state=ready가 확인돼야 앱을 배포할 수 있습니다. 태그나 portalValidationPassed를 수동으로 바꾸지 마십시오.",
                        },
                    ]
            step["elements"] = [
                element
                for element in step["elements"]
                if element["name"]
                not in {"mcpPendingNotice", "foundationIncompleteNotice", "automaticSetupNotice"}
            ]
            step["elements"].append(
                {
                    "name": "foundationIncompleteNotice",
                    "type": "Microsoft.Common.InfoBox",
                    "visible": "[equals(coalesce(steps('application').resourceGroupApi.tags['azbrief.v2.state'], ''), 'foundation')]",
                    "options": {
                        "icon": "Warning",
                        "text": (
                            "이 RG는 기반 준비만 완료됐습니다. 인프라 배포 화면에서 '기존 기반 완성'을 "
                            "같은 RG에 실행해 모델·프로젝트·BYO·권한을 먼저 완성하십시오. 모델 quota가 "
                            "없으면 이 단계가 완료되지 않습니다. 이후 CLI MCP 인증 준비로 ready가 된 뒤 "
                            "앱을 배포합니다. portalValidationPassed나 상태 태그를 수동 변경하지 마십시오."
                        ),
                    },
                }
            )
            step["elements"].append(
                {
                    "name": "mcpPendingNotice",
                    "type": "Microsoft.Common.InfoBox",
                    "visible": "[equals(coalesce(steps('application').resourceGroupApi.tags['azbrief.v2.state'], ''), 'mcp_pending')]",
                    "options": {
                        "icon": "Warning",
                        "text": (
                            "코어 인프라는 완료됐지만 MCP 인증 준비가 필요합니다. 같은 RG에서 "
                            "python -m scripts.deploy_v2 infrastructure --prepare-mcp-authentication "
                            "--subscription <구독> --tenant <테넌트> --resource-group <RG> --mode deploy를 "
                            "권한이 확인된 CLI로 실행하십시오. ready가 되기 전에는 앱 설치를 차단합니다."
                        ),
                    },
                }
            )
            step["elements"].append(
                {
                    "name": "automaticSetupNotice",
                    "type": "Microsoft.Common.InfoBox",
                    "visible": "[equals(coalesce(steps('application').resourceGroupApi.tags['azbrief.v2.state'], ''), 'ready')]",
                    "options": {
                        "icon": "Info",
                        "text": (
                            "메일 수신자·관리자·Archive 독자를 배포자로 기본 설정하고 모든 웹 UI를 켜려면 "
                            "최초 설치를 python -m scripts.deploy_v2 application --subscription <구독> "
                            "--tenant <테넌트> --resource-group <RG> --image <승인된 이미지 digest> --mode deploy로 "
                            "실행하십시오. CLI가 전용 Entra 로그인 앱과 기간 제한 시크릿을 준비합니다. "
                            "Portal만으로 최초 설치하면 웹 UI는 꺼진 상태이며, Portal 업데이트는 기존 "
                            "수신자·인증·접근 목록을 보존합니다. 로그인 권한·사설 접속·실제 발송은 별도 확인합니다."
                        ),
                    },
                }
            )
    _application_shared_binding_guard(parameters)
    return cast(dict[str, Any], _portable_tag_access(result))


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
