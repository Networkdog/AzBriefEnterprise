"""Render synthetic email design previews without Azure calls or email delivery."""

from __future__ import annotations

import argparse
import tempfile
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from bs4 import BeautifulSoup
from structlog import get_logger

from src.agent.analyzer import (
    ActionItem,
    AnalysisResult,
    ImpactSummary,
    RelevanceStatus,
    UrgencyLevel,
)
from src.email.service import EmailService
from src.rss.parser import AzureUpdate

logger = get_logger()

_COPY = {
    "ko": {
        "title": "Storage 계정의 TLS 연결 정책 변경",
        "summary": "예제 환경의 Storage 계정 2개를 확인하고 TLS 1.2 클라이언트 호환성을 점검합니다.",
        "analysis": (
            "이 보고서는 **디자인 검증용 합성 데이터**로 구성했습니다. 실제 서비스 공지나 고객 환경을 나타내지 않습니다.\n\n"
            "연결 정책을 바꾸기 전에 애플리케이션과 파일 전송 작업이 사용하는 **TLS 버전**을 확인합니다. "
            "리소스 설정과 클라이언트 사용 현황을 구분해 확인하면 변경 범위를 좁힐 수 있습니다.\n\n"
            "> **TLS**: 클라이언트와 서비스 간 통신을 암호화하는 프로토콜입니다. "
            "([Microsoft Learn](https://learn.microsoft.com/azure/storage/common/transport-layer-security-configure-minimum-version))\n\n"
            "운영 환경에 적용하기 전 테스트 요청의 성공 여부와 기존 연결 로그를 비교합니다. "
            "실제 호환성은 서비스 설정만으로 확정하지 않습니다."
        ),
        "evidence": "예제 계정 두 개에 같은 설정이 기록되어 있습니다. 클라이언트별 연결 버전은 이 예제에 포함하지 않았습니다.",
        "reason": "합성 인벤토리에서 minimumTlsVersion 값이 TLS1_0으로 설정되어 있습니다.",
        "task": "설정값을 조회하고 클라이언트 연결 로그와 대조합니다",
        "procedure": "Azure Portal에서 대상 Storage 계정을 엽니다. Configuration의 최소 TLS 버전을 확인합니다. 연결 로그와 테스트 결과를 비교합니다.",
        "risk": "호환되지 않는 클라이언트가 있으면 정책 변경 후 연결에 실패할 수 있습니다.",
        "precaution": "이 명령은 읽기 전용입니다. 실제 변경 전 담당자의 승인을 받습니다.",
        "rollback": "조회만 하므로 상태가 변경되지 않습니다.",
        "finding": "디자인 예시: 실행 대상과 권한을 실제 환경에서 확인해야 합니다.",
        "security": "최소 TLS 버전과 클라이언트 호환성을 함께 검토합니다.",
        "operations": "배치 전송과 애플리케이션 연결을 구분해 테스트합니다.",
        "checks": "애플리케이션 담당자에게 실제 클라이언트 버전과 테스트 결과를 확인합니다.",
        "reference": "Azure Storage 최소 TLS 버전 구성",
        "description": "최소 TLS 버전 설정과 클라이언트 연결 확인 방법을 다루는 참고 문서입니다.",
        "context": "설정 확인 절차와 제한 사항을 확인합니다.",
        "visual_alt": "Azure Portal에서 Storage 계정의 최소 TLS 버전을 구성하는 화면",
        "visual_caption": "Storage 계정의 Configuration 화면에서 최소 TLS 버전을 선택합니다.",
        "opportunity": "새 네트워크 기능의 적용 가능성 검토",
        "opportunity_summary": "예제 워크로드에 필요한 운영 조건을 먼저 확인한 뒤 새 기능의 적용 가능성을 비교합니다.",
        "low": "현재 범위 밖의 서비스 변경",
        "low_summary": "예제 범위에서 직접 적용할 근거가 확인되지 않았습니다.",
        "skip": "합성 예제: 분석 결과가 없는 항목도 목록에 남깁니다.",
    },
    "en": {
        "title": "Reviewing a Storage account TLS policy change",
        "summary": "Review two sample Storage accounts and validate TLS 1.2 client compatibility.",
        "analysis": (
            "This report contains **synthetic design data**, not a live announcement or customer environment.\n\n"
            "Check the **TLS version** used by applications and transfer jobs before changing connection policies. "
            "Separate the configured resource setting from observed client behavior.\n\n"
            "> **TLS**: A protocol that encrypts traffic between clients and services. "
            "([Microsoft Learn](https://learn.microsoft.com/azure/storage/common/transport-layer-security-configure-minimum-version))\n\n"
            "Compare test requests and existing connection logs before rollout. A resource setting alone does not prove client compatibility."
        ),
        "evidence": "Two sample accounts share the same setting. Per-client connection versions are not included in this fixture.",
        "reason": "The synthetic inventory records minimumTlsVersion as TLS1_0.",
        "task": "Read the configuration and compare it with client connection logs",
        "procedure": "Open the target Storage account in Azure Portal. Check the minimum TLS version under Configuration. Compare connection logs and test results.",
        "risk": "Incompatible clients may fail to connect after the policy changes.",
        "precaution": "This command is read-only. Obtain approval before making a real change.",
        "rollback": "No state changes are made by this read-only check.",
        "finding": "Design sample: validate the target and permissions in the real environment.",
        "security": "Review the minimum TLS version together with client compatibility.",
        "operations": "Test batch transfers separately from application connections.",
        "checks": "Ask the application owner for observed client versions and test results.",
        "reference": "Configure a minimum TLS version for Azure Storage",
        "description": "Reference documentation for minimum TLS configuration and client connection checks.",
        "context": "Review the configuration procedure and its constraints.",
        "visual_alt": "Azure portal pane for configuring a Storage account's minimum TLS version",
        "visual_caption": "Select the minimum TLS version on the Storage account Configuration pane.",
        "opportunity": "Evaluating a new networking capability",
        "opportunity_summary": "Establish the sample workload's operational requirements before comparing the new capability.",
        "low": "A service change outside the current scope",
        "low_summary": "Direct applicability has not been established in this sample scope.",
        "skip": "Synthetic sample: keep an item without an analysis result visible in the index.",
    },
    "ja": {
        "title": "Storage アカウントの TLS 接続ポリシー変更",
        "summary": "サンプルの Storage アカウント2件を確認し、TLS 1.2 クライアントとの互換性を調べます。",
        "analysis": (
            "このレポートは**デザイン検証用の合成データ**です。実際の発表や顧客環境を示すものではありません。\n\n"
            "接続ポリシーを変更する前に、アプリケーションや転送ジョブで使われる **TLS バージョン**を確認します。 "
            "リソースの設定とクライアントの実際の動作は分けて確認します。\n\n"
            "> **TLS**: クライアントとサービス間の通信を暗号化するプロトコルです。 "
            "([Microsoft Learn](https://learn.microsoft.com/azure/storage/common/transport-layer-security-configure-minimum-version))\n\n"
            "適用前にテスト結果と接続ログを比較します。設定だけで互換性を断定しません。"
        ),
        "evidence": "サンプルの2アカウントには同じ設定があります。クライアント別の接続バージョンは含まれていません。",
        "reason": "合成インベントリでは minimumTlsVersion が TLS1_0 に設定されています。",
        "task": "設定を読み取り、クライアントの接続ログと照合します",
        "procedure": "Azure Portal で対象の Storage アカウントを開きます。Configuration の最小 TLS バージョンを確認します。接続ログとテスト結果を比較します。",
        "risk": "互換性のないクライアントは変更後に接続できない可能性があります。",
        "precaution": "このコマンドは読み取り専用です。実際の変更には承認が必要です。",
        "rollback": "読み取りのみで状態は変更されません。",
        "finding": "デザイン例: 実環境の対象と権限を確認してください。",
        "security": "最小 TLS バージョンとクライアントの互換性を確認します。",
        "operations": "バッチ転送とアプリケーション接続を分けてテストします。",
        "checks": "アプリケーション担当者にクライアントのバージョンとテスト結果を確認します。",
        "reference": "Azure Storage の最小 TLS バージョンを構成する",
        "description": "最小 TLS バージョンの構成とクライアント接続の確認に関する参考ドキュメントです。",
        "context": "設定の確認手順と制約を調べます。",
        "visual_alt": "Azure Portal で Storage アカウントの最小 TLS バージョンを構成する画面",
        "visual_caption": "Storage アカウントの Configuration 画面で最小 TLS バージョンを選択します。",
        "opportunity": "新しいネットワーク機能の適用可能性",
        "opportunity_summary": "サンプル環境の運用要件を整理し、新機能の適用可能性を比較します。",
        "low": "現在のスコープ外のサービス変更",
        "low_summary": "サンプル範囲では直接適用できる根拠が確認できませんでした。",
        "skip": "合成データ例: 分析結果のない項目も一覧に残します。",
    },
}


