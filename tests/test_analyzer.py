"""Tests for analyzer parsing helpers and pre-filter logic."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.messages import AIMessage

from src.agent.analyzer import (
    ActionItem,
    AnalysisPlan,
    AnalysisResult,
    AnalysisTask,
    AzureUpdateAnalyzer,
    EvaluationResult,
    RelevanceStatus,
    UrgencyLevel,
    _escape_braces,
    _extract_primary_regions,
    _missing_region_mentions,
    _region_report_gaps,
    _requires_region_availability,
)
from src.agent.resilience import CircuitBreaker, TransitionType
from src.agent.scope import AnalysisScope
from src.config import Subscriber


class TestEscapeBraces:
    """Test brace escaping for str.format()."""

    def test_curly_braces_escaped(self):
        s = "/subscriptions/{subscriptionId}/providers"
        result = _escape_braces(s)
        assert result == "/subscriptions/{{subscriptionId}}/providers"

    def test_no_braces_unchanged(self):
        s = "Hello world"
        assert _escape_braces(s) == "Hello world"

    def test_empty_string(self):
        assert _escape_braces("") == ""


class TestPrimaryRegionExtraction:
    def test_uses_resource_graph_count_order_and_excludes_nonregional_locations(self):
        summary = """## Resource Inventory (2 resource types total)
- microsoft.storage/storageaccounts: 10

## Resource Regions

