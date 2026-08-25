"""Microsoft Foundry backend integration for AzBrief (optional, opt-in).

This module bridges the AzBrief LangGraph agent to **Microsoft Foundry Agent
Service**. It is entirely opt-in and degrades gracefully so the default
Azure OpenAI path is never affected:

* Nothing here is imported at package-import time by the core loop.
* Every Foundry SDK import is deferred into a function.
* Each public function returns a safe fallback (``None`` / unchanged state) when
  the SDK is missing, the endpoint is unset, or a live call fails.

Enable it with ``llm_backend='foundry'`` plus ``foundry_project_endpoint``.
The agents are pre-published in the Foundry project (see
``scripts/provision_foundry_agents.py``) and referenced here **by name**, so
their tools, model and guardrails stay governed in the portal. AzBrief runs
them through the Agents data plane and inserts the merged result as a LangGraph
node ahead of planning; the Plan-Execute-Evaluate loop, KQL determinism, and
G-Eval quality pipeline are unchanged.

Scope note: only the *agents* run on Foundry. The chat model does not — the
project endpoint does not serve chat completions (see the comment above
``_run_hosted_agent_sync``), so the analyzer keeps its Azure OpenAI path
against the same Foundry account.

Requires ``azure-ai-projects`` and ``azure-ai-agents``.
"""

from __future__ import annotations

import threading
from typing import Any, Awaitable, Callable, Optional

from structlog import get_logger

from src.config import Settings

logger = get_logger()

# Marker prepended to the enrichment text so the planning node can recognize it.
ENRICHMENT_HEADER = (
    "## Additional Context (Microsoft Foundry enrichment — "
    "web/Bing, Azure MCP, Microsoft Learn MCP, memory)"
)

# English prompt (token economy); the enrichment agent supplies grounding facts
# only — never the final Korean/localized report.
ENRICHMENT_PROMPT = (
    "You are a research assistant enriching context for an Azure Update analysis.\n"
    "Use your available tools (web/Bing search for latest announcements, Azure MCP "
    "for live resource queries, Microsoft Learn MCP for official documentation, and "
    "memory of past analyses) to gather concise, factual, up-to-date context that "
    "helps assess this update's importance, its impact on the tenant's resources, "
    "and its job relevance.\n\n"
    "Return a compact bullet list (max ~300 words). Cite documentation URLs when "
    "available. Do NOT write the final report — only supply grounding facts.\n\n"
    "Azure Update under analysis:\n{update_context}"
)

# ── Hosted multi-agent pipeline ────────────────────────────────
# Each stage is a separate Foundry-hosted agent so its tools, model and
# guardrails stay governed in the Foundry project. 'research' and 'impact' are
# independent; 'action' consumes both; 'review' audits the merged result.
MULTI_AGENT_HEADER = "## Additional Context (Microsoft Foundry multi-agent)"

MULTI_AGENT_STAGE_LABELS = {
    "research": "Research findings",
    "impact": "Tenant impact assessment",
    "action": "Proposed actions",
    "review": "Review notes",
}