def build_demo_items(language: str) -> list[dict]:
    """Build high, medium, low and skipped examples with no tenant data."""
    text = _COPY[language]
    doc_url = "https://learn.microsoft.com/azure/storage/common/transport-layer-security-configure-minimum-version"
    update = AzureUpdate(
        id="900001",
        title=text["title"],
        description="Synthetic design fixture",
        link=doc_url,
        published_date=datetime(2026, 9, 8, tzinfo=timezone.utc),
        categories=["Storage"],
        azure_services=["Azure Storage"],
        update_type="Design sample",
        status=None,
    )
    resources = [
        {
            "name": name,
            "type": "Microsoft.Storage/storageAccounts",
            "subscription": "Production sample",
            "subscriptionId": "00000000-0000-0000-0000-000000000001",
            "resourceGroup": "rg-platform-production",
            "reason": text["reason"],
        }
        for name in ("stsampleapplication01", "stsamplebatchprocessing02")
    ]
    result = AnalysisResult(
        update_id=update.id,
        update_title=update.title,
        update_category="retirement",
        urgency=UrgencyLevel.HIGH,
        relevance=RelevanceStatus.RELEVANT,
        importance="high",
        impact_level="high",
        job_relevance="medium",
        one_line_summary=text["summary"],
        relevance_reason=text["analysis"],
        relevance_evidence=text["evidence"],
        affected_resources=resources,
        impact_summary=text["security"],
        impact_details=ImpactSummary(
            security_impact=text["security"], operational_impact=text["operations"]
        ),
        action_items=[
            ActionItem(
                step=1,
                task=text["task"],
                why=text["reason"],
                target_resources=[resources[0]["name"]],
                procedure=text["procedure"],
                cli_command="az storage account show --name stsampleapplication01 --resource-group rg-platform-production --query '{minimumTlsVersion:minimumTlsVersion,allowBlobPublicAccess:allowBlobPublicAccess}'",
                deadline="2026-12-31 (sample)",
                estimated_time="30 min (sample)",
                risk_if_not_done=text["risk"],
                precaution=text["precaution"],
                rollback=text["rollback"],
                reference_url=doc_url,
                verification_status="caution",
                verification_notes=[text["finding"]],
            )
        ],
        recommendations=[],
        additional_checks=[text["checks"]],
        reference_docs=[
            {
                "title": text["reference"],
                "url": doc_url,
                "description": text["description"],
                "related_content": text["context"],
            }
        ],
        visual_assets=[
            {
                "url": (
                    "https://learn.microsoft.com/en-us/azure/storage/common/media/"
                    "transport-layer-security-configure-minimum-version/"
                    "configure-minimum-version-portal.png"
                ),
                "alt": text["visual_alt"],
                "caption": text["visual_caption"],
                "source_url": doc_url,
                "source_title": text["reference"],
            }
        ],
        should_notify=True,
    )
    items = [
        {
            "update": update,
            "result": result,
            "archive_url": "https://azbrief.example/archive/sample-1",
        }
    ]
    for index, kind in enumerate(("opportunity", "low"), 2):
        next_update = replace(update, id=f"90000{index}", title=text[kind])
        next_result = result.model_copy(
            deep=True,
            update={
                "update_id": next_update.id,
                "update_title": next_update.title,
                "update_category": "new_feature" if kind == "opportunity" else "feature_change",
                "urgency": UrgencyLevel.LOW,
                "relevance": (
                    RelevanceStatus.OPPORTUNITY
                    if kind == "opportunity"
                    else RelevanceStatus.NOT_RELEVANT
                ),
                "importance": "medium" if kind == "opportunity" else "low",
                "impact_level": "low",
                "job_relevance": "high" if kind == "opportunity" else "low",
                "one_line_summary": text[kind + "_summary"],
                "affected_resources": [],
                "action_items": [],
                "impact_details": None,
            },
        )
        items.append({"update": next_update, "result": next_result})
    items.append(
        {
            "update": replace(update, id="900004", title="Design sample — pending analysis"),
            "result": None,
            "skip_reason": text["skip"],
        }
    )
    return items


