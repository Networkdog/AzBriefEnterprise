"""Tests for the G-Eval LLM-as-a-Judge report quality evaluator."""

import json
from datetime import datetime
from types import SimpleNamespace

import pytest

from src.agent.analyzer import (
    ActionItem,
    AnalysisResult,
    ImpactSummary,
    RelevanceStatus,
    UrgencyLevel,
)
from src.agent.geval import (
    DIMENSIONS,
    DimensionScore,
    GEvalJudge,
    GEvalReport,
    _coerce_score,
    _enum_value,
    _extract_content_logprobs,
    _grade_for,
    _weighted_score_from_logprobs,
)
from src.rss.parser import AzureUpdate

# ============================================================================
# Fixtures & fakes
# ============================================================================


def _fake_settings(deployment="azbrief-quality-reviewer", logprob=True, target=4.5):
    settings = SimpleNamespace(
        foundry_quality_reviewer_agent_name=deployment,
        geval_logprob_normalization=logprob,
        geval_target_score=target,
    )
    settings.foundry_agent_for_role = lambda role: settings.foundry_quality_reviewer_agent_name
    return settings


class _FakeMsg:
    def __init__(self, content, metadata=None):
        self.content = content
        self.response_metadata = metadata or {}


class _FakeJudgeLLM:
    """Fake chat model that maps dimension titles to canned scores."""

    def __init__(self, scores, *, with_logprobs=False, fail_titles=None, bad_json_titles=None):
        # scores: {title_substring: int_score}
        self.scores = scores
        self.with_logprobs = with_logprobs
        self.fail_titles = set(fail_titles or [])
        self.bad_json_titles = set(bad_json_titles or [])
        self.dimension_calls = 0
        self.score_only_calls = 0

    def _match_title(self, text):
        for title, score in self.scores.items():
            if title in text:
                return title, score
        return None, 3

    async def ainvoke(self, messages):
        system = messages[0].content
        human = messages[-1].content
        title, score = self._match_title(human)

        is_score_only = system.startswith("You output only")
        if title in self.fail_titles:
            raise RuntimeError("judge boom")

        if is_score_only:
            self.score_only_calls += 1
            meta = {}
            if self.with_logprobs:
                nxt = min(5, score + 1)
                meta = {
                    "logprobs": {
                        "content": [
                            {
                                "token": str(score),
                                "top_logprobs": [
                                    {"token": str(score), "logprob": -0.1},
                                    {"token": str(nxt), "logprob": -2.0},
                                ],
                            }
                        ]
                    }
                }
            return _FakeMsg(str(score), meta)

        self.dimension_calls += 1
        if title in self.bad_json_titles:
            return _FakeMsg("not json at all, sorry")
        return _FakeMsg(
            json.dumps(
                {
                    "reasoning": f"reasoning for {title}",
                    "score": score,
                    "feedback_for_improvement": f"improve {title}" if score < 5 else "",
                }
            )
        )

    def bind(self, **kwargs):
        return self


@pytest.fixture
def sample_update():
    return AzureUpdate(
        id="geval-test-001",
        title="Retirement: TLS 1.0/1.1 for Azure Storage ends 2026-10-31",
        description="Azure Storage will block TLS 1.0/1.1 connections.",
        link="https://azure.microsoft.com/updates/tls-retirement",
        published_date=datetime(2026, 6, 15),
        categories=["Storage"],
        azure_services=["Storage Accounts"],
        update_type="Retirement",
        status="Launched",
    )


