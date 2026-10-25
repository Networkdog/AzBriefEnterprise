"""Configure one workload's MCP identity from an already-authorized deployment identity."""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import time
from email.message import Message
from pathlib import Path
from typing import IO, Any, NoReturn
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import UUID

GRAPH = "https://graph.microsoft.com/v1.0/"
ROLE_VALUE = "Mcp.Tools.ReadWrite.All"
SCOPE_VALUE = "Mcp.Tools.ReadWrite"
VSCODE_CLIENT_ID = "aebc6443-996d-45c2-90f0-388ff96faa56"


class McpIdentityError(RuntimeError):
    """Expose only a fixed operation/code, never Graph bodies or credential material."""


class NoRedirects(HTTPRedirectHandler):
    """Keep bearer credentials on the fixed Microsoft Graph endpoint."""

    def redirect_request(
        self, req: Request, fp: IO[bytes], code: int, msg: str, headers: Message, newurl: str
    ) -> NoReturn:
        raise McpIdentityError("Graph redirects are not permitted")


def guid(value: object) -> str:
    if not isinstance(value, str):
        raise McpIdentityError("Invalid directory identity type")
    try:
        result = UUID(value)
    except ValueError as error:
        raise McpIdentityError("Invalid directory identity") from error
    if not result.int:
        raise McpIdentityError("Empty directory identity")
    return str(result)


