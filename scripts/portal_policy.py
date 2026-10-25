"""Generate the server-side equivalent of the canonical Portal validation policy."""

from __future__ import annotations

import ast
import hashlib
import json
from typing import Any

from scripts.ui_expressions import parse_expression, safe_member

MISSING = {"__azbrief_missing": True}
RESOURCE = {"id": None, "name": None, "type": None, "location": None}
LIST_ROW = {
    **RESOURCE,
    "tags": {"deploymentProfile": None},
    "properties": {
        "provisioningState": None,
        "customerId": None,
        "model": {"format": None, "name": None, "version": None},
    },
    "sku": {"name": None, "capacity": None},
}
PARENT = {
    **RESOURCE,
    "kind": None,
    "properties": {
        "provisioningState": None,
        "allowProjectManagement": None,
        "disableLocalAuth": None,
        "customSubDomainName": None,
        "publicNetworkAccess": None,
        "allowBlobPublicAccess": None,
        "allowSharedKeyAccess": None,
        "supportsHttpsTrafficOnly": None,
        "minimumTlsVersion": None,
        "defaultDomain": None,
        "staticIp": None,
        "dataLocation": None,
        "hostName": None,
        "customerId": None,
        "fromSenderDomain": None,
        "domainManagement": None,
        "verificationStates": {
            name: {"status": None} for name in ("Domain", "SPF", "DKIM", "DKIM2")
        },
        "vnetConfiguration": {"internal": None, "infrastructureSubnetId": None},
        "appLogsConfiguration": {
            "destination": None,
            "logAnalyticsConfiguration": {"customerId": None},
        },
        "networkInjections": [
            {
                "scenario": None,
                "useMicrosoftManagedNetwork": None,
                "subnetArmId": None,
            }
        ],
        "workloadProfiles": [{"name": None, "workloadProfileType": None}],
        "linkedDomains": [None],
    },
}
PARENT_FIELDS = {
    "existingFoundryAccountApi": {
        "provisioningState",
        "allowProjectManagement",
        "disableLocalAuth",
        "customSubDomainName",
        "publicNetworkAccess",
        "networkInjections",
    },
    "existingStorageAccountApi": {
        "provisioningState",
        "publicNetworkAccess",
        "allowBlobPublicAccess",
        "allowSharedKeyAccess",
        "supportsHttpsTrafficOnly",
        "minimumTlsVersion",
    },
    "existingContainerAppsEnvironmentApi": {
        "provisioningState",
        "publicNetworkAccess",
        "defaultDomain",
        "staticIp",
        "vnetConfiguration",
        "appLogsConfiguration",
        "workloadProfiles",
    },
    "existingCommunicationServicesApi": {
        "provisioningState",
        "linkedDomains",
        "dataLocation",
        "hostName",
    },
    "existingLogAnalyticsWorkspaceApi": {"provisioningState", "customerId"},
    "existingEmailServiceApi": {"provisioningState", "dataLocation"},
    "existingEmailDomainApi": {
        "provisioningState",
        "fromSenderDomain",
        "domainManagement",
        "verificationStates",
    },
}
SUBNET = {
    "id": None,
    "name": None,
    "properties": {
        "addressPrefix": None,
        "addressPrefixes": [None],
        "delegations": [{"properties": {"serviceName": None}}],
        **{
            key: [
                {
                    "id": None,
                    "properties": {
                        "link": None,
                        "linkedResourceId": None,
                        "resourceId": None,
                    },
                }
            ]
            for key in (
                "ipConfigurations",
                "privateEndpoints",
                "serviceAssociationLinks",
                "resourceNavigationLinks",
            )
        },
    },
}


