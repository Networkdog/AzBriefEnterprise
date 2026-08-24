"""Tests for _parse_analysis_result and notification logic in AzureUpdateAnalyzer.

Covers JSON parsing, regex fallback, Korean field names, and should_notify rules.
"""

import json

import pytest

from src.agent.analyzer import (
    ActionItem,
    AnalysisResult,
    AzureUpdateAnalyzer,
    ImpactSummary,
    RelevanceStatus,
    UrgencyLevel,
)
from src.rss.parser import AzureUpdate


def _make_update(**overrides):
    """Create a minimal AzureUpdate for testing."""
    from datetime import datetime, timezone

    defaults = {
        "id": "test-123",
        "title": "Test Update",
        "description": "Test description",
        "link": "https://azure.microsoft.com/updates?id=123",
        "published_date": datetime(2026, 3, 10, tzinfo=timezone.utc),
        "categories": [],
        "azure_services": [],
        "update_type": None,
        "status": None,
    }
    defaults.update(overrides)
    return AzureUpdate(**defaults)


def _make_state(raw_analysis: str) -> dict:
    """Build a minimal AgentState dict with raw_analysis."""
    return {
        "analysis_result": {"raw_analysis": raw_analysis},
        "update": {"title": "Test Update"},
    }


def _parse(raw_analysis: str, **update_kwargs) -> AnalysisResult:
    """Parse a raw analysis string into AnalysisResult."""
    analyzer = object.__new__(AzureUpdateAnalyzer)
    state = _make_state(raw_analysis)
    update = _make_update(**update_kwargs)
    return analyzer._parse_analysis_result(state, update)