class Graph:
    """Bounded Graph-only transport; authorization failures never become absence."""

    def __init__(self, token: str):
        self.token = token
        self.opener = build_opener(NoRedirects())
        self.deadline = time.monotonic() + 600

    def request(
        self, method: str, path: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        if path.startswith("/") or "://" in path or "\n" in path:
            raise McpIdentityError("Graph request escaped its fixed endpoint")
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        for attempt in range(4):
            request = Request(
                GRAPH + path,
                data=body,
                method=method,
                headers={
                    "Authorization": "Bearer " + self.token,
                    "Content-Type": "application/json",
                },
            )
            try:
                with self.opener.open(request, timeout=45) as response:
                    content = response.read()
            except HTTPError as error:
                if error.code in {429, 503} and attempt < 3:
                    retry = error.headers.get("Retry-After", "")
                    delay = int(retry) if retry.isdigit() else 5 * (attempt + 1)
                    if time.monotonic() + delay + 45 > self.deadline:
                        raise McpIdentityError(
                            "Graph retry delay exceeds the setup budget"
                        ) from None
                    time.sleep(delay)
                    continue
                raise McpIdentityError(f"Graph {method} failed with HTTP {error.code}") from None
            except (URLError, TimeoutError):
                raise McpIdentityError("Graph connection failed") from None
            if not content:
                return {}
            try:
                result = json.loads(content)
            except (ValueError, UnicodeError):
                raise McpIdentityError("Graph returned an invalid JSON response") from None
            if not isinstance(result, dict) or result.get("@odata.nextLink") or result.get("error"):
                raise McpIdentityError("Graph response is incomplete, paged or failed")
            return result
        raise McpIdentityError("Graph retries exhausted")


def rows(response: dict[str, Any]) -> list[dict[str, Any]]:
    values = response.get("value")
    if (
        response.get("@odata.nextLink")
        or response.get("error")
        or not isinstance(values, list)
        or any(not isinstance(value, dict) for value in values)
    ):
        raise McpIdentityError("Directory inventory is incomplete or paged")
    return values


def manifest(config: dict[str, str], client_id: str = "") -> dict[str, Any]:
    api: dict[str, Any] = {
        "requestedAccessTokenVersion": 2,
        "oauth2PermissionScopes": [
            {
                "id": guid(config["scopeId"]),
                "value": SCOPE_VALUE,
                "type": "User",
                "adminConsentDisplayName": "Azure MCP Tools ReadWrite",
                "adminConsentDescription": "Delegated permission for authenticated Azure MCP tool calls",
                "userConsentDisplayName": "Azure MCP Tools ReadWrite",
                "userConsentDescription": "Delegated permission for authenticated Azure MCP tool calls",
                "isEnabled": True,
            }
        ],
    }
    if client_id:
        api["preAuthorizedApplications"] = [
            {"appId": VSCODE_CLIENT_ID, "delegatedPermissionIds": [guid(config["scopeId"])]}
        ]
    result: dict[str, Any] = {
        "displayName": "AzBrief v2 Azure MCP API",
        "uniqueName": config["uniqueName"],
        "signInAudience": "AzureADMyOrg",
        "notes": "AzBrief v2 MCP setup for " + config["workloadResourceGroupId"].lower(),
        "appRoles": [
            {
                "id": guid(config["appRoleId"]),
                "displayName": "Azure MCP Tools ReadWrite All",
                "description": "Application permission for authenticated Azure MCP tool calls",
                "value": ROLE_VALUE,
                "isEnabled": True,
                "allowedMemberTypes": ["Application"],
            }
        ],
        "api": api,
        "requiredResourceAccess": [],
    }
    if client_id:
        result["identifierUris"] = ["api://" + guid(client_id)]
    return result


def check_application(document: dict[str, Any], expected: dict[str, Any], client_id: str) -> None:
    guid(document.get("id"))
    if guid(document.get("appId")) != client_id:
        raise McpIdentityError("Application readback redirected its client ID")
    for field in ("displayName", "uniqueName", "signInAudience", "notes", "requiredResourceAccess"):
        if document.get(field) != expected[field]:
            raise McpIdentityError("Refusing a foreign or modified MCP application")
    role_fields = ("id", "displayName", "description", "value", "isEnabled", "allowedMemberTypes")
    actual_roles = document.get("appRoles")
    if (
        not isinstance(actual_roles, list)
        or len(actual_roles) != 1
        or not isinstance(actual_roles[0], dict)
    ):
        raise McpIdentityError("MCP application role inventory differs")
    if {field: actual_roles[0].get(field) for field in role_fields} != expected["appRoles"][0]:
        raise McpIdentityError("MCP application role differs")
    api = document.get("api")
    if not isinstance(api, dict):
        raise McpIdentityError("MCP application API definition is missing")
    for field in ("requestedAccessTokenVersion", "oauth2PermissionScopes"):
        if api.get(field) != expected["api"][field]:
            raise McpIdentityError("MCP application scope definition differs")
    if api.get("preAuthorizedApplications", []) not in (
        [],
        expected["api"].get("preAuthorizedApplications", []),
    ):
        raise McpIdentityError("Refusing to replace unrelated preauthorized clients")
    if document.get("identifierUris", []) not in ([], ["api://" + client_id]):
        raise McpIdentityError("Refusing to replace an unrelated MCP audience")
    if document.get("passwordCredentials") or document.get("keyCredentials"):
        raise McpIdentityError("Automatic MCP setup never adopts credential-bearing applications")


def provision(graph: Graph, config: dict[str, str]) -> dict[str, str]:
    """Create/reuse only the owned MCP registration and its exact project app-role grant."""
    tenant = guid(config["tenantId"])
    installer = guid(config["installerPrincipalId"])
    project = guid(config["projectPrincipalId"])
    if installer == project or installer in {
        guid(config["controlPlanePrincipalId"]),
        guid(config["mcpPrincipalId"]),
    }:
        raise McpIdentityError("Use a distinct deployment-only identity")
    unique_name = config["uniqueName"]
    if not unique_name.startswith("azbrief-v2-mcp-") or any(
        value in unique_name for value in ("'", "?", "/", "\\")
    ):
        raise McpIdentityError("Invalid workload MCP unique name")
    query = urlencode({"$filter": f"uniqueName eq '{unique_name}'"})
    applications = rows(graph.request("GET", "applications?" + query))
    if len(applications) > 1:
        raise McpIdentityError("MCP application inventory is ambiguous")
    if not applications:
        graph.request(
            "POST",
            "applications",
            {
                **manifest(config),
                "owners@odata.bind": [GRAPH + "directoryObjects/" + installer],
            },
        )
        applications = rows(graph.request("GET", "applications?" + query))
    if len(applications) != 1:
        raise McpIdentityError("Created MCP application is not uniquely readable")
    application = applications[0]
    object_id = guid(application.get("id"))
    client_id = guid(application.get("appId"))
    expected = manifest(config, client_id)
    check_application(application, expected, client_id)
    owners = rows(graph.request("GET", f"applications/{object_id}/owners?$select=id"))
    if installer not in {guid(owner.get("id")) for owner in owners}:
        raise McpIdentityError("The deployment identity does not own this MCP application")
    graph.request(
        "PATCH",
        "applications/" + object_id,
        {name: value for name, value in expected.items() if name != "uniqueName"},
    )
    final_app = graph.request("GET", "applications/" + object_id)
    check_application(final_app, expected, client_id)
    if (
        final_app.get("identifierUris") != expected["identifierUris"]
        or final_app["api"].get("preAuthorizedApplications")
        != expected["api"]["preAuthorizedApplications"]
    ):
        raise McpIdentityError("MCP audience/preauthorization write did not persist")
    principal_query = urlencode({"$filter": f"appId eq '{client_id}'"})
    principals = rows(graph.request("GET", "servicePrincipals?" + principal_query))
    if not principals:
        graph.request("POST", "servicePrincipals", {"appId": client_id})
        principals = rows(graph.request("GET", "servicePrincipals?" + principal_query))
    if len(principals) != 1:
        raise McpIdentityError("MCP service principal inventory is ambiguous")
    principal = principals[0]
    principal_id = guid(principal.get("id"))
    if (
        guid(principal.get("appId")) != client_id
        or guid(principal.get("appOwnerOrganizationId")) != tenant
        or principal.get("servicePrincipalType") != "Application"
        or principal.get("accountEnabled") is not True
    ):
        raise McpIdentityError("MCP service principal ownership/readiness differs")
    assignment = {
        "principalId": project,
        "resourceId": principal_id,
        "appRoleId": guid(config["appRoleId"]),
    }
    assignments_path = f"servicePrincipals/{principal_id}/appRoleAssignedTo?" + urlencode(
        {"$filter": f"principalId eq {project}"}
    )
    assignments = rows(graph.request("GET", assignments_path))
    if not assignments:
        graph.request("POST", f"servicePrincipals/{principal_id}/appRoleAssignedTo", assignment)
        assignments = rows(graph.request("GET", assignments_path))
    if len(assignments) != 1 or {key: assignments[0].get(key) for key in assignment} != assignment:
        raise McpIdentityError("The exact Foundry-to-MCP app-role assignment is not confirmed")
    return {
        "clientId": client_id,
        "servicePrincipalId": principal_id,
        "appRoleId": assignment["appRoleId"],
    }


def main() -> None:
    config = json.loads(os.environ["AZBRIEF_MCP_SETUP"])
    if not isinstance(config, dict) or any(not isinstance(value, str) for value in config.values()):
        raise McpIdentityError("Invalid MCP setup contract")
    result = subprocess.run(
        [
            "az",
            "account",
            "get-access-token",
            "--resource",
            "https://graph.microsoft.com",
            "--query",
            "accessToken",
            "--output",
            "tsv",
            "--only-show-errors",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    if result.returncode:
        raise McpIdentityError("Deployment identity could not acquire a Graph token")
    token = result.stdout.strip()
    segments = token.split(".")
    if len(segments) != 3:
        raise McpIdentityError("Deployment identity returned an invalid token")
    claims = json.loads(base64.urlsafe_b64decode(segments[1] + "=" * (-len(segments[1]) % 4)))
    roles = set(claims.get("roles", []))
    if (
        guid(claims.get("oid")) != guid(config["installerPrincipalId"])
        or guid(claims.get("tid")) != guid(config["tenantId"])
        or not roles.intersection({"Application.ReadWrite.OwnedBy", "Application.ReadWrite.All"})
        or "AppRoleAssignment.ReadWrite.All" not in roles
        or not roles.intersection({"Application.Read.All", "Application.ReadWrite.All"})
    ):
        raise McpIdentityError(
            "The selected deployment identity lacks preapproved Graph permissions"
        )
    outputs = provision(Graph(token), config)
    Path(os.environ["AZ_SCRIPTS_OUTPUT_PATH"]).write_text(json.dumps(outputs), encoding="utf-8")


if __name__ == "__main__":
    try:
        main()
    except McpIdentityError as error:
        sys.stderr.write(
            json.dumps({"event": "mcp_identity_setup_failed", "error": str(error)}) + "\n"
        )
        raise SystemExit(1) from None
