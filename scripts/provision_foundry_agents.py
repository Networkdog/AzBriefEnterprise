"""Provision the specialist Prompt Agents used by the AzBrief Hosted Agent.

One Hosted Agent owns orchestration. Six persisted Prompt Agents provide
coordination, Resource Graph, Azure MCP, Azure API, report writing, and quality
review expertise. Agent definitions live in the Foundry project's data plane
and cannot be created by ARM.

Base instructions are derived from
:data:`src.agent.foundry_backend.RUNTIME_AGENT_INSTRUCTIONS` and
:data:`src.agent.foundry_backend.SPECIALIST_PROMPTS`. Role-scoped operational rules
are compiled from the bounded ``Foundry Runtime Guidance`` section in each
``.github/skills/*/SKILL.md``. The detailed developer workflow is never sent to
the model, while ``--check`` detects any change to the runtime section as Agent
instruction drift.

The script derives app-owned FunctionTool definitions from the live LangChain
Pydantic schemas, publishes strict specialist JSON response formats, and preserves
non-app-owned Foundry tools when creating a new immutable Agent version.
Optional managed tools (Web Search, MCP, memory) can still be attached in
Foundry. Hosted execution is ready only when ``--check`` passes.

Usage:
    python -m scripts.provision_foundry_agents --dry-run
    python -m scripts.provision_foundry_agents
    python -m scripts.provision_foundry_agents --model gpt-4o --roles resource_graph azure_api
    python -m scripts.provision_foundry_agents --check
    python -m scripts.provision_foundry_agents --delete
"""

from __future__ import annotations

import argparse
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

# Agent instructions contain characters the Windows console code page cannot encode.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.agent.foundry_backend import (  # noqa: E402
    RUNTIME_AGENT_INSTRUCTIONS,
    SPECIALIST_LOCAL_TOOL_NAMES,
    SPECIALIST_PROMPTS,
    build_foundry_function_tools,
    build_specialist_text_options,
    select_specialist_tools,
)
from src.config import (  # noqa: E402
    SPECIALIST_AGENT_ROLES,
    get_azure_credential,
    get_settings,
)

# The runtime prompt ends with the update context; an agent's standing
# instructions are everything before that.
_CONTEXT_MARKER = "\n\nAzure Update under analysis:"
_RUNTIME_GUIDANCE_HEADING = "## Foundry Runtime Guidance"
_RUNTIME_GUIDANCE_END = "<!-- End Foundry Runtime Guidance -->"
_SKILL_ROOT = Path(__file__).resolve().parent.parent / ".github" / "skills"
_RUNTIME_SKILLS_BY_PURPOSE: dict[str, tuple[str, ...]] = {
    "coordinator": ("foundry-agent-architecture",),
    "resource_graph": (
        "kql-resource-graph",
        "azure-service-integration",
    ),
    "azure_mcp": (
        "foundry-agent-architecture",
        "azure-service-integration",
    ),
    "azure_api": (
        "azure-service-integration",
        "foundry-agent-architecture",
    ),
    "report_writer": (
        "report-quality",
        "language-naturalness",
        "email-template",
    ),
    "quality_reviewer": (
        "report-evaluation",
        "report-quality",
        "language-naturalness",
    ),
}
_RETIRED_APP_FUNCTION_NAMES = frozenset(
    {
        "search_update_related_docs",
        "search_azure_docs",
        "get_service_documentation",
    }
)
_APP_OWNED_FUNCTION_NAMES = (
    frozenset().union(*SPECIALIST_LOCAL_TOOL_NAMES.values()) | _RETIRED_APP_FUNCTION_NAMES
)
_PRESERVE_DEFINITION_VALUE = object()


def specialist_instructions(role: str) -> str:
    """Return standing instructions derived from the runtime specialist contract."""
    if role in SPECIALIST_PROMPTS:
        return SPECIALIST_PROMPTS[role].split(_CONTEXT_MARKER)[0].strip()
    return RUNTIME_AGENT_INSTRUCTIONS[role]


