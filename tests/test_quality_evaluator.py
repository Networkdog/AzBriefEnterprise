"""Tests for the report quality evaluator."""

from datetime import datetime

import pytest

from scripts.evaluate_report import QualityReport, ReportQualityEvaluator
from src.agent.analyzer import (
    ActionItem,
    AnalysisResult,
    ImpactSummary,
    RelevanceStatus,
    UrgencyLevel,
)
from src.rss.parser import AzureUpdate


@pytest.fixture
def sample_update():
    return AzureUpdate(
        id="test-update-001",
        title="Generally Available: TLS 1.0/1.1 retirement for Azure Storage",
        description="Azure Storage will block TLS 1.0 and 1.1 connections starting October 31, 2024.",
        link="https://azure.microsoft.com/updates/tls-retirement",
        published_date=datetime(2024, 6, 15),
        categories=["Storage"],
        azure_services=["Storage Accounts"],
        update_type="Retirement",
        status="Launched",
    )


@pytest.fixture
def high_quality_result():
    """A well-formed AnalysisResult that should score high."""
    return AnalysisResult(
        update_id="test-update-001",
        update_title="TLS 1.0/1.1 retirement for Azure Storage",
        update_category="retirement",
        urgency=UrgencyLevel.HIGH,
        relevance=RelevanceStatus.RELEVANT,
        one_line_summary="Storage Account TLS 1.0/1.1 차단 — 3개 계정 마이그레이션 필요",
        relevance_evidence="현재 환경에서 Storage Account 22개 중 3개(sthottierpoc, config1748, alertbotst)가 TLS 1.0을 사용하고 있어 이 업데이트에 해당합니다.",
        relevance_reason=(
            "**Azure Storage**의 TLS 1.0 및 1.1 지원이 2024년 10월 31일에 종료됩니다. "
            "이후 해당 프로토콜을 사용하는 연결은 차단됩니다.\n\n"
            "> **TLS (Transport Layer Security)**: 데이터 전송 시 암호화를 제공하는 프로토콜입니다. "
            "TLS 1.2 이상이 현재 산업 표준이며, 이전 버전에는 알려진 취약점이 있습니다.\n\n"
            "이번 변경은 보안 강화를 위한 것으로, TLS 1.0/1.1의 알려진 취약점(BEAST, POODLE 등)을 "
            "제거하기 위한 업계 전반의 움직임과 일치합니다.\n\n"
            "> **minimumTlsVersion**: Storage Account의 최소 TLS 버전을 설정하는 속성입니다. "
            "Azure Portal의 Configuration 섹션에서 변경할 수 있습니다.\n\n"
            "현재 환경의 Storage Account를 **minimumTlsVersion** 속성 기준으로 평가했습니다. "
            "대부분은 이미 TLS 1.2를 사용하고 있으나 일부 계정이 이전 버전을 사용 중입니다.\n\n"
            "TLS 버전 확인 후 클라이언트 호환성을 검증하고, 이상이 없으면 변경을 진행합니다.\n\n"
            "변경을 완료하면 보안 감사에서 TLS 관련 지적 사항이 해소됩니다. "
            "2024년 10월 31일 이후에는 TLS 1.0/1.1 연결이 차단되므로 기한 내에 완료해야 합니다."
        ),
        affected_resources=[
            {
                "name": "sthottierpoc",
                "type": "microsoft.storage/storageaccounts",
                "resourceGroup": "rg-storage",
                "subscription": "dev-subscription",
                "reason": "minimumTlsVersion: TLS1_0 — 2024-10-31 이후 차단 예정",
                "action_required": True,
            },
            {
                "name": "config1748",
                "type": "microsoft.storage/storageaccounts",
                "resourceGroup": "rg-config",
                "subscription": "dev-subscription",
                "reason": "minimumTlsVersion: TLS1_0 — 2024-10-31 이후 차단 예정",
                "action_required": True,
            },
            {
                "name": "alertbotst",
                "type": "microsoft.storage/storageaccounts",
                "resourceGroup": "rg-alertbot",
                "subscription": "prod-subscription",
                "reason": "minimumTlsVersion: TLS1_1 — 2024-10-31 이후 차단 예정",
                "action_required": True,
            },
        ],
        impact_summary="TLS 1.0/1.1 연결 차단으로 3개 Storage Account에 영향",
        impact_details=ImpactSummary(
            security_impact="TLS 1.0/1.1 차단으로 보안 취약점 해소",
            operational_impact="3개 Storage Account의 TLS 버전 변경 작업 필요",
        ),
        action_items=[
            ActionItem(
                step=1,
                urgency="high",
                task="클라이언트 애플리케이션의 TLS 1.2 호환성을 확인합니다",
                why="TLS 버전 변경 전 클라이언트 연결이 중단되지 않도록 사전 검증이 필요합니다.",
                target_resources=["sthottierpoc", "config1748", "alertbotst"],
                procedure="Azure Portal > Storage Account > Monitoring > Metrics > Transactions > API version별 필터링으로 TLS 1.0/1.1 사용 현황 확인",
                estimated_time="15분",
                risk_if_not_done="클라이언트 호환성 미확인 시 TLS 변경 후 서비스 중단 가능",
                precaution="운영 시간 외에 테스트 환경에서 먼저 검증할 것을 권장합니다.",
            ),
            ActionItem(
                step=2,
                urgency="high",
                task="3개 Storage Account의 TLS 최소 버전을 1.2로 변경합니다",
                why="2024-10-31 이후 TLS 1.0/1.1 연결이 차단되므로 사전에 변경해야 합니다.",
                target_resources=["sthottierpoc", "config1748", "alertbotst"],
                procedure="Azure Portal > Storage Account > Settings > Configuration > Minimum TLS version > TLS 1.2 선택 > Save",
                cli_command="az storage account update --name <name> --min-tls-version TLS1_2",
                estimated_time="5분/계정",
                deadline="2024-10-31 (retirement date from update)",
                risk_if_not_done="기한 초과 시 서비스 연결 장애 발생",
                rollback="동일 경로에서 TLS 최소 버전을 TLS1_0으로 되돌릴 수 있습니다.",
            ),
        ],
        recommendations=[],
        reference_docs=[
            {
                "title": "Azure Storage TLS retirement",
                "url": "https://learn.microsoft.com/azure/storage/common/transport-layer-security",
                "related_content": "TLS 1.0/1.1 retirement timeline and migration guide",
            },
        ],
        additional_checks=[
            "Private Endpoint를 통한 연결에서도 TLS 1.2 이상을 사용하는지 확인이 필요합니다.",
        ],
        should_notify=True,
    )