STAGE_PROMPTS: dict[str, str] = {
    "research": (
        "You are the RESEARCH agent for an Azure Update analysis pipeline.\n"
        "Establish what actually changed: the capability or change itself, its "
        "release stage, effective dates and retirement deadlines, prerequisites, "
        "and the official documentation that describes it.\n"
        "Return a compact bullet list (max ~250 words) of verifiable facts with "
        "documentation URLs. State plainly when a fact could not be confirmed.\n\n"
        "Azure Update under analysis:\n{update_context}"
    ),
    "impact": (
        "You are the IMPACT agent for an Azure Update analysis pipeline.\n"
        "Determine how this update touches the tenant's actual Azure estate: which "
        "resource types and configurations are involved, whether the relevant "
        "services and regions are in use, and what is demonstrably NOT affected.\n"
        "Use your live resource tools. Never guess a resource name — report an "
        "absence as an absence.\n"
        "Return a compact bullet list (max ~250 words).\n\n"
        "Azure Update under analysis:\n{update_context}"
    ),
    "action": (
        "You are the ACTION agent for an Azure Update analysis pipeline.\n"
        "Using the research and impact findings below, propose concrete next steps "
        "an Azure administrator can execute or verify themselves. Each step must "
        "name what to check, where, and the criterion for done. Do not invent "
        "deadlines. Read-only verification steps are preferred over mutations.\n"
        "Return a compact bullet list (max ~250 words).\n\n"
        "Azure Update under analysis:\n{update_context}\n\n"
        "Findings so far:\n{prior_findings}"
    ),
    "review": (
        "You are the REVIEW agent for an Azure Update analysis pipeline.\n"
        "Audit the findings below against the update text. Flag any claim that is "
        "not supported by the evidence, any named resource that was never returned "
        "by a tool, and any missing critical fact. Be brief.\n"
        "Return a compact bullet list (max ~150 words). Reply exactly 'NO ISSUES' "
        "when everything checks out.\n\n"
        "Azure Update under analysis:\n{update_context}\n\n"
        "Findings so far:\n{prior_findings}"
    ),
}


def foundry_available() -> bool:
    """Return True if the Foundry Agents data-plane SDKs are importable."""
    try:
        import azure.ai.agents  # noqa: F401
        import azure.ai.projects  # noqa: F401

        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Hosted agent invocation
# ---------------------------------------------------------------------------
# The Foundry *project* endpoint does not serve chat completions. Authenticating
# an inference client against it returns 401 ("audience is incorrect"), and the
# SDK's ``project_endpoint=`` form additionally requires a default Azure OpenAI
# *connection* that an agent-only project does not have. The chat model
# therefore always goes through the analyzer's Azure OpenAI path — the same
# Foundry account, via its ``.openai.azure.com`` endpoint — and this module owns
# only what is genuinely Foundry-specific: the hosted agents.

_AGENT_ROSTER_CACHE: dict[str, dict[str, str]] = {}
_AGENT_ROSTER_LOCK = threading.Lock()


def _enum_name(value: Any) -> str:
    """Normalize an SDK enum (or its ``str`` form) to a bare lowercase name."""
    text = getattr(value, "value", None) or str(value)
    return text.rsplit(".", 1)[-1].strip().lower()


def _agent_roster(agents_client: Any, project_endpoint: str) -> dict[str, str]:
    """Return a cached ``{agent_name: agent_id}`` map for the project.

    The roster is listed once per project endpoint and reused, so a four-stage
    pipeline does not pay four listing round-trips per update.
    """
    with _AGENT_ROSTER_LOCK:
        cached = _AGENT_ROSTER_CACHE.get(project_endpoint)
    if cached:
        return cached
    roster = {a.name: a.id for a in agents_client.list_agents() if getattr(a, "name", None)}
    with _AGENT_ROSTER_LOCK:
        _AGENT_ROSTER_CACHE[project_endpoint] = roster
    return roster


def _latest_agent_text(agents_client: Any, thread_id: str) -> str:
    """Return the newest assistant message text in a thread, or ''.

    The listing is newest-first, so the first assistant message carrying text is
    the run's answer.
    """
    for message in agents_client.messages.list(thread_id=thread_id):
        if _enum_name(getattr(message, "role", "")) not in ("agent", "assistant"):
            continue
        parts = [
            part.text.value
            for part in (getattr(message, "content", None) or [])
            if getattr(getattr(part, "text", None), "value", None)
        ]
        if parts:
            return "\n".join(parts).strip()
    return ""