@pytest.fixture
def sample_result():
    return AnalysisResult(
        update_id="geval-test-001",
        update_title="TLS 1.0/1.1 retirement for Azure Storage",
        update_category="retirement",
        urgency=UrgencyLevel.HIGH,
        importance="high",
        impact_level="high",
        job_relevance="high",
        relevance=RelevanceStatus.RELEVANT,
        one_line_summary="Storage Account TLS 1.0/1.1 차단 — 3개 계정 마이그레이션 필요",
        relevance_evidence="Storage Account 22개 중 3개가 TLS 1.0을 사용 중입니다.",
        relevance_reason="**Azure Storage**의 TLS 1.0/1.1 지원이 종료됩니다.",
        affected_resources=[
            {
                "name": "sthottierpoc",
                "type": "microsoft.storage/storageaccounts",
                "resourceGroup": "rg-storage",
                "reason": "minimumTlsVersion: TLS1_0",
            }
        ],
        impact_summary="보안 영향",
        impact_details=ImpactSummary(
            security_impact="TLS 1.0 취약점 제거", operational_impact="클라이언트 호환성 검증 필요"
        ),
        action_items=[
            ActionItem(
                step=1,
                task="minimumTlsVersion를 TLS1_2로 변경",
                why="2026-10-31 이후 차단",
                target_resources=["sthottierpoc"],
                procedure="Azure Portal > Configuration",
                deadline="2026-10-31",
            )
        ],
        recommendations=["TLS 버전 상향"],
        reference_docs=[{"title": "TLS 설정", "url": "https://learn.microsoft.com/tls"}],
        should_notify=True,
    )


# ============================================================================
# Pure helper tests
# ============================================================================


@pytest.mark.parametrize(
    "raw,expected",
    [
        (4, 4.0),
        ("5", 5.0),
        (3.4, 3.5),
        (3.5, 3.5),
        ("3.5", 3.5),
        (3.2, 3.0),
        (0, 1.0),
        (9, 5.0),
        ("nope", 3.0),
        (None, 3.0),
        (-2, 1.0),
    ],
)
def test_coerce_score(raw, expected):
    """Scores snap to half points in [1, 5]; half-point resolution is intentional."""
    assert _coerce_score(raw) == expected


@pytest.mark.parametrize(
    "score,band",
    [(4.9, "S"), (4.5, "S"), (4.2, "A"), (3.6, "B"), (3.1, "C"), (2.4, "D"), (1.2, "F")],
)
def test_grade_for(score, band):
    assert _grade_for(score).startswith(band)


def test_weighted_score_from_logprobs_weighting():
    content = [
        {
            "token": "4",
            "top_logprobs": [
                {"token": "4", "logprob": -0.1},
                {"token": "3", "logprob": -2.0},
            ],
        }
    ]
    score = _weighted_score_from_logprobs(content)
    # Weighted between 3 and 4, closer to 4
    assert 3.8 < score < 4.0


def test_weighted_score_from_logprobs_skips_non_digit_tokens():
    content = [
        {"token": "Score", "top_logprobs": []},
        {"token": ":", "top_logprobs": []},
        {
            "token": "5",
            "top_logprobs": [
                {"token": "5", "logprob": -0.01},
                {"token": "4", "logprob": -6.0},
            ],
        },
    ]
    score = _weighted_score_from_logprobs(content)
    assert abs(score - 5.0) < 0.01


def test_weighted_score_from_logprobs_none_on_degenerate_distribution():
    """A single candidate carries no distribution information.

    Some deployments honor logprobs=True but return only the chosen token
    regardless of top_logprobs=N. Weighting one candidate yields exactly that
    token's value — a fake continuous score with zero added resolution — and it
    would silently overwrite the better-informed reasoning-pass score. The
    honest result is None (caller keeps the integer, normalized=False).
    """
    content = [{"token": "3", "top_logprobs": [{"token": "3", "logprob": -0.0004}]}]
    assert _weighted_score_from_logprobs(content) is None


def test_weighted_score_from_logprobs_none_when_no_digit():
    content = [{"token": "hello", "top_logprobs": []}]
    assert _weighted_score_from_logprobs(content) is None


def test_extract_content_logprobs():
    msg = _FakeMsg("4", {"logprobs": {"content": [{"token": "4"}]}})
    assert _extract_content_logprobs(msg) == [{"token": "4"}]
    assert _extract_content_logprobs(_FakeMsg("4")) == []


def test_enum_value():
    assert _enum_value(UrgencyLevel.HIGH) == "high"
    assert _enum_value("plain") == "plain"
    assert _enum_value(None) == "-"


# ============================================================================
# Aggregation tests
# ============================================================================


def test_geval_report_calculate_weighted_and_grade():
    report = GEvalReport(target_score=4.5)
    report.dimension_scores = [
        DimensionScore("a", "A", 4, 4.0, "r", "fb", weight=1.0),
        DimensionScore("b", "B", 4, 4.0, "r", "", weight=1.0),
    ]
    report.calculate()
    assert abs(report.weighted_score - 4.0) < 1e-9
    assert abs(report.percentage - 80.0) < 1e-9
    assert report.grade.startswith("A")
    assert report.passed is False  # 4.0 < 4.5 target