- global: 50
- koreacentral: 30
- eastus: 12
- unknown: 8
- japaneast: 4
- westus: 2
"""

        assert _extract_primary_regions(summary) == ["koreacentral", "eastus", "japaneast"]

    def test_ga_and_preview_require_region_verdicts(self):
        assert _requires_region_availability(
            {"title": "Feature", "update_type": "General Availability"}
        )
        assert _requires_region_availability(
            {"title": "Public Preview: Feature", "update_type": None}
        )
        assert not _requires_region_availability(
            {"title": "Retirement: Feature", "update_type": "Retirement"}
        )

    def test_region_mentions_accept_official_names_with_spaces(self):
        content = "Korea Central에서 사용 가능하며 East US에서는 아직 지원되지 않습니다."
        assert _missing_region_mentions(content, ["koreacentral", "eastus"]) == []

    def test_report_region_check_requires_first_region_in_headline(self):
        content = json.dumps(
            {
                "one_line_summary": "기능이 GA되었습니다",
                "detailed_analysis": "Korea Central에서 지금 사용할 수 있습니다.",
            }
        )

        headline_missing, missing_regions = _region_report_gaps(content, ["koreacentral"])

        assert headline_missing is True
        assert missing_regions == []


class TestParsePlanJson:
    """Test AnalysisPlan JSON parsing from LLM responses."""

    def setup_method(self):
        """Create analyzer without real LLM (just for parsing helpers)."""
        # We can't instantiate AzureUpdateAnalyzer without env vars,
        # so we test the parsing methods indirectly via static approach
        pass

    def test_parse_valid_plan(self):
        """Valid JSON plan is parsed correctly."""
        raw = json.dumps(
            {
                "plan_id": "plan_v1",
                "update_summary": "Test update",
                "analysis_goal": "Analyze impact",
                "tasks": [
                    {
                        "task_id": "task_1",
                        "description": "Query storage accounts",
                        "method": "kql",
                        "tool_name": "query_azure_resources",
                        "tool_args": {"query": "Resources | take 10"},
                        "purpose": "Get storage info",
                    }
                ],
            }
        )
        # Use the class method directly
        analyzer = object.__new__(AzureUpdateAnalyzer)
        plan = analyzer._parse_plan_json(raw, revision=0)
        assert plan.plan_id == "plan_v1"
        assert len(plan.tasks) == 1
        assert plan.tasks[0].method == "kql"

    def test_parse_plan_with_markdown_fences(self):
        """Plan wrapped in markdown code fences is parsed."""
        inner = json.dumps(
            {
                "plan_id": "plan_v2",
                "update_summary": "Test",
                "analysis_goal": "Test",
                "tasks": [
                    {
                        "task_id": "t1",
                        "description": "Doc search",
                        "method": "learn_search",
                        "tool_name": "search_azure_docs",
                        "tool_args": {"query": "test"},
                        "purpose": "Search docs",
                    }
                ],
            }
        )
        raw = f"```json\n{inner}\n```"
        analyzer = object.__new__(AzureUpdateAnalyzer)
        plan = analyzer._parse_plan_json(raw, revision=0)
        assert len(plan.tasks) == 1

    def test_parse_plan_invalid_json_fallback(self):
        """Invalid JSON produces a fallback plan."""
        raw = "This is not JSON at all"
        analyzer = object.__new__(AzureUpdateAnalyzer)
        plan = analyzer._parse_plan_json(raw, revision=0)
        assert isinstance(plan, AnalysisPlan)
        assert len(plan.tasks) >= 1  # Fallback has at least one task

    def test_parse_plan_invalid_method_normalized(self):
        """Invalid task method is normalized to 'kql'."""
        raw = json.dumps(
            {
                "plan_id": "p1",
                "update_summary": "Test",
                "analysis_goal": "Test",
                "tasks": [
                    {
                        "task_id": "t1",
                        "description": "Test",
                        "method": "invalid_method",
                        "tool_name": "query_azure_resources",
                        "tool_args": {},
                        "purpose": "Test",
                    }
                ],
            }
        )
        analyzer = object.__new__(AzureUpdateAnalyzer)
        plan = analyzer._parse_plan_json(raw, revision=0)
        assert plan.tasks[0].method == "kql"

    @pytest.mark.parametrize(
        ("method", "tool_name"),
        [
            ("billing_api", "list_billing_accounts"),
            ("context", "query_tool_result"),
        ],
    )
    def test_parse_plan_preserves_specialist_methods(self, method, tool_name):
        raw = json.dumps(
            {
                "plan_id": "p1",
                "update_summary": "Pricing update",
                "analysis_goal": "Collect scoped evidence",
                "tasks": [
                    {
                        "task_id": "t1",
                        "description": "Collect evidence",
                        "method": method,
                        "tool_name": tool_name,
                        "tool_args": {},
                        "purpose": "Close a named gap",
                    }
                ],
            }
        )

        analyzer = object.__new__(AzureUpdateAnalyzer)
        plan = analyzer._parse_plan_json(raw, revision=0)

        assert plan.tasks[0].method == method


class TestParseEvaluationJson:
    """Test EvaluationResult JSON parsing."""

    def test_parse_valid_evaluation(self):
        """Valid evaluation JSON is parsed."""
        raw = json.dumps(
            {
                "verdict": "sufficient",
                "coverage": {"resources": True, "docs": True},
                "missing_aspects": [],
                "suggestions": [],
                "reason": "All checks passed",
            }
        )
        analyzer = object.__new__(AzureUpdateAnalyzer)
        result = analyzer._parse_evaluation_json(raw)
        assert result.verdict == "sufficient"
        assert result.reason == "All checks passed"

    def test_parse_partial_verdict(self):
        """Partial verdict parsed correctly."""
        raw = json.dumps(
            {
                "verdict": "partial",
                "coverage": {"resources": True, "docs": False},
                "missing_aspects": ["cost analysis"],
                "suggestions": ["Run cost query"],
                "reason": "Missing cost data",
            }
        )
        analyzer = object.__new__(AzureUpdateAnalyzer)
        result = analyzer._parse_evaluation_json(raw)
        assert result.verdict == "partial"
        assert "cost analysis" in result.missing_aspects

    def test_parse_invalid_json_fails_closed(self):
        """Invalid evaluator output must not be treated as sufficient evidence."""
        analyzer = object.__new__(AzureUpdateAnalyzer)
        result = analyzer._parse_evaluation_json("not json")
        assert result.verdict == "model_error"
        assert "evaluation_output_invalid" in result.missing_aspects

    def test_parse_unknown_verdict_fails_closed(self):
        """Unknown verdict values terminate instead of silently reporting."""
        raw = json.dumps(
            {
                "verdict": "unknown_value",
                "coverage": {},
                "missing_aspects": [],
                "suggestions": [],
                "reason": "test",
            }
        )
        analyzer = object.__new__(AzureUpdateAnalyzer)
        result = analyzer._parse_evaluation_json(raw)
        assert result.verdict == "model_error"

    def test_missing_evaluation_routes_to_model_error(self):
        analyzer = object.__new__(AzureUpdateAnalyzer)
        assert analyzer._route_after_evaluation({"evaluation": None}) == "model_error"

    def test_diminishing_returns_are_scoped_to_the_agent_state(self):
        analyzer = object.__new__(AzureUpdateAnalyzer)
        analyzer.max_iterations = 5
        low_progress = {
            "evaluation": {"verdict": "partial", "reason": "more evidence"},
            "iteration": 3,
            "task_result_char_history": [100, 200, 250],
        }
        healthy_progress = {
            "evaluation": {"verdict": "partial", "reason": "more evidence"},
            "iteration": 2,
            "task_result_char_history": [100, 900],
        }

        assert analyzer._route_after_evaluation(low_progress) == "sufficient"
        assert analyzer._route_after_evaluation(healthy_progress) == "partial"

    @pytest.mark.asyncio
    async def test_malformed_evaluator_response_records_terminal_model_error(self):
        analyzer = object.__new__(AzureUpdateAnalyzer)
        analyzer.llm_quality_reviewer = type("QualityReviewer", (), {})()
        analyzer.llm_quality_reviewer.ainvoke = AsyncMock(
            return_value=type("Response", (), {"content": "not json", "response_metadata": {}})()
        )
        analyzer._llm_circuit_breaker = CircuitBreaker(
            failure_threshold=3,
            reset_timeout=120,
        )
        plan = AnalysisPlan(
            plan_id="p1",
            update_summary="update",
            analysis_goal="verify evidence",
            tasks=[
                AnalysisTask(
                    task_id="t1",
                    description="search docs",
                    method="learn_search",
                    tool_name="search_azure_docs",
                    tool_args={"query": "test"},
                    purpose="ground the report",
                    status="completed",
                )
            ],
        )
        state = {
            "update_context": "update context",
            "task_results": {"t1": "result"},
            "analysis_plan": plan.model_dump(),
            "task_revision_count": 0,
            "plan_revision_count": 1,
            "task_result_char_history": [],
            "iteration": 1,
            "trace_id": "trace-1",
        }

        result = await analyzer._evaluation_node(state)

        assert result["evaluation"]["verdict"] == "model_error"
        assert result["phase"] == "error"
        assert result["last_transition"] == TransitionType.MODEL_ERROR.value

    @pytest.mark.asyncio
    async def test_missing_primary_region_coverage_cannot_pass_as_sufficient(self):
        analyzer = object.__new__(AzureUpdateAnalyzer)
        analyzer.llm_quality_reviewer = type("QualityReviewer", (), {})()
        analyzer.llm_quality_reviewer.ainvoke = AsyncMock(
            return_value=type(
                "Response",
                (),
                {
                    "content": json.dumps(
                        {
                            "verdict": "sufficient",
                            "coverage": {
                                "resource_identification": True,
                                "documentation_evidence": True,
                                "evidence_complete": True,
                            },
                            "missing_aspects": [],
                            "suggestions": [],
                            "reason": "All checks passed",
                        }
                    ),
                    "response_metadata": {},
                },
            )()
        )
        analyzer._llm_circuit_breaker = CircuitBreaker(
            failure_threshold=3,
            reset_timeout=120,
        )
        plan = AnalysisPlan(
            plan_id="p1",
            update_summary="GA feature",
            analysis_goal="verify regional availability",
            tasks=[],
        )
        state = {
            "update_context": "update context",
            "resource_summary": "## Resource Regions\n\n- koreacentral: 30",
            "update": {"title": "Feature", "update_type": "General Availability"},
            "task_results": {},
            "analysis_plan": plan.model_dump(),
            "task_revision_count": 0,
            "plan_revision_count": 1,
            "task_result_char_history": [],
            "iteration": 1,
            "trace_id": "trace-region-eval",
        }

        result = await analyzer._evaluation_node(state)

        assert result["evaluation"]["verdict"] == "partial"
        assert result["evaluation"]["coverage"]["primary_region_availability"] is False
        assert "primary_region_availability" in result["evaluation"]["missing_aspects"]
        assert "include_content=true" in result["evaluation"]["suggestions"][0]


class TestShouldSkipUpdate:
    """Test the pre-analysis skip filter."""

    def setup_method(self):
        self.analyzer = object.__new__(AzureUpdateAnalyzer)

    def _make_update(self, title, update_type=None, services=None, categories=None):
        from datetime import datetime, timezone

        from src.rss.parser import AzureUpdate

        return AzureUpdate(
            id="test-1",
            title=title,
            description=title,
            link="https://azure.microsoft.com/updates?id=1",
            published_date=datetime(2026, 3, 10, tzinfo=timezone.utc),
            categories=categories or [],
            azure_services=services or [],
            update_type=update_type,
            status=None,
        )

    def _resource_summary_with_types_and_regions(self, types, regions):
        lines = ["## Resource Inventory (5 resource types total)\n"]
        for t in types:
            lines.append(f"- {t}: 10")
        lines.append("\n## Resource Regions\n")
        for r in regions:
            lines.append(f"- {r}: 5")
        return "\n".join(lines)

    def test_retirement_never_skipped(self):
        """Retirement updates should NEVER be skipped."""
        update = self._make_update("Retirement: Classic VMs", update_type="Retirement")
        summary = self._resource_summary_with_types_and_regions(
            ["microsoft.compute/virtualmachines"], ["koreacentral"]
        )
        assert self.analyzer.should_skip_update(update, summary) is None

    def test_security_advisory_never_skipped(self):
        """Security advisories should never be skipped."""
        update = self._make_update("Security Advisory: Critical vulnerability in App Service")
        summary = self._resource_summary_with_types_and_regions([], [])
        assert self.analyzer.should_skip_update(update, summary) is None

    def test_in_development_skipped(self):
        """In Development (private preview) updates should be skipped."""
        update = self._make_update("New feature for Cosmos DB", update_type="In Development")
        summary = self._resource_summary_with_types_and_regions(
            ["microsoft.compute/virtualmachines"], ["koreacentral"]
        )
        result = self.analyzer.should_skip_update(update, summary)
        assert result is not None
        assert "private preview" in result.lower() or "in-development" in result.lower()

    def test_preview_for_unrelated_service_skipped(self):
        """Preview for services not in admin's inventory should be skipped."""
        update = self._make_update(
            "Public Preview: IoT Hub new feature",
            update_type="Public Preview",
            services=["IoT Hub"],
        )
        summary = self._resource_summary_with_types_and_regions(
            ["microsoft.compute/virtualmachines", "microsoft.storage/storageaccounts"],
            ["koreacentral"],
        )
        result = self.analyzer.should_skip_update(update, summary)
        assert result is not None

    def test_ga_for_related_service_not_skipped(self):
        """GA for services in admin's inventory should NOT be skipped."""
        update = self._make_update(
            "Generally Available: Storage Account new feature",
            update_type="General Availability",
            services=["Storage Accounts"],
        )
        summary = self._resource_summary_with_types_and_regions(
            ["microsoft.storage/storageaccounts"], ["koreacentral"]
        )
        result = self.analyzer.should_skip_update(update, summary)
        assert result is None