@pytest.fixture
def low_quality_result():
    """A poorly-formed AnalysisResult that should score low."""
    return AnalysisResult(
        update_id="test-update-002",
        update_title="Some update",
        urgency=UrgencyLevel.MEDIUM,
        relevance=RelevanceStatus.RELEVANT,
        one_line_summary="A new feature has been released",
        relevance_evidence="",
        relevance_reason="This is a short analysis.",
        affected_resources=[],
        impact_summary="Some impact",
        action_items=[],
        recommendations=[],
        reference_docs=[],
        should_notify=True,
    )


@pytest.fixture
def evaluator():
    return ReportQualityEvaluator()


class TestQualityScoring:
    """Test quality scoring across dimensions."""

    def test_high_quality_report_scores_high(self, evaluator, high_quality_result, sample_update):
        qr = evaluator.evaluate(high_quality_result, sample_update, language="ko")
        assert (
            qr.percentage >= 80
        ), f"High quality report scored {qr.percentage:.1f}%, expected >= 80%"
        assert qr.grade in ("S", "A+", "A", "B+"), f"Expected grade A or better, got {qr.grade}"

    def test_low_quality_report_scores_low(self, evaluator, low_quality_result, sample_update):
        qr = evaluator.evaluate(low_quality_result, sample_update, language="ko")
        assert qr.percentage < 80, f"Low quality report scored {qr.percentage:.1f}%, expected < 80%"
        assert qr.grade not in (
            "S",
            "A+",
            "A",
        ), f"Low quality should not get A grade, got {qr.grade}"

    def test_quality_report_has_all_categories(self, evaluator, high_quality_result, sample_update):
        qr = evaluator.evaluate(high_quality_result, sample_update, language="ko")
        expected_cats = {
            "content_accuracy",
            "structure",
            "language",
            "actionability",
            "scannability",
        }
        actual_cats = set(qr.category_scores.keys())
        assert expected_cats == actual_cats, f"Missing categories: {expected_cats - actual_cats}"

    def test_max_score_is_100(self, evaluator, high_quality_result, sample_update):
        qr = evaluator.evaluate(high_quality_result, sample_update, language="ko")
        assert qr.max_score == 100, f"Max score should be 100, got {qr.max_score}"

    def test_content_accuracy_category(self, evaluator, high_quality_result, sample_update):
        qr = evaluator.evaluate(high_quality_result, sample_update, language="ko")
        cat = qr.category_scores.get("content_accuracy", {})
        assert cat["max"] == 25, f"Content accuracy max should be 25, got {cat['max']}"
        assert cat["score"] >= 20, f"High quality content should score >= 20, got {cat['score']}"

    def test_relevance_mismatch_penalized(self, evaluator, sample_update):
        """Relevant but no affected resources → penalized."""
        result = AnalysisResult(
            update_id="test",
            update_title="Test",
            urgency=UrgencyLevel.MEDIUM,
            relevance=RelevanceStatus.RELEVANT,
            one_line_summary="Test summary with specific details and 3 resources",
            relevance_evidence="Test evidence with 3 resources found",
            relevance_reason="Some analysis text " * 50,
            affected_resources=[],  # Mismatch: relevant but no resources
            impact_summary="",
            action_items=[],
            recommendations=[],
            reference_docs=[{"title": "Doc", "url": "https://learn.microsoft.com/test"}],
            should_notify=True,
        )
        qr = evaluator.evaluate(result, sample_update, language="ko")
        # Find relevance classification item
        rel_item = next(i for i in qr.items if i.name == "relevance_classification")
        assert rel_item.score < rel_item.max_score, "Relevance mismatch should be penalized"

    def test_fabricated_deadline_penalized(self, evaluator, sample_update):
        result = AnalysisResult(
            update_id="test",
            update_title="Test",
            urgency=UrgencyLevel.HIGH,
            relevance=RelevanceStatus.RELEVANT,
            one_line_summary="Test summary with 5 resources affected by change",
            relevance_evidence="5 resources found in environment",
            relevance_reason="Analysis " * 100,
            affected_resources=[{"name": "r1", "type": "t1", "reason": "prop: val — reason"}],
            impact_summary="",
            action_items=[
                ActionItem(
                    step=1,
                    task="Do something",
                    why="Important",
                    deadline="within 2 weeks",  # Fabricated!
                    procedure="Some procedure",
                ),
            ],
            recommendations=[],
            reference_docs=[{"title": "Doc", "url": "https://learn.microsoft.com/test"}],
            should_notify=True,
        )
        qr = evaluator.evaluate(result, sample_update, language="ko")
        date_item = next(i for i in qr.items if i.name == "no_fabricated_dates")
        assert date_item.score < date_item.max_score, "Fabricated deadline should be penalized"

    def test_korean_translation_pattern_penalized(self, evaluator, sample_update):
        result = AnalysisResult(
            update_id="test",
            update_title="Test",
            urgency=UrgencyLevel.MEDIUM,
            relevance=RelevanceStatus.NOT_RELEVANT,
            one_line_summary="Test summary about specific service change",
            relevance_evidence="현재 환경에 관련 리소스가 0개입니다",
            relevance_reason=(
                "이번 업데이트는 TLS 1.0을 사용하는 모든 리소스에 영향을 미치는 변경한 내용입니다. "
                "마이그레이션을 수행하는 것을 권장합니다. "
                "이 변경에 의해 기존 연결이 중단됩니다.\n\n"
                "> **TLS**: Transport Layer Security입니다.\n\n"
                "> **Storage Account**: Azure의 스토리지 서비스입니다."
            ),
            affected_resources=[],
            impact_summary="",
            action_items=[],
            recommendations=[],
            reference_docs=[{"title": "Doc", "url": "https://learn.microsoft.com/test"}],
            should_notify=False,
        )
        qr = evaluator.evaluate(result, sample_update, language="ko")
        trans_item = next(i for i in qr.items if i.name == "translation_avoidance")
        assert trans_item.score < trans_item.max_score, "Translation patterns should be penalized"
        assert (
            len(trans_item.deductions) >= 2
        ), f"Expected 2+ deductions, got {len(trans_item.deductions)}"

    def test_korean_causative_pattern_penalized(self, evaluator, sample_update):
        """'~할 수 있게 합니다' conflates the enabler (update) with the actor (admin)."""
        result = AnalysisResult(
            update_id="test",
            update_title="Test",
            urgency=UrgencyLevel.MEDIUM,
            relevance=RelevanceStatus.NOT_RELEVANT,
            one_line_summary="Test summary about specific service change",
            relevance_evidence="현재 환경에 관련 리소스가 0개입니다",
            relevance_reason=(
                "이번 GA는 Azure Virtual Network routing appliance를 VNet 내부에 배치해, "
                "VM 기반 포워딩 계층 없이도 라우팅 트래픽을 고대역폭으로 전달할 수 있게 합니다."
            ),
            affected_resources=[],
            impact_summary="",
            action_items=[],
            recommendations=[],
            reference_docs=[{"title": "Doc", "url": "https://learn.microsoft.com/test"}],
            should_notify=False,
        )
        qr = evaluator.evaluate(result, sample_update, language="ko")
        trans_item = next(i for i in qr.items if i.name == "translation_avoidance")
        assert any("사역형" in d for d in trans_item.deductions), trans_item.deductions

    def test_korean_natural_rewrite_not_penalized(self, evaluator, sample_update):
        """The recommended rewrite must not trip the causative check."""
        result = AnalysisResult(
            update_id="test",
            update_title="Test",
            urgency=UrgencyLevel.MEDIUM,
            relevance=RelevanceStatus.NOT_RELEVANT,
            one_line_summary="Test summary about specific service change",
            relevance_evidence="현재 환경에 관련 리소스가 0개입니다",
            relevance_reason=(
                "이제 Azure Virtual Network routing appliance를 VNet 안에 배치할 수 있습니다. "
                "VM 기반 포워딩 계층 없이도 라우팅 트래픽이 고대역폭으로 처리됩니다."
            ),
            affected_resources=[],
            impact_summary="",
            action_items=[],
            recommendations=[],
            reference_docs=[{"title": "Doc", "url": "https://learn.microsoft.com/test"}],
            should_notify=False,
        )
        qr = evaluator.evaluate(result, sample_update, language="ko")
        trans_item = next(i for i in qr.items if i.name == "translation_avoidance")
        assert not any("사역형" in d for d in trans_item.deductions), trans_item.deductions

    def test_korean_release_stage_predicate_penalized(self, evaluator, sample_update):
        """The release stage belongs in a '~로' phrase, not in the predicate."""
        result = AnalysisResult(
            update_id="test",
            update_title="Test",
            urgency=UrgencyLevel.MEDIUM,
            relevance=RelevanceStatus.NOT_RELEVANT,
            one_line_summary="Test summary about specific service change",
            relevance_evidence="현재 환경에 관련 리소스가 0개입니다",
            relevance_reason=(
                "이번 업데이트는 Azure SQL Database의 Dynamic Data Masking에 "
                "정규식 기반 마스킹을 추가하는 public preview입니다."
            ),
            affected_resources=[],
            impact_summary="",
            action_items=[],
            recommendations=[],
            reference_docs=[{"title": "Doc", "url": "https://learn.microsoft.com/test"}],
            should_notify=False,
        )
        qr = evaluator.evaluate(result, sample_update, language="ko")
        trans_item = next(i for i in qr.items if i.name == "translation_avoidance")
        assert any("출시 단계" in d for d in trans_item.deductions), trans_item.deductions

    def test_korean_release_stage_rewrite_not_penalized(self, evaluator, sample_update):
        """The recommended rewrite must not trip the release-stage check."""
        result = AnalysisResult(
            update_id="test",
            update_title="Test",
            urgency=UrgencyLevel.MEDIUM,
            relevance=RelevanceStatus.NOT_RELEVANT,
            one_line_summary="Test summary about specific service change",
            relevance_evidence="현재 환경에 관련 리소스가 0개입니다",
            relevance_reason=(
                "Azure SQL Database의 DDM(Dynamic Data Masking)에 정규식 기반 마스킹 기능이 "
                "public preview로 추가되었습니다."
            ),
            affected_resources=[],
            impact_summary="",
            action_items=[],
            recommendations=[],
            reference_docs=[{"title": "Doc", "url": "https://learn.microsoft.com/test"}],
            should_notify=False,
        )
        qr = evaluator.evaluate(result, sample_update, language="ko")
        trans_item = next(i for i in qr.items if i.name == "translation_avoidance")
        assert not any("출시 단계" in d for d in trans_item.deductions), trans_item.deductions

    def test_korean_classifier_predicate_penalized(self, evaluator, sample_update):
        """'이번 preview는 … 기능입니다' / '이번 GA는 … 변화입니다' are the same defect."""
        for bad in (
            "이번 preview는 NSP 간에 신뢰 관계를 만들어 통신할 수 있게 하는 Perimeter link 기능입니다.",
            "이번 GA는 PowerShell 런타임을 최신 버전으로 올린 변화입니다.",
        ):
            result = AnalysisResult(
                update_id="test",
                update_title="Test",
                urgency=UrgencyLevel.MEDIUM,
                relevance=RelevanceStatus.NOT_RELEVANT,
                one_line_summary="Test summary about specific service change",
                relevance_evidence="현재 환경에 관련 리소스가 0개입니다",
                relevance_reason=bad,
                affected_resources=[],
                impact_summary="",
                action_items=[],
                recommendations=[],
                reference_docs=[{"title": "Doc", "url": "https://learn.microsoft.com/test"}],
                should_notify=False,
            )
            qr = evaluator.evaluate(result, sample_update, language="ko")
            trans_item = next(i for i in qr.items if i.name == "translation_avoidance")
            assert any("출시 단계" in d for d in trans_item.deductions), (
                bad,
                trans_item.deductions,
            )

    def test_korean_announcement_frame_penalized(self, evaluator, sample_update):
        """The report must describe what changed, not the announcement that carried it."""
        cases = [
            (
                "이번 공지는 Nested confidential(cc_v5) VM 시리즈가 "
                "2026년 9월 1일에 은퇴한다는 내용입니다.",
                "내용/공지입니다",
            ),
            (
                "이번 공지는 External Data Import를 2026년 9월 30일에 종료하는 공지입니다.",
                "내용/공지입니다",
            ),
            (
                "이번 GA로 Azure Firewall explicit proxy를 정식으로 사용할 수 있습니다.",
                "원인 부사구",
            ),
            (
                "cc_v5 시리즈는 2026년 9월 1일에 은퇴합니다.",
                "은퇴",
            ),
        ]
        for bad, marker in cases:
            result = AnalysisResult(
                update_id="test",
                update_title="Test",
                urgency=UrgencyLevel.MEDIUM,
                relevance=RelevanceStatus.NOT_RELEVANT,
                one_line_summary="Test summary about specific service change",
                relevance_evidence="현재 환경에 관련 리소스가 0개입니다",
                relevance_reason=bad,
                affected_resources=[],
                impact_summary="",
                action_items=[],
                recommendations=[],
                reference_docs=[{"title": "Doc", "url": "https://learn.microsoft.com/test"}],
                should_notify=False,
            )
            qr = evaluator.evaluate(result, sample_update, language="ko")
            trans_item = next(i for i in qr.items if i.name == "translation_avoidance")
            assert any(marker in d for d in trans_item.deductions), (bad, trans_item.deductions)

    def test_korean_announcement_frame_rewrite_not_penalized(self, evaluator, sample_update):
        """The recommended rewrites start from a time or the changed thing — not the announcement."""
        markers = ("내용/공지입니다", "원인 부사구", "은퇴")
        for good in (
            "2026년 9월 1일부터 Nested confidential(cc_v5) VM 시리즈의 제공이 종료됩니다.",
            "이제 Azure Firewall explicit proxy를 public preview로 사용할 수 있게 되었습니다.",
        ):
            result = AnalysisResult(
                update_id="test",
                update_title="Test",
                urgency=UrgencyLevel.MEDIUM,
                relevance=RelevanceStatus.NOT_RELEVANT,
                one_line_summary="Test summary about specific service change",
                relevance_evidence="현재 환경에 관련 리소스가 0개입니다",
                relevance_reason=good,
                affected_resources=[],
                impact_summary="",
                action_items=[],
                recommendations=[],
                reference_docs=[{"title": "Doc", "url": "https://learn.microsoft.com/test"}],
                should_notify=False,
            )
            qr = evaluator.evaluate(result, sample_update, language="ko")
            trans_item = next(i for i in qr.items if i.name == "translation_avoidance")
            assert not any(m in d for d in trans_item.deductions for m in markers), (
                good,
                trans_item.deductions,
            )

    def test_korean_english_verb_stem_penalized(self, evaluator, sample_update):
        """English tokens are nouns in Korean — the predicate must be a Korean verb."""
        for bad in (
            "Azure Databricks의 Lakeflow Connect에서 SharePoint 커넥터가 GA되었습니다.",
            "AV36P 노드는 2029년 6월 30일에 retire됩니다.",
        ):
            result = AnalysisResult(
                update_id="test",
                update_title="Test",
                urgency=UrgencyLevel.MEDIUM,
                relevance=RelevanceStatus.NOT_RELEVANT,
                one_line_summary="Test summary about specific service change",
                relevance_evidence="현재 환경에 관련 리소스가 0개입니다",
                relevance_reason=bad,
                affected_resources=[],
                impact_summary="",
                action_items=[],
                recommendations=[],
                reference_docs=[{"title": "Doc", "url": "https://learn.microsoft.com/test"}],
                should_notify=False,
            )
            qr = evaluator.evaluate(result, sample_update, language="ko")
            trans_item = next(i for i in qr.items if i.name == "translation_avoidance")
            assert any("동사 어간" in d for d in trans_item.deductions), (
                bad,
                trans_item.deductions,
            )

    def test_korean_english_noun_use_not_penalized(self, evaluator, sample_update):
        """Only the verb-stem use is banned; English terms as nouns stay fine."""
        for good in (
            "Azure Databricks의 Lakeflow Connect에서 SharePoint 커넥터가 정식 출시되었습니다.",
            "AV36P 노드는 2029년 6월 30일부터 지원이 종료됩니다.",
            "이번 GA의 핵심은 구성 자동화와 운영 단순화입니다.",
            "Storage Account 3개의 TLS 버전이 1.2로 변경되었습니다.",
        ):
            result = AnalysisResult(
                update_id="test",
                update_title="Test",
                urgency=UrgencyLevel.MEDIUM,
                relevance=RelevanceStatus.NOT_RELEVANT,
                one_line_summary="Test summary about specific service change",
                relevance_evidence="현재 환경에 관련 리소스가 0개입니다",
                relevance_reason=good,
                affected_resources=[],
                impact_summary="",
                action_items=[],
                recommendations=[],
                reference_docs=[{"title": "Doc", "url": "https://learn.microsoft.com/test"}],
                should_notify=False,
            )
            qr = evaluator.evaluate(result, sample_update, language="ko")
            trans_item = next(i for i in qr.items if i.name == "translation_avoidance")
            assert not any("동사 어간" in d for d in trans_item.deductions), (
                good,
                trans_item.deductions,
            )

    def test_korean_nominalized_predicates_penalized(self, evaluator, sample_update):
        """'~다는 점입니다 / ~는 방식입니다' hide the verb inside a noun."""
        result = AnalysisResult(
            update_id="test",
            update_title="Test",
            urgency=UrgencyLevel.MEDIUM,
            relevance=RelevanceStatus.NOT_RELEVANT,
            one_line_summary="Test summary about specific service change",
            relevance_evidence="현재 환경에 관련 리소스가 0개입니다",
            relevance_reason=(
                "이번 업데이트의 핵심은 Unity AI Gateway를 정식 출시했다는 점입니다. "
                "이 기능으로 달라지는 지점은 접근 제어 모델을 따로 만들지 않아도 된다는 점입니다. "
                "explicit proxy는 브라우저가 프라이빗 IP로 트래픽을 보내는 방식입니다."
            ),
            affected_resources=[],
            impact_summary="",
            action_items=[],
            recommendations=[],
            reference_docs=[{"title": "Doc", "url": "https://learn.microsoft.com/test"}],
            should_notify=False,
        )
        qr = evaluator.evaluate(result, sample_update, language="ko")
        trans_item = next(i for i in qr.items if i.name == "translation_avoidance")
        assert any("명사화 종결" in d for d in trans_item.deductions), trans_item.deductions

    def test_korean_concept_box_definitions_not_penalized(self, evaluator, sample_update):
        """Concept boxes must end with ~입니다 — the net must not flag them."""
        result = AnalysisResult(
            update_id="test",
            update_title="Test",
            urgency=UrgencyLevel.MEDIUM,
            relevance=RelevanceStatus.NOT_RELEVANT,
            one_line_summary="Test summary about specific service change",
            relevance_evidence="현재 환경에 관련 리소스가 0개입니다",
            relevance_reason=(
                "Azure Databricks가 Unity AI Gateway를 정식 출시했습니다.\n\n"
                "> **Lakeflow Connect**: 데이터를 수집하는 연결 기능입니다.\n\n"
                "> **Unity Catalog**: 권한을 한곳에서 관리하는 거버넌스 계층입니다.\n\n"
                "> **Explicit proxy**: 프록시 주소를 직접 지정해 트래픽을 보내는 방식입니다.\n\n"
                "이제 접근 제어 모델을 AI 전용으로 따로 만들지 않아도 됩니다."
            ),
            affected_resources=[],
            impact_summary="",
            action_items=[],
            recommendations=[],
            reference_docs=[{"title": "Doc", "url": "https://learn.microsoft.com/test"}],
            should_notify=False,
        )
        qr = evaluator.evaluate(result, sample_update, language="ko")
        trans_item = next(i for i in qr.items if i.name == "translation_avoidance")
        assert not any("명사화 종결" in d for d in trans_item.deductions), trans_item.deductions

    def test_korean_announcement_opening_penalized(self, evaluator, sample_update):
        """The report must not open with '이번 업데이트는…' — it reads as boilerplate."""
        for bad in (
            "이번 업데이트는 Azure SQL Database에 정규식 마스킹을 추가합니다.",
            "이번 공지는 cc_v5 시리즈의 종료 일정을 안내합니다.",
            "**이번 GA**는 Azure Firewall explicit proxy를 정식 출시했습니다.",
            "이번 업데이트의 핵심은 Unity AI Gateway 출시입니다.",
            "이번 업데이트로 Azure Firewall explicit proxy가 정식 출시되었습니다.",
        ):
            result = AnalysisResult(
                update_id="test",
                update_title="Test",
                urgency=UrgencyLevel.MEDIUM,
                relevance=RelevanceStatus.NOT_RELEVANT,
                one_line_summary="Test summary about specific service change",
                relevance_evidence="현재 환경에 관련 리소스가 0개입니다",
                relevance_reason=bad,
                affected_resources=[],
                impact_summary="",
                action_items=[],
                recommendations=[],
                reference_docs=[{"title": "Doc", "url": "https://learn.microsoft.com/test"}],
                should_notify=False,
            )
            qr = evaluator.evaluate(result, sample_update, language="ko")
            trans_item = next(i for i in qr.items if i.name == "translation_avoidance")
            assert any("서두가 공지 프레임" in d for d in trans_item.deductions), (
                bad,
                trans_item.deductions,
            )

    def test_korean_fact_first_opening_not_penalized(self, evaluator, sample_update):
        """Openings that start from the change or a time adverb must pass."""
        for good in (
            "Azure SQL Database의 DDM에 정규식 기반 마스킹 기능이 public preview로 추가되었습니다.",
            "2026년 9월 1일부터 cc_v5 VM 시리즈의 제공이 종료됩니다.",
            "이제 Azure Firewall explicit proxy를 public preview로 사용할 수 있게 되었습니다.",
            "현재 환경의 Storage Account 22개 중 3개가 TLS 1.0을 허용합니다.",
            "이 기능은 보안 경계를 강화합니다.",
        ):
            result = AnalysisResult(
                update_id="test",
                update_title="Test",
                urgency=UrgencyLevel.MEDIUM,
                relevance=RelevanceStatus.NOT_RELEVANT,
                one_line_summary="Test summary about specific service change",
                relevance_evidence="현재 환경에 관련 리소스가 0개입니다",
                relevance_reason=good,
                affected_resources=[],
                impact_summary="",
                action_items=[],
                recommendations=[],
                reference_docs=[{"title": "Doc", "url": "https://learn.microsoft.com/test"}],
                should_notify=False,
            )
            qr = evaluator.evaluate(result, sample_update, language="ko")
            trans_item = next(i for i in qr.items if i.name == "translation_avoidance")
            assert not any("서두가" in d for d in trans_item.deductions), (
                good,
                trans_item.deductions,
            )

    def test_korean_environment_verdict_opening_penalized(self, evaluator, sample_update):
        """The body must explain the update before ruling on whether it applies."""
        for bad in (
            "현재 환경에는 이 기능을 적용할 **ExpressRoute virtual network gateway**가 없습니다.",
            "현재 환경에는 즉시 조치할 항목이 없습니다.",
            "현재 환경에서는 **즉시 변경할 설정이 없습니다**.",
            "현재 환경 기준으로는 즉시 조치할 항목이 없습니다.",
            "이 구독에는 해당 서비스가 없어 적용 대상이 아닙니다.",
        ):
            result = AnalysisResult(
                update_id="test",
                update_title="Test",
                urgency=UrgencyLevel.MEDIUM,
                relevance=RelevanceStatus.NOT_RELEVANT,
                one_line_summary="Test summary about specific service change",
                relevance_evidence="현재 환경에 관련 리소스가 0개입니다",
                relevance_reason=bad,
                affected_resources=[],
                impact_summary="",
                action_items=[],
                recommendations=[],
                reference_docs=[{"title": "Doc", "url": "https://learn.microsoft.com/test"}],
                should_notify=False,
            )
            qr = evaluator.evaluate(result, sample_update, language="ko")
            trans_item = next(i for i in qr.items if i.name == "translation_avoidance")
            assert any("서두가 환경 판정" in d for d in trans_item.deductions), (
                bad,
                trans_item.deductions,
            )

    def test_improvement_suggestions_generated(self, evaluator, low_quality_result, sample_update):
        qr = evaluator.evaluate(low_quality_result, sample_update, language="ko")
        assert (
            len(qr.improvement_suggestions) > 0
        ), "Low quality should generate improvement suggestions"

    def test_grade_boundaries(self, evaluator):
        """Test grade assignment boundaries."""
        qr = QualityReport()
        qr.items = [ScoreItem("test", "test", 100, 96)]
        qr.calculate()
        assert qr.grade == "S"

        qr.items = [ScoreItem("test", "test", 100, 91)]
        qr.calculate()
        assert qr.grade == "A+"

        qr.items = [ScoreItem("test", "test", 100, 50)]
        qr.calculate()
        assert qr.grade == "D"


# Import ScoreItem for grade test
from scripts.evaluate_report import ScoreItem
