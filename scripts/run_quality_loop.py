#!/usr/bin/env python
"""
AzBrief 보고서 품질 반복 개선 데모 (모의 데이터 사용)

Azure API 없이 모의 AnalysisResult를 생성하여:
1. 보고서 생성 (모의) → 품질 평가 → 문제 식별
2. 문제 수정한 보고서 → 재평가 → 점수 비교
3. 추가 개선 → 재평가 → 최종 점수

Usage:
    python -m scripts.run_quality_loop
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.evaluate_report import (
    QualityReport,
    ReportQualityEvaluator,
    _build_feedback_prompt,
    _print_quality_report,
)
from src.agent.analyzer import (
    ActionItem,
    AnalysisResult,
    ImpactSummary,
    RelevanceStatus,
    UrgencyLevel,
)
from src.email.service import EmailService
from src.rss.parser import AzureUpdate

# ============================================================================
# 모의 Azure Update
# ============================================================================
MOCK_UPDATE = AzureUpdate(
    id="update-2026-04-15-aks-ubuntu-2204",
    title="Retirement: AKS Ubuntu 22.04 node image support ending July 2026",
    description=(
        "Azure Kubernetes Service (AKS) will end support for Ubuntu 22.04 "
        "node images on July 31, 2026. All node pools using Ubuntu 22.04 must "
        "migrate to Ubuntu 24.04 (Noble Numbat) before this date. Node pools "
        "still running Ubuntu 22.04 after the deadline will not receive "
        "security patches or bug fixes."
    ),
    link="https://azure.microsoft.com/updates/aks-ubuntu-2204-retirement",
    published_date=datetime(2026, 4, 15),
    categories=["Containers"],
    azure_services=["Azure Kubernetes Service (AKS)"],
    update_type="Retirement",
    status="Launched",
)


# ============================================================================
# Iteration 1: 초기 품질 (의도적으로 문제가 있는 보고서)
# ============================================================================
def make_iteration1_report() -> AnalysisResult:
    """Iteration 1: 여러 품질 문제가 있는 초기 보고서."""
    return AnalysisResult(
        update_id="update-2026-04-15-aks-ubuntu-2204",
        update_title="AKS Ubuntu 22.04 retirement",
        update_category="new_feature",  # ❌ 잘못된 카테고리 (retirement인데 new_feature)
        urgency=UrgencyLevel.MEDIUM,  # ❌ retirement인데 medium
        relevance=RelevanceStatus.RELEVANT,
        one_line_summary="A new feature has been released",  # ❌ 모호하고 부정확
        relevance_evidence="",  # ❌ 비어있음
        relevance_reason=(
            "이번 업데이트는 AKS Ubuntu 22.04 노드 이미지 지원이 종료된다는 내용입니다. "  # ❌ "~한 내용입니다" 번역체
            "Resource Graph 쿼리 결과에 의하면 1개의 클러스터가 있습니다. "  # ❌ 내부 프로세스 노출
            "마이그레이션을 수행하는 것을 권장합니다. "  # ❌ "~하는 것을 권장" 번역체
            "이 변경에 의해 기존 노드 풀이 영향을 받습니다. "  # ❌ "~에 의해" 번역체
            "업그레이드하는 것이 필요합니다. "  # ❌ "~하는 것이 필요" 번역체
            "Azure Portal을 통해 노드 풀을 업그레이드할 수 있습니다."  # ❌ "~을 통해"
        ),
        affected_resources=[
            {
                "name": "aks-aigora-dev",
                "type": "microsoft.containerservice/managedclusters",
                "resourceGroup": "rg-aigora-dev",
                "subscription": "dev-subscription",
                "reason": "업그레이드 필요",  # ❌ 구체적 속성값 없음
                "action_required": True,
            },
        ],
        impact_summary="Some impact",
        impact_details=None,  # ❌ impact_details 없음
        action_items=[
            ActionItem(
                step=1,
                urgency="medium",
                task="업그레이드를 고려할 수 있습니다",  # ❌ 불확실한 표현
                why="",  # ❌ why 비어있음
                target_resources=[],  # ❌ 대상 리소스 없음
                procedure="",  # ❌ 절차 없음
                deadline="within 2 weeks",  # ❌ 조작된 기한
            ),
        ],
        recommendations=[],
        reference_docs=[],  # ❌ 참고 문서 없음
        additional_checks=[],
        should_notify=True,
    )


# ============================================================================
# Iteration 2: 주요 문제 수정
# ============================================================================
def make_iteration2_report() -> AnalysisResult:
    """Iteration 2: 주요 컨텐츠 오류 수정 (카테고리, 증거, 내부노출 등)."""
    return AnalysisResult(
        update_id="update-2026-04-15-aks-ubuntu-2204",
        update_title="AKS Ubuntu 22.04 retirement",
        update_category="retirement",  # ✅ 수정
        urgency=UrgencyLevel.HIGH,  # ✅ retirement → high
        relevance=RelevanceStatus.RELEVANT,
        one_line_summary="AKS Ubuntu 22.04 지원 종료 2026-07-31 — 클러스터 1개 마이그레이션 필요",  # ✅ 구체적
        relevance_evidence="현재 환경에서 AKS 클러스터 1개(aks-aigora-dev)가 Ubuntu 22.04 노드 이미지를 사용 중이므로 이 업데이트에 해당합니다.",  # ✅
        relevance_reason=(
            "**AKS**의 Ubuntu 22.04 노드 이미지 지원이 2026년 7월 31일에 종료됩니다. "
            "이후 해당 이미지를 사용하는 노드 풀에는 보안 패치와 버그 수정이 제공되지 않습니다.\n\n"
            "> **Ubuntu 22.04 (Jammy Jellyfish)**: AKS 노드 풀에서 사용하는 Linux 배포판입니다. "
            "Canonical의 LTS 지원이 종료되며 Azure에서도 지원을 중단합니다.\n\n"
            "현재 환경의 AKS 클러스터를 노드 이미지 버전 기준으로 평가했습니다. "
            "aks-aigora-dev 클러스터의 노드 풀이 Ubuntu 22.04를 사용 중입니다.\n\n"  # ❌ 리소스 이름이 분석 본문에 등장 (→ affected_resources로 이동해야)
            "노드 풀의 OS SKU를 Ubuntu 24.04로 전환해야 합니다. "
            "전환 전 워크로드 호환성을 검증하고, 이상이 없으면 전환을 진행합니다.\n\n"
            "기한 내에 전환을 완료하면 보안 패치가 지속적으로 제공됩니다. "
            "2026년 7월 31일 이후에는 보안 패치가 중단되므로 기한 내에 완료해야 합니다."
        ),
        affected_resources=[
            {
                "name": "aks-aigora-dev",
                "type": "microsoft.containerservice/managedclusters",
                "resourceGroup": "rg-aigora-dev",
                "subscription": "dev-subscription",
                "reason": "nodeImageVersion: AKSUbuntu-2204gen2containerd-202604.01.0 — Ubuntu 22.04 지원 종료 대상",  # ✅ 속성값 포함
                "action_required": True,
            },
        ],
        impact_summary="Ubuntu 22.04 지원 종료로 1개 AKS 클러스터 영향",
        impact_details=ImpactSummary(
            security_impact="지원 종료 후 보안 패치 미제공으로 취약점 노출 위험",
            operational_impact="노드 풀 OS SKU 전환 작업 필요 (1개 클러스터)",
        ),  # ✅ impact_details 추가
        action_items=[
            ActionItem(
                step=1,
                urgency="high",
                task="aks-aigora-dev 클러스터의 워크로드 호환성을 검증합니다",  # ✅ 구체적
                why="Ubuntu 24.04로 전환 전 애플리케이션 호환성을 확인해야 합니다.",  # ✅
                target_resources=["aks-aigora-dev"],  # ✅
                procedure="Azure Portal > AKS > aks-aigora-dev > Node pools > 노드 풀 선택 > OS SKU 확인",
                estimated_time="30분",
                precaution="스테이징 환경에서 먼저 테스트할 것을 권장합니다.",
            ),
            ActionItem(
                step=2,
                urgency="high",
                task="노드 풀의 OS SKU를 Ubuntu 24.04로 전환합니다",
                why="2026-07-31 이후 Ubuntu 22.04 노드 풀에 보안 패치가 제공되지 않습니다.",
                target_resources=["aks-aigora-dev"],
                procedure="Azure Portal > AKS > Node pools > 노드 풀 선택 > Update > OS SKU: Ubuntu 24.04 선택",
                cli_command="az aks nodepool update --cluster-name aks-aigora-dev --name <nodepool> --resource-group rg-aigora-dev --os-sku Ubuntu2404",
                estimated_time="15분 + 노드 롤링 업데이트 시간",
                deadline="2026-07-31 (retirement date from update)",  # ✅ 실제 날짜
                risk_if_not_done="보안 패치 미제공으로 취약점 노출",
                rollback="동일 CLI 명령으로 OS SKU를 Ubuntu2204로 되돌릴 수 있습니다.",
            ),
        ],
        recommendations=[],
        reference_docs=[
            {
                "title": "AKS Ubuntu 22.04 retirement notice",
                "url": "https://learn.microsoft.com/azure/aks/supported-kubernetes-versions",
                "related_content": "Node image retirement timeline and migration guide",
            },
        ],  # ✅ 참고 문서 추가
        additional_checks=[
            "노드 풀에 사용 중인 커스텀 확장(custom script extension) 호환성 확인이 필요합니다.",
        ],
        should_notify=True,
    )


# ============================================================================
# Iteration 3: 언어 품질 및 디자인 최종 개선
# ============================================================================
def make_iteration3_report() -> AnalysisResult:
    """Iteration 3: 번역체 제거, 컨셉 박스 추가, 콘텐츠 중복 제거, 완성도 향상."""
    return AnalysisResult(
        update_id="update-2026-04-15-aks-ubuntu-2204",
        update_title="AKS Ubuntu 22.04 retirement",
        update_category="retirement",
        urgency=UrgencyLevel.HIGH,
        relevance=RelevanceStatus.RELEVANT,
        one_line_summary="AKS Ubuntu 22.04 지원 종료 2026-07-31 — 클러스터 1개 마이그레이션 필요",
        relevance_evidence="현재 환경에서 AKS 클러스터 1개(aks-aigora-dev)가 Ubuntu 22.04 노드 이미지를 사용 중이므로 이 업데이트에 해당합니다.",
        relevance_reason=(
            "**AKS**의 Ubuntu 22.04 노드 이미지 지원이 2026년 7월 31일에 종료됩니다. "
            "이후 해당 이미지를 사용하는 노드 풀에는 보안 패치와 버그 수정이 제공되지 않습니다.\n\n"
            "> **Ubuntu 22.04 (Jammy Jellyfish)**: AKS 노드 풀의 기본 Linux 배포판으로, "
            "Canonical의 LTS 지원 주기에 따라 운영됩니다. LTS 종료와 함께 Azure에서도 지원을 중단합니다.\n\n"
            "이번 변경은 Canonical Ubuntu의 LTS 수명 주기 종료에 따른 것입니다. "
            "Ubuntu 22.04의 표준 지원은 2027년 4월에 종료되나, AKS에서는 이보다 앞선 "
            "2026년 7월 31일에 지원을 중단합니다. 후속 버전인 **Ubuntu 24.04 (Noble Numbat)**가 "
            "새로운 기본 노드 이미지로 채택되었으며, 커널 6.8 기반의 향상된 보안 기능과 "
            "성능 최적화를 제공합니다.\n\n"
            "> **노드 이미지(Node Image)**: AKS 노드 풀의 각 VM에 적용되는 OS 이미지입니다. "
            "Kubernetes 런타임, containerd, 보안 패치가 포함되어 있으며 주기적으로 업데이트됩니다.\n\n"
            "> **OS SKU**: AKS 노드 풀 생성 시 선택하는 운영 체제 종류입니다. "
            "Ubuntu, AzureLinux, Windows 등을 선택할 수 있으며, 노드 풀 단위로 설정합니다.\n\n"
            "현재 환경의 AKS 클러스터를 **agentPoolProfiles**의 OS SKU와 노드 이미지 버전 기준으로 "
            "평가했습니다. Ubuntu 22.04를 사용하는 노드 풀이 확인되었으며, 전환 대상입니다.\n\n"
            "사전 검증, 전환 실행, 워크로드 확인의 3단계로 진행합니다. "
            "스테이징 환경에서 먼저 Ubuntu 24.04 호환성을 검증한 후 프로덕션에 적용하는 것이 안전합니다.\n\n"
            "기한 내에 전환을 완료하면 보안 패치가 지속적으로 제공됩니다. "
            "2026년 7월 31일 이후에는 Ubuntu 22.04 노드 풀에 보안 패치가 중단되므로, "
            "전환이 늦어질 경우 보안 취약점에 노출될 위험이 있습니다."
        ),
        affected_resources=[
            {
                "name": "aks-aigora-dev",
                "type": "microsoft.containerservice/managedclusters",
                "resourceGroup": "rg-aigora-dev",
                "subscription": "dev-subscription",
                "reason": "nodeImageVersion: AKSUbuntu-2204gen2containerd-202604.01.0, osSKU: Ubuntu — Ubuntu 22.04 지원 종료 대상",
                "action_required": True,
            },
        ],
        impact_summary="Ubuntu 22.04 지원 종료로 1개 AKS 클러스터 영향",
        impact_details=ImpactSummary(
            security_impact="지원 종료 후 보안 패치 미제공으로 CVE 취약점 노출 위험 증가",
            operational_impact="노드 풀 OS SKU 전환 작업 필요 (1개 클러스터, 예상 작업 시간 약 1시간)",
        ),
        action_items=[
            ActionItem(
                step=1,
                urgency="high",
                task="aks-aigora-dev 클러스터의 워크로드 호환성을 검증합니다",
                why="Ubuntu 24.04는 커널 6.8 기반으로, 기존 워크로드의 커널 의존성을 사전에 확인해야 합니다.",
                target_resources=["aks-aigora-dev"],
                procedure="Azure Portal > AKS > aks-aigora-dev > Node pools > 노드 풀 선택 > OS SKU 확인 후, 스테이징 환경에서 Ubuntu 24.04 노드 풀 생성 및 워크로드 배포 테스트",
                estimated_time="30분",
                precaution="운영 환경 변경 전 스테이징에서 반드시 검증해야 합니다.",
            ),
            ActionItem(
                step=2,
                urgency="high",
                task="aks-aigora-dev 클러스터의 노드 풀을 Ubuntu 24.04로 전환합니다",
                why="2026-07-31 이후 Ubuntu 22.04 노드 풀에 보안 패치가 제공되지 않습니다.",
                target_resources=["aks-aigora-dev"],
                procedure="Azure Portal > AKS > Node pools > 노드 풀 선택 > Update > OS SKU: Ubuntu 24.04 선택 > 노드 롤링 업데이트 진행",
                cli_command="az aks nodepool update --cluster-name aks-aigora-dev --name <nodepool> --resource-group rg-aigora-dev --os-sku Ubuntu2404",
                estimated_time="15분 + 노드 롤링 업데이트 시간",
                deadline="2026-07-31 (retirement date from update)",
                risk_if_not_done="보안 패치 미제공으로 CVE 취약점 노출, 컴플라이언스 감사 지적 가능",
                precaution="노드 드레인(drain) 후 순차적으로 전환하여 서비스 중단을 방지합니다.",
                rollback="동일 CLI 명령으로 OS SKU를 Ubuntu2204로 되돌릴 수 있습니다.",
            ),
        ],
        recommendations=[],
        reference_docs=[
            {
                "title": "AKS supported Kubernetes versions and node images",
                "url": "https://learn.microsoft.com/azure/aks/supported-kubernetes-versions",
                "related_content": "Node image retirement timeline, OS SKU migration guide",
            },
            {
                "title": "AKS node pool OS SKU migration",
                "url": "https://learn.microsoft.com/azure/aks/node-image-upgrade",
                "related_content": "노드 이미지 업그레이드 절차 및 CLI 명령 참조",
            },
        ],
        additional_checks=[
            "노드 풀에 사용 중인 커스텀 확장(custom script extension)의 Ubuntu 24.04 호환성을 점검해야 합니다.",
            "Pod Security Standards 설정이 Ubuntu 24.04의 커널 6.8과 호환되는지 CSA 사전 검토가 필요합니다.",
        ],
        should_notify=True,
    )


# ============================================================================
# Main Loop
# ============================================================================
def main():
    evaluator = ReportQualityEvaluator()
    email_service = EmailService.__new__(
        EmailService
    )  # skip __init__ (no settings needed for build)

    iterations = [
        ("Iteration 1: 초기 보고서 (의도적 품질 문제 포함)", make_iteration1_report),
        ("Iteration 2: 주요 컨텐츠 오류 수정", make_iteration2_report),
        ("Iteration 3: 언어 품질 + 디자인 최종 개선", make_iteration3_report),
    ]

    scores = []

    for iteration_name, make_report in iterations:
        print(f"\n{'#' * 72}")
        print(f"  {iteration_name}")
        print(f"{'#' * 72}")

        result = make_report()
        print(f"\n  📋 Update Category: {getattr(result, 'update_category', 'N/A')}")
        print(f"  📋 Urgency: {result.urgency.value}")
        print(f"  📋 Relevance: {result.relevance.value}")
        print(f"  💬 Summary: {result.one_line_summary}")

        # Evaluate
        qr = evaluator.evaluate(result, MOCK_UPDATE, language="ko")
        _print_quality_report(qr, verbose=True)
        scores.append((iteration_name, qr))

        # Generate feedback for display
        feedback = _build_feedback_prompt(qr)
        if feedback:
            print(f"\n  📝 다음 반복을 위한 피드백 ({len(feedback)} chars):")
            for line in feedback.split("\n")[:10]:
                if line.strip():
                    print(f"    {line}")
            if len(feedback.split("\n")) > 10:
                print(f"    ... ({len(feedback.split(chr(10))) - 10}줄 더)")

    # ========================================================================
    # Final Summary
    # ========================================================================
    print(f"\n{'=' * 72}")
    print(f"  📊 반복 개선 결과 요약")
    print(f"{'=' * 72}")
    print(f"  {'Iteration':<45} {'Score':>7} {'Grade':>6} {'Change':>8}")
    print(f"  {'-'*45} {'-'*7} {'-'*6} {'-'*8}")

    prev_score = 0
    for name, qr in scores:
        short_name = name.split(":")[0].strip()
        delta = qr.total_score - prev_score if prev_score else 0
        delta_str = f"+{delta}" if delta > 0 else str(delta) if delta < 0 else "-"
        print(
            f"  {short_name:<45} {qr.total_score:>3}/{qr.max_score:<3} {qr.grade:>6} {delta_str:>8}"
        )
        prev_score = qr.total_score

    # Score improvement
    first_score = scores[0][1].total_score
    last_score = scores[-1][1].total_score
    improvement = last_score - first_score
    print(
        f"\n  🏆 총 점수 향상: {first_score} → {last_score} (+{improvement}점, "
        f"{improvement / first_score * 100:.0f}% 향상)"
    )
    print(f"  📈 최종 등급: {scores[-1][1].grade}")

    # Category improvements
    print(f"\n  📊 카테고리별 점수 변화:")
    first_cats = scores[0][1].category_scores
    last_cats = scores[-1][1].category_scores
    for cat in sorted(first_cats.keys()):
        f_pct = first_cats[cat]["percentage"]
        l_pct = last_cats.get(cat, {}).get("percentage", 0)
        f_score = first_cats[cat]["score"]
        l_score = last_cats.get(cat, {}).get("score", 0)
        l_max = last_cats.get(cat, {}).get("max", 0)
        bar = "█" * int(l_pct / 5) + "░" * (20 - int(l_pct / 5))
        delta = l_pct - f_pct
        delta_str = f"+{delta:.0f}%" if delta > 0 else f"{delta:.0f}%"
        print(f"    {cat:<22} {f_score:>2}→{l_score}/{l_max:<3} {bar} {l_pct:.0f}% ({delta_str})")

    # Remaining issues
    last_qr = scores[-1][1]
    remaining = [item for item in last_qr.items if item.score < item.max_score]
    if remaining:
        print(f"\n  ⚠️ 남은 개선 항목 ({len(remaining)}개):")
        for item in remaining:
            gap = item.max_score - item.score
            print(f"    • {item.name}: {item.score}/{item.max_score} (-{gap}) — {item.reason}")
            for d in item.deductions[:1]:
                print(f"      ↳ {d}")
    else:
        print(f"\n  ✅ 모든 항목 만점!")

    print(f"\n{'=' * 72}\n")


if __name__ == "__main__":
    main()