def evidence_schema(parameters: dict[str, Any], tag_keys: tuple[str, ...]) -> dict[str, Any]:
    schema: dict[str, Any] = {"groupMode": None, "tagCount": None}
    for step in parameters["steps"]:
        fields: dict[str, Any] = {}
        for element in step["elements"]:
            name = element["name"]
            kind = element["type"]
            if kind == "Microsoft.Solutions.ArmApiControl":
                if name == "resourceGroupApi":
                    fields[name] = {
                        "id": None,
                        "tags": {"__tag_keys": tag_keys},
                        "error": "presence",
                        "nextLink": "presence",
                    }
                elif name == "mcpSetupNetworkApi":
                    fields[name] = {
                        **RESOURCE,
                        "properties": {
                            "provisioningState": None,
                            "subnets": [
                                {
                                    "id": None,
                                    "name": None,
                                    "properties": {
                                        "delegations": [{"properties": {"serviceName": None}}],
                                    },
                                }
                            ],
                        },
                        "error": "presence",
                        "nextLink": "presence",
                    }
                elif name == "virtualNetworkApi":
                    fields[name] = {
                        **RESOURCE,
                        "properties": {"subnets": [SUBNET]},
                        "error": "presence",
                        "nextLink": "presence",
                    }
                elif name in {
                    "sharedFoundryHostsApi",
                    "sharedFoundryDeploymentsApi",
                    "sharedFoundryProjectsApi",
                    "logAnalyticsWorkspacesApi",
                    "containerAppsApi",
                    "jobsApi",
                }:
                    fields[name] = {
                        "value": [LIST_ROW],
                        "error": "presence",
                        "nextLink": "presence",
                    }
                else:
                    fields[name] = {
                        **RESOURCE,
                        "kind": None,
                        "properties": {
                            field: PARENT["properties"][field]
                            for field in PARENT["properties"]
                            if field in PARENT_FIELDS[name]
                        },
                        "error": "presence",
                        "nextLink": "presence",
                    }
            elif kind == "Microsoft.Solutions.ResourceSelector":
                fields[name] = RESOURCE
            elif kind not in {"Microsoft.Common.InfoBox", "Microsoft.Common.TextBlock"}:
                fields[name] = None
        schema[step["name"]] = fields
    return schema


def _quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


