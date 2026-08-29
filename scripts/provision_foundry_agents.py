"""Provision the Foundry Prompt Agents used by the AzBrief runtime.

The running app calls a required primary agent, optional role-specific codex
and fast agents, and an optional four-stage enrichment roster. Agent definitions
live in the Foundry project's data plane and cannot be created by ARM.

Base instructions are derived from
:data:`src.agent.foundry_backend.RUNTIME_AGENT_INSTRUCTIONS` and
:data:`src.agent.foundry_backend.STAGE_PROMPTS`. Role-scoped operational rules
are compiled from the bounded ``Foundry Runtime Guidance`` section in each
``.github/skills/*/SKILL.md``. The detailed developer workflow is never sent to
the model, while ``--check`` detects any change to the runtime section as Agent
instruction drift.

The script derives app-owned FunctionTool definitions from the live LangChain
Pydantic schemas, publishes strict stage JSON response formats, and preserves
non-app-owned Foundry tools when creating a new immutable Agent version.
Optional managed tools (Web Search, MCP, memory) can still be attached in
Foundry. Enrichment is ready only when ``--check`` passes.

Usage:
    python -m scripts.provision_foundry_agents --dry-run
    python -m scripts.provision_foundry_agents
    python -m scripts.provision_foundry_agents --model gpt-4o --stages research impact
    python -m scripts.provision_foundry_agents --runtime-roles primary codex
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
    ENRICHMENT_LOCAL_TOOL_NAMES,
    RUNTIME_AGENT_INSTRUCTIONS,
    STAGE_PROMPTS,
    build_foundry_function_tools,
    build_stage_text_options,
    select_enrichment_tools,
)
from src.config import (  # noqa: E402
    FOUNDRY_AGENT_STAGES,
    LLM_ROLES,
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
    # The current deployment reuses primary for unset runtime roles, so primary
    # carries the complete compact set. Distinct role agents receive narrower sets.
    "primary": (
        "foundry-agent-architecture",
        "azure-service-integration",
        "kql-resource-graph",
        "report-quality",
        "report-evaluation",
        "language-naturalness",
        "email-template",
    ),
    "planner": (
        "foundry-agent-architecture",
        "azure-service-integration",
        "kql-resource-graph",
    ),
    "evaluator": (
        "foundry-agent-architecture",
        "report-evaluation",
        "report-quality",
    ),
    "reporter": (
        "report-quality",
        "report-evaluation",
        "language-naturalness",
        "email-template",
    ),
    "codex": ("kql-resource-graph", "azure-service-integration"),
    "fast": ("language-naturalness", "report-quality"),
    "research": ("foundry-agent-architecture",),
    "impact": (
        "foundry-agent-architecture",
        "azure-service-integration",
        "kql-resource-graph",
    ),
    "action": ("report-quality",),
    "review": (
        "foundry-agent-architecture",
        "report-evaluation",
        "report-quality",
        "language-naturalness",
    ),
}
_RETIRED_APP_FUNCTION_NAMES = frozenset(
    {
        "get_resource_type_summary",
        "get_resource_configurations",
        "get_resource_dependencies",
        "get_resource_health",
        "get_policy_compliance",
        "get_service_health_events",
    }
)
_APP_OWNED_FUNCTION_NAMES = (
    frozenset().union(*ENRICHMENT_LOCAL_TOOL_NAMES.values()) | _RETIRED_APP_FUNCTION_NAMES
)


def stage_instructions(stage: str) -> str:
    """Standing instructions for a stage, derived from its runtime prompt."""
    return STAGE_PROMPTS[stage].split(_CONTEXT_MARKER)[0].strip()


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
    """Return standing instructions for a runtime role or enrichment stage."""
    if purpose in RUNTIME_AGENT_INSTRUCTIONS:
        base = RUNTIME_AGENT_INSTRUCTIONS[purpose]
    else:
        base = stage_instructions(purpose)
    skill_guidance = runtime_skill_instructions(purpose)
    return f"{base}\n\n{skill_guidance}" if skill_guidance else base


def resolve_runtime_roster(roles: list[str] | None) -> list[tuple[str, str]]:
    """Return unique ``(agent_name, role)`` pairs for runtime agents."""
    settings = get_settings()
    wanted = roles or list(LLM_ROLES)
    roster: list[tuple[str, str]] = []
    seen: set[str] = set()
    for role in wanted:
        name = settings.foundry_agent_for_role(role)
        if not name or name in seen:
            continue
        roster.append((name, role))
        seen.add(name)
    return roster


def resolve_roster(stages: list[str] | None) -> list[tuple[str, str]]:
    """Return ``(agent_name, stage)`` pairs to provision.

    Prefers ``FOUNDRY_ENRICHMENT_AGENTS`` so provisioned names match what the
    running app looks up. Falls back to ``azbrief-<stage>``.
    """
    settings = get_settings()
    configured = {spec.stage: spec.name for spec in settings.get_foundry_enrichment_agents()}
    wanted = stages or list(FOUNDRY_AGENT_STAGES)
    return [(configured.get(stage, f"azbrief-{stage}"), stage) for stage in wanted]


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


def _managed_server_tools(purpose: str) -> tuple[Any, ...]:
    """Build server-side tools required by one enrichment Agent."""
    from azure.ai.projects.models import MCPTool, WebSearchTool

    settings = get_settings()
    if purpose == "research":
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
        if settings.foundry_research_web_search_enabled:
            tools.append(WebSearchTool(search_context_size="medium"))
        return tuple(tools)

    if (
        purpose == "impact"
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
                    "and Advisor tools. Use these tools as the primary source for live tenant "
                    "evidence; there is no single `azure` proxy tool. Always pass tenant "
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
        managed_text: Any = None,
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
            if getattr(tool, "name", None) not in _APP_OWNED_FUNCTION_NAMES
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
                managed_text
                if managed_text is not None
                else getattr(previous_definition, "text", None)
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
    """Build app-owned Foundry FunctionTools once from the live LangChain schemas."""
    from src.agent.tools import get_all_tools

    tools = get_all_tools()
    return {
        stage: tuple(build_foundry_function_tools(select_enrichment_tools(stage, tools)))
        for stage in ENRICHMENT_LOCAL_TOOL_NAMES
    }


def _managed_tool_names(purpose: str) -> frozenset[str]:
    """Return the exact app-owned function names required by one Agent purpose."""
    return ENRICHMENT_LOCAL_TOOL_NAMES.get(purpose, frozenset())


def _has_managed_tools(version: Any, purpose: str) -> bool:
    """Return whether the latest Agent version has the exact required functions."""
    required = _managed_tool_names(purpose)
    if not required:
        return True
    definition = getattr(version, "definition", None)
    deployed = {
        str(getattr(tool, "name", "") or "") for tool in (getattr(definition, "tools", None) or [])
    }
    return deployed.intersection(_APP_OWNED_FUNCTION_NAMES) == required


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
    """Return required server-side tools that are absent or stale."""
    required = {
        key: _server_tool_payload(tool)
        for tool in _managed_server_tools(purpose)
        if (key := _server_tool_key(tool)) is not None
    }
    definition = getattr(version, "definition", None)
    deployed = {
        key: _server_tool_payload(tool)
        for tool in (getattr(definition, "tools", None) or [])
        if (key := _server_tool_key(tool)) is not None
    }
    return {key for key, payload in required.items() if deployed.get(key) != payload}


def _has_managed_text(version: Any, purpose: str) -> bool:
    """Return whether the latest Agent version has the exact stage output schema."""
    expected = build_stage_text_options(purpose)
    if expected is None:
        return True
    definition = getattr(version, "definition", None)
    actual = getattr(definition, "text", None)
    if actual is None:
        return False
    return actual.as_dict() == expected.as_dict()


def _roster_conflicts(roster: list[tuple[str, str]]) -> dict[str, set[str]]:
    """Return Agent names assigned to more than one distinct purpose."""
    purposes: dict[str, set[str]] = {}
    for name, purpose in roster:
        purposes.setdefault(name, set()).add(purpose)
    return {name: values for name, values in purposes.items() if len(values) > 1}


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
        for name, stage in roster:
            print(f"--- {name} [{stage}]\n{agent_instructions(stage)}\n")
        print("Dry run - nothing was created.")
        return 0

    client = _client(endpoint)
    failures = 0
    try:
        deployed = {agent.name: agent for agent in client.list_agents()}
        for name, stage in roster:
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

                instructions = agent_instructions(stage)
                managed_tools = [
                    *_managed_server_tools(stage),
                    *_managed_function_tools().get(stage, ()),
                ]
                managed_text = build_stage_text_options(stage)
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
                    and _has_managed_tools(latest, stage)
                    and not _server_tool_drift(latest, stage)
                    and _has_managed_text(latest, stage)
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
            except Exception as exc:  # one bad stage must not abort the rest
                failures += 1
                print(f"FAILED  {name}: {exc}")
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
            expected_instructions = agent_instructions(purpose).strip()
            actual_instructions = str(getattr(definition, "instructions", "") or "").strip()
            if actual_instructions != expected_instructions:
                failures += 1
                print(f"STALE   {name} ({purpose}) instructions differ from runtime contract")

            if not _has_managed_text(latest, purpose):
                failures += 1
                print(f"NO-FORMAT {name} ({purpose}) stage JSON schema is missing or stale")

            tools = list(getattr(definition, "tools", None) or [])
            deployed_tool_names = {str(getattr(tool, "name", "") or "") for tool in tools}
            missing_tools = sorted(_managed_tool_names(purpose) - deployed_tool_names)
            extra_tools = sorted(
                deployed_tool_names.intersection(_APP_OWNED_FUNCTION_NAMES)
                - _managed_tool_names(purpose)
            )
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
            elif actual_instructions == expected_instructions:
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
        "--runtime-roles",
        nargs="+",
        choices=list(LLM_ROLES),
        help="Runtime roles to provision (default: configured unique agents)",
    )
    parser.add_argument(
        "--stages",
        nargs="+",
        choices=list(FOUNDRY_AGENT_STAGES),
        help="Subset of stages to provision (default: all four)",
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
    roster = resolve_runtime_roster(args.runtime_roles) + resolve_roster(args.stages)
    if args.check:
        sys.exit(validate_roster(roster))
    sys.exit(provision(roster, model or "(not used)", args.dry_run, args.delete))


if __name__ == "__main__":
    main()
