"""Offline contracts for Learn more traversal and analysis evidence propagation."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from langchain_core.messages import AIMessage
from pydantic import ValidationError
from structlog.testing import capture_logs

from src.agent import context_store, foundry_backend
from src.agent.analyzer import AnalysisPlan, AnalysisResult, AnalysisTask, AzureUpdateAnalyzer
from src.agent.context_store import ToolResultStore, get_result_store
from src.agent.documentation import (
    DocumentationInvestigation,
    DocumentationLimits,
    DocumentationTraversalError,
    current_documentation,
    documentation_context,
)
from src.agent.foundry_backend import foundry_invocation_context
from src.agent.resilience import CircuitBreaker
from src.agent.scope import SCOPED_ANALYSIS_TOOL_NAMES, AnalysisScope
from src.agent.tools import (
    FetchDocumentationLinkInput,
    FetchDocumentationLinkTool,
    QueryToolResultTool,
    SearchAzureDocsTool,
    get_all_tools,
)
from src.config import Settings
from src.rss.parser import AzureUpdate
from src.services.microsoft_learn import (
    DocumentationFetchResult,
    DocumentationLink,
    DocumentationPage,
    MicrosoftLearnService,
)

BASE = "https://learn.microsoft.com/en-us/azure/example/"
ANNOUNCEMENT = "https://azure.microsoft.com/updates?id=123456"


def _link(path: str, text: str = "Configuration requirements") -> DocumentationLink:
    return {"url": BASE + path, "text": text, "section": "Prerequisites"}


def _page(
    path: str,
    *links: DocumentationLink,
    content: str = "Documented feature facts.",
    requested_url: str = "",
) -> DocumentationPage:
    return {
        "title": path,
        "url": BASE + path,
        "requested_url": requested_url or BASE + path,
        "content": content,
        "sections": ["Prerequisites"],
        "links": list(links),
        "code_blocks": [],
        "visuals": [],
        "links_truncated": False,
    }


class FakeLearnService(MicrosoftLearnService):
    """A real service interface with no network or credentials."""

    def __init__(self, pages: dict[str, DocumentationPage | str]) -> None:
        super().__init__()
        self.pages = pages
        self.calls: list[str] = []

    async def fetch_documentation_page(self, url: str) -> DocumentationFetchResult:
        self.calls.append(url)
        page = self.pages[url]
        if isinstance(page, str):
            return {"success": False, "data": None, "error": page}
        return {"success": True, "data": page, "error": ""}


@pytest.fixture(autouse=True)
def isolated_store(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(context_store, "_store", ToolResultStore())
    monkeypatch.setattr("src.agent.analyzer._VERBOSE", False)


def _service() -> FakeLearnService:
    return FakeLearnService(
        {
            BASE + "root": _page("root", _link("first"), _link("other")),
            BASE + "first": _page("first", _link("second"), _link("root")),
            BASE + "other": _page("other", _link("first")),
            BASE + "second": _page("second", _link("third"), content="Exact migration condition."),
            BASE + "third": _page("third"),
        }
    )


async def _prefetch(investigation: DocumentationInvestigation, service: FakeLearnService) -> None:
    await investigation.prefetch(
        service, [_link("root")], source_url=ANNOUNCEMENT, topic="Example feature configuration"
    )


@pytest.mark.asyncio
async def test_depth_one_is_automatic_but_depth_two_requires_a_question() -> None:
    service = _service()
    investigation = DocumentationInvestigation("traversal")

    await _prefetch(investigation, service)

    assert service.calls == [BASE + "root", BASE + "first", BASE + "other"]
    assert investigation.summary()["depth_0_pages"] == 1
    assert investigation.summary()["depth_1_pages"] == 2
    assert investigation.summary()["depth_2_pages"] == 0
    assert "depth=2" in investigation.render_context()
    assert "Unfetched body-link candidates" in investigation.render_context()

    with pytest.raises(DocumentationTraversalError, match="question"):
        await investigation.follow_link(
            service, parent_url=BASE + "first", url=BASE + "second", question="   "
        )
    assert len(service.calls) == 3

    result = await investigation.follow_link(
        service,
        parent_url=BASE + "first",
        url=BASE + "second",
        question="Which exact migration prerequisite applies?",
    )
    assert "Exact migration condition." in result
    assert "Depth: 2" in result
    assert "Parent URL: " + BASE + "first" in result
    assert "Which exact migration prerequisite applies?" in result
    assert investigation.summary()["depth_2_pages"] == 1
    assert BASE + "third" not in service.calls


@pytest.mark.asyncio
async def test_depth_three_and_guessed_parent_or_child_are_rejected() -> None:
    service = _service()
    investigation = DocumentationInvestigation("depth-limit")
    await _prefetch(investigation, service)
    await investigation.follow_link(
        service,
        parent_url=BASE + "first",
        url=BASE + "second",
        question="Which migration prerequisite applies?",
    )

    for parent, child, reason in [
        ("second", "third", "depth 2"),
        ("root", "third", "body link"),
        ("invented", "second", "body link"),
    ]:
        with pytest.raises(DocumentationTraversalError, match=reason):
            await investigation.follow_link(
                service,
                parent_url=BASE + parent,
                url=BASE + child,
                question="Which region supports this feature?",
            )
    assert len(service.calls) == 4
    assert "depth-limited evidence gap" in investigation.render_context()
    assert "depth_limit" in investigation.gaps[(BASE + "second", BASE + "third")]


@pytest.mark.asyncio
async def test_duplicate_roots_body_links_and_cycles_do_not_refetch() -> None:
    service = _service()
    investigation = DocumentationInvestigation("duplicates")
    await investigation.prefetch(
        service,
        [_link("root"), _link("root#section"), _link("root?utm_source=tracking")],
        source_url=ANNOUNCEMENT,
        topic="configuration",
    )
    assert service.calls == [BASE + "root", BASE + "first", BASE + "other"]
    result = await investigation.follow_link(
        service,
        parent_url=BASE + "other",
        url=BASE + "first",
        question="What configuration is required?",
    )
    assert "Documented feature facts" in result
    assert len(service.calls) == 3


@pytest.mark.asyncio
async def test_redirect_alias_deduplicates_stored_documents_and_follow_up() -> None:
    service = FakeLearnService(
        {
            BASE + "root": _page("root", _link("alias"), _link("first")),
            BASE + "alias": _page("first", _link("second"), requested_url=BASE + "alias"),
            BASE + "second": _page("second"),
        }
    )
    investigation = DocumentationInvestigation("redirect-alias")
    await _prefetch(investigation, service)
    assert service.calls == [BASE + "root", BASE + "alias"]
    await investigation.follow_link(
        service,
        parent_url=BASE + "alias",
        url=BASE + "second",
        question="Which supported version is required?",
    )
    assert investigation.documents[BASE + "second"].depth == 2
    assert len(service.calls) == 3


@pytest.mark.asyncio
async def test_meaningful_version_parameters_are_distinct_sources() -> None:
    service = FakeLearnService(
        {
            BASE + "root": _page("root", _link("version?view=v1"), _link("version?view=v2")),
            BASE + "version?view=v1": _page("version?view=v1", content="Version one rules."),
            BASE + "version?view=v2": _page("version?view=v2", content="Version two rules."),
        }
    )
    investigation = DocumentationInvestigation("versions")
    await _prefetch(investigation, service)
    assert len(service.calls) == 3
    assert "Version one rules" in investigation.render_context()
    assert "Version two rules" in investigation.render_context()


@pytest.mark.asyncio
async def test_shared_attempt_budget_applies_to_later_second_hop() -> None:
    service = _service()
    investigation = DocumentationInvestigation("budget", DocumentationLimits(fetch_attempts=3))
    await _prefetch(investigation, service)
    with pytest.raises(DocumentationTraversalError, match="budget"):
        await investigation.follow_link(
            service,
            parent_url=BASE + "first",
            url=BASE + "second",
            question="Which migration prerequisite applies?",
        )
    assert len(service.calls) == 3
    assert investigation.summary()["fetch_attempts"] == 3
    assert "fetch_budget_exhausted" in investigation.render_context()


@pytest.mark.asyncio
async def test_root_and_automatic_link_limits_remain_explicit() -> None:
    service = _service()
    investigation = DocumentationInvestigation(
        "limits", DocumentationLimits(root_pages=1, first_hop_per_page=1)
    )
    await investigation.prefetch(
        service,
        [_link("root"), _link("extra")],
        source_url=ANNOUNCEMENT,
        topic="configuration",
    )
    assert service.calls == [BASE + "root", BASE + "first"]
    context = investigation.render_context()
    assert "root_page_limit" in context
    assert "automatic_first_hop_limit" in context
    assert BASE + "extra" in context
    await investigation.follow_link(
        service,
        parent_url=BASE + "root",
        url=BASE + "other",
        question="What authentication restrictions apply?",
    )
    assert "automatic_first_hop_limit" not in investigation.render_context()


@pytest.mark.asyncio
async def test_decision_links_are_prioritized_before_generic_related_articles() -> None:
    generic = _link("generic", "Overview")
    generic["section"] = "See also"
    service = FakeLearnService(
        {
            BASE + "root": _page("root", generic, _link("limits", "Service limitations")),
            BASE + "limits": _page("limits"),
        }
    )
    investigation = DocumentationInvestigation(
        "priority", DocumentationLimits(first_hop_per_page=1)
    )
    await _prefetch(investigation, service)
    assert service.calls == [BASE + "root", BASE + "limits"]
    assert BASE + "generic" in investigation.render_context()


@pytest.mark.asyncio
async def test_full_article_past_preview_is_searchable_with_the_owning_trace() -> None:
    marker = "MANDATORY_PREREQUISITE_NEEDLE"
    service = FakeLearnService(
        {BASE + "root": _page("root", content=("Background detail.\n" * 350) + marker)}
    )
    investigation = DocumentationInvestigation("full-text")
    await _prefetch(investigation, service)
    document = investigation.documents[BASE + "root"]
    assert marker not in document.preview
    assert document.reference in investigation.render_context()
    stored = get_result_store().get(document.reference, trace_id="full-text")
    assert stored is not None and marker in stored.content and not stored.is_partial
    assert get_result_store().get(document.reference, trace_id="different-analysis") is None
    with foundry_invocation_context("full-text", "documentation-check"):
        result = await QueryToolResultTool().ainvoke({"ref": document.reference, "pattern": marker})
    assert marker in result


@pytest.mark.asyncio
async def test_complete_source_code_blocks_survive_a_long_article_preview() -> None:
    page = _page("root", content="Background details.\n" * 400)
    page["code_blocks"] = ["```azurecli\naz resource list --query '[].id'\n```"]
    service = FakeLearnService({BASE + "root": page})
    investigation = DocumentationInvestigation("source-code")
    await _prefetch(investigation, service)
    assert "az resource list" in investigation.render_context()
    assert "```azurecli\naz resource list --query '[].id'\n```" in investigation.render_context()
    assert "```\n```azurecli" not in investigation.render_context()
    assert "action verification is still required" in investigation.render_context()


@pytest.mark.asyncio
async def test_content_capacity_is_not_silent_truncation() -> None:
    service = FakeLearnService({BASE + "root": _page("root", content="facts" * 200)})
    investigation = DocumentationInvestigation(
        "content-limit", DocumentationLimits(retained_chars=200)
    )
    await _prefetch(investigation, service)
    assert not investigation.documents
    assert "retained_content_budget_exhausted" in investigation.render_context()
    assert "fetched=true" not in investigation.render_context()


@pytest.mark.asyncio
async def test_failed_pages_remain_gaps_and_are_not_retried() -> None:
    service = _service()
    service.pages[BASE + "first"] = "HTTP 403"
    investigation = DocumentationInvestigation("failure")
    with capture_logs() as logs:
        await _prefetch(investigation, service)
    assert any(entry["event"] == "documentation_evidence_gap" for entry in logs)
    assert "HTTP 403" in investigation.render_context()
    assert BASE + "first" not in investigation.documents
    with pytest.raises(DocumentationTraversalError, match="403"):
        await investigation.follow_link(
            service,
            parent_url=BASE + "root",
            url=BASE + "first",
            question="Which required configuration applies?",
        )
    assert service.calls.count(BASE + "first") == 1


@pytest.mark.asyncio
async def test_extraction_incompleteness_is_visible_even_when_page_body_succeeded() -> None:
    service = _service()
    page = _page("root")
    page["links_truncated"] = True
    service.pages[BASE + "root"] = page
    investigation = DocumentationInvestigation("link-limit")
    await _prefetch(investigation, service)
    assert "article_link_extraction_limit" in investigation.render_context()
    assert "fetched=true" in investigation.render_context()


@pytest.mark.asyncio
async def test_time_limit_and_cancellation_do_not_become_success() -> None:
    service = _service()

    async def wait_forever(_url: str) -> DocumentationFetchResult:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    service.fetch_documentation_page = AsyncMock(side_effect=wait_forever)
    investigation = DocumentationInvestigation(
        "time-limit", DocumentationLimits(fetch_seconds=0.01)
    )
    await _prefetch(investigation, service)
    assert not investigation.documents
    assert "fetch_time_budget_exhausted" in investigation.render_context()

    service.fetch_documentation_page = AsyncMock(side_effect=asyncio.CancelledError)
    with pytest.raises(asyncio.CancelledError):
        await _prefetch(DocumentationInvestigation("cancelled"), service)


@pytest.mark.parametrize(
    "limits",
    [
        {"root_pages": 0},
        {"fetch_attempts": 0},
        {"preview_chars": 0},
        {"fetch_seconds": float("inf")},
        {"fetch_seconds": float("nan")},
    ],
)
def test_limits_are_validated(limits: dict) -> None:
    with pytest.raises(ValueError, match="positive"):
        DocumentationLimits(**limits)


@pytest.mark.asyncio
async def test_tool_is_request_local_and_requires_a_structured_question() -> None:
    service = _service()
    tool = FetchDocumentationLinkTool(service=service)
    args = {
        "parent_url": BASE + "first",
        "url": BASE + "second",
        "question": "Which migration prerequisite applies?",
    }
    with pytest.raises(DocumentationTraversalError, match="active analysis"):
        await tool.ainvoke(args)
    with pytest.raises(ValidationError):
        FetchDocumentationLinkInput(parent_url=BASE + "first", url=BASE + "second", question="")
    with documentation_context("tool") as investigation:
        await _prefetch(investigation, service)
        result = await tool.ainvoke(args)
        assert "Exact migration condition" in result
    assert current_documentation() is None
    assert tool.name in AzureUpdateAnalyzer.PLANNING_TOOL_NAMES
    assert tool.name in SCOPED_ANALYSIS_TOOL_NAMES
    assert tool.name not in foundry_backend.SPECIALIST_LOCAL_TOOL_NAMES["azure_api"]
    assert tool.name not in foundry_backend.SPECIALIST_LOCAL_TOOL_NAMES["resource_graph"]
    assert tool.name in {entry.name for entry in get_all_tools()}


def test_persisted_coordinator_guidance_names_the_runtime_follow_up_contract() -> None:
    from scripts.provision_foundry_agents import agent_instructions

    instructions = agent_instructions("coordinator")
    for rule in (
        "automatically fetches Azure Update Learn more targets at depth 0",
        "fetch_documentation_link",
        "parent_url",
        "shared page/time budgets",
        "query_tool_result",
    ):
        assert rule in instructions


@pytest.mark.asyncio
async def test_concurrent_analyses_do_not_share_budgets_or_source_graphs() -> None:
    async def one_analysis(trace_id: str) -> str:
        with documentation_context(trace_id) as investigation:
            await _prefetch(investigation, _service())
            await asyncio.sleep(0)
            assert current_documentation() is investigation
            assert investigation.summary()["fetch_attempts"] == 3
            return investigation.documents[BASE + "root"].reference

    first, second = await asyncio.gather(one_analysis("first"), one_analysis("second"))
    assert first != second
    assert get_result_store().get(first, trace_id="second") is None
    assert get_result_store().get(second, trace_id="first") is None
    assert current_documentation() is None


@pytest.mark.asyncio
async def test_planning_keeps_second_hop_and_retrieved_excerpts_for_later_phases() -> None:
    service = _service()
    service.pages[BASE + "second"] = _page(
        "second", content=("Background.\n" * 500) + "PINNED_PREREQUISITE"
    )
    analyzer = object.__new__(AzureUpdateAnalyzer)
    analyzer.settings = SimpleNamespace(custom_system_prompt="")
    analyzer.tools = [FetchDocumentationLinkTool(service=service), QueryToolResultTool()]
    analyzer._llm_circuit_breaker = CircuitBreaker()
    plan = AnalysisPlan(plan_id="p1", update_summary="update", analysis_goal="brief", tasks=[])
    calls = 0

    async def coordinate(_messages: list) -> AIMessage:
        nonlocal calls
        calls += 1
        if calls == 1:
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "follow",
                        "name": "fetch_documentation_link",
                        "args": {
                            "parent_url": BASE + "first",
                            "url": BASE + "second",
                            "question": "Which pinned prerequisite is mandatory?",
                        },
                    }
                ],
            )
        if calls == 2:
            investigation = current_documentation()
            assert investigation is not None
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "read",
                        "name": "query_tool_result",
                        "args": {
                            "ref": investigation.documents[BASE + "second"].reference,
                            "pattern": "PINNED_PREREQUISITE",
                        },
                    }
                ],
            )
        return AIMessage(content=plan.model_dump_json())

    model = SimpleNamespace(ainvoke=coordinate)
    analyzer.llm_coordinator = SimpleNamespace(bind_tools=Mock(return_value=model))
    with documentation_context("planning-evidence") as investigation:
        await _prefetch(investigation, service)
        state = {
            "update_context": "Original public announcement",
            "documentation_context": investigation.render_context(),
            "trace_id": "planning-evidence",
        }
        result = await analyzer._planning_node(state)
        merged = {**state, **result}
        context = analyzer._update_context_with_documentation(merged)
        assert "PINNED_PREREQUISITE" in context
        assert "Retrieved documentation excerpts" in context
        assert "Depth: 2" in context
        assert result["task_results"] == {}
        assert "PINNED_PREREQUISITE" not in state["documentation_context"]
        assert len(service.calls) == 4


@pytest.mark.asyncio
async def test_execution_marks_a_depth_violation_failed_without_argument_repair() -> None:
    service = _service()
    analyzer = object.__new__(AzureUpdateAnalyzer)
    analyzer.tools = [FetchDocumentationLinkTool(service=service)]
    analyzer._inject_enrichment_tasks = lambda plan, state: plan
    analyzer._fix_tool_args = AsyncMock()
    plan = AnalysisPlan(
        plan_id="p1",
        update_summary="update",
        analysis_goal="brief",
        tasks=[
            AnalysisTask(
                task_id="doc",
                description="Verify an observed prerequisite",
                method="learn_search",
                tool_name="fetch_documentation_link",
                tool_args={
                    "parent_url": BASE + "second",
                    "url": BASE + "third",
                    "question": "Which prerequisite remains required?",
                },
                purpose="Close the remaining evidence gap",
            )
        ],
    )
    with documentation_context("execute") as investigation:
        await _prefetch(investigation, service)
        await investigation.follow_link(
            service,
            parent_url=BASE + "first",
            url=BASE + "second",
            question="Which prerequisite is required?",
        )
        state = {"analysis_plan": plan.model_dump(), "trace_id": "execute", "task_results": {}}
        result = await analyzer._execution_node(state)
    task = result["analysis_plan"]["tasks"][0]
    assert task["status"] == "failed"
    assert task["retry_count"] == 1
    assert "depth 2" in task["error"]
    analyzer._fix_tool_args.assert_not_awaited()
    assert BASE + "third" not in service.calls


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["evaluation", "report", "revise_tasks"])
async def test_all_downstream_phase_prompts_receive_documentation_evidence(phase: str) -> None:
    analyzer = object.__new__(AzureUpdateAnalyzer)
    analyzer.settings = SimpleNamespace(custom_system_prompt="", report_language="ko")
    analyzer._llm_circuit_breaker = CircuitBreaker()
    analyzer.max_iterations = 5
    if phase == "evaluation":
        response = '{"verdict":"sufficient","coverage":{"documentation_evidence":true}}'
    elif phase == "revise_tasks":
        response = "[]"
    else:
        response = '{"update_category":"feature_change","detailed_analysis":"Synthetic brief."}'
    model = SimpleNamespace(ainvoke=AsyncMock(return_value=AIMessage(content=response)))
    analyzer.llm_quality_reviewer = model
    analyzer.llm_report_writer = model
    analyzer.llm_coordinator = model
    plan = AnalysisPlan(plan_id="p1", update_summary="update", analysis_goal="brief", tasks=[])
    state = {
        "update_context": "Original public announcement",
        "documentation_context": "DEPTH_TWO_SUPPORTED_FACT from https://learn.microsoft.com",
        "resource_summary": "Synthetic inventory",
        "analysis_plan": plan.model_dump(),
        "task_results": {},
        "trace_id": "downstream",
        "update": {"title": "Configuration", "update_type": "Feature Change"},
        "evaluation": {"verdict": "partial", "missing_aspects": ["documentation_evidence"]},
    }
    await getattr(analyzer, f"_{phase}_node")(state)
    messages = model.ainvoke.await_args.args[0]
    assert any("DEPTH_TWO_SUPPORTED_FACT" in message.content for message in messages)


@pytest.mark.asyncio
async def test_missing_documentation_coverage_cannot_be_called_sufficient() -> None:
    analyzer = object.__new__(AzureUpdateAnalyzer)
    analyzer._llm_circuit_breaker = CircuitBreaker()
    analyzer.max_iterations = 5
    analyzer.llm_quality_reviewer = SimpleNamespace(
        ainvoke=AsyncMock(
            return_value=AIMessage(
                content='{"verdict":"sufficient","coverage":{"documentation_evidence":false}}'
            )
        )
    )
    plan = AnalysisPlan(plan_id="p1", update_summary="update", analysis_goal="brief", tasks=[])
    result = await analyzer._evaluation_node(
        {
            "update_context": "Original public announcement",
            "documentation_context": "Unfetched body-link candidates, depth=2",
            "resource_summary": "",
            "analysis_plan": plan.model_dump(),
            "trace_id": "documentation-gap",
            "update": {"title": "Configuration", "update_type": "Feature Change"},
        }
    )
    assert result["evaluation"]["verdict"] == "partial"
    assert "documentation_evidence" in result["evaluation"]["missing_aspects"]
    assert any(
        "fetch_documentation_link" in suggestion
        for suggestion in result["evaluation"]["suggestions"]
    )


@pytest.mark.asyncio
async def test_specialists_receive_docs_without_copying_a_stale_snapshot_into_base(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        _env_file=None,
        azure_tenant_id="00000000-0000-0000-0000-000000000000",
        foundry_project_endpoint="https://example.services.ai.azure.com/api/projects/test",
        foundry_coordinator_agent_name="coordinator",
        foundry_resource_graph_agent_name="resource-graph",
        foundry_azure_mcp_agent_name="azure-mcp",
        foundry_azure_api_agent_name="azure-api",
        foundry_report_writer_agent_name="report-writer",
        foundry_quality_reviewer_agent_name="quality-reviewer",
    )
    prompts: list[str] = []

    async def invoke(_endpoint: str, _agent: str, prompt: str, _timeout: int, **kwargs) -> str:
        prompts.append(prompt)
        return json.dumps({"status": "partial", "claims": [], "gaps": ["Synthetic test gap"]})

    monkeypatch.setattr(foundry_backend, "foundry_available", lambda: True)
    monkeypatch.setattr(foundry_backend, "_invoke_foundry_agent", invoke)
    node = foundry_backend.build_specialist_collaboration_node(settings)
    assert node is not None
    result = await node(
        {"update_context": "Announcement", "documentation_context": "PREFETCHED_FACT"}
    )
    assert len(prompts) == 3 and all("PREFETCHED_FACT" in prompt for prompt in prompts)
    assert "PREFETCHED_FACT" not in result["update_context"]


@pytest.mark.asyncio
@pytest.mark.parametrize("critic_enabled", [False, True])
async def test_analysis_snapshot_contains_recursive_evidence_and_releases_refs(
    monkeypatch: pytest.MonkeyPatch,
    critic_enabled: bool,
) -> None:
    service = _service()
    analyzer = object.__new__(AzureUpdateAnalyzer)
    analyzer.settings = SimpleNamespace(
        report_language="ko",
        geval_enabled=True,
        geval_runtime_enabled=critic_enabled,
        action_verification_enabled=False,
        trajectory_eval_enabled=False,
    )
    analyzer.tools = [SearchAzureDocsTool(service=service)]
    analyzer.get_resource_summary = AsyncMock(return_value=("Synthetic inventory", True))
    analyzer._build_community_section = AsyncMock(return_value="")
    analyzer._summarize_announcement = AsyncMock(return_value="Source-only localized summary")
    analyzer._critic_pass = AsyncMock(
        side_effect=lambda result, *_: result.model_copy(
            update={"one_line_summary": "A rewritten environment verdict"}
        )
    )
    monkeypatch.setattr("src.agent.analyzer.setup_telemetry", lambda _: None)
    references: list[str] = []

    async def graph(state: dict) -> dict:
        assert "Depth: 1" in state["documentation_context"]
        investigation = current_documentation()
        assert investigation is not None
        await investigation.follow_link(
            service,
            parent_url=BASE + "first",
            url=BASE + "second",
            question="Which migration prerequisite applies?",
        )
        references.extend(doc.reference for doc in investigation.documents.values())
        return {**state, "documentation_context": investigation.render_context()}

    async def parse(state: dict, update: AzureUpdate) -> tuple[AnalysisResult, dict]:
        result = AnalysisResult(
            update_id=update.id,
            update_title=update.title,
            relevance="opportunity",
            relevance_reason="Synthetic grounded brief.",
            affected_resources=[],
            impact_summary="Synthetic impact.",
            recommendations=[],
            reference_docs=[{"title": "Migration", "url": BASE + "second"}],
            should_notify=True,
        )
        return result, state

    analyzer.graph = SimpleNamespace(ainvoke=graph)
    analyzer._parse_report_with_recovery = parse
    update = AzureUpdate(
        id="123456",
        title="Example configuration update",
        description="Public update body",
        link=ANNOUNCEMENT,
        published_date=None,
        categories=[],
        azure_services=["Example"],
        update_type="Feature Change",
        status=None,
        learn_more_links=[_link("root")],
    )
    result = await analyzer.analyze_update(
        update,
        trace_id="full-analysis",
        scope=AnalysisScope(subscriptions=["11111111-1111-1111-1111-111111111111"]),
    )
    assert "Exact migration condition" in result._evidence_update_context
    assert result.one_line_summary == "Source-only localized summary"
    analyzer._summarize_announcement.assert_awaited_once_with(
        update, "ko", trace_id="full-analysis"
    )
    assert analyzer._critic_pass.await_count == int(critic_enabled)
    assert "Depth: 2" in analyzer._last_update_context
    assert BASE + "first" in result._evidence_update_context
    assert "documentation_context" not in result.model_dump()
    assert all(get_result_store().get(reference) is None for reference in references)
    assert current_documentation() is None


@pytest.mark.asyncio
async def test_failure_and_cancellation_release_document_context_and_refs() -> None:
    analyzer = object.__new__(AzureUpdateAnalyzer)

    async def fail(_update: object, trace_id: str) -> AnalysisResult:
        investigation = current_documentation()
        assert investigation is not None
        await _prefetch(investigation, _service())
        raise asyncio.CancelledError

    analyzer._analyze_update_scoped = fail
    with pytest.raises(asyncio.CancelledError):
        await analyzer.analyze_update(object(), trace_id="cleanup")
    assert len(get_result_store()) == 0
    assert current_documentation() is None


@pytest.mark.asyncio
async def test_large_gap_catalog_is_bounded_but_remains_retrievable() -> None:
    links = [
        _link(f"configuration-{index:03}", f"Configuration limit {index}") for index in range(90)
    ]
    service = FakeLearnService(
        {
            BASE + "root": _page("root", *links),
            links[0]["url"]: _page("configuration-000"),
        }
    )
    investigation = DocumentationInvestigation(
        "many-links", DocumentationLimits(first_hop_per_page=1, preview_chars=1000)
    )
    await _prefetch(investigation, service)
    context = investigation.render_context()
    assert len(context) < 12_000
    root_ref = investigation.documents[BASE + "root"].reference
    entry = get_result_store().get(root_ref, trace_id="many-links")
    assert entry is not None and links[-1]["url"] in entry.content
    assert "additional candidates" in context
    assert "[TRUNCATED PREVIEW" in context