class TestParseAnalysisResultJSON:
    """Test _parse_analysis_result with valid JSON input."""

    def test_full_json_report(self):
        """Complete JSON report is parsed correctly."""
        report = json.dumps(
            {
                "update_category": "retirement",
                "urgency": "critical",
                "relevance": "relevant",
                "one_line_summary": "Classic VMs will be retired",
                "detailed_analysis": "3 Classic VMs need migration.",
                "affected_resources": [
                    {"name": "vm-1", "type": "Microsoft.ClassicCompute/virtualMachines"}
                ],
                "impact_summary": {
                    "cost_impact": "No additional cost",
                    "security_impact": "N/A",
                    "performance_impact": "N/A",
                    "operational_impact": "Migration required",
                },
                "action_items": [
                    {
                        "step": 1,
                        "urgency": "critical",
                        "task": "Migrate Classic VMs to ARM",
                        "deadline": "2026-09-06",
                    }
                ],
                "recommendations": ["Migrate immediately"],
                "reference_docs": [
                    {
                        "title": "Classic VM migration",
                        "url": "https://learn.microsoft.com/azure/virtual-machines/classic-vm-deprecation",
                    }
                ],
                "additional_checks": ["Verify network config after migration"],
            }
        )

        result = _parse(report)
        assert result.update_category == "retirement"
        assert result.urgency == UrgencyLevel.CRITICAL
        assert result.relevance == RelevanceStatus.RELEVANT
        assert result.one_line_summary == "Classic VMs will be retired"
        assert "3 Classic VMs" in result.relevance_reason
        assert len(result.affected_resources) == 1
        assert result.affected_resources[0]["name"] == "vm-1"
        assert len(result.action_items) == 1
        assert result.action_items[0].deadline == "2026-09-06"
        assert result.impact_details is not None
        assert result.impact_details.operational_impact == "Migration required"
        assert len(result.reference_docs) == 1
        assert result.additional_checks == ["Verify network config after migration"]
        assert result.should_notify is True

    def test_json_in_markdown_fences(self):
        """JSON wrapped in markdown code fences is parsed."""
        inner = json.dumps(
            {
                "urgency": "low",
                "relevance": "opportunity",
                "one_line_summary": "New feature available",
                "detailed_analysis": "A new feature is in GA.",
                "affected_resources": [],
                "recommendations": [],
                "reference_docs": [],
            }
        )
        raw = f"Here is the analysis:\n```json\n{inner}\n```"
        result = _parse(raw)
        assert result.relevance == RelevanceStatus.OPPORTUNITY
        assert result.urgency == UrgencyLevel.LOW
        assert result.should_notify is True  # opportunity → notify

    def test_not_relevant_suppresses_notification(self):
        """NOT_RELEVANT suppresses notification regardless of urgency."""
        report = json.dumps(
            {
                "urgency": "critical",
                "relevance": "not_relevant",
                "detailed_analysis": "This service is not used.",
                "affected_resources": [],
                "recommendations": [],
                "reference_docs": [],
            }
        )
        result = _parse(report)
        assert result.relevance == RelevanceStatus.NOT_RELEVANT
        assert result.should_notify is False

    def test_unknown_relevance_notifies(self):
        """UNKNOWN relevance triggers notification (resource query may have failed)."""
        report = json.dumps(
            {
                "urgency": "medium",
                "relevance": "unknown",
                "detailed_analysis": "Could not determine impact.",
                "affected_resources": [],
                "recommendations": [],
                "reference_docs": [],
            }
        )
        result = _parse(report)
        assert result.relevance == RelevanceStatus.UNKNOWN
        assert result.should_notify is True

    def test_korean_field_names(self):
        """Korean field names are recognized."""
        report = json.dumps(
            {
                "긴급도": "high",
                "관련성": "관련",
                "한줄_요약": "중요한 업데이트",
                "상세_분석": "이 업데이트는 중요합니다.",
                "영향받는_리소스": [
                    {"name": "stor-1", "type": "Microsoft.Storage/storageAccounts"}
                ],
                "적용 방안": ["즉시 조치하세요."],
                "참고_문서": [{"title": "가이드", "url": "https://learn.microsoft.com/azure/test"}],
                "추가_확인_필요": ["TLS 버전 확인"],
            }
        )
        result = _parse(report)
        assert result.urgency == UrgencyLevel.HIGH
        assert result.relevance == RelevanceStatus.RELEVANT
        assert result.one_line_summary == "중요한 업데이트"
        assert len(result.affected_resources) == 1
        assert len(result.additional_checks) == 1

    def test_korean_category_mapping(self):
        """Korean category values are mapped to English enum values."""
        for kr, en in [
            ("은퇴", "retirement"),
            ("기능 변경", "feature_change"),
            ("신규 기능", "new_feature"),
            ("리전 확장", "region_expansion"),
        ]:
            report = json.dumps(
                {
                    "update_category": kr,
                    "urgency": "low",
                    "relevance": "not_relevant",
                    "detailed_analysis": "Test",
                    "affected_resources": [],
                    "recommendations": [],
                    "reference_docs": [],
                }
            )
            result = _parse(report)
            assert result.update_category == en, f"Expected {en} for {kr}"


class TestParseAnalysisResultFallback:
    """Test _parse_analysis_result with invalid/partial JSON (regex fallback)."""

    def test_invalid_json_uses_regex_extraction(self):
        """Invalid JSON falls back to regex extraction."""
        raw = """This is not valid JSON but contains:
        "urgency": "high"
        "relevance": "relevant"
        "one_line_summary": "Important update"
        "detailed_analysis": "Some analysis content"
        """
        result = _parse(raw)
        # Should still extract urgency via regex
        assert result.urgency == UrgencyLevel.HIGH

    def test_empty_raw_analysis(self):
        """Empty raw analysis produces safe defaults."""
        result = _parse("")
        # Empty input falls to UNKNOWN (resource query may have failed)
        assert result.relevance == RelevanceStatus.UNKNOWN
        assert result.should_notify is True  # UNKNOWN triggers notification for safety

    def test_truncated_json_still_parses(self):
        """Truncated JSON (incomplete) is handled gracefully."""
        raw = '{"urgency": "critical", "relevance": "relevant", "detailed_analysis": "Truncated'
        result = _parse(raw)
        # Should attempt to close braces and parse
        assert result.update_title == "Test Update"

    def test_learn_urls_extracted_from_raw(self):
        """Microsoft Learn URLs are extracted when no reference_docs field exists."""
        raw = """Some analysis text.
        See https://learn.microsoft.com/azure/storage/blobs/overview for details.
        Also https://learn.microsoft.com/azure/virtual-machines/sizes for more.
        """
        result = _parse(raw)
        urls = [r["url"] for r in result.reference_docs]
        assert any("storage/blobs" in u for u in urls)


