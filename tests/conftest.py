"""Shared test fixtures and configuration."""

import pytest


@pytest.fixture
def sample_rss_xml():
    """Sample RSS XML feed for testing."""
    return """<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0">
  <channel>
    <title>Azure updates</title>
    <link>https://azure.microsoft.com/en-us/updates/</link>
    <item>
      <title>Generally Available: Azure Blob Storage SFTP Resumable Uploads</title>
      <link>https://azure.microsoft.com/updates?id=123456</link>
      <guid>https://azure.microsoft.com/updates?id=123456</guid>
      <description>&lt;p&gt;SFTP now supports resumable uploads for Blob Storage.&lt;/p&gt;</description>
      <pubDate>Mon, 10 Mar 2026 18:00:00 Z</pubDate>
      <category>Blob Storage</category>
      <category>Storage</category>
      <category>Features</category>
    </item>
    <item>
      <title>Public Preview: AKS Automatic Node Repair</title>
      <link>https://azure.microsoft.com/updates?id=123457</link>
      <guid>https://azure.microsoft.com/updates?id=123457</guid>
      <description>AKS node repair is now in preview.</description>
      <pubDate>Tue, 11 Mar 2026 12:00:00 Z</pubDate>
      <category>AKS</category>
      <category>Containers</category>
    </item>
    <item>
      <title>Retirement: Classic VMs will be retired on September 6, 2023</title>
      <link>https://azure.microsoft.com/updates?id=100001</link>
      <guid>https://azure.microsoft.com/updates?id=100001</guid>
      <description>Classic VMs are being retired.</description>
      <pubDate>Wed, 12 Mar 2026 09:00:00 Z</pubDate>
      <category>Virtual Machines</category>
      <category>Retirements</category>
    </item>
  </channel>
</rss>"""


@pytest.fixture
def sample_update():
    """Create a sample AzureUpdate for testing."""
    from datetime import datetime, timezone

    from src.rss.parser import AzureUpdate

    return AzureUpdate(
        id="123456",
        title="Generally Available: Azure Blob Storage SFTP Resumable Uploads",
        description="SFTP now supports resumable uploads for Blob Storage.",
        link="https://azure.microsoft.com/updates?id=123456",
        published_date=datetime(2026, 3, 10, 18, 0, tzinfo=timezone.utc),
        categories=["Blob Storage", "Storage", "Features"],
        azure_services=["Blob Storage"],
        update_type="General Availability",
        status=None,
    )


@pytest.fixture
def sample_analysis_result():
    """Create a sample AnalysisResult for testing."""
    from src.agent.analyzer import (
        ActionItem,
        AnalysisResult,
        ImpactSummary,
        RelevanceStatus,
        UrgencyLevel,
    )

    return AnalysisResult(
        update_id="123456",
        update_title="Azure Blob Storage SFTP Resumable Uploads",
        update_category="new_feature",
        urgency=UrgencyLevel.LOW,
        relevance=RelevanceStatus.OPPORTUNITY,
        one_line_summary="Blob Storage SFTP 재개 가능 업로드가 GA됨",
        relevance_reason="현재 환경에서 2개의 Storage Account가 SFTP를 사용 중입니다.",
        affected_resources=[
            {"name": "stor1", "type": "Microsoft.Storage/storageAccounts", "resourceGroup": "rg-1"}
        ],
        impact_summary="SFTP 업로드 대용량 파일 전송 안정성 향상",
        impact_details=ImpactSummary(
            cost_impact="추가 비용 없음",
            security_impact="해당 없음",
            performance_impact="대용량 파일 전송 시 안정성 향상",
            operational_impact="SFTP 클라이언트 업데이트 필요할 수 있음",
        ),
        action_items=[
            ActionItem(
                step=1,
                task="SFTP 클라이언트가 재개 가능 업로드를 지원하는지 확인",
                urgency="low",
            ),
        ],
        recommendations=["SFTP 클라이언트 호환성을 확인하세요."],
        reference_docs=[
            {
                "title": "Azure Blob Storage SFTP support",
                "url": "https://learn.microsoft.com/azure/storage/blobs/secure-file-transfer-protocol-support",
            }
        ],
        should_notify=True,
    )
