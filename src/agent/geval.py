"""G-Eval LLM-as-a-Judge quality evaluation for AzBrief analysis reports.

This module implements the multi-dimensional G-Eval methodology for autonomously
scoring and improving Azure infrastructure analysis reports:

- **Five orthogonal quality dimensions**, each with a 1-5 anchored rubric where
  5 is defined as an *unreachable theoretical ideal* and 4 is the best
  production-acceptable grade. This ceiling prevents score saturation and keeps
  the self-correction loop pushing for improvement.
- **Chain-of-Thought (form-filling)**: the judge writes explicit evaluation
  reasoning *before* emitting a score, preventing shallow pattern matching.
- **Score normalization via token log-probabilities**: when the judge model
  exposes ``logprobs``, the integer score is refined into a continuous value
  (e.g. 3.82) by weighting the probability mass over the score tokens {1..5}.
  This gives the self-improvement loop the resolution to detect a 1% quality
  gain between iterations.
- **Dimension-independent parallel evaluation**: each dimension is judged in its
  own LLM call (``asyncio.gather``) so a strong impression in one dimension does
  not bleed into another (halo effect / anchor bleed).
- **Edge-case handling & verbosity-bias defense** baked into every rubric.

The judge is decoupled from the analyzer: it consumes an ``AnalysisResult`` plus
the original ``AzureUpdate`` and returns a structured ``GEvalReport`` with
per-dimension feedback that the rewrite loop injects back into report generation.
"""

from __future__ import annotations

import asyncio
import math
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional

import structlog

from src.agent.resilience import CircuitBreaker, parse_json_resilient, retry_with_backoff
from src.config import get_settings
from src.email.templates import CAPABILITY_CATEGORIES

if TYPE_CHECKING:  # pragma: no cover - typing only
    from src.agent.analyzer import AnalysisResult
    from src.config import Settings, Subscriber
    from src.rss.parser import AzureUpdate

logger = structlog.get_logger(__name__)

# Score tokens the judge is constrained to emit for a dimension.
_SCORE_TOKENS = {"1", "2", "3", "4", "5"}


# ============================================================================
# Dimension definitions (1-5 anchored rubrics)
# ============================================================================


@dataclass(frozen=True)
class GEvalDimension:
    """A single orthogonal quality dimension with its scoring rubric.

    Attributes:
        key: Machine identifier (snake_case).
        title: Human-readable dimension name.
        weight: Relative weight in the aggregate score (default 1.0 = equal).
        rubric: The 1-5 anchored scoring criteria the judge applies verbatim.
        steps: Ordered Chain-of-Thought evaluation steps (form-filling).
        edge_cases: Explicit exemptions that must not be penalized.
    """

    key: str
    title: str
    weight: float
    rubric: str
    steps: str
    edge_cases: str


# D1 — Actionability & Practical Helpfulness (실질적 유용성 및 실행 가능성)
_ACTIONABILITY = GEvalDimension(
    key="actionability",
    title="Actionability & Practical Helpfulness",
    weight=1.2,
    rubric=(
        "5 (Ideal): Predicts second-order dependency conflicts, ships preemptive "
        "rollback automation, per-resource phased migration timeline, and pre/post "
        "validation test methodology. A flawless master plan for the reader's actions.\n"
        "4 (Excellent): Lists the exact affected resources (from Resource Graph) and "
        "the precise Azure CLI/PowerShell commands, policy changes, and clear deadlines "
        "needed to resolve them. The reader can act immediately with zero extra searching.\n"
        "3 (Adequate): Main direction is present but specific commands are missing or "
        "resource identification is abstract; the reader must consult Microsoft docs for "
        "the details.\n"
        "2 (Poor): Merely states that an update happened; seriously lacks concrete steps "
        "or linkage to the reader's internal resources. Not executable.\n"
        "1 (Harmful): Provides critically wrong commands that would corrupt existing "
        "configuration or cause a production outage. Negative value."
    ),
    steps=(
        "1. Identify every concrete action the report asks the reader to take and check "
        "whether each has a WHY, a WHERE (procedure/CLI), a WHAT (task), and a WHEN "
        "(deadline).\n"
        "2. Verify the affected resources are named specifically (not 'some resources') "
        "and tied to real property evidence.\n"
        "3. Assess whether a busy admin could start acting within 5 minutes without "
        "opening the Azure Portal or searching further. Penalize any gap against the rubric."
    ),
    edge_cases=(
        "If the source data shows ZERO affected resources or no constraint, a report that "
        "transparently states this and provides only forward monitoring guidance is CORRECT "
        "— do NOT deduct for 'missing commands'. Do NOT fabricate deadlines or commands. "
        "For a Capability-family update (카테고리 new_feature / new_service / region_expansion / "
        "preview / sdk_tooling) the useful answer is an OPPORTUNITY, not an action list: what "
        "becomes possible, for which named candidate resources, at what adoption cost, and "
        "whose responsibility it is. Asserting that such an update has no operational impact or "
        "no risk if skipped is a tautology (a new capability never changes existing behaviour) "
        "— treat it as a substantive gap, not as helpful reassurance."
    ),
)