@lru_cache(maxsize=None)
def _load_runtime_skill_guidance(skill_name: str) -> str:
    """Load the bounded runtime section from one repository Skill."""
    path = _SKILL_ROOT / skill_name / "SKILL.md"
    text = path.read_text(encoding="utf-8")
    if text.count(_RUNTIME_GUIDANCE_HEADING) != 1 or text.count(_RUNTIME_GUIDANCE_END) != 1:
        raise RuntimeError(f"{path} must contain one bounded {_RUNTIME_GUIDANCE_HEADING!r} section")
    section = text.split(_RUNTIME_GUIDANCE_HEADING, 1)[1]
    section = section.split(_RUNTIME_GUIDANCE_END, 1)[0]
    guidance = section.strip()
    if not guidance:
        raise RuntimeError(f"{path} has an empty Foundry runtime guidance section")
    return guidance


def runtime_skill_names(purpose: str) -> tuple[str, ...]:
    """Return repository Skills assigned to one Foundry Agent purpose."""
    return _RUNTIME_SKILLS_BY_PURPOSE.get(purpose, ())


def runtime_skill_instructions(purpose: str) -> str:
    """Compile role-scoped Skill guidance for one Foundry Agent definition."""
    blocks = [
        f"### Skill: {name}\n{_load_runtime_skill_guidance(name)}"
        for name in runtime_skill_names(purpose)
    ]
    if not blocks:
        return ""
    return "## AzBrief Runtime Skills\n\n" + "\n\n".join(blocks)


def agent_instructions(purpose: str) -> str:
    """Return standing instructions for one specialist Prompt Agent."""
    base = specialist_instructions(purpose)
    skill_guidance = runtime_skill_instructions(purpose)
    return f"{base}\n\n{skill_guidance}" if skill_guidance else base


def resolve_specialist_roster(roles: list[str] | None) -> list[tuple[str, str]]:
    """Return ``(agent_name, role)`` pairs for the complete specialist team."""
    settings = get_settings()
    configured = {
        "coordinator": settings.foundry_coordinator_agent_name,
        "resource_graph": settings.foundry_resource_graph_agent_name,
        "azure_mcp": settings.foundry_azure_mcp_agent_name,
        "azure_api": settings.foundry_azure_api_agent_name,
        "report_writer": settings.foundry_report_writer_agent_name,
        "quality_reviewer": settings.foundry_quality_reviewer_agent_name,
    }
    wanted = roles or list(SPECIALIST_AGENT_ROLES)
    return [(configured.get(role) or f"azbrief-{role.replace('_', '-')}", role) for role in wanted]


def _tool_type(tool: Any) -> str:
    """Return one Agent tool's stable type value."""
    value = getattr(tool, "type", "")
    return str(getattr(value, "value", value) or "")


def _server_tool_key(tool: Any) -> Optional[tuple[str, str]]:
    """Return the identity used to replace one app-managed server tool."""
    tool_type = _tool_type(tool)
    if tool_type == "mcp":
        return tool_type, str(getattr(tool, "server_label", "") or "")
    if tool_type in {"web_search", "web_search_preview"}:
        return "web_search", ""
    return None


_APP_OWNED_SERVER_TOOL_KEYS = frozenset(
    {
        ("mcp", "microsoft_learn"),
        ("mcp", "azure_read_only"),
        ("web_search", ""),
    }
)


def _server_tool_configuration_error(purpose: str) -> str:
    """Return a message when a required managed server tool is not configured."""
    if purpose != "azure_mcp":
        return ""
    settings = get_settings()
    missing = []
    if not settings.azure_mcp_server_url:
        missing.append("AZURE_MCP_SERVER_URL")
    if not settings.azure_mcp_project_connection_name:
        missing.append("AZURE_MCP_PROJECT_CONNECTION_NAME")
    return f"azure_mcp requires {', '.join(missing)}" if missing else ""