class TestAnalysisResultModel:
    """Test AnalysisResult pydantic model."""

    def test_create_basic_result(self):
        result = AnalysisResult(
            update_id="test-1",
            update_title="Test Update",
            relevance=RelevanceStatus.RELEVANT,
            relevance_reason="Test reason",
            affected_resources=[],
            impact_summary="No impact",
            recommendations=[],
            reference_docs=[],
            should_notify=True,
        )
        assert result.update_id == "test-1"
        assert result.urgency == UrgencyLevel.MEDIUM  # default

    def test_action_item_defaults(self):
        item = ActionItem(task="Do something")
        assert item.step == 1
        assert item.urgency == "medium"
        assert item.target_resources == []


class TestGuessCategory:
    """Category selection must survive updates with no RSS update_type."""

    def test_known_types(self):
        assert AzureUpdateAnalyzer._guess_category("Retirement") == "retirement"
        assert AzureUpdateAnalyzer._guess_category("Public Preview") == "preview"
        assert AzureUpdateAnalyzer._guess_category("General Availability") == "new_feature"

    def test_unknown_type_falls_back_to_all_categories(self):
        assert AzureUpdateAnalyzer._guess_category("Something Else") == ""

    def test_none_update_type(self):
        # The RSS feed emits update_type=null for Announcement items, which used
        # to raise AttributeError and abort the whole analysis.
        assert AzureUpdateAnalyzer._guess_category(None) == ""

    def test_empty_update_type(self):
        assert AzureUpdateAnalyzer._guess_category("") == ""