# D2 — Contextual Accuracy & Faithfulness (맥락적 사실성 및 데이터 충실성)
_FAITHFULNESS = GEvalDimension(
    key="faithfulness",
    title="Contextual Accuracy & Faithfulness",
    weight=1.3,
    rubric=(
        "5 (Ideal): Perfectly reconciles incomplete or contradictory source data with "
        "flawless logic, developing 100% fact-grounded reasoning strictly within the given "
        "context without importing any outside data.\n"
        "4 (Excellent): Every major claim, cost prediction, and resource-impact statement "
        "is accurately grounded in the provided context. No hallucination and no distorted "
        "description of unsupported features.\n"
        "3 (Adequate): Core claims are grounded, but there is minor nuance drift or a vague "
        "estimate not stated in the source; not critically misleading.\n"
        "2 (Poor): Contains claims that clearly depart from the provided context; omits a "
        "key constraint or precondition, causing the reader to misunderstand the facts.\n"
        "1 (Harmful): Directly contradicts core source information, or brazenly presents "
        "fabricated information (e.g. invented API results) as fact. Critical hallucination."
    ),
    steps=(
        "1. Extract the key factual propositions from the SOURCE UPDATE CONTEXT and the "
        "tool/resource findings.\n"
        "2. Traverse the report's claims and cross-verify each against a source proposition; "
        "flag any claim with no traceable origin.\n"
        "3. Identify omitted, altered, or fabricated details and score strictly against the "
        "rubric. A single fabricated fact caps the score at 1-2."
    ),
    edge_cases=(
        "Transparently admitting a limit — e.g. 'compatibility in this environment cannot be "
        "confirmed with the currently collected data' — is a POSITIVE faithfulness signal, "
        "not a deduction."
    ),
)

# D3 — Job Relevance & Subscriber Personalization (직무 연관성 및 독자 맞춤형 정교화)
_JOB_RELEVANCE = GEvalDimension(
    key="job_relevance",
    title="Job Relevance & Subscriber Personalization",
    weight=1.0,
    rubric=(
        "5 (Ideal): Proactively derives the business-level risks the subscriber's role will "
        "face next; reads as though the organization's most senior mentor wrote it solely for "
        "that role, with technical depth, tone, and focus 100% tailored.\n"
        "4 (Excellent): The entire narrative is rewritten for the subscriber's specific role "
        "(e.g. security officer, infra engineer); information irrelevant to that role is "
        "filtered out cleanly.\n"
        "3 (Adequate): Some role-relevant keywords or paragraphs exist, but generic update "
        "description and role-tailored explanation are mixed disjointly rather than organically.\n"
        "2 (Poor): The document is dominated by role-irrelevant content and only mentions job "
        "relevance at a shallow, formulaic level ('be careful about security').\n"
        "1 (Harmful): Completely misreads the role — e.g. gives a marketing angle to a security "
        "officer — producing conflicting, confusing guidance."
    ),
    steps=(
        "1. Determine the subscriber's declared role and interests (or, if none provided, a "
        "general Azure administrator).\n"
        "2. Check whether the narrative's focus, terminology, and depth are re-centered on that "
        "role and whether irrelevant material is filtered.\n"
        "3. Judge how organically the personalization is woven in versus bolted on, and score "
        "against the rubric."
    ),
    edge_cases=(
        "If NO subscriber profile is supplied, evaluate relevance to a general Azure "
        "administrator and do not over-penalize the absence of narrow personalization. A report "
        "that stays crisply on-topic for admins still earns up to 4. AzBrief reports serve a "
        "mixed audience, so a few concise inline concept boxes (blockquote glossary entries for "
        "genuinely non-obvious terms) are an INTENDED feature — do NOT penalize them as 'tutorial "
        "content'. Only deduct when glossary content is excessive (many boxes for common terms) or "
        "when the administrator's decision is not stated up front."
    ),
)

# D4 — Structural Clarity & Visual Design (구조적 명확성 및 시각적 디자인)
_STRUCTURE = GEvalDimension(
    key="structure",
    title="Structural Clarity & Visual Design",
    weight=0.9,
    rubric=(
        "5 (Ideal): Information density and cognitive whitespace are in perfect harmony; an "
        "artful combination of data layering, Markdown tables, and blockquotes lets the reader "
        "grasp the core threat and required action in one second.\n"
        "4 (Excellent): Logical H1/H2/H3 sectioning, bold emphasis on critical items, and "
        "Markdown tables for resource lists and comparison data are applied consistently and "
        "completely.\n"
        "3 (Adequate): Markdown is used, but some structural inconsistency exists or data that "
        "belongs in a table is laid out as long prose, degrading scannability in places.\n"
        "2 (Poor): Paragraph breaks are unclear, dense text blocks dominate and raise cognitive "
        "load, and visual emphasis is absent. Hard to read.\n"
        "1 (Harmful): Frequent Markdown syntax errors break rendering, or the layout is so "
        "chaotic the document's message is impossible to parse."
    ),
    steps=(
        "1. Check for a logical heading hierarchy and clear section separation.\n"
        "2. Verify that resource/cost/comparison data is tabulated and that key warnings use "
        "bold or blockquotes to guide the eye.\n"
        "3. Estimate the reader's cognitive load and time-to-comprehension, then score against "
        "the rubric."
    ),
    edge_cases=(
        "Do not reward verbosity. A short report that sharply structures the essential points "
        "outscores a long, dense one. Judge the Markdown source as it will render. "
        "The narrative analysis body is intentionally heading-free by product design — its "
        "structure is carried by the report's own sections and by paragraph breaks. Do NOT "
        "penalize the absence of subheadings inside the body, and do NOT reward padding "
        "(filler paragraphs, glossary boxes for obvious terms) that exists only to fill a section."
    ),
)