def _run_hosted_agent_sync(project_endpoint: str, agent_name: str, prompt: str) -> str:
    """Run one hosted agent to completion and return its text (blocking).

    Args:
        project_endpoint: Foundry project endpoint.
        agent_name: Name of an agent already published in the project.
        prompt: Fully rendered prompt to send as the user message.

    Returns:
        The agent's response text, or '' when the agent is absent or the run did
        not complete.
    """
    from azure.ai.agents.models import AgentThreadCreationOptions, ThreadMessageOptions
    from azure.ai.projects import AIProjectClient

    from src.config import get_azure_credential

    agents_client = AIProjectClient(
        endpoint=project_endpoint,
        credential=get_azure_credential(),
    ).agents

    agent_id = _agent_roster(agents_client, project_endpoint).get(agent_name)
    if not agent_id:
        logger.warning("foundry_agent_not_found", agent=agent_name)
        return ""

    run = agents_client.create_thread_and_process_run(
        agent_id=agent_id,
        thread=AgentThreadCreationOptions(
            messages=[ThreadMessageOptions(role="user", content=prompt)]
        ),
    )
    status = _enum_name(getattr(run, "status", ""))
    if status != "completed":
        logger.warning(
            "foundry_agent_run_incomplete",
            agent=agent_name,
            status=status,
            error=str(getattr(run, "last_error", "") or ""),
        )
        return ""
    return _latest_agent_text(agents_client, run.thread_id)


def build_enrichment_node(
    settings: Settings,
) -> Optional[Callable[[dict], Awaitable[dict]]]:
    """Build a LangGraph enrichment node backed by a Foundry agent, or None.

    The node invokes a pre-configured Foundry Agent Service agent (referenced by
    ``foundry_enrichment_agent_name``) whose server-side tools gather richer
    context for the update under analysis. The returned text is appended to
    ``update_context`` so the existing planning node consumes it unchanged.

    Returns ``None`` when the Foundry backend is not usable (wrong backend,
    no agent name, or SDK missing); the analyzer then keeps its original
    ``plan`` entry point and the core loop is unaffected.

    Args:
        settings: Application settings.

    Returns:
        An async node callable ``(state) -> dict``, or ``None``.
    """
    if not settings.use_foundry:
        return None
    if not settings.foundry_enrichment_agent_name:
        return None
    if not foundry_available():
        logger.warning(
            "foundry_enrichment_disabled",
            reason="langchain-azure-ai not installed (pip install azbrief[foundry])",
        )
        return None

    agent_name = settings.foundry_enrichment_agent_name
    project_endpoint = settings.foundry_project_endpoint
    timeout_s = settings.foundry_agent_timeout_s

    async def enrich_node(state: dict) -> dict:
        update_context = state.get("update_context", "")
        prompt = ENRICHMENT_PROMPT.format(update_context=update_context)
        enriched = await _invoke_hosted_agent(
            project_endpoint, agent_name, "latest", prompt, timeout_s
        )
        if enriched:
            merged = f"{update_context}\n\n{ENRICHMENT_HEADER}\n{enriched}"
            logger.info(
                "foundry_enrichment_done",
                agent=agent_name,
                added_chars=len(enriched),
            )
            return {"update_context": merged}
        logger.info("foundry_enrichment_empty", agent=agent_name)
        # Graceful degrade: leave state unchanged, analysis continues.
        return {}

    return enrich_node


# ---------------------------------------------------------------------------
# Hosted multi-agent pipeline (enterprise profile)
# ---------------------------------------------------------------------------


async def _invoke_hosted_agent(
    project_endpoint: str,
    agent_name: str,
    version: str,
    prompt: str,
    timeout_s: int,
) -> str:
    """Run one Foundry-hosted agent and return its text, or '' on any failure.

    Args:
        project_endpoint: Foundry project endpoint.
        agent_name: Name of the agent already published in the project.
        version: Accepted for call-site compatibility and ignored — the Agents
            data plane addresses an agent by id, with no version selector.
        prompt: Fully rendered prompt to send.
        timeout_s: Per-agent wall-clock timeout.

    Returns:
        The agent's response text, or an empty string when it failed.
    """
    import asyncio

    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_run_hosted_agent_sync, project_endpoint, agent_name, prompt),
            timeout=timeout_s,
        )
    except Exception as exc:  # pragma: no cover - requires live SDK
        logger.warning("foundry_agent_failed", agent=agent_name, error=str(exc))
        return ""


