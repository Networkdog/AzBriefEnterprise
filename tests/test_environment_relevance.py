"""Regression coverage for category-aware environment relevance."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from bs4 import BeautifulSoup
from langchain_core.messages import AIMessage

from scripts.evaluate_report import ReportQualityEvaluator
from src.agent.analyzer import AnalysisResult, AzureUpdateAnalyzer, RelevanceStatus
from src.agent.geval import DIMENSIONS, GEvalJudge
from src.agent.prompts import build_report_prompt, build_system_prompt
from src.agent.prompts.phases import PLANNING_PROMPT
from src.agent.prompts.report.categories import CATEGORY_TEMPLATES
from src.agent.resilience import CircuitBreaker
from src.archive.models import ArchiveAnalysisResultV1
from src.archive.page import render_archive_page
from src.config import Subscriber
from src.email.service import EmailService
from src.i18n.labels import get_labels
from src.rss.parser import AzureUpdate


@pytest.mark.parametrize(
    ("language", "heading", "previous_heading"),
    [
        ("ko", "환경 연관성", "선택 근거"),
        ("en", "Environment Relevance", "Why Included"),
        ("ja", "環境との関連性", "選択理由"),
    ],
)
def test_relevance_heading_is_shared_across_delivery_surfaces(
    language: str,
    heading: str,
    previous_heading: str,
    sample_update: AzureUpdate,
    sample_analysis_result: AnalysisResult,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = sample_analysis_result.model_copy(
        update={"relevance_evidence": "Known workload fit; adoption prerequisites remain explicit."}
    )
    service = EmailService.__new__(EmailService)
    service.settings = SimpleNamespace(feedback_ui_enabled=False, feedback_base_url="")
    monkeypatch.setattr(service, "_build_retirement_countdown_html", lambda _: "")

    single = service.build_email_content(sample_update, result, language=language)
    digest = service.build_digest_content(
        [{"update": sample_update, "result": result}], language=language
    )
    archive = render_archive_page(nonce="test", profile="test", user="reader", language=language)
    assert get_labels(language)["relevance_evidence"] == heading
    for content in (single, digest):
        visible_text = BeautifulSoup(content["html_content"], "html.parser").get_text(
            " ", strip=True
        )
        assert visible_text.count(heading) == 1
        assert content["html_content"].count(result.relevance_evidence) == 1
        assert heading in content["plain_content"]
        assert previous_heading not in content["html_content"]
    assert f'"relevance_evidence":"{heading}"' in archive
    assert previous_heading not in archive
    assert f"## {heading}\n" in GEvalJudge.render_report_markdown(
        result, sample_update, language=language
    )


@pytest.mark.parametrize("category", list(CATEGORY_TEMPLATES))
def test_report_prompt_uses_category_aware_relevance_without_resource_gate(category: str) -> None:
    prompt = build_report_prompt(
        category=category,
        update_context="An update with verified public documentation.",
        resource_summary="No matching ARM resources in the analyzed scope.",
        task_results_summary="Workload and SDK usage are not established by that inventory.",
        report_language="English",
    )
    prompt = " ".join(prompt.split())
    assert "### Environment Relevance" in prompt
    assert "Resource ownership is not a relevance gate" in prompt
    assert "WHY this update was selected" not in prompt
    assert "not_relevant (none)" not in prompt
    assert "Be liberal with `not_relevant`" not in prompt
    assert "Value first; evaluation is optional" in prompt
    assert "never invent adoption plans" in prompt
    assert "at the analysis time and within the analyzed scope" in prompt
    assert "Lead with the **decision hinge**" in prompt
    assert "condition under which the alternative or keeping the current state is better" in prompt
    assert "evidence that closes the decision" in prompt


@pytest.mark.parametrize("phase", ["planning", "evaluation", "report"])
def test_shared_assessment_does_not_equate_low_impact_with_low_relevance(phase: str) -> None:
    prompt = build_system_prompt(phase=phase, language="ko")
    assert "low=safe to ignore" not in prompt
    assert "Resource ownership is not a relevance gate" in prompt
    assert "The report will correctly mark the update as `not_relevant`" not in PLANNING_PROMPT


@pytest.mark.parametrize("phase", ["planning", "evaluation", "report"])
def test_shared_assessment_requires_csa_decision_brief(phase: str) -> None:
    prompt = " ".join(build_system_prompt(phase=phase, language="ko").split())
    assert "A CSA briefing is a decision memo, not feature education" in prompt
    assert "decision hinge" in prompt
    assert "when the alternative or keeping the current state is better" in prompt
    assert "owning operational responsibility" in prompt
    assert "evidence that closes the decision" in prompt
    assert "Never expose or imitate internal Microsoft sales motions" in prompt


def test_report_system_prompt_uses_one_update_first_opening_rule() -> None:
    prompt = " ".join(build_system_prompt(phase="report", language="ko").split())
    assert "Update-first opening (MANDATORY)" in prompt
    assert "Administrator-first opening" not in prompt
    assert "do not give the environment verdict before naming the subject" in prompt


@pytest.mark.parametrize(
    ("category", "relevance", "evidence"),
    [
        (
            "new_service",
            RelevanceStatus.OPPORTUNITY,
            "수집된 업무 요구는 수동 데이터 정제입니다. 이 서비스는 해당 절차를 자동화할 수 있어 도입 후보입니다.",
        ),
        (
            "new_feature",
            RelevanceStatus.OPPORTUNITY,
            "수집된 설계 요구에 따라 신규 워크로드의 장애 복구를 단순화할 수 있습니다. 도입 시 지원 조건을 검토합니다.",
        ),
        (
            "sdk_tooling",
            RelevanceStatus.RELEVANT,
            "제공된 애플리케이션 코드가 지원 종료 대상 SDK를 사용하므로 패키지를 교체해야 합니다.",
        ),
        (
            "retirement",
            RelevanceStatus.NOT_RELEVANT,
            "분석 당시 조회 범위에는 Azure Databricks 워크스페이스가 없습니다. 이 범위에서 마이그레이션할 대상은 없습니다.",
        ),
    ],
)
def test_non_resource_rationale_is_not_penalized_for_missing_names_or_counts(
    category: str,
    relevance: RelevanceStatus,
    evidence: str,
    sample_update: AzureUpdate,
    sample_analysis_result: AnalysisResult,
) -> None:
    result = sample_analysis_result.model_copy(
        update={
            "update_category": category,
            "relevance": relevance,
            "relevance_evidence": evidence,
            "affected_resources": [],
            "action_items": [],
            "recommendations": [],
        }
    )
    report = ReportQualityEvaluator().evaluate(result, sample_update)
    items = {item.name: item for item in report.items}
    for key in ("relevance_classification", "relevance_evidence", "affected_resources"):
        assert items[key].score == items[key].max_score, items[key].deductions
    if relevance != RelevanceStatus.RELEVANT:
        for key in ("action_items_presence", "action_items_quality"):
            assert items[key].score == items[key].max_score
    assert report.max_score == 100


def test_missing_rationale_still_fails_even_for_opportunity(
    sample_update: AzureUpdate, sample_analysis_result: AnalysisResult
) -> None:
    result = sample_analysis_result.model_copy(
        update={"relevance_evidence": "", "affected_resources": []}
    )
    report = ReportQualityEvaluator().evaluate(result, sample_update)
    item = next(item for item in report.items if item.name == "relevance_classification")
    assert item.score < item.max_score


def test_non_applicable_classification_cannot_hide_affected_resources(
    sample_update: AzureUpdate, sample_analysis_result: AnalysisResult
) -> None:
    result = sample_analysis_result.model_copy(
        update={
            "relevance": RelevanceStatus.NOT_RELEVANT,
            "relevance_evidence": "This change does not apply, despite the listed affected account.",
        }
    )
    report = ReportQualityEvaluator().evaluate(result, sample_update)
    item = next(item for item in report.items if item.name == "relevance_classification")
    assert item.score < item.max_score


def test_semantic_judge_checks_value_and_absence_claims_separately() -> None:
    dimensions = {dimension.key: dimension for dimension in DIMENSIONS}
    assert "Value first" in dimensions["actionability"].edge_cases
    assert "empty ARM inventory" in dimensions["faithfulness"].edge_cases
    assert "adoption plans" in dimensions["faithfulness"].edge_cases


def test_archive_retains_original_evidence_and_schema(
    sample_analysis_result: AnalysisResult,
) -> None:
    original_text = "현재 환경에 Azure Databricks 워크스페이스 리소스가 없어 직접 영향이 없습니다."
    payload = sample_analysis_result.model_dump(
        mode="json", exclude={"job_relevance", "visual_assets"}
    )
    payload["relevance_evidence"] = original_text
    archived = ArchiveAnalysisResultV1.model_validate(payload)
    restored = ArchiveAnalysisResultV1.model_validate_json(archived.model_dump_json())
    assert restored.relevance_evidence == original_text
    assert "job_relevance" not in restored.model_dump()
    assert "environment_relevance" not in restored.model_dump()


@pytest.mark.asyncio
@pytest.mark.parametrize("language", ["ko", "en"])
async def test_no_resources_does_not_skip_subscriber_role_assessment(
    language: str, sample_update: AzureUpdate, sample_analysis_result: AnalysisResult
) -> None:
    evidence = "분석 시점의 조회 범위에서 종료 대상 워크스페이스를 확인하지 못했습니다."
    result = sample_analysis_result.model_copy(
        update={
            "relevance": RelevanceStatus.NOT_RELEVANT,
            "should_notify": False,
            "affected_resources": [],
            "action_items": [],
            "relevance_evidence": evidence,
        }
    )
    analyzer = AzureUpdateAnalyzer.__new__(AzureUpdateAnalyzer)
    analyzer.settings = SimpleNamespace(report_language="ko", action_verification_enabled=False)
    analyzer._llm_circuit_breaker = CircuitBreaker()
    analyzer.llm_report_writer = SimpleNamespace(
        ainvoke=AsyncMock(
            return_value=AIMessage(
                content=json.dumps({"subscriber_relevance": "send", "job_relevance": "high"})
            )
        )
    )
    subscriber = Subscriber(
        name="Reader",
        email="reader@example.com",
        role="Data platform architecture and service lifecycle planning",
        language=language,
        focus_services=["Azure Databricks"],
    )
    tailored = await analyzer.customize_for_subscriber(result, subscriber, sample_update)

    analyzer.llm_report_writer.ainvoke.assert_awaited_once()
    messages = analyzer.llm_report_writer.ainvoke.call_args.args[0]
    assert json.dumps(evidence, ensure_ascii=False) in messages[1].content
    assert tailored.job_relevance == "high"
    assert tailored.relevance == RelevanceStatus.NOT_RELEVANT
    assert tailored.affected_resources == []
    assert result.job_relevance == ""