def _managed_server_tools(purpose: str) -> tuple[Any, ...]:
    """Build server-side tools required by one specialist Prompt Agent."""
    from azure.ai.projects.models import MCPTool, WebSearchTool

    settings = get_settings()
    if purpose == "coordinator":
        tools: list[Any] = [
            MCPTool(
                server_label="microsoft_learn",
                server_url="https://learn.microsoft.com/api/mcp",
                require_approval="never",
                server_description=(
                    "Primary source for official Microsoft Learn documentation. "
                    "Use this before Web Search."
                ),
            )
        ]
        if settings.foundry_coordinator_web_search_enabled:
            tools.append(WebSearchTool(search_context_size="medium"))
        return tuple(tools)

    if (
        purpose == "azure_mcp"
        and settings.azure_mcp_server_url
        and settings.azure_mcp_project_connection_name
    ):
        subscription_hint = settings.azure_subscription_id or "the target subscription ID"
        return (
            MCPTool(
                server_label="azure_read_only",
                server_url=settings.azure_mcp_server_url,
                project_connection_id=settings.azure_mcp_project_connection_name,
                require_approval="never",
                server_description=(
                    "Read-only Azure MCP Server exposing direct resource-group, Resource Health, "
                    "and Advisor tools. This is the specialist's only tenant inspection surface; "
                    "there is no single `azure` proxy tool. Always pass tenant "
                    f"`{settings.azure_tenant_id}` and subscription `{subscription_hint}`."
                ),
            ),
        )
    return ()


class _FoundryAdminClient:
    """Small lifecycle-safe adapter over the current Foundry Agent version API."""

    def __init__(self, endpoint: str) -> None:
        from azure.ai.projects import AIProjectClient

        self._credential = get_azure_credential()
        self._project = AIProjectClient(endpoint=endpoint, credential=self._credential)

    def list_agents(self):
        """List logical Agents, each carrying its latest version."""
        return self._project.agents.list()

    def create_version(
        self,
        name: str,
        model: str,
        instructions: str,
        previous_definition: Any = None,
        managed_tools: Optional[list[Any]] = None,
        managed_text: Any = _PRESERVE_DEFINITION_VALUE,
    ):
        """Create an immutable Prompt Agent version, preserving prior configuration."""
        from azure.ai.projects.models import PromptAgentDefinition

        previous_tools = list(getattr(previous_definition, "tools", None) or [])
        managed_server_keys = {
            key for tool in (managed_tools or []) if (key := _server_tool_key(tool)) is not None
        }
        preserved_tools = [
            tool
            for tool in previous_tools
            if _tool_type(tool) != "function"
            and getattr(tool, "name", None) not in _APP_OWNED_FUNCTION_NAMES
            and _server_tool_key(tool) not in _APP_OWNED_SERVER_TOOL_KEYS
            and _server_tool_key(tool) not in managed_server_keys
        ]
        definition = PromptAgentDefinition(
            model=model,
            instructions=instructions,
            temperature=getattr(previous_definition, "temperature", None),
            top_p=getattr(previous_definition, "top_p", None),
            reasoning=getattr(previous_definition, "reasoning", None),
            tools=[*(managed_tools or []), *preserved_tools],
            tool_choice=getattr(previous_definition, "tool_choice", None),
            text=(
                getattr(previous_definition, "text", None)
                if managed_text is _PRESERVE_DEFINITION_VALUE
                else managed_text
            ),
            structured_inputs=getattr(previous_definition, "structured_inputs", None),
        )
        return self._project.agents.create_version(
            agent_name=name,
            definition=definition,
        )

    def delete_agent(self, name: str) -> int:
        """Delete every immutable version of one logical Agent."""
        versions = list(self._project.agents.list_versions(name, include_drafts=True))
        for version in versions:
            self._project.agents.delete_version(
                agent_name=name,
                agent_version=version.version,
                force=True,
            )
        return len(versions)

    def close(self) -> None:
        """Close the project client and its credential."""
        self._project.close()
        self._credential.close()