# D5 — Cloud Architectural Depth (클라우드 아키텍처 관점의 심층 통찰)
_ARCH_DEPTH = GEvalDimension(
    key="architectural_depth",
    title="Cloud Architectural Depth",
    weight=1.0,
    rubric=(
        "5 (Ideal): Sees the long-term impact of a single update across the whole ecosystem — "
        "on-prem hybrid control, compliance (ISO 27017 etc.), and disaster-recovery strategy — "
        "with penetrating second- and third-order insight.\n"
        "4 (Excellent): Analyzes concrete system-level optimization grounded in the relevant "
        "Well-Architected Framework pillars (cost, security, performance, reliability, "
        "operations).\n"
        "3 (Adequate): Lists surface-level pros and cons of the feature but lacks depth in "
        "analyzing trade-offs across the architecture pillars.\n"
        "2 (Poor): Almost no consideration of cloud architecture or infrastructure design; stays "
        "at the level of a translated release note.\n"
        "1 (Harmful): Built on a critical misunderstanding of architecture principles that, if "
        "followed, would cause a security incident or large-scale cost waste."
    ),
    steps=(
        "1. Check whether the report discusses second/third-order ripple effects behind the "
        "surface feature (e.g. egress cost, latency, blast radius).\n"
        "2. Cross-check recommendations against cloud security standards (ISO 27017, CSA STAR) "
        "and Well-Architected best practices.\n"
        "3. Identify missing architectural insight and score strictly against the rubric."
    ),
    edge_cases=(
        "For a trivial UI change or simple notification, clearly declaring 'no impact on the "
        "current infrastructure architecture' is POSITIVE — do not fabricate elaborate analysis. "
        "Reward a sharp one-line architectural point over padded prose. When the update does NOT "
        "affect the tenant's current resources, architectural depth means CONCISE, GROUNDED "
        "future-state considerations only — never invent ISO/CSA/Private Link/DR/audit specifics "
        "to appear deep. Unsupported 'depth' is a faithfulness violation and MUST lower this "
        "score, not raise it."
    ),
)

DIMENSIONS: tuple[GEvalDimension, ...] = (
    _ACTIONABILITY,
    _FAITHFULNESS,
    _JOB_RELEVANCE,
    _STRUCTURE,
    _ARCH_DEPTH,
)


# ============================================================================
# Prompt templates
# ============================================================================

_JUDGE_SYSTEM = (
    "You are the foremost authority on Microsoft Azure cloud infrastructure operations "
    "and a strict quality-assurance architect. Your task is to judge whether an Azure "
    "analysis report reaches perfection (5) on ONE dimension.\n\n"
    "SCORING PHILOSOPHY (critical):\n"
    "- 5 is an UNREACHABLE theoretical ideal that surpasses even human expert architects. "
    "Award it essentially never.\n"
    "- 4 is the best grade acceptable for a production environment. Award 4 when the report "
    "actually meets the rubric's own band-4 definition, even though it is not perfect — "
    "perfection is band 5, not band 4.\n"
    "- Score by matching the report against the RUBRIC BAND DEFINITIONS verbatim. Pick the "
    "band whose description fits the report best. Do NOT withhold a band merely because some "
    "unrelated minor flaw exists; a flaw matters only insofar as the rubric's own wording for "
    "that band accounts for it.\n"
    "- RESOLUTION: you MAY use half-points (e.g. 3.5) when the report sits genuinely between "
    "two bands. Use the full range — reports of visibly different quality MUST NOT all receive "
    "the same score. Defaulting everything to 3 is itself an evaluation failure.\n"
    "- Do not be seduced by verbosity (verbosity bias): a short, sharp report beats a long, "
    "padded one. Reward genuine insight, not word count.\n"
    "- FAITHFULNESS IS PARAMOUNT: never reward depth, actionability, or personalization that "
    "is achieved by asserting claims not grounded in the SOURCE UPDATE CONTEXT or the ADMIN "
    "ENVIRONMENT & TOOL EVIDENCE. A grounded, concise report outranks an impressive but "
    "speculative one on every dimension.\n"
    "- Judge ONLY on observable evidence in the report text; never guess the author's intent."
)

_JUDGE_PROMPT = """## Evaluation Task

Evaluate the [FINAL ANALYSIS REPORT] — which was built from the [SOURCE UPDATE CONTEXT] —
on the **{title}** dimension, on a 1-5 scale.

## Rubric (apply verbatim)

{rubric}

## Edge-Case Handling

{edge_cases}

## Evaluation Steps (write explicit reasoning for each before scoring)

{steps}

## SOURCE UPDATE CONTEXT

{update_context}

## SUBSCRIBER PROFILE

{subscriber}

## FINAL ANALYSIS REPORT

{report_text}

## Output

Respond with a single JSON object and nothing else:
{{"reasoning": "<your step-by-step analysis and critique>", "score": <number 1-5, half-points such as 3.5 allowed>, "feedback_for_improvement": "<specific, concrete instructions the rewrite agent MUST apply to raise this dimension; empty string if already ideal>"}}"""