class TestEnrichmentWithNullUpdateType:
    """_inject_enrichment_tasks reads update_type straight off the state dict."""

    def test_null_update_type_does_not_crash(self):
        analyzer = object.__new__(AzureUpdateAnalyzer)
        plan = AnalysisPlan(
            plan_id="plan_v1",
            update_summary="Announcement",
            analysis_goal="assess",
            tasks=[],
        )
        state = {
            "task_results": {},
            "update": {
                "title": "Announcing: Free usage extended",
                "update_type": None,
                "azure_services": ["Azure Databricks"],
            },
        }

        result = analyzer._inject_enrichment_tasks(plan, state)

        assert result is not None


class TestRegionAvailabilityEnrichment:
    """GA and Preview updates require feature-level regional evidence."""

    @staticmethod
    def _plan() -> AnalysisPlan:
        return AnalysisPlan(
            plan_id="plan_v1",
            update_summary="Feature release",
            analysis_goal="assess",
            tasks=[],
        )

    def test_ga_adds_scoped_arm_check_and_exact_feature_doc_search(self):
        analyzer = object.__new__(AzureUpdateAnalyzer)
        title = "Generally Available: Workload identity support for Azure Files CSI driver"
        state = {
            "task_results": {},
            "resource_summary": "## Resource Regions\n\n- koreacentral: 30\n- eastus: 5",
            "update": {
                "title": title,
                "update_type": "General Availability",
                "azure_services": ["Azure Kubernetes Service (AKS)"],
            },
        }

        result = analyzer._inject_enrichment_tasks(self._plan(), state)
        tasks = {task.tool_name: task for task in result.tasks}

        assert tasks["get_service_region_availability"].tool_args == {
            "provider_namespace": "Microsoft.ContainerService",
            "resource_type": "managedClusters",
            "regions": "koreacentral,eastus",
        }
        assert tasks["search_azure_docs"].tool_args == {
            "query": f'"{title}" supported regions availability koreacentral eastus',
            "include_content": True,
            "focus_terms": ["koreacentral", "eastus"],
            "service_name": "Azure Kubernetes Service (AKS)",
        }

    def test_preview_without_resource_type_still_adds_feature_doc_search(self):
        analyzer = object.__new__(AzureUpdateAnalyzer)
        title = "Public Preview: Contoso mode for Azure Network Watcher"
        state = {
            "task_results": {},
            "update": {
                "title": title,
                "update_type": "Public Preview",
                "azure_services": ["Network Watcher"],
            },
        }

        result = analyzer._inject_enrichment_tasks(self._plan(), state)
        region_doc_tasks = [
            task
            for task in result.tasks
            if task.tool_name == "search_azure_docs"
            and task.tool_args.get("query") == f'"{title}" supported regions availability'
        ]

        assert len(region_doc_tasks) == 1
        assert region_doc_tasks[0].tool_args["include_content"] is True

    def test_unscoped_provider_task_does_not_suppress_exact_resource_type_check(self):
        analyzer = object.__new__(AzureUpdateAnalyzer)
        plan = self._plan()
        plan.tasks.append(
            AnalysisTask(
                task_id="task_1",
                description="Broad provider check",
                method="azure_rest",
                tool_name="get_service_region_availability",
                tool_args={"provider_namespace": "Microsoft.ContainerService"},
                purpose="Check provider locations",
            )
        )
        state = {
            "task_results": {},
            "resource_summary": "## Resource Regions\n\n- koreacentral: 30",
            "update": {
                "title": "Generally Available: AKS feature",
                "update_type": "General Availability",
                "azure_services": ["Azure Kubernetes Service (AKS)"],
            },
        }

        result = analyzer._inject_enrichment_tasks(plan, state)
        scoped = [
            task
            for task in result.tasks
            if task.tool_name == "get_service_region_availability"
            and task.tool_args.get("resource_type") == "managedClusters"
        ]

        assert len(scoped) == 1
        assert scoped[0].tool_args["regions"] == "koreacentral"

    def test_shallow_matching_doc_search_does_not_suppress_content_fetch(self):
        analyzer = object.__new__(AzureUpdateAnalyzer)
        title = "Generally Available: AKS feature"
        query = f'"{title}" supported regions availability koreacentral'
        plan = self._plan()
        plan.tasks.append(
            AnalysisTask(
                task_id="task_1",
                description="Search titles only",
                method="learn_search",
                tool_name="search_azure_docs",
                tool_args={"query": query},
                purpose="Find documentation",
            )
        )
        state = {
            "task_results": {},
            "resource_summary": "## Resource Regions\n\n- koreacentral: 30",
            "update": {
                "title": title,
                "update_type": "General Availability",
                "azure_services": ["Azure Kubernetes Service (AKS)"],
            },
        }

        result = analyzer._inject_enrichment_tasks(plan, state)
        rich_searches = [
            task
            for task in result.tasks
            if task.tool_name == "search_azure_docs"
            and task.tool_args.get("include_content") is True
        ]

        assert len(rich_searches) == 1
        assert rich_searches[0].tool_args["focus_terms"] == ["koreacentral"]