def _client(endpoint: str) -> _FoundryAdminClient:
    """Build a lifecycle-safe current Foundry Agent administration client."""
    return _FoundryAdminClient(endpoint)


def _latest_version(agent: Any) -> Any:
    """Return one logical Agent's latest immutable version."""
    return getattr(getattr(agent, "versions", None), "latest", None)


def _definition_matches(version: Any, model: str, instructions: str) -> bool:
    """Return whether a deployed version already matches the runtime contract."""
    definition = getattr(version, "definition", None)
    return bool(
        definition is not None
        and getattr(definition, "model", None) == model
        and str(getattr(definition, "instructions", "") or "").strip() == instructions.strip()
    )


@lru_cache(maxsize=1)
def _managed_function_tools() -> dict[str, tuple[Any, ...]]:
    """Build specialist FunctionTools once from the live LangChain schemas."""
    from src.agent.tools import get_all_tools

    tools = get_all_tools()
    return {
        role: tuple(build_foundry_function_tools(select_specialist_tools(role, tools)))
        for role in SPECIALIST_LOCAL_TOOL_NAMES
    }


def _managed_tool_names(purpose: str) -> frozenset[str]:
    """Return the exact app-owned function names required by one specialist."""
    return SPECIALIST_LOCAL_TOOL_NAMES.get(purpose, frozenset())


def _function_tool_names(tools: list[Any]) -> set[str]:
    """Return every deployed FunctionTool name, including retired app definitions."""
    names = set()
    for tool in tools:
        name = str(getattr(tool, "name", "") or "")
        if name and (_tool_type(tool) == "function" or name in _APP_OWNED_FUNCTION_NAMES):
            names.add(name)
    return names


def _has_managed_tools(version: Any, purpose: str) -> bool:
    """Return whether the latest Agent version has the exact required functions."""
    required = _managed_tool_names(purpose)
    definition = getattr(version, "definition", None)
    deployed = _function_tool_names(list(getattr(definition, "tools", None) or []))
    return deployed == required


def _server_tool_payload(tool: Any) -> dict[str, Any]:
    """Return a stable serialized server-tool definition for drift checks."""
    as_dict = getattr(tool, "as_dict", None)
    payload = as_dict() if callable(as_dict) else dict(tool)
    if payload.get("type") != "mcp":
        return payload

    normalized = dict(payload)
    server_url = normalized.get("server_url")
    if isinstance(server_url, str):
        normalized["server_url"] = server_url.rstrip("/")

    allowed_tools = normalized.get("allowed_tools")
    if isinstance(allowed_tools, dict):
        allowed_tools = allowed_tools.get("tool_names")
    if isinstance(allowed_tools, list):
        normalized["allowed_tools"] = sorted(str(name) for name in allowed_tools)
    return normalized


def _server_tool_drift(version: Any, purpose: str) -> set[tuple[str, str]]:
    """Return app-managed server tools that are absent, stale, or assigned elsewhere."""
    required = {
        key: _server_tool_payload(tool)
        for tool in _managed_server_tools(purpose)
        if (key := _server_tool_key(tool)) is not None
    }
    definition = getattr(version, "definition", None)
    deployed = {
        key: _server_tool_payload(tool)
        for tool in (getattr(definition, "tools", None) or [])
        if (key := _server_tool_key(tool)) in _APP_OWNED_SERVER_TOOL_KEYS
    }
    drift = {key for key, payload in required.items() if deployed.get(key) != payload}
    return drift | (set(deployed) - set(required))


def _has_managed_text(version: Any, purpose: str) -> bool:
    """Return whether the latest Agent version has the exact specialist output schema."""
    expected = build_specialist_text_options(purpose)
    definition = getattr(version, "definition", None)
    actual = getattr(definition, "text", None)
    if expected is None:
        return actual is None
    if actual is None:
        return False
    return actual.as_dict() == expected.as_dict()