class TestParseAnalysisResultActionItems:
    """Test action item parsing edge cases."""

    def test_action_items_with_all_fields(self):
        """Action items with all fields are parsed correctly."""
        report = json.dumps(
            {
                "urgency": "high",
                "relevance": "relevant",
                "detailed_analysis": "Need action.",
                "affected_resources": [],
                "action_items": [
                    {
                        "step": 1,
                        "urgency": "high",
                        "task": "Upgrade TLS",
                        "why": "Security requirement",
                        "target_resources": ["stor-1", "stor-2"],
                        "procedure": "az storage account update --min-tls-version TLS1_2",
                        "cli_command": "az storage account update ...",
                        "estimated_time": "30 minutes",
                        "deadline": "2026-04-01",
                        "risk_if_not_done": "Service disruption",
                        "precaution": "Test in staging first",
                        "rollback": "az storage account update --min-tls-version TLS1_0",
                    }
                ],
                "recommendations": [],
                "reference_docs": [],
            }
        )
        result = _parse(report)
        assert len(result.action_items) == 1
        ai = result.action_items[0]
        assert ai.step == 1
        assert ai.task == "Upgrade TLS"
        assert ai.target_resources == ["stor-1", "stor-2"]
        assert ai.rollback != ""

    def test_action_items_korean_fields(self):
        """Korean action item field names are recognized."""
        report = json.dumps(
            {
                "urgency": "medium",
                "relevance": "opportunity",
                "detailed_analysis": "Test.",
                "affected_resources": [],
                "액션_아이템": [
                    {
                        "우선순위": 2,
                        "긴급도": "medium",
                        "작업": "기능 테스트",
                        "대상_리소스": ["app-1"],
                        "절차": "Azure Portal에서 확인",
                        "기한": "2026-05-01",
                        "미조치_위험": "기회 손실",
                    }
                ],
                "recommendations": [],
                "reference_docs": [],
            }
        )
        result = _parse(report)
        assert len(result.action_items) == 1
        ai = result.action_items[0]
        assert ai.task == "기능 테스트"
        assert ai.deadline == "2026-05-01"


class TestShouldNotifyLogic:
    """Test should_notify computation separately."""

    @pytest.mark.parametrize(
        "urgency,relevance,expected",
        [
            ("critical", "relevant", True),
            ("critical", "not_relevant", False),
            ("high", "relevant", True),
            ("high", "not_relevant", False),
            ("medium", "relevant", True),
            ("medium", "not_relevant", False),
            ("medium", "opportunity", True),
            ("medium", "unknown", True),
            ("low", "relevant", True),
            ("low", "opportunity", True),
            ("low", "not_relevant", False),
            ("low", "unknown", True),
        ],
    )
    def test_notification_matrix(self, urgency, relevance, expected):
        """Notification follows the urgency x relevance matrix."""
        report = json.dumps(
            {
                "urgency": urgency,
                "relevance": relevance,
                "detailed_analysis": "Test.",
                "affected_resources": [],
                "recommendations": [],
                "reference_docs": [],
            }
        )
        result = _parse(report)
        assert result.should_notify is expected, (
            f"urgency={urgency}, relevance={relevance}: "
            f"expected should_notify={expected}, got {result.should_notify}"
        )


