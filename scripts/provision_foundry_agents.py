"""Provision the Foundry Prompt Agents used by the AzBrief runtime.

The running app calls a required primary agent, optional role-specific codex
and fast agents, and an optional four-stage enrichment roster. Agent definitions
live in the Foundry project's data plane and cannot be created by ARM.

Instructions are derived from :data:`src.agent.foundry_backend.STAGE_PROMPTS`,
so the agent's standing role and the per-run message can never drift: the
runtime prompt is the same text plus the update context appended.

Server-side tools (Bing/Web grounding, Azure MCP, Microsoft Learn MCP, memory)
are attached in the Foundry portal — they need connection IDs this script has
no business inventing. Research and impact agents are not production-ready
until those tools are attached and ``--check`` passes.

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
from pathlib import Path
from typing import Any

# Agent instructions contain characters the Windows console code page cannot encode.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.agent.foundry_backend import RUNTIME_AGENT_INSTRUCTIONS, STAGE_PROMPTS  # noqa: E402
from src.config import (  # noqa: E402
    FOUNDRY_AGENT_STAGES,
    LLM_ROLES,
    get_azure_credential,
    get_settings,
)

# The runtime prompt ends with the update context; an agent's standing
# instructions are everything before that.
_CONTEXT_MARKER = "\n\nAzure Update under analysis:"
_TOOL_REQUIRED_STAGES = frozenset({"research", "impact"})


def stage_instructions(stage: str) -> str:
    """Standing instructions for a stage, derived from its runtime prompt."""
    return STAGE_PROMPTS[stage].split(_CONTEXT_MARKER)[0].strip()


def agent_instructions(purpose: str) -> str:
    """Return standing instructions for a runtime role or enrichment stage."""
    if purpose in RUNTIME_AGENT_INSTRUCTIONS:
        return RUNTIME_AGENT_INSTRUCTIONS[purpose]
    return stage_instructions(purpose)


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
    ):
        """Create an immutable Prompt Agent version, preserving prior configuration."""
        from azure.ai.projects.models import PromptAgentDefinition

        definition = PromptAgentDefinition(
            model=model,
            instructions=instructions,
            temperature=getattr(previous_definition, "temperature", None),
            top_p=getattr(previous_definition, "top_p", None),
            reasoning=getattr(previous_definition, "reasoning", None),
            tools=list(getattr(previous_definition, "tools", None) or []),
            tool_choice=getattr(previous_definition, "tool_choice", None),
            text=getattr(previous_definition, "text", None),
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
        and str(getattr(definition, "instructions", "") or "").strip()
        == instructions.strip()
    )


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
                latest = _latest_version(existing) if existing is not None else None
                if existing is None:
                    created = client.create_version(name, model, instructions)
                    print(f"created {name} (version {created.version})")
                elif _definition_matches(latest, model, instructions):
                    print(f"current {name} (version {latest.version})")
                else:
                    created = client.create_version(
                        name,
                        model,
                        instructions,
                        previous_definition=getattr(latest, "definition", None),
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
            actual_instructions = str(
                getattr(definition, "instructions", "") or ""
            ).strip()
            if actual_instructions != expected_instructions:
                failures += 1
                print(f"STALE   {name} ({purpose}) instructions differ from runtime contract")

            tools = list(getattr(definition, "tools", None) or [])
            if purpose in _TOOL_REQUIRED_STAGES and not tools:
                failures += 1
                print(f"NO-TOOL {name} ({purpose}) requires at least one server-side tool")
            elif actual_instructions == expected_instructions:
                print(
                    f"OK      {name} ({purpose}, version={latest.version}, tools={len(tools)})"
                )
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
