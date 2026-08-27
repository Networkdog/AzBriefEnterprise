"""Tests for the runtime G-Eval critic pass in src/agent/analyzer.py.

Covers evidence-context construction, the keep-only-if-improved rule, and the
per-state feedback channel that keeps concurrent analyses from leaking rewrite
instructions into each other.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from src.agent.analyzer import AnalysisResult, AzureUpdateAnalyzer, RelevanceStatus
from src.agent.resilience import TOOL_RESULT_BUDGET_CHARS


def _analyzer(**attrs) -> AzureUpdateAnalyzer:
    analyzer = object.__new__(AzureUpdateAnalyzer)
    analyzer._last_resource_summary = ""
    analyzer._last_task_results = {}
    analyzer._last_update_context = ""
    analyzer._last_geval = None
    analyzer.settings = SimpleNamespace(report_language="ko")
    analyzer.llm = object()
    for key, value in attrs.items():
        setattr(analyzer, key, value)
    return analyzer


def _geval_report(score: float, *, passed: bool, flaws=()) -> SimpleNamespace:
    return SimpleNamespace(
        weighted_score=score,
        target_score=4.5,
        passed=passed,
        critical_flaws=list(flaws),
    )


class TestBuildEvidenceContext:
    def test_empty_without_evidence(self):
        assert _analyzer().build_evidence_context() == ""

    def test_includes_resource_summary_and_tool_results(self):
        analyzer = _analyzer(
            _last_resource_summary="26 storage accounts",
            _last_task_results={"task-1": "acct-871 found"},
        )
        evidence = analyzer.build_evidence_context()
        assert "26 storage accounts" in evidence
        assert "task-1" in evidence
        assert "acct-871 found" in evidence

    def test_tool_result_keeps_the_analyzer_budget(self):
        # A smaller cap hides grounded resource names past the cutoff and causes
        # false faithfulness penalties, so the judge gets the analyzer's budget.
        analyzer = _analyzer(_last_task_results={"task-1": "x" * (TOOL_RESULT_BUDGET_CHARS + 500)})
        evidence = analyzer.build_evidence_context()
        assert evidence.count("x") == TOOL_RESULT_BUDGET_CHARS

    def test_result_snapshot_wins_over_shared_last_evidence(self):
        analyzer = _analyzer(
            _last_resource_summary="wrong shared summary",
            _last_task_results={"wrong": "wrong shared result"},
        )
        result = AnalysisResult(
            update_id="u1",
            update_title="Update 1",
            relevance=RelevanceStatus.RELEVANT,
            relevance_reason="reason",
            affected_resources=[],
            impact_summary="impact",
            recommendations=[],
            reference_docs=[],
            should_notify=True,
        )
        analyzer._attach_result_evidence(
            result,
            "correct result summary",
            {"task-1": "correct result evidence"},
            "correct update context",
        )

        evidence = analyzer.build_evidence_context(result)

        assert "correct result summary" in evidence
        assert "correct result evidence" in evidence
        assert "wrong shared" not in evidence
        assert "_evidence_resource_summary" not in result.model_dump()


class TestCriticPass:
    """The critic routes results without inspecting them, so plain sentinels
    stand in for AnalysisResult."""

    @pytest.mark.asyncio
    async def test_passing_report_is_not_rewritten(self, sample_update):
        analyzer = _analyzer()
        original = SimpleNamespace(name="original")
        judge = SimpleNamespace(
            evaluate=AsyncMock(return_value=_geval_report(4.7, passed=True)),
            build_feedback_prompt=lambda report: "should not be called",
        )

        with patch("src.agent.geval.GEvalJudge", return_value=judge):
            out = await analyzer._critic_pass(original, sample_update, {"trace_id": "t"})

        assert out is original
        assert judge.evaluate.await_count == 1

    @pytest.mark.asyncio
    async def test_failing_report_is_rewritten_when_score_improves(self, sample_update):
        analyzer = _analyzer()
        original = SimpleNamespace(name="original")
        revised = SimpleNamespace(name="revised")
        judge = SimpleNamespace(
            evaluate=AsyncMock(
                side_effect=[
                    _geval_report(3.0, passed=False),
                    _geval_report(4.2, passed=False),
                ]
            ),
            build_feedback_prompt=lambda report: "fix the grounding",
        )
        analyzer._report_node = AsyncMock(return_value={"analysis_result": {}})
        analyzer._parse_analysis_result = lambda state, upd: revised

        with patch("src.agent.geval.GEvalJudge", return_value=judge):
            out = await analyzer._critic_pass(original, sample_update, {"trace_id": "t"})

        assert out is revised
        assert analyzer._last_geval.weighted_score == 4.2
        # The rewrite instructions travel through state, never global settings.
        forwarded_state = analyzer._report_node.await_args.args[0]
        assert forwarded_state["report_feedback"] == "fix the grounding"

    @pytest.mark.asyncio
    async def test_rewrite_discarded_when_score_drops(self, sample_update):
        analyzer = _analyzer()
        original = SimpleNamespace(name="original")
        judge = SimpleNamespace(
            evaluate=AsyncMock(
                side_effect=[
                    _geval_report(3.0, passed=False),
                    _geval_report(2.4, passed=False),
                ]
            ),
            build_feedback_prompt=lambda report: "fix the grounding",
        )
        analyzer._report_node = AsyncMock(return_value={"analysis_result": {}})
        analyzer._parse_analysis_result = lambda state, upd: SimpleNamespace(name="worse")

        with patch("src.agent.geval.GEvalJudge", return_value=judge):
            out = await analyzer._critic_pass(original, sample_update, {"trace_id": "t"})

        assert out is original

    @pytest.mark.asyncio
    async def test_critical_flaw_forces_rewrite_even_when_passing(self, sample_update):
        analyzer = _analyzer()
        original = SimpleNamespace(name="original")
        revised = SimpleNamespace(name="revised")
        judge = SimpleNamespace(
            evaluate=AsyncMock(
                side_effect=[
                    _geval_report(4.6, passed=True, flaws=["faithfulness: unverified resource"]),
                    _geval_report(4.8, passed=True),
                ]
            ),
            build_feedback_prompt=lambda report: "cite the query output",
        )
        analyzer._report_node = AsyncMock(return_value={"analysis_result": {}})
        analyzer._parse_analysis_result = lambda state, upd: revised

        with patch("src.agent.geval.GEvalJudge", return_value=judge):
            out = await analyzer._critic_pass(original, sample_update, {"trace_id": "t"})

        assert out is revised

    @pytest.mark.asyncio
    async def test_empty_feedback_skips_rewrite(self, sample_update):
        analyzer = _analyzer()
        original = SimpleNamespace(name="original")
        judge = SimpleNamespace(
            evaluate=AsyncMock(return_value=_geval_report(3.0, passed=False)),
            build_feedback_prompt=lambda report: "",
        )
        analyzer._report_node = AsyncMock()

        with patch("src.agent.geval.GEvalJudge", return_value=judge):
            out = await analyzer._critic_pass(original, sample_update, {"trace_id": "t"})

        assert out is original
        analyzer._report_node.assert_not_awaited()
