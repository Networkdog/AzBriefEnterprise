"""Tests for the rule-based agent trajectory (process-quality) evaluator."""

import itertools

from src.agent.analyzer import AnalysisPlan, AnalysisTask
from src.agent.trajectory import (
    TrajectoryEvaluator,
    TrajectoryMetrics,
    TrajectoryReport,
)

_ids = itertools.count(1)


def _task(
    tool_name: str = "query_azure_resources",
    method: str = "kql",
    status: str = "completed",
    retry_count: int = 0,
    max_retries: int = 3,
) -> AnalysisTask:
    return AnalysisTask(
        task_id=f"t{next(_ids)}",
        description="desc",
        method=method,  # type: ignore[arg-type]
        tool_name=tool_name,
        tool_args={},
        purpose="purpose",
        status=status,  # type: ignore[arg-type]
        retry_count=retry_count,
        max_retries=max_retries,
    )


def _plan(tasks) -> AnalysisPlan:
    return AnalysisPlan(
        plan_id="p1",
        update_summary="s",
        analysis_goal="g",
        tasks=tasks,
    )


def test_all_completed_scores_perfect():
    plan = _plan([_task(status="completed") for _ in range(3)])
    report = TrajectoryEvaluator().evaluate(plan, iterations=1)
    assert report.score == 100.0
    assert report.grade == "A"
    assert report.passed is True
    assert report.metrics.tool_call_accuracy == 1.0
    assert not any(i.severity == "critical" for i in report.issues)


def test_some_failed_penalized_and_flagged():
    plan = _plan(
        [
            _task(status="completed"),
            _task(status="completed"),
            _task(status="failed", retry_count=3),
        ]
    )
    report = TrajectoryEvaluator().evaluate(plan, iterations=1)
    assert report.score < 100.0
    assert report.metrics.failed_tasks == 1
    codes = {i.code for i in report.issues}
    assert "tool_failures" in codes
    assert "retries_exhausted" in codes


def test_all_failed_is_critical_and_capped():
    plan = _plan([_task(status="failed"), _task(status="failed")])
    report = TrajectoryEvaluator().evaluate(plan, iterations=1)
    assert report.passed is False
    assert report.score <= 20.0
    assert any(i.code == "all_tasks_failed" and i.severity == "critical" for i in report.issues)


def test_empty_plan_is_critical_zero():
    plan = _plan([])
    report = TrajectoryEvaluator().evaluate(plan, iterations=0)
    assert report.score == 0.0
    assert report.grade == "F"
    assert report.passed is False
    assert report.issues and report.issues[0].code == "empty_plan"


def test_kql_failure_rate_flagged():
    plan = _plan(
        [
            _task(method="kql", status="failed"),
            _task(method="kql", status="failed"),
            _task(method="kql", status="completed"),
        ]
    )
    report = TrajectoryEvaluator().evaluate(plan, iterations=1)
    assert report.metrics.kql_failure_rate > 0.4
    assert any(i.code == "kql_failure_rate" for i in report.issues)


def test_high_retry_burden_flagged():
    plan = _plan(
        [
            _task(status="completed", retry_count=2),
            _task(status="completed", retry_count=1),
        ]
    )
    report = TrajectoryEvaluator().evaluate(plan, iterations=1)
    assert report.metrics.avg_retries >= 0.75
    assert any(i.code == "high_retry_burden" for i in report.issues)


def test_revision_churn_penalized():
    plan = _plan([_task(status="completed")])
    clean = TrajectoryEvaluator().evaluate(plan, iterations=1)
    churned = TrajectoryEvaluator().evaluate(plan, iterations=3, plan_revisions=1, task_revisions=2)
    assert churned.score < clean.score
    assert any(i.code == "revision_churn" for i in churned.issues)


def test_evaluate_from_state_roundtrip():
    plan = _plan([_task(status="completed"), _task(status="failed")])
    final_state = {
        "analysis_plan": plan.model_dump(),
        "iteration": 2,
        "plan_revision_count": 0,
        "task_revision_count": 1,
    }
    report = TrajectoryEvaluator().evaluate_from_state(final_state)
    assert isinstance(report, TrajectoryReport)
    assert report.metrics.total_tasks == 2
    assert report.metrics.iterations == 2


def test_evaluate_from_state_no_plan_returns_none():
    assert TrajectoryEvaluator().evaluate_from_state({}) is None
    assert TrajectoryEvaluator().evaluate_from_state({"analysis_plan": None}) is None


def test_metrics_and_report_to_dict():
    plan = _plan([_task(status="completed")])
    report = TrajectoryEvaluator().evaluate(plan, iterations=1)
    d = report.to_dict()
    assert set(d) >= {"score", "grade", "passed", "metrics", "issues"}
    assert "tool_call_accuracy" in d["metrics"]


def test_metrics_accuracy_empty_denominator():
    m = TrajectoryMetrics()
    # No executed tasks → accuracy defaults to 1.0 (flagged separately, not 0.0).
    assert m.tool_call_accuracy == 1.0
    assert m.avg_retries == 0.0
    assert m.kql_failure_rate == 0.0