_SCORE_ONLY_SYSTEM = "You output only a single integer digit and nothing else."

_SCORE_ONLY_PROMPT = """Based on the evaluation reasoning below for the "{title}" dimension
(scale 1-5, where 5 is an unreachable ideal and 4 is production-excellent), output ONLY the
final integer score: one of 1, 2, 3, 4, or 5. No words, no punctuation.

Reasoning:
{reasoning}

Final score (single digit):"""


# ============================================================================
# Result data structures
# ============================================================================


@dataclass
class DimensionScore:
    """Score and feedback for a single quality dimension."""

    key: str
    title: str
    integer_score: int
    score: float  # continuous 1-5 (logprob-normalized when available)
    reasoning: str
    feedback: str
    weight: float = 1.0
    normalized: bool = False
    error: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "title": self.title,
            "integer_score": self.integer_score,
            "score": round(self.score, 3),
            "weight": self.weight,
            "normalized": self.normalized,
            "reasoning": self.reasoning,
            "feedback": self.feedback,
            "error": self.error,
        }


@dataclass
class GEvalReport:
    """Aggregated multi-dimensional G-Eval result."""

    dimension_scores: list[DimensionScore] = field(default_factory=list)
    weighted_score: float = 0.0  # 0-5
    percentage: float = 0.0  # 0-100
    grade: str = ""
    passed: bool = False
    target_score: float = 4.5
    aggregated_feedback: list[str] = field(default_factory=list)
    critical_flaws: list[str] = field(default_factory=list)
    elapsed_s: float = 0.0

    def calculate(self) -> None:
        """Compute the weighted aggregate, grade, verdict, and flaws."""
        if not self.dimension_scores:
            return
        total_weight = sum(d.weight for d in self.dimension_scores) or 1.0
        self.weighted_score = sum(d.score * d.weight for d in self.dimension_scores) / total_weight
        self.percentage = self.weighted_score / 5.0 * 100.0
        self.grade = _grade_for(self.weighted_score)
        self.passed = self.weighted_score >= self.target_score

        self.aggregated_feedback = [
            f"[{d.title}] {d.feedback}"
            for d in sorted(self.dimension_scores, key=lambda x: x.score)
            if d.feedback and d.feedback.strip()
        ]
        # A faithfulness score of 1 is a critical hallucination; any dimension <= 2
        # is a serious flaw that blocks a passing verdict.
        self.critical_flaws = [
            f"{d.title}: {d.integer_score}/5 — {d.feedback or d.reasoning[:160]}"
            for d in self.dimension_scores
            if d.integer_score <= 2
        ]

    def as_dict(self) -> dict[str, Any]:
        return {
            "weighted_score": round(self.weighted_score, 3),
            "percentage": round(self.percentage, 1),
            "grade": self.grade,
            "passed": self.passed,
            "target_score": self.target_score,
            "dimensions": [d.as_dict() for d in self.dimension_scores],
            "critical_flaws": self.critical_flaws,
            "elapsed_s": round(self.elapsed_s, 2),
        }


def _grade_for(score: float) -> str:
    """Map a continuous 1-5 score to a labeled grade band."""
    if score >= 4.5:
        return "S (이상적 근접 / near-ideal)"
    if score >= 4.0:
        return "A (프로덕션 우수 / production-excellent)"
    if score >= 3.5:
        return "B (양호 / good)"
    if score >= 3.0:
        return "C (보통 / adequate)"
    if score >= 2.0:
        return "D (미흡 / poor)"
    return "F (불량 / unusable)"


# ============================================================================
# The judge
# ============================================================================