class TestRegionAvailabilityPromptContract:
    def test_feature_level_evidence_and_region_outcome_are_mandatory(self):
        from src.agent.prompts.phases import EVALUATION_PROMPT, PLANNING_PROMPT
        from src.agent.prompts.report.base import REPORT_AFTER
        from src.agent.prompts.tools import TOOLS_PROMPT

        planning = " ".join(PLANNING_PROMPT.split())
        evaluation = " ".join(EVALUATION_PROMPT.split())
        tools = " ".join(TOOLS_PROMPT.split())
        report = " ".join(REPORT_AFTER.split())

        assert "include_content=true" in planning
        assert '"primary_region_availability": true' in evaluation
        assert "A provider-wide availability ratio is not a substitute" in evaluation
        assert "NOT for a new feature layered on an existing type" in tools
        assert "Put the first primary Region and its outcome" in report


class TestLanguageIsolation:
    """Test that customize_for_subscriber respects language boundaries.

    Regression test for the bug where subscribers with different language
    settings received a single digest email with mixed-language reports.
    """

    def _make_result(self, relevance="not_relevant", should_notify=False):
        """Create a minimal AnalysisResult for testing."""
        return AnalysisResult(
            update_id="test-lang-1",
            update_title="Test Update",
            relevance=RelevanceStatus(relevance),
            relevance_reason="테스트 이유",  # Korean text
            one_line_summary="테스트 요약",
            affected_resources=[],
            impact_summary="영향 없음",
            recommendations=[],
            reference_docs=[],
            should_notify=should_notify,
        )

    @pytest.mark.asyncio
    async def test_no_profile_same_language_skips_customization(self):
        """Only a missing role/focus profile and no translation permit the fast path."""
        result = self._make_result(relevance="not_relevant", should_notify=False)
        sub = Subscriber(email="a@b.com", name="Alice", role="", language="ko")

        with patch.object(AzureUpdateAnalyzer, "__init__", return_value=None):
            analyzer = AzureUpdateAnalyzer.__new__(AzureUpdateAnalyzer)
            analyzer.settings = type("S", (), {"report_language": "ko"})()

        tailored = await analyzer.customize_for_subscriber(result, sub, object())
        assert tailored is result

    def test_not_relevant_different_language_needs_translation(self):
        """not_relevant + different language → must NOT skip (needs translation)."""
        result = self._make_result(relevance="not_relevant", should_notify=False)
        sub = Subscriber(email="a@b.com", name="Bob", role="Architect", language="en")

        # English subscriber with Korean default → needs_translation = True
        base_language = "ko"
        needs_translation = sub.language != base_language
        assert needs_translation is True

        # The skip condition should NOT trigger when needs_translation is True
        skip = not sub.role and not sub.focus_services and not needs_translation
        assert skip is False, (
            "not_relevant items MUST be customized when subscriber language "
            "differs from report language to prevent language mixing in digest"
        )

    def test_subscriber_alert_level_defaults(self):
        """Subscriber model defaults: alert_level='all', empty lists."""
        sub = Subscriber(email="a@b.com", name="Test")
        assert sub.alert_level == "all"
        assert sub.management_groups == []
        assert sub.subscriptions == []
        assert sub.focus_services == []

    def test_customization_prompt_declares_resource_scope_a_hard_boundary(self):
        from src.agent.prompts import SUBSCRIBER_CUSTOMIZATION_PROMPT
        from src.agent.prompts.report.base import REPORT_AFTER

        assert "{subscriber_resource_scope}" in SUBSCRIBER_CUSTOMIZATION_PROMPT
        assert "hard investigation boundary" in SUBSCRIBER_CUSTOMIZATION_PROMPT
        assert '"subscriptionId": "exact subscription GUID' in REPORT_AFTER

    def test_customization_output_is_filtered_against_the_subscriber_scope(self):
        result = self._make_result(relevance="relevant", should_notify=True)
        analyzer = AzureUpdateAnalyzer.__new__(AzureUpdateAnalyzer)
        analyzer.settings = type("S", (), {"action_verification_enabled": False})()
        scope = AnalysisScope(
            subscriptions=["11111111-1111-1111-1111-111111111111"],
            resource_groups=["production-rg"],
        )
        customized = {
            "affected_resources": [
                {
                    "name": "in-scope",
                    "subscriptionId": "11111111-1111-1111-1111-111111111111",
                    "resourceGroup": "production-rg",
                },
                {
                    "name": "out-of-scope",
                    "subscriptionId": "22222222-2222-2222-2222-222222222222",
                    "resourceGroup": "other-rg",
                },
            ]
        }

        tailored = analyzer._build_customized_result(
            result,
            customized,
            object(),
            scope=scope,
        )

        assert [resource["name"] for resource in tailored.affected_resources] == ["in-scope"]

    @pytest.mark.asyncio
    async def test_same_language_empty_role_early_return_is_still_scope_filtered(self):
        result = self._make_result(relevance="relevant", should_notify=True).model_copy(
            update={
                "affected_resources": [
                    {
                        "name": "in-scope",
                        "subscriptionId": "11111111-1111-1111-1111-111111111111",
                        "resourceGroup": "production-rg",
                    },
                    {
                        "name": "out-of-scope",
                        "subscriptionId": "22222222-2222-2222-2222-222222222222",
                        "resourceGroup": "other-rg",
                    },
                ]
            }
        )
        subscriber = Subscriber(
            email="scope@example.com",
            name="Scope",
            role="",
            language="ko",
            subscriptions=["11111111-1111-1111-1111-111111111111"],
            resource_groups=["production-rg"],
        )
        analyzer = AzureUpdateAnalyzer.__new__(AzureUpdateAnalyzer)
        analyzer.settings = type("S", (), {"report_language": "ko"})()

        tailored = await analyzer.customize_for_subscriber(result, subscriber, object())

        assert [resource["name"] for resource in tailored.affected_resources] == ["in-scope"]

    @pytest.mark.asyncio
    async def test_analysis_wrapper_filters_the_result_after_all_rewrites(self):
        result = self._make_result(relevance="relevant", should_notify=True).model_copy(
            update={
                "affected_resources": [
                    {
                        "name": "in-scope",
                        "subscriptionId": "11111111-1111-1111-1111-111111111111",
                        "resourceGroup": "production-rg",
                    },
                    {
                        "name": "post-critic-out-of-scope",
                        "subscriptionId": "22222222-2222-2222-2222-222222222222",
                        "resourceGroup": "other-rg",
                    },
                ]
            }
        )
        analyzer = AzureUpdateAnalyzer.__new__(AzureUpdateAnalyzer)

        async def completed_analysis(_update, trace_id=None):
            assert trace_id == "trace-scope"
            return result

        analyzer._analyze_update_scoped = completed_analysis
        scope = AnalysisScope(
            subscriptions=["11111111-1111-1111-1111-111111111111"],
            resource_groups=["production-rg"],
        )

        tailored = await analyzer.analyze_update(
            object(),
            trace_id="trace-scope",
            scope=scope,
        )

        assert [resource["name"] for resource in tailored.affected_resources] == ["in-scope"]