def test_geval_report_weighted_respects_weights():
    report = GEvalReport(target_score=4.5)
    report.dimension_scores = [
        DimensionScore("a", "A", 5, 5.0, "r", "", weight=3.0),
        DimensionScore("b", "B", 1, 1.0, "r", "fix", weight=1.0),
    ]
    report.calculate()
    # (5*3 + 1*1) / 4 = 4.0
    assert abs(report.weighted_score - 4.0) < 1e-9


def test_geval_report_critical_flaws_and_feedback_order():
    report = GEvalReport(target_score=4.0)
    report.dimension_scores = [
        DimensionScore("a", "Faithfulness", 1, 1.0, "hallucination", "fix facts", weight=1.0),
        DimensionScore("b", "Structure", 4, 4.0, "ok", "polish tables", weight=1.0),
    ]
    report.calculate()
    assert len(report.critical_flaws) == 1
    assert "Faithfulness" in report.critical_flaws[0]
    # Weakest dimension feedback appears first
    assert report.aggregated_feedback[0].startswith("[Faithfulness]")


def test_geval_report_passed_true_at_target():
    report = GEvalReport(target_score=4.5)
    report.dimension_scores = [DimensionScore("a", "A", 5, 4.6, "r", "", weight=1.0)]
    report.calculate()
    assert report.passed is True


# ============================================================================
# Judge rendering tests (no network)
# ============================================================================


def test_render_report_markdown_has_all_sections(sample_result, sample_update):
    judge = GEvalJudge(llm=_FakeJudgeLLM({}), settings=_fake_settings())
    md = judge.render_report_markdown(sample_result, sample_update)
    assert "# TLS 1.0/1.1 retirement" in md
    assert "한 줄 요약" in md
    assert "관련성 근거" in md
    assert "상세 분석" in md
    assert "영향받는 리소스" in md
    assert "sthottierpoc" in md
    assert "조치 항목" in md
    assert "참고 문서" in md
    assert "중요성:** high" in md


def test_render_report_markdown_checks_precede_references(sample_result, sample_update):
    """'추가 검토 항목' must come before '참고 문서' — same order as the email."""
    judge = GEvalJudge(llm=_FakeJudgeLLM({}), settings=_fake_settings())
    sample_result.additional_checks = ["프라이빗 엔드포인트 구성 여부를 점검합니다"]
    md = judge.render_report_markdown(sample_result, sample_update)
    assert md.index("## 추가 검토 항목") < md.index("## 참고 문서")


def test_render_subscriber_none_and_present():
    judge = GEvalJudge(llm=_FakeJudgeLLM({}), settings=_fake_settings())
    assert "No specific subscriber" in judge._render_subscriber(None)
    sub = SimpleNamespace(
        name="Bob", role="Security", language="en", focus_services=["Key Vault"], alert_level="all"
    )
    rendered = judge._render_subscriber(sub)
    assert "Security" in rendered
    assert "Key Vault" in rendered


def test_render_update_context(sample_update):
    judge = GEvalJudge(llm=_FakeJudgeLLM({}), settings=_fake_settings())
    ctx = judge._render_update_context(sample_update)
    assert "Retirement" in ctx
    assert "Storage Accounts" in ctx


# ============================================================================
# Logprob gating
# ============================================================================


def test_logprob_disabled_for_reasoning_model():
    judge = GEvalJudge(llm=_FakeJudgeLLM({}), settings=_fake_settings(deployment="o3-mini"))
    assert judge.enable_logprob_normalization is False


def test_logprob_respects_setting():
    judge = GEvalJudge(llm=_FakeJudgeLLM({}), settings=_fake_settings(logprob=False))
    assert judge.enable_logprob_normalization is False


def test_target_score_override():
    judge = GEvalJudge(llm=_FakeJudgeLLM({}), settings=_fake_settings(), target_score=4.9)
    assert judge.target_score == 4.9


# ============================================================================
# End-to-end evaluate() with a fake judge LLM
# ============================================================================

_ALL_TITLES = {d.title: 4 for d in DIMENSIONS}