class GEvalJudge:
    """LLM-as-a-Judge that scores AzBrief reports across five G-Eval dimensions."""

    def __init__(
        self,
        llm: Any = None,
        *,
        enable_logprob_normalization: Optional[bool] = None,
        target_score: Optional[float] = None,
        settings: "Settings | None" = None,
    ) -> None:
        """Initialize the judge.

        Args:
            llm: Optional pre-built chat model. If omitted, a dedicated
                deterministic (temperature=0) judge model is created from settings.
            enable_logprob_normalization: Force logprob normalization on/off.
                Defaults to the ``geval_logprob_normalization`` setting. Auto-
                disabled for o-series reasoning models that lack logprob support.
            target_score: Passing threshold on the 1-5 scale. Defaults to the
                ``geval_target_score`` setting.
            settings: Optional settings override (mostly for tests).
        """
        self.settings = settings or get_settings()
        self._deployment = self.settings.azure_openai_deployment_name
        self._is_reasoning = self._is_reasoning_model(self._deployment)
        self._llm = llm if llm is not None else self._create_judge_llm()

        if enable_logprob_normalization is None:
            enable_logprob_normalization = getattr(
                self.settings, "geval_logprob_normalization", True
            )
        # o-series reasoning models do not support logprobs.
        self.enable_logprob_normalization = bool(
            enable_logprob_normalization and not self._is_reasoning
        )

        self.target_score = (
            target_score
            if target_score is not None
            else getattr(self.settings, "geval_target_score", 4.5)
        )
        # One shared breaker: sustained judge failures degrade gracefully to
        # integer scores rather than crashing the evaluation.
        self._breaker = CircuitBreaker(failure_threshold=3, reset_timeout=120)

    # ------------------------------------------------------------------
    # LLM construction
    # ------------------------------------------------------------------

    @staticmethod
    def _is_reasoning_model(deployment_name: str) -> bool:
        """Return True for o-series reasoning models (o1/o3/o4)."""
        name_lower = (deployment_name or "").lower()
        return any(
            name_lower.startswith(prefix) or f"/{prefix}" in name_lower
            for prefix in ("o1", "o3", "o4")
        )

    def _create_judge_llm(self) -> Any:
        """Create a deterministic judge model (temperature=0 for consistency)."""
        from langchain_openai import AzureChatOpenAI, ChatOpenAI

        if self.settings.use_azure_openai:
            kwargs: dict[str, Any] = {
                "azure_endpoint": self.settings.azure_openai_endpoint,
                "api_version": self.settings.azure_openai_api_version,
                "azure_deployment": self._deployment,
                "request_timeout": 120,
            }
            if self._is_reasoning:
                kwargs["reasoning_effort"] = "medium"
            else:
                # A judge must be maximally deterministic and calibrated.
                kwargs["temperature"] = 0
                kwargs["seed"] = 42
            if self.settings.azure_openai_api_key:
                kwargs["api_key"] = self.settings.azure_openai_api_key
            else:
                from azure.identity import get_bearer_token_provider

                from src.config import get_azure_credential

                credential = get_azure_credential()
                kwargs["azure_ad_token_provider"] = get_bearer_token_provider(
                    credential, "https://cognitiveservices.azure.com/.default"
                )
            return AzureChatOpenAI(**kwargs)

        kwargs = {
            "api_key": self.settings.openai_api_key,
            "model": "gpt-4o",
            "request_timeout": 120,
        }
        if self._is_reasoning:
            kwargs["reasoning_effort"] = "medium"
        else:
            kwargs["temperature"] = 0
            kwargs["seed"] = 42
        return ChatOpenAI(**kwargs)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def evaluate(
        self,
        result: "AnalysisResult",
        update: "AzureUpdate",
        *,
        subscriber: "Subscriber | None" = None,
        language: str = "ko",
        report_markdown: Optional[str] = None,
        update_context: Optional[str] = None,
        evidence_context: Optional[str] = None,
    ) -> GEvalReport:
        """Score a report across all five dimensions in parallel.

        Args:
            result: The analysis result to judge.
            update: The original Azure Update (used for source context).
            subscriber: Optional subscriber profile for the personalization axis.
            language: Report language (for context only; judge reads any language).
            report_markdown: Pre-rendered report text. If omitted, one is rendered
                from ``result`` via :meth:`render_report_markdown`.
            update_context: Pre-built source context. If omitted, one is built from
                ``update``.
            evidence_context: The admin environment + tool evidence the report was
                generated from (resource summary, Resource Graph results, doc
                snippets). Supplying it lets the judge fairly assess environment-
                specific claims for the faithfulness dimension instead of treating
                every tenant-specific statement as ungrounded.

        Returns:
            A populated :class:`GEvalReport`.
        """
        t0 = time.time()
        report_text = report_markdown or self.render_report_markdown(result, update, language)
        ctx_text = update_context or self._render_update_context(update)
        if evidence_context and evidence_context.strip():
            ctx_text += (
                "\n\n## ADMIN ENVIRONMENT & TOOL EVIDENCE "
                "(ground truth the report was built from — treat tenant-specific claims "
                "grounded here as faithful)\n\n" + evidence_context.strip()
            )
        subscriber_text = self._render_subscriber(subscriber)

        trace = getattr(result, "update_id", "") or getattr(update, "id", "")
        logger.info(
            "geval_started",
            update_id=trace,
            dimensions=len(DIMENSIONS),
            logprob_normalization=self.enable_logprob_normalization,
            target_score=self.target_score,
        )

        # Dimension-independent parallel evaluation (halo-effect isolation).
        tasks = [
            self._evaluate_dimension(dim, ctx_text, subscriber_text, report_text)
            for dim in DIMENSIONS
        ]
        scores = await asyncio.gather(*tasks, return_exceptions=True)

        report = GEvalReport(target_score=self.target_score)
        for dim, outcome in zip(DIMENSIONS, scores):
            if isinstance(outcome, Exception):
                logger.warning("geval_dimension_error", dimension=dim.key, error=str(outcome)[:200])
                report.dimension_scores.append(
                    DimensionScore(
                        key=dim.key,
                        title=dim.title,
                        integer_score=3,
                        score=3.0,
                        reasoning="",
                        feedback="",
                        weight=dim.weight,
                        error=str(outcome)[:200],
                    )
                )
            else:
                report.dimension_scores.append(outcome)

        report.calculate()
        report.elapsed_s = time.time() - t0

        logger.info(
            "geval_done",
            update_id=trace,
            weighted_score=round(report.weighted_score, 3),
            percentage=round(report.percentage, 1),
            grade=report.grade,
            passed=report.passed,
            critical_flaws=len(report.critical_flaws),
            elapsed_s=round(report.elapsed_s, 2),
            scores={d.key: round(d.score, 2) for d in report.dimension_scores},
        )
        return report

    def build_feedback_prompt(self, report: GEvalReport) -> str:
        """Build a rewrite-guidance prompt from a G-Eval report.

        The text is injected into the report-generation system prompt for the
        next iteration of the self-correction loop.

        Args:
            report: The G-Eval result of the previous iteration.

        Returns:
            A feedback prompt string, or empty string if already at/above target.
        """
        if report.passed and not report.critical_flaws:
            return ""

        lines = [
            "## Report Quality Feedback (G-Eval, previous iteration)",
            (
                f"Previous weighted score: {report.weighted_score:.2f}/5.00 "
                f"({report.percentage:.0f}%) — target {report.target_score:.1f}/5. "
                "This is internal guidance for you — do NOT copy these headings or meta-text "
                "into the report body. Apply EVERY instruction below.\n\n"
                "PRIORITY 1 — GROUNDING: First fix faithfulness. Remove or explicitly hedge any "
                "claim not grounded in the source update or the admin environment/tool evidence. "
                "Prefer DELETING unsupported content over adding new content — a shorter, fully "
                "grounded report scores higher than a longer speculative one. Only add depth, "
                "actions, or personalization that the evidence directly supports."
            ),
            "",
        ]
        if report.critical_flaws:
            lines.append("### Critical flaws to fix first")
            for flaw in report.critical_flaws:
                lines.append(f"- {flaw}")
            lines.append("")

        lines.append("### Per-dimension instructions (weakest first)")
        for d in sorted(report.dimension_scores, key=lambda x: x.score):
            if d.score >= 5.0 or not d.feedback.strip():
                continue
            lines.append(f"- **{d.title}** ({d.score:.1f}/5): {d.feedback.strip()}")
        lines.append("")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Per-dimension evaluation
    # ------------------------------------------------------------------

    async def _evaluate_dimension(
        self,
        dim: GEvalDimension,
        update_context: str,
        subscriber_text: str,
        report_text: str,
    ) -> DimensionScore:
        """Judge a single dimension (CoT reasoning + optional logprob refinement)."""
        from langchain_core.messages import HumanMessage, SystemMessage

        prompt = _JUDGE_PROMPT.format(
            title=dim.title,
            rubric=dim.rubric,
            edge_cases=dim.edge_cases,
            steps=dim.steps,
            update_context=update_context,
            subscriber=subscriber_text,
            report_text=report_text,
        )

        async def _call() -> Any:
            return await self._llm.ainvoke(
                [SystemMessage(content=_JUDGE_SYSTEM), HumanMessage(content=prompt)]
            )

        response = await retry_with_backoff(
            _call, max_retries=2, is_foreground=True, circuit_breaker=self._breaker
        )
        raw = response.content if hasattr(response, "content") else str(response)
        parsed = parse_json_resilient(raw) or {}

        raw_score = _coerce_score(parsed.get("score"))
        integer_score = int(round(raw_score))
        reasoning = str(parsed.get("reasoning", "")).strip()
        feedback = str(parsed.get("feedback_for_improvement", "")).strip()

        continuous = raw_score
        normalized = False
        # Only attempt logprob normalization when the judge returned a whole number.
        # A half-point score already carries more resolution than the reasoning-only
        # normalization pass can recover, and that pass sees neither the report nor
        # the evidence — it must never overwrite a better-informed score.
        if self.enable_logprob_normalization and reasoning and continuous == float(integer_score):
            refined = await self._normalize_score(dim, reasoning)
            if refined is not None:
                continuous = refined
                normalized = True

        logger.debug(
            "geval_dimension_scored",
            dimension=dim.key,
            integer_score=integer_score,
            continuous=round(continuous, 3),
            normalized=normalized,
        )
        return DimensionScore(
            key=dim.key,
            title=dim.title,
            integer_score=integer_score,
            score=continuous,
            reasoning=reasoning,
            feedback=feedback,
            weight=dim.weight,
            normalized=normalized,
        )

    async def _normalize_score(self, dim: GEvalDimension, reasoning: str) -> Optional[float]:
        """Refine an integer score into a continuous one via token log-probabilities.

        Returns the probability-weighted score over the score tokens {1..5}, or
        ``None`` if logprobs are unavailable (graceful degradation).
        """
        from langchain_core.messages import HumanMessage, SystemMessage

        try:
            scorer = self._llm.bind(logprobs=True, top_logprobs=5)
            prompt = _SCORE_ONLY_PROMPT.format(title=dim.title, reasoning=reasoning[:4000])
            response = await scorer.ainvoke(
                [SystemMessage(content=_SCORE_ONLY_SYSTEM), HumanMessage(content=prompt)]
            )
        except Exception as exc:  # pragma: no cover - network/model dependent
            logger.debug("geval_logprob_call_failed", dimension=dim.key, error=str(exc)[:160])
            return None

        content_logprobs = _extract_content_logprobs(response)
        if not content_logprobs:
            return None
        return _weighted_score_from_logprobs(content_logprobs)

    # ------------------------------------------------------------------
    # Rendering helpers
    # ------------------------------------------------------------------

    def render_report_markdown(
        self, result: "AnalysisResult", update: "AzureUpdate", language: str = "ko"
    ) -> str:
        """Render an ``AnalysisResult`` into the Markdown report the judge scores.

        This reconstructs the reader-facing deliverable (summary, badges, evidence,
        analysis body, impact, affected resources, action items, references) so the
        judge evaluates the actual report rather than a raw object dump.
        """
        parts: list[str] = []
        title = getattr(result, "update_title", "") or getattr(update, "title", "")
        parts.append(f"# {title}")

        urgency = _enum_value(getattr(result, "urgency", ""))
        importance = getattr(result, "importance", "") or "-"
        impact_level = getattr(result, "impact_level", "") or "-"
        job_rel = getattr(result, "job_relevance", "") or "-"
        relevance = _enum_value(getattr(result, "relevance", ""))
        category = getattr(result, "update_category", "") or "-"
        parts.append(
            f"**카테고리:** {category} | **긴급도:** {urgency} | **중요성:** {importance} | "
            f"**영향도:** {impact_level} | **직무연관성:** {job_rel} | **관련성:** {relevance}"
        )

        summary = getattr(result, "one_line_summary", "")
        if summary:
            parts.append(f"> **한 줄 요약:** {summary}")

        evidence = getattr(result, "relevance_evidence", "")
        if evidence:
            parts.append(f"## 관련성 근거\n\n{evidence}")

        analysis = getattr(result, "relevance_reason", "")
        if analysis:
            parts.append(f"## 상세 분석\n\n{analysis}")

        impact = getattr(result, "impact_details", None)
        if impact is not None:
            rows = []
            for label, val in (
                ("비용", getattr(impact, "cost_impact", "")),
                ("보안", getattr(impact, "security_impact", "")),
                ("성능", getattr(impact, "performance_impact", "")),
                ("운영", getattr(impact, "operational_impact", "")),
            ):
                if val and val.strip():
                    rows.append(f"| {label} | {val.strip()} |")
            if rows:
                heading = "활용 기회" if category in CAPABILITY_CATEGORIES else "영향 분석"
                parts.append(
                    f"## {heading}\n\n| 차원 | 내용 |\n|------|------|\n" + "\n".join(rows)
                )

        resources = getattr(result, "affected_resources", None) or []
        if resources:
            header = "## 영향받는 리소스\n\n| 이름 | 유형 | 리소스 그룹 | 사유 |\n|------|------|------------|------|"
            # Group resources that share the same non-empty reason into one row so the
            # judge sees the same compact layout the email renders.
            groups: dict = {}
            for idx, r in enumerate(resources):
                if not isinstance(r, dict):
                    continue
                reason_key = (r.get("reason") or "").strip()
                key = reason_key if reason_key else f"\x00__no_reason__{idx}"
                groups.setdefault(key, []).append(r)
            rows = []
            for group in groups.values():
                names = "<br>".join(str(r.get("name", "-")) for r in group)
                types = "<br>".join(dict.fromkeys(str(r.get("type", "-")) for r in group))
                rgs = "<br>".join(dict.fromkeys(str(r.get("resourceGroup", "-")) for r in group))
                reason = group[0].get("reason", "-") or "-"
                rows.append(f"| {names} | {types} | {rgs} | {reason} |")
            if rows:
                parts.append(header + "\n" + "\n".join(rows))

        actions = getattr(result, "action_items", None) or []
        if actions:
            # Render as a scannable action table (the email shows structured action
            # cards; a table faithfully represents that for the judge) plus per-step
            # detail for the richer fields.
            parts.append("## 조치 항목")
            header = (
                "| 단계 | 조치 | 대상 | 기한 | 미조치 위험 |\n"
                "|------|------|------|------|-------------|"
            )
            rows = []
            for ai in actions:
                step = getattr(ai, "step", "") or getattr(ai, "priority", "")
                task = str(getattr(ai, "task", "") or "").replace("\n", " ")
                targets = getattr(ai, "target_resources", "") or ""
                if isinstance(targets, list):
                    targets = ", ".join(str(v) for v in targets)
                deadline = str(getattr(ai, "deadline", "") or "-").replace("\n", " ")
                risk = str(getattr(ai, "risk_if_not_done", "") or "-").replace("\n", " ")
                rows.append(f"| {step} | {task} | {targets or '-'} | {deadline} | {risk} |")
            parts.append(header + "\n" + "\n".join(rows))
            for ai in actions:
                step = getattr(ai, "step", "") or getattr(ai, "priority", "")
                task = getattr(ai, "task", "")
                block = [f"### {step}. {task}"]
                for label, attr in (
                    ("이유", "why"),
                    ("절차", "procedure"),
                    ("명령어", "cli_command"),
                    ("사전 확인", "precaution"),
                    ("롤백", "rollback"),
                ):
                    val = getattr(ai, attr, "")
                    if isinstance(val, list):
                        val = ", ".join(str(v) for v in val)
                    if val and str(val).strip():
                        block.append(f"- **{label}:** {val}")
                if len(block) > 1:
                    parts.append("\n".join(block))

        checks = getattr(result, "additional_checks", None) or []
        if checks:
            parts.append("## 추가 검토 항목\n\n" + "\n".join(f"- {c}" for c in checks))

        refs = getattr(result, "reference_docs", None) or []
        if refs:
            lines = ["## 참고 문서"]
            for d in refs:
                if isinstance(d, dict):
                    lines.append(f"- [{d.get('title', d.get('url', '문서'))}]({d.get('url', '')})")
            parts.append("\n".join(lines))

        return "\n\n".join(parts)

    @staticmethod
    def _render_update_context(update: "AzureUpdate") -> str:
        """Build the source-context block the judge cross-checks claims against."""
        services = getattr(update, "azure_services", None) or []
        cats = getattr(update, "categories", None) or []
        published = getattr(update, "published_date", None)
        parts = [
            f"Title: {getattr(update, 'title', '')}",
            f"Update type: {getattr(update, 'update_type', '') or 'N/A'}",
            f"Status: {getattr(update, 'status', '') or 'N/A'}",
            f"Services: {', '.join(services) if services else 'N/A'}",
            f"Categories: {', '.join(cats) if cats else 'N/A'}",
            f"Published: {published.isoformat() if published else 'N/A'}",
            f"Link: {getattr(update, 'link', '')}",
            "",
            "Description:",
            getattr(update, "description", "") or "",
        ]
        learn = getattr(update, "learn_more_links", None) or []
        if learn:
            parts.append("")
            parts.append("Learn More links: " + ", ".join(str(x) for x in learn[:5]))
        return "\n".join(parts)

    @staticmethod
    def _render_subscriber(subscriber: "Subscriber | None") -> str:
        """Render the subscriber profile for the job-relevance dimension."""
        if subscriber is None:
            return (
                "No specific subscriber profile provided. Evaluate relevance to a general "
                "Azure administrator responsible for a mixed resource estate."
            )
        focus = getattr(subscriber, "focus_services", None) or []
        return (
            f"Name: {getattr(subscriber, 'name', '')}\n"
            f"Role: {getattr(subscriber, 'role', '') or 'General Azure administrator'}\n"
            f"Language: {getattr(subscriber, 'language', 'ko')}\n"
            f"Focus services: {', '.join(focus) if focus else 'all'}\n"
            f"Alert level: {getattr(subscriber, 'alert_level', 'all')}"
        )