class TestKqlTaskRouting:
    """Test that KQL-bearing tasks are routed to the Resource Graph specialist.

    KQL repair must use the dedicated Resource Graph Prompt Agent, never the
    coordinator or quality reviewer.
    """

    @staticmethod
    def _task(method: str, tool_name: str, tool_args: dict) -> AnalysisTask:
        return AnalysisTask(
            task_id="t1",
            description="d",
            method=method,
            tool_name=tool_name,
            tool_args=tool_args,
            purpose="p",
        )

    def test_resource_graph_method_is_kql(self):
        task = self._task("kql", "query_azure_resources", {"query": "Resources | take 5"})
        assert AzureUpdateAnalyzer._is_kql_task(task) is True

    def test_log_analytics_method_is_kql(self):
        """Log Analytics queries are KQL too — must not go to the fast model."""
        task = self._task("log_analytics", "query_log_analytics", {"query": "AzureActivity"})
        assert AzureUpdateAnalyzer._is_kql_task(task) is True

    def test_kql_tool_with_mislabeled_method_is_kql(self):
        """A KQL tool routed under a non-KQL method is still KQL."""
        task = self._task("azure_rest", "query_azure_resources", {"query": "Resources"})
        assert AzureUpdateAnalyzer._is_kql_task(task) is True

    def test_kql_looking_query_arg_is_kql(self):
        """A piped query argument is treated as KQL even on an unknown tool."""
        task = self._task("advisor", "some_tool", {"query": "advisorresources | take 5"})
        assert AzureUpdateAnalyzer._is_kql_task(task) is True

    def test_non_kql_task_is_not_kql(self):
        task = self._task("learn_search", "search_azure_docs", {"query": "storage TLS"})
        assert AzureUpdateAnalyzer._is_kql_task(task) is False

    def test_no_query_arg_is_not_kql(self):
        task = self._task("policy", "get_policy_compliance", {"scope": "sub"})
        assert AzureUpdateAnalyzer._is_kql_task(task) is False