class _Evidence:
    def __init__(self) -> None:
        self.index = 0

    def get(self, parent: str, key: str, default: Any) -> str:
        return safe_member("[" + parent + "]", key, default)[1:-1]

    def atom(self, parent: str, key: str, presence: bool = False) -> str:
        self.index += 1
        name = f"atom{self.index}"
        source = f"string(coalesce({parent}, parse('{{}}')))"
        if "." in key:
            alias = key.replace(".", "_")
            source = (
                "replace(replace("
                + source
                + ", "
                + _quote(json.dumps(alias) + ":")
                + ", "
                + _quote(json.dumps(key.replace(".", "/")) + ":")
                + "), "
                + _quote(json.dumps(key) + ":")
                + ", "
                + _quote(json.dumps(alias) + ":")
                + ")"
            )
            key = alias
        source = (
            "replace("
            + source
            + ", "
            + _quote(json.dumps(key) + ":null")
            + ", "
            + _quote(json.dumps(key) + ":" + json.dumps(MISSING, separators=(",", ":")))
            + ")"
        )
        rows = (
            "filter(parse(concat('[', " + source + ", ']')), "
            f"(presentAtom) => contains(presentAtom, {_quote(key)}))"
        )
        value = name + "." + key
        if presence:
            missing = _quote(json.dumps(MISSING, separators=(",", ":")))
            value = f"if(or(empty({value}), equals(string({value}), {missing})), '', 'present')"
        rows = f"map({rows}, ({name}) => {value})"
        return (
            """concat('"', encodeBase64(string(filter("""
            + rows
            + ", (serializedAtom) => not(equals(string(serializedAtom), "
            + _quote(json.dumps(MISSING, separators=(",", ":")))
            + """))))), '"')"""
        )

    def value_atom(self, expression: str) -> str:
        return (
            """concat('"', encodeBase64(string(map(parse('[1]'), """
            "(atomValue) => " + expression + """ ))), '"')"""
        )

    def object(self, parent: str, schema: dict[str, Any], active: set[str] | None = None) -> str:
        if active is not None and not active:
            return _quote(json.dumps(self.missing(schema), separators=(",", ":")))
        self.index += 1
        name = f"record{self.index}"
        body = self._object(name, schema, active)
        return (
            "string(first(map(parse(concat('[', string(coalesce("
            + parent
            + ", parse('{}'))), ']')), ("
            + name
            + ") => parse("
            + body
            + "))))"
        )

    def step(self, name: str, schema: dict[str, Any], active: set[str]) -> str:
        parts = ["'{'"]
        for index, (key, shape) in enumerate(schema.items()):
            parts.append(_quote(("" if index == 0 else ",") + json.dumps(key) + ":"))
            source = f"steps('{name}').{key}"
            if key not in active:
                value = _quote(json.dumps(self.missing(shape), separators=(",", ":")))
            elif shape is None:
                value = self.value_atom(source)
            else:
                value = self.object(source, shape)
            parts.append(value)
        parts.append("'}'")
        return "concat(" + ", ".join(parts) + ")"

    @staticmethod
    def missing(schema: Any) -> Any:
        if schema is None or schema == "presence":
            return "W10="
        if isinstance(schema, list):
            return {"present": False, "rows": []}
        if "__tag_keys" in schema:
            return {key: "W10=" for key in schema["__tag_keys"]}
        return {key: _Evidence.missing(value) for key, value in schema.items()}

    def _object(self, parent: str, schema: dict[str, Any], active: set[str] | None = None) -> str:
        parts = ["'{'"]
        for index, (key, shape) in enumerate(schema.items()):
            parts.append(_quote(("" if index == 0 else ",") + json.dumps(key) + ":"))
            if active is not None and key not in active:
                value = _quote(json.dumps(self.missing(shape), separators=(",", ":")))
            elif shape is None:
                value = self.atom(parent, key)
            elif shape == "presence":
                value = self.atom(parent, key, presence=True)
            elif isinstance(shape, list):
                self.index += 1
                item = f"row{self.index}"
                original = self.get(parent, key, MISSING)
                available = f"not(equals(string({original}), '{json.dumps(MISSING, separators=(',', ':'))}'))"
                rows = self.get(parent, key, [])
                if shape[0] is None:
                    encoded = f"map({rows}, ({item}) => encodeBase64({item}))"
                else:
                    encoded = f"map({rows}, ({item}) => parse({self.object(item, shape[0])}))"
                value = (
                    """concat('{"present":', if("""
                    + available
                    + """, 'true', 'false'), ',"rows":', string("""
                    + encoded
                    + "), '}')"
                )
            elif "__tag_keys" in shape:
                tag_schema = {name: None for name in shape["__tag_keys"]}
                value = self.object(self.get(parent, key, {}), tag_schema)
            else:
                value = self.object(self.get(parent, key, {}), shape)
            parts.append(value)
        parts.append("'}'")
        return "concat(" + ", ".join(parts) + ")"