def _roster_conflicts(roster: list[tuple[str, str]]) -> dict[str, set[str]]:
    """Return Agent names assigned to more than one distinct purpose."""
    purposes: dict[str, set[str]] = {}
    display_names: dict[str, set[str]] = {}
    for name, purpose in roster:
        canonical = name.strip().casefold()
        purposes.setdefault(canonical, set()).add(purpose)
        display_names.setdefault(canonical, set()).add(name.strip())
    return {
        "/".join(sorted(display_names[canonical])): values
        for canonical, values in purposes.items()
        if len(values) > 1
    }


def provision(roster: list[tuple[str, str]], model: str, dry_run: bool, delete: bool) -> int:
    """Create, update or delete the roster. Returns a process exit code."""
    conflicts = _roster_conflicts(roster)
    if conflicts:
        for name, purposes in conflicts.items():
            print(f"CONFLICT {name}: assigned to {', '.join(sorted(purposes))}")
        return 1

    settings = get_settings()
    endpoint = settings.foundry_project_endpoint
    if not endpoint and not dry_run:
        print("FOUNDRY_PROJECT_ENDPOINT is not set - nothing to provision.")
        return 1

    # Output goes to a Windows console too, where the default code page cannot
    # encode em dashes or box drawing.
    print(f"Project : {endpoint or '(not set)'}")
    print(f"Model   : {model}")
    print(f"Agents  : {', '.join(f'{n} ({s})' for n, s in roster)}\n")

    if dry_run:
        for name, role in roster:
            print(f"--- {name} [{role}]\n{agent_instructions(role)}\n")
        print("Dry run - nothing was created.")
        return 0

    if not delete:
        configuration_errors = [
            (name, role, error)
            for name, role in roster
            if (error := _server_tool_configuration_error(role))
        ]
        if configuration_errors:
            for name, role, error in configuration_errors:
                print(f"CONFIG  {name} ({role}) {error}")
            return 1

    client = _client(endpoint)
    failures = 0
    try:
        deployed = {agent.name: agent for agent in client.list_agents()}
        for name, role in roster:
            try:
                existing = deployed.get(name)
                if delete:
                    if existing is None:
                        print(f"skip    {name} (not found)")
                        continue
                    deleted_versions = client.delete_agent(name)
                    deployed.pop(name, None)
                    print(f"deleted {name} ({deleted_versions} version(s))")
                    continue

                instructions = agent_instructions(role)
                managed_tools = [
                    *_managed_server_tools(role),
                    *_managed_function_tools().get(role, ()),
                ]
                managed_text = build_specialist_text_options(role)
                latest = _latest_version(existing) if existing is not None else None
                if existing is None:
                    created = client.create_version(
                        name,
                        model,
                        instructions,
                        managed_tools=managed_tools,
                        managed_text=managed_text,
                    )
                    print(f"created {name} (version {created.version})")
                elif (
                    _definition_matches(latest, model, instructions)
                    and _has_managed_tools(latest, role)
                    and not _server_tool_drift(latest, role)
                    and _has_managed_text(latest, role)
                ):
                    print(f"current {name} (version {latest.version})")
                else:
                    created = client.create_version(
                        name,
                        model,
                        instructions,
                        previous_definition=getattr(latest, "definition", None),
                        managed_tools=managed_tools,
                        managed_text=managed_text,
                    )
                    print(f"updated {name} (version {created.version})")
            except Exception as exc:  # one bad role must not abort the rest
                failures += 1
                print(f"FAILED  {name} ({role}): {exc}")
    finally:
        client.close()

    if failures:
        print(f"\n{failures} agent(s) failed. Fix provisioning before running AzBrief.")
    return 1 if failures else 0