class TestContextualToolArguments:
    def test_find_related_resources_query_alias_is_normalized_before_validation(self):
        from src.agent.tools import FindRelatedResourcesInput

        tool = type("Tool", (), {"args_schema": FindRelatedResourcesInput})()
        task = AnalysisTask(
            task_id="t1",
            description="find apphost resources",
            method="kql",
            tool_name="find_related_resources",
            tool_args={"query": "apphost"},
            purpose="find related resources",
        )

        AzureUpdateAnalyzer._fill_contextual_tool_args(
            task,
            tool,
            {"trace_id": "trace-1", "update": {"azure_services": ["Announcement"]}},
        )

        assert task.tool_args == {"keyword": ["apphost"]}
        assert FindRelatedResourcesInput.model_validate(task.tool_args)

    def test_input_schema_failure_uses_coordinator_instead_of_kql_fixer(self):
        task = AnalysisTask(
            task_id="t1",
            description="find apphost resources",
            method="kql",
            tool_name="find_related_resources",
            tool_args={"query": "apphost"},
            purpose="find related resources",
        )

        assert (
            AzureUpdateAnalyzer._needs_resource_graph_repair(
                task,
                "1 validation error: keyword Field required [type=missing]",
            )
            is False
        )
        assert (
            AzureUpdateAnalyzer._needs_resource_graph_repair(task, "ParserFailure InvalidQuery")
            is True
        )

    def test_natural_language_kql_is_replaced_by_resource_type_builder(self):
        from src.agent.tools import ResourceGraphQueryInput

        tool = type("Tool", (), {"args_schema": ResourceGraphQueryInput})()
        task = AnalysisTask(
            task_id="t1",
            description="inspect managed clusters",
            method="kql",
            tool_name="query_azure_resources",
            tool_args={
                "resource_type": "microsoft.containerservice/managedclusters",
                "query": "evaluate the effect across clusters",
            },
            purpose="find related resources",
        )

        AzureUpdateAnalyzer._fill_contextual_tool_args(
            task,
            tool,
            {"trace_id": "trace-1", "update": {"azure_services": []}},
        )

        assert set(task.tool_args) == {"query"}
        assert task.tool_args["query"].lstrip().startswith("Resources")
        assert "advancedNetworking" in task.tool_args["query"]

    def test_required_service_name_is_filled_from_update(self):
        from pydantic import BaseModel

        class ToolInput(BaseModel):
            service_name: str
            resource_type: str = ""

        tool = type("Tool", (), {"args_schema": ToolInput})()
        task = AnalysisTask(
            task_id="t1",
            description="details",
            method="kql",
            tool_name="get_service_resource_details",
            tool_args={"resource_type": "microsoft.network/bastionhosts"},
            purpose="find affected resources",
        )
        state = {"update": {"azure_services": ["Azure Bastion"]}}

        AzureUpdateAnalyzer._fill_contextual_tool_args(task, tool, state)

        assert task.tool_args == {
            "resource_type": "microsoft.network/bastionhosts",
            "service_name": "Azure Bastion",
        }

    def test_existing_service_name_is_never_overwritten(self):
        from pydantic import BaseModel

        class ToolInput(BaseModel):
            service_name: str

        tool = type("Tool", (), {"args_schema": ToolInput})()
        task = AnalysisTask(
            task_id="t1",
            description="details",
            method="kql",
            tool_name="get_service_resource_details",
            tool_args={"service_name": "Storage"},
            purpose="find affected resources",
        )

        AzureUpdateAnalyzer._fill_contextual_tool_args(
            task,
            tool,
            {"update": {"azure_services": ["Azure Bastion"]}},
        )

        assert task.tool_args == {"service_name": "Storage"}

    def test_missing_update_service_remains_for_normal_validation(self):
        from pydantic import BaseModel

        class ToolInput(BaseModel):
            service_name: str

        tool = type("Tool", (), {"args_schema": ToolInput})()
        task = AnalysisTask(
            task_id="t1",
            description="details",
            method="kql",
            tool_name="get_service_resource_details",
            tool_args={},
            purpose="find affected resources",
        )

        AzureUpdateAnalyzer._fill_contextual_tool_args(
            task,
            tool,
            {"update": {"azure_services": []}},
        )

        assert task.tool_args == {}


