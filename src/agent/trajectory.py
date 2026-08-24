"""Agent trajectory & tool-call accuracy evaluation for AzBrief.

While :mod:`src.agent.geval` judges the *quality of the report* (the output),
this module judges *how the agent behaved to produce it* (the process). The two
are orthogonal: a report can read well yet be built on a brittle trajectory
(every KQL query failed and was patched), or a clean trajectory can still yield a
shallow report. Measuring both is the agentic-eval discipline recommended for
non-deterministic agents — trajectory / tool-call accuracy / task adherence.

The evaluator is **rule-based and evidence-grounded**: it derives every metric
from the executed :class:`~src.agent.analyzer.AnalysisPlan` (task statuses,
retry counts, methods) plus the loop counters already tracked in ``AgentState``
(iterations, plan/task revisions). It needs no LLM call and no log file, so it is
cheap enough to run on every analysis and deterministic enough to gate in CI.

Dimensions rolled into a single 0-100 process score:

- **Tool-call accuracy** — completed vs. failed tasks, retry burden.
- **Task adherence** — did the plan actually get executed, or abandoned?
- **Trajectory efficiency** — iteration count and plan/task revision churn
  (excess churn signals a weak initial plan / diminishing returns).
- **KQL health** — Resource Graph is the most failure-prone method; its failure
  rate is surfaced explicitly because it is AzBrief's dominant defect class.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional

import structlog

from src.agent.tools import KQL_TOOL_NAMES

if TYPE_CHECKING:  # pragma: no cover - typing only
    from src.agent.analyzer import AnalysisPlan

logger = structlog.get_logger(__name__)


# Severity ordering for sorting issues (most severe first).
_SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2}


@dataclass(frozen=True)
class TrajectoryIssue:
    """A single behavioural finding about the agent's run.

    Attributes:
        severity: One of ``critical`` | ``warning`` | ``info``.
        code: Stable machine identifier (snake_case) for filtering/alerting.
        message: Human-readable explanation (Korean-friendly, but written here in
            English like the rest of the code; user-facing rendering is separate).
    """

    severity: str
    code: str
    message: str


@dataclass
class TrajectoryMetrics:
    """Raw, evidence-grounded counters extracted from one analysis run."""

    total_tasks: int = 0
    completed_tasks: int = 0
    failed_tasks: int = 0
    skipped_tasks: int = 0
    total_retries: int = 0
    exhausted_tasks: int = 0  # tasks that hit max_retries and still failed
    kql_tasks: int = 0
    kql_failed: int = 0
    iterations: int = 0
    plan_revisions: int = 0
    task_revisions: int = 0
    enrichment_injected: int = 0

    @property
    def executed_tasks(self) -> int:
        """Tasks that reached a terminal executed state (completed or failed)."""
        return self.completed_tasks + self.failed_tasks

    @property
    def tool_call_accuracy(self) -> float:
        """Fraction of executed tasks that completed successfully (0.0-1.0).

        Returns 1.0 for an empty plan denominator so a plan with no executed
        tasks is flagged via a separate issue rather than a misleading 0.0.
        """
        if self.executed_tasks == 0:
            return 1.0
        return self.completed_tasks / self.executed_tasks

    @property
    def avg_retries(self) -> float:
        """Average retry count per executed task."""
        if self.executed_tasks == 0:
            return 0.0
        return self.total_retries / self.executed_tasks

    @property
    def kql_failure_rate(self) -> float:
        """Fraction of KQL tasks that failed (0.0-1.0)."""
        if self.kql_tasks == 0:
            return 0.0
        return self.kql_failed / self.kql_tasks

    def to_dict(self) -> dict[str, Any]:
        """Flatten metrics + derived rates for structured logging."""
        return {
            "total_tasks": self.total_tasks,
            "completed_tasks": self.completed_tasks,
            "failed_tasks": self.failed_tasks,
            "skipped_tasks": self.skipped_tasks,
            "total_retries": self.total_retries,
            "exhausted_tasks": self.exhausted_tasks,
            "kql_tasks": self.kql_tasks,
            "kql_failed": self.kql_failed,
            "iterations": self.iterations,
            "plan_revisions": self.plan_revisions,
            "task_revisions": self.task_revisions,
            "enrichment_injected": self.enrichment_injected,
            "tool_call_accuracy": round(self.tool_call_accuracy, 3),
            "avg_retries": round(self.avg_retries, 3),
            "kql_failure_rate": round(self.kql_failure_rate, 3),
        }


@dataclass
class TrajectoryReport:
    """The process-quality verdict for one analysis run.

    Attributes:
        metrics: The raw counters and derived rates.
        issues: Behavioural findings, most severe first.
        score: 0-100 process score (100 = flawless execution trajectory).
        grade: Letter grade derived from ``score`` (A/B/C/D/F).
    """

    metrics: TrajectoryMetrics
    issues: list[TrajectoryIssue] = field(default_factory=list)
    score: float = 100.0
    grade: str = "A"

    @property
    def passed(self) -> bool:
        """A trajectory passes when no critical issue is present."""
        return not any(i.severity == "critical" for i in self.issues)

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": round(self.score, 1),
            "grade": self.grade,
            "passed": self.passed,
            "metrics": self.metrics.to_dict(),
            "issues": [
                {"severity": i.severity, "code": i.code, "message": i.message} for i in self.issues
            ],
        }


# ---------------------------------------------------------------------------
# Scoring thresholds (tunable, deterministic)
# ---------------------------------------------------------------------------

# Penalty weights (points off a 100 base).
_PENALTY_PER_FAILED_TASK = 12.0
_MAX_FAILED_TASK_PENALTY = 48.0
_PENALTY_HIGH_RETRIES = 12.0  # avg_retries above threshold
_PENALTY_PER_EXTRA_ITERATION = 6.0  # iterations beyond the first
_PENALTY_PER_PLAN_REVISION = 8.0
_PENALTY_PER_TASK_REVISION = 4.0
_PENALTY_HIGH_KQL_FAILURE = 10.0

# Behavioural thresholds.
_AVG_RETRY_WARN = 0.75  # avg retries per task above this is churny
_KQL_FAILURE_WARN = 0.4  # >40% KQL tasks failing is a systemic query problem
_ITERATION_WARN = 3  # more than this many loop iterations is inefficient


class TrajectoryEvaluator:
    """Compute a :class:`TrajectoryReport` from an executed plan + loop counters.

    Stateless and cheap; safe to instantiate per call or reuse. All inputs come
    from data the analyzer already produces, so evaluation cannot fail the run —
    on any unexpected shape it degrades to a permissive report.
    """

    def evaluate(
        self,
        plan: "AnalysisPlan",
        *,
        iterations: int = 0,
        plan_revisions: int = 0,
        task_revisions: int = 0,
        enrichment_injected: int = 0,
    ) -> TrajectoryReport:
        """Evaluate one analysis trajectory.

        Args:
            plan: The final executed :class:`AnalysisPlan` (task statuses filled in).
            iterations: Number of execute passes the loop ran (``AgentState.iteration``).
            plan_revisions: ``AgentState.plan_revision_count``.
            task_revisions: ``AgentState.task_revision_count``.
            enrichment_injected: How many enrichment tasks were auto-added.

        Returns:
            A populated :class:`TrajectoryReport`.
        """
        metrics = self._collect_metrics(
            plan,
            iterations=iterations,
            plan_revisions=plan_revisions,
            task_revisions=task_revisions,
            enrichment_injected=enrichment_injected,
        )
        issues = self._detect_issues(metrics)
        score = self._score(metrics, issues)
        report = TrajectoryReport(
            metrics=metrics,
            issues=issues,
            score=score,
            grade=self._grade(score),
        )
        logger.info("trajectory_evaluated", **report.to_dict())
        return report

    def evaluate_from_state(self, final_state: dict[str, Any]) -> Optional[TrajectoryReport]:
        """Convenience wrapper: evaluate directly from a LangGraph final state.

        Reconstructs the plan from ``final_state['analysis_plan']`` and reads the
        loop counters. Returns ``None`` when no plan is present (e.g. the run
        aborted before planning) so callers can skip cleanly.
        """
        plan_dict = final_state.get("analysis_plan")
        if not plan_dict:
            return None
        try:
            from src.agent.analyzer import AnalysisPlan

            plan = AnalysisPlan(**plan_dict)
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("trajectory_plan_reconstruct_failed", error=str(exc))
            return None

        return self.evaluate(
            plan,
            iterations=int(final_state.get("iteration", 0) or 0),
            plan_revisions=int(final_state.get("plan_revision_count", 0) or 0),
            task_revisions=int(final_state.get("task_revision_count", 0) or 0),
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _collect_metrics(
        self,
        plan: "AnalysisPlan",
        *,
        iterations: int,
        plan_revisions: int,
        task_revisions: int,
        enrichment_injected: int,
    ) -> TrajectoryMetrics:
        tasks = list(getattr(plan, "tasks", []) or [])
        m = TrajectoryMetrics(
            total_tasks=len(tasks),
            iterations=max(0, iterations),
            plan_revisions=max(0, plan_revisions),
            task_revisions=max(0, task_revisions),
            enrichment_injected=max(0, enrichment_injected),
        )
        for t in tasks:
            status = getattr(t, "status", "pending")
            retries = int(getattr(t, "retry_count", 0) or 0)
            method = getattr(t, "method", "")
            tool = getattr(t, "tool_name", "")
            m.total_retries += retries
            if status == "completed":
                m.completed_tasks += 1
            elif status == "failed":
                m.failed_tasks += 1
                if retries >= int(getattr(t, "max_retries", 0) or 0):
                    m.exhausted_tasks += 1
            elif status == "skipped":
                m.skipped_tasks += 1
            # The tool decides, not the LLM-supplied method label: revision tasks are
            # frequently labelled "kql" whatever tool they actually call.
            if tool in KQL_TOOL_NAMES or (not tool and method == "kql"):
                m.kql_tasks += 1
                if status == "failed":
                    m.kql_failed += 1
        return m

    def _detect_issues(self, m: TrajectoryMetrics) -> list[TrajectoryIssue]:
        issues: list[TrajectoryIssue] = []

        if m.total_tasks == 0:
            issues.append(
                TrajectoryIssue(
                    "critical",
                    "empty_plan",
                    "Planner produced no tasks — the agent gathered no evidence.",
                )
            )
            return issues

        if m.executed_tasks > 0 and m.completed_tasks == 0:
            issues.append(
                TrajectoryIssue(
                    "critical",
                    "all_tasks_failed",
                    f"All {m.executed_tasks} executed tasks failed — report is ungrounded.",
                )
            )

        if 0 < m.tool_call_accuracy < 1.0:
            issues.append(
                TrajectoryIssue(
                    "warning" if m.tool_call_accuracy >= 0.5 else "critical",
                    "tool_failures",
                    f"{m.failed_tasks}/{m.executed_tasks} tasks failed "
                    f"(tool-call accuracy {m.tool_call_accuracy:.0%}).",
                )
            )

        if m.exhausted_tasks > 0:
            issues.append(
                TrajectoryIssue(
                    "warning",
                    "retries_exhausted",
                    f"{m.exhausted_tasks} task(s) exhausted all retries and still failed.",
                )
            )

        if m.avg_retries >= _AVG_RETRY_WARN:
            issues.append(
                TrajectoryIssue(
                    "warning",
                    "high_retry_burden",
                    f"Average {m.avg_retries:.1f} retries per task — tool args were often wrong.",
                )
            )

        if m.kql_tasks > 0 and m.kql_failure_rate >= _KQL_FAILURE_WARN:
            issues.append(
                TrajectoryIssue(
                    "warning",
                    "kql_failure_rate",
                    f"{m.kql_failed}/{m.kql_tasks} KQL queries failed "
                    f"({m.kql_failure_rate:.0%}) — systemic query-generation problem.",
                )
            )

        if m.iterations > _ITERATION_WARN:
            issues.append(
                TrajectoryIssue(
                    "info",
                    "many_iterations",
                    f"{m.iterations} execute passes — plan may have been weak initially.",
                )
            )

        if m.plan_revisions > 0 or m.task_revisions > 1:
            issues.append(
                TrajectoryIssue(
                    "info",
                    "revision_churn",
                    f"{m.plan_revisions} plan / {m.task_revisions} task revisions.",
                )
            )

        issues.sort(key=lambda i: _SEVERITY_ORDER.get(i.severity, 9))
        return issues

    def _score(self, m: TrajectoryMetrics, issues: list[TrajectoryIssue]) -> float:
        if m.total_tasks == 0:
            return 0.0

        score = 100.0
        score -= min(_PENALTY_PER_FAILED_TASK * m.failed_tasks, _MAX_FAILED_TASK_PENALTY)
        if m.avg_retries >= _AVG_RETRY_WARN:
            score -= _PENALTY_HIGH_RETRIES
        if m.iterations > 1:
            score -= _PENALTY_PER_EXTRA_ITERATION * (m.iterations - 1)
        score -= _PENALTY_PER_PLAN_REVISION * m.plan_revisions
        score -= _PENALTY_PER_TASK_REVISION * m.task_revisions
        if m.kql_tasks > 0 and m.kql_failure_rate >= _KQL_FAILURE_WARN:
            score -= _PENALTY_HIGH_KQL_FAILURE

        # An all-failed trajectory can never score above the floor.
        if m.executed_tasks > 0 and m.completed_tasks == 0:
            score = min(score, 20.0)

        return max(0.0, min(100.0, score))

    @staticmethod
    def _grade(score: float) -> str:
        if score >= 90:
            return "A"
        if score >= 80:
            return "B"
        if score >= 70:
            return "C"
        if score >= 60:
            return "D"
        return "F"