def _render_findings(sections: list[tuple[str, str]]) -> str:
    """Render collected stage outputs as labelled markdown sections."""
    blocks = []
    for stage, text in sections:
        label = MULTI_AGENT_STAGE_LABELS.get(stage, stage)
        blocks.append(f"### {label}\n{text}")
    return "\n\n".join(blocks)


def build_multi_agent_node(
    settings: Settings,
) -> Optional[Callable[[dict], Awaitable[dict]]]:
    """Build a LangGraph node that runs the Foundry hosted multi-agent pipeline.

    Stages run as separate Foundry-hosted agents: ``research`` and ``impact``
    concurrently, then ``action`` on their combined output, then ``review``.
    Every stage is optional — the roster in ``FOUNDRY_AGENTS`` decides which
    ones exist, and a stage that errors or times out simply contributes
    nothing. The merged text is appended to ``update_context``, so the
    Plan-Execute-Evaluate loop downstream is unchanged.

    Returns ``None`` when the pipeline is not usable (wrong backend, empty
    roster, or SDK missing); the analyzer then falls back to the single
    enrichment agent or to no enrichment at all.

    Args:
        settings: Application settings.

    Returns:
        An async node callable ``(state) -> dict``, or ``None``.
    """
    if not settings.use_foundry:
        return None

    roster = settings.get_foundry_agents()
    if not roster:
        return None

    if not foundry_available():
        logger.warning(
            "foundry_multi_agent_disabled",
            reason="langchain-azure-ai not installed (pip install azbrief[foundry])",
        )
        return None

    by_stage = {spec.stage: spec for spec in roster}
    project_endpoint = settings.foundry_project_endpoint
    timeout_s = settings.foundry_agent_timeout_s

    def _prompt(stage: str, update_context: str, prior: str) -> str:
        spec = by_stage[stage]
        base = STAGE_PROMPTS[stage].format(
            update_context=update_context,
            prior_findings=prior or "(none)",
        )
        return f"{base}\n\n{spec.instructions}" if spec.instructions else base

    async def _run_stage(stage: str, update_context: str, prior: str) -> tuple[str, str]:
        spec = by_stage[stage]
        text = await _invoke_hosted_agent(
            project_endpoint,
            spec.name,
            spec.version,
            _prompt(stage, update_context, prior),
            timeout_s,
        )
        return stage, text

    async def multi_agent_node(state: dict) -> dict:
        import asyncio
        import time

        update_context = state.get("update_context", "")
        started = time.time()
        sections: list[tuple[str, str]] = []

        # Phase 1 — independent stages in parallel.
        parallel = [s for s in ("research", "impact") if s in by_stage]
        if parallel:
            results = await asyncio.gather(
                *[_run_stage(s, update_context, "") for s in parallel],
                return_exceptions=True,
            )
            for item in results:
                if isinstance(item, BaseException):
                    logger.warning("foundry_multi_agent_stage_error", error=str(item))
                    continue
                stage, text = item
                if text:
                    sections.append((stage, text))

        # Phase 2 — dependent stages, each seeing everything gathered so far.
        for stage in ("action", "review"):
            if stage not in by_stage:
                continue
            prior = _render_findings(sections)
            try:
                _, text = await _run_stage(stage, update_context, prior)
            except Exception as exc:  # pragma: no cover - requires live SDK
                logger.warning("foundry_multi_agent_stage_error", stage=stage, error=str(exc))
                continue
            if text and text.strip().upper() != "NO ISSUES":
                sections.append((stage, text))

        if not sections:
            logger.info("foundry_multi_agent_empty", stages=list(by_stage))
            return {}

        merged_findings = _render_findings(sections)
        merged = f"{update_context}\n\n{MULTI_AGENT_HEADER}\n{merged_findings}"
        logger.info(
            "foundry_multi_agent_done",
            stages=[stage for stage, _ in sections],
            added_chars=len(merged_findings),
            elapsed_s=round(time.time() - started, 1),
        )
        return {"update_context": merged}

    logger.info(
        "foundry_multi_agent_ready",
        stages=sorted(by_stage),
        agents=[spec.name for spec in roster],
    )
    return multi_agent_node
