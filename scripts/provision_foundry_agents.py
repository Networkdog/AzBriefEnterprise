"""Provision the hosted Foundry agents that back the multi-agent pipeline.

The ARM template wires ``FOUNDRY_AGENTS`` to a four-stage roster but cannot
create the agents themselves — agent definitions live in the Foundry project's
data plane, not in ARM. Without this step the pipeline silently contributes
nothing and every analysis falls back to the plain LangGraph path, which is a
correctness-preserving degradation but not what the deployment promised.

Instructions are derived from :data:`src.agent.foundry_backend.STAGE_PROMPTS`,
so the agent's standing role and the per-run message can never drift: the
runtime prompt is the same text plus the update context appended.

Server-side tools (Bing/Web grounding, Azure MCP, Microsoft Learn MCP, memory)
are attached in the Foundry portal — they need connection IDs this script has
no business inventing. An agent created here works immediately; adding tools
makes it better.

Usage:
    python -m scripts.provision_foundry_agents --dry-run
    python -m scripts.provision_foundry_agents
    python -m scripts.provision_foundry_agents --model gpt-4o --stages research impact
    python -m scripts.provision_foundry_agents --delete
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.agent.foundry_backend import STAGE_PROMPTS  # noqa: E402
from src.config import FOUNDRY_AGENT_STAGES, get_azure_credential, get_settings  # noqa: E402

# The runtime prompt ends with the update context; an agent's standing
# instructions are everything before that.
_CONTEXT_MARKER = "\n\nAzure Update under analysis:"


def stage_instructions(stage: str) -> str:
    """Standing instructions for a stage, derived from its runtime prompt."""
    return STAGE_PROMPTS[stage].split(_CONTEXT_MARKER)[0].strip()


def resolve_roster(stages: list[str] | None) -> list[tuple[str, str]]:
    """Return ``(agent_name, stage)`` pairs to provision.

    Prefers the configured ``FOUNDRY_AGENTS`` roster so the created names match
    what the running app looks up. Falls back to ``azbrief-<stage>``.
    """
    settings = get_settings()
    configured = {spec.stage: spec.name for spec in settings.get_foundry_agents()}
    wanted = stages or list(FOUNDRY_AGENT_STAGES)
    return [(configured.get(stage, f"azbrief-{stage}"), stage) for stage in wanted]


def _client(endpoint: str):
    """Build an AgentsClient for the project's data plane."""
    from azure.ai.projects import AIProjectClient

    return AIProjectClient(endpoint=endpoint, credential=get_azure_credential()).agents


def _find(client, name: str):
    """Return an existing agent with this name, or None."""
    for agent in client.list_agents():
        if agent.name == name:
            return agent
    return None


def provision(roster: list[tuple[str, str]], model: str, dry_run: bool, delete: bool) -> int:
    """Create, update or delete the roster. Returns a process exit code."""
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
            print(f"--- {name} [{stage}]\n{stage_instructions(stage)}\n")
        print("Dry run - nothing was created.")
        return 0

    client = _client(endpoint)
    failures = 0
    try:
        for name, stage in roster:
            try:
                existing = _find(client, name)
                if delete:
                    if existing is None:
                        print(f"skip    {name} (not found)")
                        continue
                    client.delete_agent(existing.id)
                    print(f"deleted {name}")
                    continue

                instructions = stage_instructions(stage)
                if existing is None:
                    created = client.create_agent(model=model, name=name, instructions=instructions)
                    print(f"created {name} ({created.id})")
                else:
                    client.update_agent(
                        existing.id, model=model, name=name, instructions=instructions
                    )
                    print(f"updated {name} ({existing.id})")
            except Exception as exc:  # one bad stage must not abort the rest
                failures += 1
                print(f"FAILED  {name}: {exc}")
    finally:
        close = getattr(client, "close", None)
        if close:
            close()

    if failures:
        print(f"\n{failures} agent(s) failed. The pipeline degrades to the LangGraph path.")
    return 1 if failures else 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--model",
        default=None,
        help="Model deployment name (default: FOUNDRY_MODEL_DEPLOYMENT, then AZURE_OPENAI_DEPLOYMENT_NAME)",
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
    parser.add_argument("--delete", action="store_true", help="Delete the roster instead")
    args = parser.parse_args()

    settings = get_settings()
    model = args.model or settings.foundry_model_deployment or settings.azure_openai_deployment_name
    sys.exit(provision(resolve_roster(args.stages), model, args.dry_run, args.delete))


if __name__ == "__main__":
    main()