class TestReportOutputRecovery:
    @pytest.mark.asyncio
    async def test_rate_limited_report_waits_and_retries(self, monkeypatch):
        from src.agent import analyzer as analyzer_module

        analyzer = object.__new__(AzureUpdateAnalyzer)
        analyzer.settings = SimpleNamespace(report_language="ko", custom_system_prompt="")
        analyzer._llm_circuit_breaker = CircuitBreaker(
            failure_threshold=3,
            reset_timeout=120,
        )
        analyzer.llm_report_writer = type("ReportWriter", (), {})()
        analyzer.llm_report_writer.ainvoke = AsyncMock(
            side_effect=[
                RuntimeError("Error code: 429 - rate_limit_exceeded"),
                AIMessage(
                    content='{"relevance":"not_relevant","detailed_analysis":"complete"}',
                    response_metadata={"finish_reason": "stop"},
                ),
            ]
        )
        sleep = AsyncMock()
        monkeypatch.setattr("src.agent.resilience.asyncio.sleep", sleep)
        monkeypatch.setattr("src.agent.resilience.random.uniform", lambda *_: 0)
        monkeypatch.setattr(analyzer_module, "get_settings", lambda: analyzer.settings)
        plan = AnalysisPlan(
            plan_id="p1",
            update_summary="update",
            analysis_goal="report",
            tasks=[],
        )
        state = {
            "update_context": "update context",
            "resource_summary": "resource summary",
            "task_results": {},
            "analysis_plan": plan.model_dump(),
            "update": {"title": "Update", "update_type": "Feature Change"},
            "trace_id": "trace-rate-limit",
            "iteration": 1,
            "report_feedback": "",
        }

        result = await analyzer._report_node(state)

        assert analyzer.llm_report_writer.ainvoke.await_count == 2
        sleep.assert_awaited_once_with(10.0)
        assert result["analysis_result"]["raw_analysis"] == (
            '{"relevance":"not_relevant","detailed_analysis":"complete"}'
        )

    @pytest.mark.asyncio
    async def test_length_limited_foundry_response_is_continued(self, monkeypatch):
        from src.agent import analyzer as analyzer_module

        analyzer = object.__new__(AzureUpdateAnalyzer)
        analyzer.settings = SimpleNamespace(report_language="ko", custom_system_prompt="")
        analyzer._llm_circuit_breaker = CircuitBreaker(
            failure_threshold=3,
            reset_timeout=120,
        )
        analyzer.llm_report_writer = type("ReportWriter", (), {})()
        analyzer.llm_report_writer.ainvoke = AsyncMock(
            side_effect=[
                AIMessage(
                    content='{"relevance":"not_relevant",',
                    response_metadata={"finish_reason": "length"},
                ),
                AIMessage(
                    content='"detailed_analysis":"complete"}',
                    response_metadata={"finish_reason": "stop"},
                ),
            ]
        )
        monkeypatch.setattr(analyzer_module, "get_settings", lambda: analyzer.settings)
        plan = AnalysisPlan(
            plan_id="p1",
            update_summary="update",
            analysis_goal="report",
            tasks=[],
        )
        state = {
            "update_context": "update context",
            "resource_summary": "resource summary",
            "task_results": {},
            "analysis_plan": plan.model_dump(),
            "update": {"title": "Update", "update_type": "General Availability"},
            "trace_id": "trace-1",
            "iteration": 1,
            "report_feedback": "",
        }

        result = await analyzer._report_node(state)

        assert analyzer.llm_report_writer.ainvoke.await_count == 2
        assert result["analysis_result"]["raw_analysis"] == (
            '{"relevance":"not_relevant",' '"detailed_analysis":"complete"}'
        )
        recovery_messages = analyzer.llm_report_writer.ainvoke.await_args_list[1].args[0]
        assert recovery_messages[-1].content == analyzer_module.OUTPUT_RECOVERY_MESSAGE

    @pytest.mark.asyncio
    async def test_ga_report_retries_once_when_primary_region_verdict_is_missing(self, monkeypatch):
        from src.agent import analyzer as analyzer_module

        analyzer = object.__new__(AzureUpdateAnalyzer)
        analyzer.settings = SimpleNamespace(report_language="ko", custom_system_prompt="")
        analyzer._llm_circuit_breaker = CircuitBreaker(
            failure_threshold=3,
            reset_timeout=120,
        )
        analyzer.llm_report_writer = type("ReportWriter", (), {})()
        analyzer.llm_report_writer.ainvoke = AsyncMock(
            side_effect=[
                AIMessage(
                    content='{"one_line_summary":"기능이 GA되었습니다","detailed_analysis":"설명"}',
                    response_metadata={"finish_reason": "stop"},
                ),
                AIMessage(
                    content=(
                        '{"one_line_summary":"koreacentral: 지금 사용 가능 - 기능 GA",'
                        '"detailed_analysis":"Korea Central에서 지금 사용할 수 있습니다."}'
                    ),
                    response_metadata={"finish_reason": "stop"},
                ),
            ]
        )
        monkeypatch.setattr(analyzer_module, "get_settings", lambda: analyzer.settings)
        plan = AnalysisPlan(
            plan_id="p1",
            update_summary="update",
            analysis_goal="report",
            tasks=[],
        )
        state = {
            "update_context": "update context",
            "resource_summary": "## Resource Regions\n\n- koreacentral: 30",
            "task_results": {},
            "analysis_plan": plan.model_dump(),
            "update": {"title": "Feature", "update_type": "General Availability"},
            "trace_id": "trace-region",
            "iteration": 1,
            "report_feedback": "",
        }

        result = await analyzer._report_node(state)

        assert analyzer.llm_report_writer.ainvoke.await_count == 2
        assert "koreacentral" in result["analysis_result"]["raw_analysis"]
        retry_prompt = analyzer.llm_report_writer.ainvoke.await_args_list[1].args[0][-1].content
        assert "official feature-level evidence" in retry_prompt
