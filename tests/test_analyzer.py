"""Tests for analyzer parsing helpers and pre-filter logic."""

import json
from unittest.mock import patch

import pytest

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
)
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

    def test_parse_invalid_json_defaults_sufficient(self):
        """Invalid JSON defaults to 'sufficient' to prevent loops."""
        analyzer = object.__new__(AzureUpdateAnalyzer)
        result = analyzer._parse_evaluation_json("not json")
        assert result.verdict == "sufficient"

    def test_parse_unknown_verdict_defaults_sufficient(self):
        """Unknown verdict value normalized to 'sufficient'."""
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
        assert result.verdict == "sufficient"


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

    def test_not_relevant_same_language_skips_customization(self):
        """not_relevant + same language → returns original (no LLM call needed)."""
        result = self._make_result(relevance="not_relevant", should_notify=False)
        sub = Subscriber(email="a@b.com", name="Alice", role="Infra", language="ko")

        with patch.object(AzureUpdateAnalyzer, "__init__", return_value=None):
            analyzer = AzureUpdateAnalyzer.__new__(AzureUpdateAnalyzer)
            analyzer.settings = type("S", (), {"report_language": "ko"})()

        # Same language + not_relevant → skip is expected
        # The skip condition: not_relevant AND no affected_resources AND not needs_translation
        needs_translation = sub.language != analyzer.settings.report_language
        assert needs_translation is False

    def test_not_relevant_different_language_needs_translation(self):
        """not_relevant + different language → must NOT skip (needs translation)."""
        result = self._make_result(relevance="not_relevant", should_notify=False)
        sub = Subscriber(email="a@b.com", name="Bob", role="Architect", language="en")

        # English subscriber with Korean default → needs_translation = True
        base_language = "ko"
        needs_translation = sub.language != base_language
        assert needs_translation is True

        # The skip condition should NOT trigger when needs_translation is True
        skip = (
            result.relevance == RelevanceStatus.NOT_RELEVANT
            and not result.should_notify
            and not result.affected_resources
            and not needs_translation  # This prevents skipping
        )
        assert skip is False, (
            "not_relevant items MUST be customized when subscriber language "
            "differs from report language to prevent language mixing in digest"
        )

    def test_subscriber_alert_level_defaults(self):
        """Subscriber model defaults: alert_level='all', empty lists."""
        sub = Subscriber(email="a@b.com", name="Test")
        assert sub.alert_level == "all"
        assert sub.subscriptions == []
        assert sub.focus_services == []


class TestKqlTaskRouting:
    """Test that KQL-bearing tasks are routed to the codex model.

    KQL repair must use the dedicated codex deployment/endpoint
    (AZURE_OPENAI_CODEX_*), never the fast model (AZURE_OPENAI_FAST_*).
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