def render_previews(output_dir: Path, languages: list[str]) -> list[Path]:
    """Write full and head-style-stripped HTML previews, never initialize a transport."""
    output_dir.mkdir(parents=True, exist_ok=True)
    settings = SimpleNamespace(
        use_email=False,
        communication_services_connection_string=None,
        communication_services_endpoint=None,
        email_sender_address=None,
        feedback_ui_enabled=True,
        feedback_base_url="https://azbrief.example",
    )
    paths = []
    with (
        patch("src.email.service.get_settings", return_value=settings),
        patch("src.agent.history.get_retirement_countdown", return_value=[]),
    ):
        service = EmailService()
        for language in languages:
            items = build_demo_items(language)
            single = service.build_email_content(
                items[0]["update"],
                items[0]["result"],
                language,
                archive_url=items[0]["archive_url"],
            )
            digest = service.build_digest_content(items, "2026-09-01 — 2026-09-08", language)
            for name, content in (("single", single), ("digest", digest)):
                for stripped in (False, True):
                    markup = content["html_content"]
                    if stripped:
                        soup = BeautifulSoup(markup, "html.parser")
                        for style in soup.find_all("style"):
                            style.decompose()
                        markup = str(soup)
                    suffix = "-inline-only" if stripped else ""
                    path = output_dir / f"{name}-{language}{suffix}.html"
                    path.write_text(markup, encoding="utf-8")
                    paths.append(path)
    return paths


def main() -> None:
    """Render synthetic previews for offline design verification."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--language", choices=("all", "ko", "en", "ja"), default="all")
    args = parser.parse_args()
    output_dir = args.output_dir or Path(tempfile.mkdtemp(prefix="azbrief-email-preview-"))
    languages = list(_COPY) if args.language == "all" else [args.language]
    paths = render_previews(output_dir, languages)
    logger.info(
        "email_design_previews_written",
        directory=str(output_dir.resolve()),
        files=len(paths),
        synthetic=True,
        email_sent=False,
    )


if __name__ == "__main__":
    main()