class TestParseNewTasksJson:
    """Test _parse_new_tasks_json helper."""

    def test_parse_valid_tasks_array(self):
        """Valid JSON array of tasks is parsed."""
        raw = json.dumps(
            [
                {
                    "task_id": "task_r1",
                    "description": "Check cost",
                    "method": "cost_api",
                    "tool_name": "get_cost_by_service",
                    "tool_args": {"service_name": "storage"},
                    "purpose": "Assess cost impact",
                }
            ]
        )
        analyzer = object.__new__(AzureUpdateAnalyzer)
        tasks = analyzer._parse_new_tasks_json(raw)
        assert len(tasks) == 1
        assert tasks[0].method == "cost_api"
        assert tasks[0].task_id == "task_r1"

    def test_parse_tasks_with_markdown_fences(self):
        """Tasks in markdown code fences are parsed."""
        inner = json.dumps(
            [
                {
                    "task_id": "t1",
                    "description": "Search docs",
                    "method": "learn_search",
                    "tool_name": "search_azure_docs",
                    "tool_args": {"query": "test"},
                    "purpose": "Find docs",
                }
            ]
        )
        raw = f"```json\n{inner}\n```"
        analyzer = object.__new__(AzureUpdateAnalyzer)
        tasks = analyzer._parse_new_tasks_json(raw)
        assert len(tasks) == 1

    def test_parse_invalid_json_returns_empty(self):
        """Invalid JSON returns empty list (no crash)."""
        analyzer = object.__new__(AzureUpdateAnalyzer)
        tasks = analyzer._parse_new_tasks_json("not json at all")
        assert tasks == []

    def test_invalid_method_normalized(self):
        """Invalid method values are normalized to 'kql'."""
        raw = json.dumps(
            [
                {
                    "task_id": "t1",
                    "description": "Test",
                    "method": "bogus_method",
                    "tool_name": "query_azure_resources",
                    "tool_args": {},
                    "purpose": "Test",
                }
            ]
        )
        analyzer = object.__new__(AzureUpdateAnalyzer)
        tasks = analyzer._parse_new_tasks_json(raw)
        assert tasks[0].method == "kql"


class TestBuildTaskResultsSummary:
    """Test _build_task_results_summary helper."""

    def test_produces_formatted_summary(self):
        """Task results are formatted with status icons."""
        from src.agent.analyzer import AnalysisPlan, AnalysisTask

        plan = AnalysisPlan(
            plan_id="p1",
            update_summary="Test",
            analysis_goal="Test",
            tasks=[
                AnalysisTask(
                    task_id="t1",
                    description="Query resources",
                    method="kql",
                    tool_name="query_azure_resources",
                    tool_args={"query": "Resources | take 5"},
                    purpose="Get resources",
                    status="completed",
                ),
                AnalysisTask(
                    task_id="t2",
                    description="Search docs",
                    method="learn_search",
                    tool_name="search_azure_docs",
                    tool_args={"query": "test"},
                    purpose="Find docs",
                    status="failed",
                    error="Timeout",
                ),
            ],
        )
        task_results = {"t1": "Found 5 resources"}

        analyzer = object.__new__(AzureUpdateAnalyzer)
        summary = analyzer._build_task_results_summary(plan, task_results)

        assert "✅" in summary
        assert "❌" in summary
        assert "Found 5 resources" in summary
        assert "Timeout" in summary

    def test_empty_plan_produces_fallback_text(self):
        """Empty plan produces fallback message."""
        from src.agent.analyzer import AnalysisPlan

        plan = AnalysisPlan(
            plan_id="p1",
            update_summary="Test",
            analysis_goal="Test",
            tasks=[],
        )
        analyzer = object.__new__(AzureUpdateAnalyzer)
        summary = analyzer._build_task_results_summary(plan, {})
        assert "No task results" in summary