def validate_roster(roster: list[tuple[str, str]]) -> int:
    """Validate the deployed roster without changing Foundry data-plane objects."""
    conflicts = _roster_conflicts(roster)
    if conflicts:
        for name, purposes in conflicts.items():
            print(f"CONFLICT {name}: assigned to {', '.join(sorted(purposes))}")
        return 1

    settings = get_settings()
    endpoint = settings.foundry_project_endpoint
    if not endpoint:
        print("FOUNDRY_PROJECT_ENDPOINT is not set - nothing to validate.")
        return 1

    client = _client(endpoint)
    failures = 0
    try:
        deployed = {agent.name: agent for agent in client.list_agents()}
        for name, purpose in roster:
            issues_before = failures
            agent = deployed.get(name)
            if agent is None:
                failures += 1
                print(f"MISSING {name} ({purpose})")
                continue

            latest = _latest_version(agent)
            if latest is None:
                failures += 1
                print(f"NO-VERSION {name} ({purpose})")
                continue
            definition = getattr(latest, "definition", None)
            configuration_error = _server_tool_configuration_error(purpose)
            if configuration_error:
                failures += 1
                print(f"CONFIG  {name} ({purpose}) {configuration_error}")
                continue
            expected_instructions = agent_instructions(purpose).strip()
            actual_instructions = str(getattr(definition, "instructions", "") or "").strip()
            if actual_instructions != expected_instructions:
                failures += 1
                print(f"STALE   {name} ({purpose}) instructions differ from runtime contract")

            if not _has_managed_text(latest, purpose):
                failures += 1
                print(f"NO-FORMAT {name} ({purpose}) specialist JSON schema is missing or stale")

            tools = list(getattr(definition, "tools", None) or [])
            deployed_tool_names = _function_tool_names(tools)
            missing_tools = sorted(_managed_tool_names(purpose) - deployed_tool_names)
            extra_tools = sorted(deployed_tool_names - _managed_tool_names(purpose))
            if missing_tools:
                failures += 1
                print(
                    f"NO-TOOL {name} ({purpose}) missing app functions: "
                    f"{', '.join(missing_tools)}"
                )
            if extra_tools:
                failures += 1
                print(
                    f"EXTRA-TOOL {name} ({purpose}) stale app functions: "
                    f"{', '.join(extra_tools)}"
                )
            stale_server_tools = sorted(_server_tool_drift(latest, purpose))
            if stale_server_tools:
                failures += 1
                labels = [
                    f"{tool_type}:{label}" if label else tool_type
                    for tool_type, label in stale_server_tools
                ]
                print(
                    f"STALE-SERVER-TOOL {name} ({purpose}) missing or stale: "
                    f"{', '.join(labels)}"
                )
            if failures == issues_before:
                print(f"OK      {name} ({purpose}, version={latest.version}, tools={len(tools)})")
    finally:
        client.close()

    if failures:
        print(f"\nFoundry roster check failed with {failures} issue(s).")
        return 1
    print("\nFoundry roster check passed.")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--model",
        default=None,
        help="Model deployment name (default: FOUNDRY_MODEL_DEPLOYMENT)",
    )
    parser.add_argument(
        "--roles",
        nargs="+",
        choices=list(SPECIALIST_AGENT_ROLES),
        help="Subset of specialist roles to provision (default: complete team)",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Print the instructions without calling Foundry"
    )
    operation = parser.add_mutually_exclusive_group()
    operation.add_argument("--check", action="store_true", help="Validate the deployed roster")
    operation.add_argument("--delete", action="store_true", help="Delete the roster instead")
    args = parser.parse_args()

    settings = get_settings()
    model = args.model or settings.foundry_model_deployment
    if not model and not args.delete and not args.check:
        parser.error("--model or FOUNDRY_MODEL_DEPLOYMENT is required")
    roster = resolve_specialist_roster(args.roles)
    if args.check:
        sys.exit(validate_roster(roster))
    sys.exit(provision(roster, model or "(not used)", args.dry_run, args.delete))


if __name__ == "__main__":
    main()