@pytest.mark.asyncio
async def test_evaluate_end_to_end_integer_scores(sample_result, sample_update):
    judge = GEvalJudge(
        llm=_FakeJudgeLLM(_ALL_TITLES),
        settings=_fake_settings(logprob=False),
        target_score=4.5,
    )
    report = await judge.evaluate(sample_result, sample_update)
    assert len(report.dimension_scores) == len(DIMENSIONS)
    assert all(d.integer_score == 4 for d in report.dimension_scores)
    assert abs(report.weighted_score - 4.0) < 1e-6
    assert report.passed is False
    assert all(not d.normalized for d in report.dimension_scores)


@pytest.mark.asyncio
async def test_evaluate_with_logprob_normalization(sample_result, sample_update):
    judge = GEvalJudge(
        llm=_FakeJudgeLLM(_ALL_TITLES, with_logprobs=True),
        settings=_fake_settings(logprob=True),
    )
    report = await judge.evaluate(sample_result, sample_update)
    # Continuous scores diverge from the integer 4 because probability mass sits
    # on the neighbouring "5" candidate, pulling the normalized value above 4.0.
    assert all(d.normalized for d in report.dimension_scores)
    assert all(4.0 < d.score < 4.3 for d in report.dimension_scores)
    assert all(d.integer_score == 4 for d in report.dimension_scores)


@pytest.mark.asyncio
async def test_evaluate_passes_at_high_scores(sample_result, sample_update):
    judge = GEvalJudge(
        llm=_FakeJudgeLLM({d.title: 5 for d in DIMENSIONS}),
        settings=_fake_settings(logprob=False),
        target_score=4.5,
    )
    report = await judge.evaluate(sample_result, sample_update)
    assert abs(report.weighted_score - 5.0) < 1e-6
    assert report.passed is True
    assert report.critical_flaws == []


@pytest.mark.asyncio
async def test_evaluate_error_isolation(sample_result, sample_update):
    # One dimension raises; the rest must still score.
    fail = {"Cloud Architectural Depth"}
    judge = GEvalJudge(
        llm=_FakeJudgeLLM(_ALL_TITLES, fail_titles=fail),
        settings=_fake_settings(logprob=False),
    )
    report = await judge.evaluate(sample_result, sample_update)
    errored = [d for d in report.dimension_scores if d.error]
    assert len(errored) == 1
    assert errored[0].title == "Cloud Architectural Depth"
    assert errored[0].integer_score == 3  # graceful fallback
    # Other four scored normally
    assert len([d for d in report.dimension_scores if not d.error]) == 4


@pytest.mark.asyncio
async def test_evaluate_malformed_json_defaults(sample_result, sample_update):
    judge = GEvalJudge(
        llm=_FakeJudgeLLM(_ALL_TITLES, bad_json_titles={"Structural Clarity & Visual Design"}),
        settings=_fake_settings(logprob=False),
    )
    report = await judge.evaluate(sample_result, sample_update)
    structure = next(d for d in report.dimension_scores if d.key == "structure")
    assert structure.integer_score == 3  # default when JSON unparseable


# ============================================================================
# Feedback prompt
# ============================================================================


def test_build_feedback_prompt_empty_when_passed():
    judge = GEvalJudge(llm=_FakeJudgeLLM({}), settings=_fake_settings())
    report = GEvalReport(target_score=4.5)
    report.dimension_scores = [DimensionScore("a", "A", 5, 4.8, "r", "", weight=1.0)]
    report.calculate()
    assert judge.build_feedback_prompt(report) == ""


def test_build_feedback_prompt_lists_weakest_first():
    judge = GEvalJudge(llm=_FakeJudgeLLM({}), settings=_fake_settings())
    report = GEvalReport(target_score=4.5)
    report.dimension_scores = [
        DimensionScore("a", "Actionability", 2, 2.1, "weak", "add CLI commands", weight=1.0),
        DimensionScore("b", "Structure", 4, 4.0, "ok", "add a table", weight=1.0),
    ]
    report.calculate()
    fb = judge.build_feedback_prompt(report)
    assert "add CLI commands" in fb
    assert "Critical flaws" in fb  # score 2 triggers critical flaw section
    assert fb.index("Actionability") < fb.index("Structure")
