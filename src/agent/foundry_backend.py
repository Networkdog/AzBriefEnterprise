"""Microsoft Foundry backend integration for AzBrief (optional, opt-in).

This module bridges the AzBrief LangGraph agent to **Microsoft Foundry Agent
Service**. It is entirely opt-in and degrades gracefully so the default
Azure OpenAI path is never affected:

* Nothing here is imported at package-import time by the core loop.
* Every ``langchain-azure-ai`` / Foundry SDK import is deferred into a function.
* Each public function returns a safe fallback (``None`` / unchanged state) when
  the SDK is missing, the endpoint is unset, or a live call fails.

Enable it with ``llm_backend='foundry'`` plus ``foundry_project_endpoint``.
The **enrichment agent** is a pre-configured Foundry Agent Service agent
(referenced by ``foundry_enrichment_agent_name``) whose *server-side* tools —
Web/Bing search, Azure MCP, Microsoft Learn MCP, and memory — gather richer
context before analysis. AzBrief references it by name and inserts it as a
LangGraph node ahead of planning; the Plan-Execute-Evaluate loop, KQL
determinism, and G-Eval quality pipeline are unchanged.

Requires the optional ``foundry`` extra::

    pip install azbrief[foundry]   # installs langchain-azure-ai
"""

from __future__ import annotations

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
    """Return True if the optional 'foundry' extra (langchain-azure-ai) is importable."""
    try:
        import langchain_azure_ai  # noqa: F401

        return True
    except Exception:
        return False


def create_foundry_chat_model(
    settings: Settings,
    *,
    temperature: Optional[float] = None,
    request_timeout: int = 120,
) -> Optional[Any]:
    """Create a Foundry-backed LangChain chat model, or None on any failure.

    Uses ``AzureAIChatCompletionsModel`` from ``langchain-azure-ai`` against the
    Foundry project inference endpoint, authenticating with Managed Identity /
    ``DefaultAzureCredential``. Returns ``None`` — signalling the caller to fall
    back to Azure OpenAI — when the SDK is missing, the endpoint is unset, or
    construction fails.

    Args:
        settings: Application settings.
        temperature: Optional sampling temperature (omitted for reasoning models).
        request_timeout: Per-request timeout in seconds.

    Returns:
        A LangChain ``BaseChatModel`` instance, or ``None``.
    """
    if not settings.foundry_project_endpoint:
        return None
    try:
        from langchain_azure_ai.chat_models import AzureAIChatCompletionsModel

        from src.config import get_azure_credential

        model = settings.foundry_model_deployment or settings.azure_openai_deployment_name
        kwargs: dict[str, Any] = {
            "endpoint": settings.foundry_project_endpoint,
            "credential": get_azure_credential(),
            "model": model,
            "client_kwargs": {"timeout": request_timeout},
        }
        if settings.foundry_api_version:
            kwargs["api_version"] = settings.foundry_api_version
        if temperature is not None:
            kwargs["temperature"] = temperature
        chat = AzureAIChatCompletionsModel(**kwargs)
        logger.info(
            "foundry_chat_model_created",
            model=model,
            endpoint=settings.foundry_project_endpoint,
        )
        return chat
    except Exception as exc:  # pragma: no cover - requires live SDK
        logger.warning("foundry_chat_model_unavailable", error=str(exc))
        return None


def _extract_text(result: Any) -> str:
    """Best-effort extraction of assistant text from a LangGraph node result."""
    try:
        if isinstance(result, dict):
            messages = result.get("messages")
            if messages:
                last = messages[-1]
                content = getattr(last, "content", None)
                if content is None and isinstance(last, dict):
                    content = last.get("content")
                if isinstance(content, str):
                    return content.strip()
                if isinstance(content, list):
                    parts = [p.get("text", "") if isinstance(p, dict) else str(p) for p in content]
                    return "\n".join(parts).strip()
        if isinstance(result, str):
            return result.strip()
    except Exception:
        pass
    return ""


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

    async def enrich_node(state: dict) -> dict:
        import asyncio

        update_context = state.get("update_context", "")
        try:
            from langchain_azure_ai.agents import AgentServiceFactory
            from langchain_core.messages import HumanMessage

            from src.config import get_azure_credential

            factory = AgentServiceFactory(
                project_endpoint=project_endpoint,
                credential=get_azure_credential(),
            )
            node = factory.get_agent_node(name=agent_name, version="latest")

            prompt = ENRICHMENT_PROMPT.format(update_context=update_context)
            payload = {"messages": [HumanMessage(content=prompt)]}

            # get_agent_node().invoke may be sync-only; run off the event loop.
            ainvoke = getattr(node, "ainvoke", None)
            if callable(ainvoke):
                result = await ainvoke(payload)
            else:
                result = await asyncio.to_thread(node.invoke, payload)

            enriched = _extract_text(result)
            if enriched:
                merged = f"{update_context}\n\n{ENRICHMENT_HEADER}\n{enriched}"
                logger.info(
                    "foundry_enrichment_done",
                    agent=agent_name,
                    added_chars=len(enriched),
                )
                return {"update_context": merged}
            logger.info("foundry_enrichment_empty", agent=agent_name)
        except Exception as exc:  # pragma: no cover - requires live SDK
            logger.warning(
                "foundry_enrichment_failed",
                agent=agent_name,
                error=str(exc),
            )
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
        version: Agent version selector (usually 'latest').
        prompt: Fully rendered prompt to send.
        timeout_s: Per-agent wall-clock timeout.

    Returns:
        The agent's response text, or an empty string when it failed.
    """
    import asyncio

    try:
        from langchain_azure_ai.agents import AgentServiceFactory
        from langchain_core.messages import HumanMessage

        from src.config import get_azure_credential

        factory = AgentServiceFactory(
            project_endpoint=project_endpoint,
            credential=get_azure_credential(),
        )
        node = factory.get_agent_node(name=agent_name, version=version)
        payload = {"messages": [HumanMessage(content=prompt)]}

        ainvoke = getattr(node, "ainvoke", None)
        if callable(ainvoke):
            result = await asyncio.wait_for(ainvoke(payload), timeout=timeout_s)
        else:
            result = await asyncio.wait_for(
                asyncio.to_thread(node.invoke, payload), timeout=timeout_s
            )
        return _extract_text(result)
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