def policy_hash(parameters: dict[str, Any]) -> str:
    validations = [
        {
            "visible": element.get("visible", True),
            "rules": element.get("constraints", {}).get("validations", []),
        }
        for step in parameters["steps"]
        for element in step["elements"]
        if element.get("constraints", {}).get("validations")
    ]
    return hashlib.sha256(
        json.dumps(
            {"outputs": parameters["outputs"], "validations": validations},
            sort_keys=True,
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()


def evidence_expression(
    parameters: dict[str, Any],
    tag_keys: tuple[str, ...],
    view: dict[str, Any] | None = None,
    *,
    resources: bool = False,
) -> str:
    builder = _Evidence()
    schema = evidence_schema(parameters, tag_keys)
    active = (
        {step["name"]: {element["name"] for element in step["elements"]} for step in view["steps"]}
        if view is not None
        else None
    )
    api_controls = {
        (step["name"], element["name"])
        for step in parameters["steps"]
        for element in step["elements"]
        if element["type"] == "Microsoft.Solutions.ArmApiControl"
    }
    header = "resourcePolicyHash" if resources else "policyHash"
    parts = [_quote('{"schema":1,"' + header + '":"' + policy_hash(parameters) + '"')]
    api = (
        "coalesce(steps('"
        + ("contract" if "contract" in schema else "application")
        + "').resourceGroupApi, parse('{}'))"
    )
    bag = builder.get(api, "tags", {})
    for key, shape in schema.items():
        if key in {"groupMode", "tagCount"}:
            if resources != (key == "tagCount"):
                continue
        else:
            shape = {
                field: value
                for field, value in shape.items()
                if ((key, field) in api_controls) == resources
            }
            if not shape:
                continue
        parts.append(_quote("," + json.dumps(key) + ":"))
        if key == "groupMode":
            parts.append(builder.value_atom("resourceGroup().mode"))
        elif key == "tagCount":
            parts.append(builder.value_atom(f"length({bag})"))
        else:
            parts.append(
                builder.step(
                    key, shape, active.get(key, set()) if active is not None else set(shape)
                )
            )
    parts.append("'}'")
    return "[parse(concat(" + ", ".join(parts) + "))]"


class _Arm:
    def __init__(self) -> None:
        self.variables: dict[str, Any] = {}
        self.cache: dict[str, str] = {}
        self.global_names: dict[str, str] = {}
        self.functions: dict[str, Any] = {}
        self.function_cache: dict[str, tuple[str, list[str], dict[str, str]]] = {}

    def store(self, expression: str) -> str:
        if expression in self.cache:
            return f"variables('{self.cache[expression]}')"
        if len(self.variables) >= 240:
            return expression
        name = f"verification{len(self.variables)}"
        self.variables[name] = "[" + expression + "]"
        self.cache[expression] = name
        return f"variables('{name}')"

    def atom(self, value: str) -> str:
        return f"tryGet(json(base64ToString({value})), 0)"

    def decode(self, value: str, schema: Any) -> str:
        if schema is None or schema == "presence":
            return self.atom(value)
        if isinstance(schema, list):
            row = "lambdaVariables('decodedRow')"
            decoded = f"base64ToString({row})" if schema[0] is None else self.decode(row, schema[0])
            return (
                f"if(tryGet({value}, 'present'), "
                f"map(tryGet({value}, 'rows'), lambda('decodedRow', " + decoded + ")), null())"
            )
        if "__tag_keys" in schema:
            keys = "createArray(" + ", ".join(_quote(key) for key in schema["__tag_keys"]) + ")"
            item = "lambdaVariables('tagName')"
            decoded = f"tryGet(json(base64ToString(tryGet({value}, {item}))), 0)"
            return (
                f"toObject(filter({keys}, lambda('tagName', not(equals({decoded}, null())))), "
                f"lambda('tagName', {item}), lambda('tagName', {decoded}))"
            )
        fields = []
        for key, shape in schema.items():
            decoded = self.decode(f"tryGet({value}, {_quote(key)})", shape)
            fields.extend((_quote(key), decoded))
        result = "createObject(" + ", ".join(fields) + ")"
        return (
            self.store(result) if len(result) >= 600 and "lambdaVariables(" not in value else result
        )

    def _function(self, node: ast.expr, names: list[str]) -> str:
        key = ast.dump(node, include_attributes=False)
        if key not in self.function_cache:
            name = f"check{len(self.function_cache)}"
            shared: dict[str, str] = {}
            self.function_cache[key] = name, names, shared
            body = self._raw(node, set(names), frozenset(), shared, key)
            self.functions[name] = {
                "parameters": [{"name": "shared", "type": "object"}]
                + [
                    {
                        "name": local,
                        "type": (
                            "string"
                            if local in {"rid", "account", "domain"}
                            else "int" if local == "offset" else "object"
                        ),
                    }
                    for local in names
                ],
                "output": {"type": "bool", "value": "[" + body + "]"},
            }
        name, names, shared = self.function_cache[key]
        values = (
            "createObject("
            + ", ".join(item for field, value in shared.items() for item in (_quote(field), value))
            + ")"
        )
        return (
            "portal."
            + name
            + "("
            + ", ".join([values, *(f"lambdaVariables({_quote(local)})" for local in names)])
            + ")"
        )

    def _raw(
        self,
        node: ast.expr,
        parameters: set[str],
        locals_: frozenset[str],
        shared: dict[str, str],
        root: str,
    ) -> str:
        key = ast.dump(node, include_attributes=False)
        if key != root and key in self.function_cache:
            name, arguments, needed = self.function_cache[key]
            shared.update(needed)
            return (
                "portal."
                + name
                + "("
                + ", ".join(
                    ["parameters('shared')"]
                    + [
                        ("lambdaVariables" if argument in locals_ else "parameters")
                        + "("
                        + _quote(argument)
                        + ")"
                        for argument in arguments
                    ]
                )
                + ")"
            )
        if key in self.global_names:
            name = self.global_names[key]
            shared[name] = f"variables('{name}')"
            return f"tryGet(parameters('shared'), '{name}')"

        def render(value: ast.expr) -> str:
            return self._raw(value, parameters, locals_, shared, root)

        if isinstance(node, ast.Name):
            if node.id in {"true", "false", "null"}:
                return node.id + "()"
            return (
                (
                    "parameters"
                    if node.id in parameters and node.id not in locals_
                    else "lambdaVariables"
                )
                + "("
                + _quote(node.id)
                + ")"
            )
        if isinstance(node, ast.Attribute):
            return f"tryGet({render(node.value)}, {_quote(node.attr)})"
        if isinstance(node, ast.Subscript):
            return f"tryGet({render(node.value)}, {render(node.slice)})"
        if isinstance(node, ast.Lambda):
            local = node.args.args[0].arg
            return (
                f"lambda({_quote(local)}, "
                + self._raw(node.body, parameters, locals_ | {local}, shared, root)
                + ")"
            )
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            function = node.func.id
            if function == "steps":
                shared["portalContext"] = "variables('portalContext')"
                return "parameters('shared').portalContext." + node.args[0].value
            if function in {"basics", "location"}:
                shared["inputs"] = "parameters('inputs')"
                return "parameters('shared').inputs." + (
                    "infrastructureStage" if function == "basics" else "location"
                )
            if function == "resourceGroup":
                shared["scopeGroup"] = (
                    "createObject('mode', variables('groupMode'), "
                    "'name', resourceGroup().name, 'location', resourceGroup().location)"
                )
                return "parameters('shared').scopeGroup"
            if function == "subscription":
                shared["subscription"] = "subscription()"
                return "parameters('shared').subscription"
            if self._tag_length(node):
                shared["tagCount"] = "variables('tagCount')"
                return "parameters('shared').tagCount"
            names = {
                "parse": "json",
                "ui_if": "if",
                "ui_and": "and",
                "ui_or": "or",
                "ui_not": "not",
            }
            return (
                names.get(function, function)
                + "("
                + ", ".join(render(argument) for argument in node.args)
                + ")"
            )
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            return "sub(0, " + render(node.operand) + ")"
        return self.render(node)

    @staticmethod
    def _tag_length(node: ast.Call) -> bool:
        return node.func.id == "length" and (
            isinstance(node.args[0], ast.Name)
            and node.args[0].id == "budget"
            or isinstance(node.args[0], ast.Attribute)
            and node.args[0].attr == "tags"
            or isinstance(node.args[0], ast.Call)
            and isinstance(node.args[0].func, ast.Name)
            and node.args[0].func.id == "coalesce"
            and isinstance(node.args[0].args[0], ast.Attribute)
            and node.args[0].args[0].attr == "tags"
        )

    def render(self, node: ast.expr, locals_: frozenset[str] = frozenset()) -> str:
        if isinstance(node, ast.Constant):
            if isinstance(node.value, str):
                return _quote(node.value)
            if node.value is None:
                return "null()"
            if type(node.value) is bool:
                return str(node.value).lower() + "()"
            return str(node.value)
        if isinstance(node, ast.Name):
            if node.id in {"true", "false", "null"}:
                return node.id + "()"
            return "lambdaVariables(" + _quote(node.id) + ")"
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            return "sub(0, " + self.render(node.operand, locals_) + ")"
        if isinstance(node, ast.Attribute):
            result = f"tryGet({self.render(node.value, locals_)}, {_quote(node.attr)})"
        elif isinstance(node, ast.Subscript):
            result = (
                f"tryGet({self.render(node.value, locals_)}, {self.render(node.slice, locals_)})"
            )
        elif isinstance(node, ast.Lambda):
            name = node.args.args[0].arg
            return f"lambda({_quote(name)}, {self.render(node.body, locals_ | {name})})"
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            function = node.func.id
            if function == "steps":
                return f"variables('portalContext').{node.args[0].value}"
            if function == "basics":
                return "parameters('inputs').infrastructureStage"
            if function == "resourceGroup":
                return (
                    "createObject('mode', variables('groupMode'), "
                    "'name', resourceGroup().name, 'location', resourceGroup().location)"
                )
            if function == "location":
                return "parameters('inputs').location"
            if self._tag_length(node):
                return "variables('tagCount')"
            names = {
                "parse": "json",
                "ui_if": "if",
                "ui_and": "and",
                "ui_or": "or",
                "ui_not": "not",
            }
            result = (
                names.get(function, function)
                + "("
                + ", ".join(self.render(argument, locals_) for argument in node.args)
                + ")"
            )
        else:
            raise ValueError(f"Unsupported ARM policy syntax: {type(node).__name__}")
        captured = sorted(
            {
                child.id
                for child in ast.walk(node)
                if isinstance(child, ast.Name) and child.id in locals_
            }
        )
        if (
            len(result) >= 1800
            and captured
            and isinstance(node, ast.Call)
            and node.func.id
            in {
                "ui_and",
                "ui_or",
                "ui_not",
                "equals",
                "empty",
                "contains",
            }
        ):
            return self._function(node, captured)
        if len(result) >= 450 and not captured:
            result = self.store(result)
            if result.startswith("variables('"):
                self.global_names[ast.dump(node, include_attributes=False)] = result.split("'")[1]
            else:
                self.global_names.pop(ast.dump(node, include_attributes=False), None)
        return result


def build_policy(parameters: dict[str, Any], tag_keys: tuple[str, ...]) -> dict[str, Any]:
    arm = _Arm()
    schema = evidence_schema(parameters, tag_keys)
    fresh_tags = "coalesce(tryGet(resourceGroup(), 'tags'), createObject())"
    context = {
        key: arm.decode(f"parameters('evidence').{key}", shape) for key, shape in schema.items()
    }
    arm.variables["portalContext"] = (
        "["
        + "createObject("
        + ", ".join(
            item
            for key, value in context.items()
            if key not in {"groupMode", "tagCount"}
            for item in (_quote(key), value)
        )
        + ")]"
    )
    arm.variables["groupMode"] = "[" + context["groupMode"] + "]"
    arm.variables["tagCount"] = "[length(" + fresh_tags + ")]"
    outputs = parameters["outputs"]
    guard = arm.render(parse_expression(outputs["portalValidationPassed"]))
    field_checks = []
    for step in parameters["steps"]:
        for element in step["elements"]:
            for rule in element.get("constraints", {}).get("validations", []):
                if "isValid" not in rule:
                    continue
                value = arm.render(parse_expression(rule["isValid"]))
                visible = element.get("visible", True)
                if isinstance(visible, str):
                    value = f"if({arm.render(parse_expression(visible))}, {value}, true())"
                elif visible is False:
                    continue
                field_checks.append(value)
    if field_checks:
        guard = "and(" + ", ".join([guard, *field_checks]) + ")"
    bindings = []
    excluded = {
        "portalValidationPassed",
        "reuseCoreModel",
        "reuseSimpleModel",
        "infrastructureTags",
    }
    for key, value in outputs.items():
        if key in excluded:
            continue
        expected = (
            arm.render(parse_expression(value))
            if isinstance(value, str) and value.startswith("[")
            else arm.render(ast.Constant(value))
        )
        bindings.append(f"equals(tryGet(parameters('inputs'), {_quote(key)}), {expected})")
    step = "contract" if "contract" in schema else "application"
    decoded_tags = f"variables('portalContext').{step}.resourceGroupApi.tags"
    freshness = [
        f"or(equals(variables('groupMode'), 'New'), equals(length({fresh_tags}), {context['tagCount']}))",
        *(
            f"equals(tryGet({fresh_tags}, {_quote(key)}), tryGet({decoded_tags}, {_quote(key)}))"
            for key in tag_keys
        ),
    ]
    identity = (
        "or(equals(variables('groupMode'), 'New'), "
        f"equals(toLower(coalesce(variables('portalContext').{step}.resourceGroupApi.id, '')), "
        "toLower(resourceGroup().id)))"
    )
    checks = {
        "evidenceVersion": "equals(tryGet(parameters('evidence'), 'schema'), 1)",
        "policyVersion": f"equals(tryGet(parameters('evidence'), 'policyHash'), '{policy_hash(parameters)}')",
        "resourcePolicyVersion": f"equals(tryGet(parameters('evidence'), 'resourcePolicyHash'), '{policy_hash(parameters)}')",
        "scopeMatches": identity,
        "freshContract": "and(" + ", ".join(freshness) + ")",
        "parametersMatch": "and(" + ", ".join(bindings) + ")",
        "deploymentPolicy": guard,
    }
    if step == "application":
        checks["tagBindingsMatch"] = (
            "and("
            + ", ".join(
                "equals(tryGet(parameters('inputs').infrastructureTags, "
                + _quote(key)
                + "), "
                + "tryGet("
                + fresh_tags
                + ", "
                + _quote(key)
                + "))"
                for key in tag_keys
            )
            + ")"
        )
    values = {key: {"value": "[" + value + "]"} for key, value in checks.items()}
    result = {
        "$schema": "https://schema.management.azure.com/schemas/2019-04-01/deploymentTemplate.json#",
        "contentVersion": "1.0.0.0",
        "metadata": {
            "_generator": "scripts.build_v2_ui",
            "policyHash": policy_hash(parameters),
            "description": "Generated server-side equivalent of the canonical UI policy; do not edit.",
        },
        "parameters": {"evidence": {"type": "object"}, "inputs": {"type": "object"}},
        "variables": arm.variables,
        "functions": [{"namespace": "portal", "members": arm.functions}],
        "resources": [
            {
                "type": "Microsoft.Resources/deployments",
                "apiVersion": "2025-04-01",
                "name": "portal-policy-result",
                "properties": {
                    "mode": "Incremental",
                    "parameters": values,
                    "template": {
                        "$schema": "https://schema.management.azure.com/schemas/2019-04-01/deploymentTemplate.json#",
                        "contentVersion": "1.0.0.0",
                        "parameters": {
                            key: {"type": "bool", "allowedValues": [True]} for key in checks
                        },
                        "resources": [],
                    },
                },
            }
        ],
        "outputs": {
            role: {
                "type": "bool",
                "value": "[" + arm.render(parse_expression(outputs[role])) + "]",
            }
            for role in ("reuseCoreModel", "reuseSimpleModel")
            if role in outputs
        },
    }
    oversized = {
        key: len(str(value))
        for key, value in result["variables"].items()
        if len(str(value)) > 24000
    }
    if oversized:
        raise ValueError(
            f"Generated ARM validation expressions exceed their size bound: {oversized}"
        )
    return result