# ============================================================================
# Module-level helpers
# ============================================================================


def _enum_value(v: Any) -> str:
    """Return the ``.value`` of an enum or the string form of a value."""
    return v.value if hasattr(v, "value") else (str(v) if v is not None else "-")


def _coerce_score(raw: Any) -> float:
    """Coerce an LLM score field into a half-point value in [1, 5], defaulting to 3.

    Half-point resolution is the judge-side substitute for logprob normalization,
    which is unavailable on deployments that return only the chosen token. Without
    it the usable band count collapses and every report lands on the same score.
    """
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return 3.0
    snapped = round(val * 2) / 2  # snap to the nearest half point
    return max(1.0, min(5.0, snapped))


def _extract_content_logprobs(response: Any) -> list[dict[str, Any]]:
    """Pull the per-token logprob list out of a chat response, if present."""
    meta = getattr(response, "response_metadata", None) or {}
    logprobs = meta.get("logprobs")
    if isinstance(logprobs, dict):
        content = logprobs.get("content")
        if isinstance(content, list):
            return content
    return []


def _weighted_score_from_logprobs(
    content_logprobs: list[dict[str, Any]],
) -> Optional[float]:
    """Compute a probability-weighted score over the score tokens {1..5}.

    Finds the first generated token that is a score digit and weights the linear
    probabilities of its ``top_logprobs`` candidates that are also score digits.

    Returns ``None`` when the refinement carries **no information**, so the caller
    keeps the integer score from the reasoning pass:

    * no score token was generated, or
    * fewer than two score-digit candidates were returned.

    The second guard matters: some deployments honor ``logprobs=True`` but return
    only the single chosen token regardless of ``top_logprobs=N``. Weighting one
    candidate always yields exactly that token's value, i.e. a fake "continuous"
    score with zero added resolution. Worse, that value comes from a follow-up
    call that only sees the reasoning text — strictly less evidence than the
    reasoning pass that produced the integer — so letting it win silently
    *overwrites a better-informed judgment*. Returning ``None`` is the honest
    outcome: report ``normalized=False`` and keep the integer.
    """
    for tok_info in content_logprobs:
        tok = str(tok_info.get("token", "")).strip()
        if tok not in _SCORE_TOKENS:
            continue
        candidates = tok_info.get("top_logprobs") or []
        score_candidates = [
            cand for cand in candidates if str(cand.get("token", "")).strip() in _SCORE_TOKENS
        ]
        if len(score_candidates) < 2:
            # Degenerate distribution — no resolution to gain, and no basis to
            # override the reasoning pass. Signal "not normalized".
            return None
        weighted = 0.0
        total = 0.0
        for cand in score_candidates:
            ctok = str(cand.get("token", "")).strip()
            prob = math.exp(cand.get("logprob", -100.0))
            weighted += int(ctok) * prob
            total += prob
        if total > 0:
            return weighted / total
        return None
    return None
