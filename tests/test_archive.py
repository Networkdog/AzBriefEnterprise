"""Archive contract tests."""

import base64
import json
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
from starlette.requests import Request

from src.agent.analyzer import AnalysisResult, RelevanceStatus, UrgencyLevel
from src.archive.auth import require_archive_reader
from src.archive.models import (
    ArchiveAnalysisResultV1,
    ArchiveDocumentV1,
    ArchivePage,
    ArchiveQuery,
    ArchiveSource,
    ArchiveSummary,
    ArchiveUpdateV1,
)
from src.archive.page import render_archive_page
from src.config import get_settings

UTC = timezone.utc

_ARCHIVE_ENV = (
    "ARCHIVE_UI_ENABLED",
    "ARCHIVE_REQUIRE_AUTH",
    "ARCHIVE_ALLOWED_PRINCIPALS",
    "ADMIN_ALLOWED_PRINCIPALS",
)


@pytest.fixture(autouse=True)
def _isolated_archive_settings(monkeypatch):
    for key in _ARCHIVE_ENV:
        monkeypatch.delenv(key, raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _configure(monkeypatch, **env: str) -> None:
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()


def _request(path: str = "/archive", **headers: str) -> Request:
    raw = [
        (key.replace("_", "-").lower().encode(), value.encode()) for key, value in headers.items()
    ]
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "headers": raw,
            "scheme": "https",
            "server": ("testserver", 443),
            "client": ("10.0.0.1", 5000),
        }
    )


def _principal_blob(claims: list[dict]) -> str:
    return base64.b64encode(json.dumps({"claims": claims}).encode()).decode()


def _result(update_id: str = "570120") -> AnalysisResult:
    return AnalysisResult(
        update_id=update_id,
        update_title="Azure Kubernetes Service update",
        relevance=RelevanceStatus.RELEVANT,
        urgency=UrgencyLevel.HIGH,
        importance="high",
        impact_level="medium",
        job_relevance="high",
        one_line_summary="AKS 운영 구성을 확인해야 합니다.",
        relevance_reason="현재 AKS 클러스터와 관련이 있습니다.",
        affected_resources=[
            {"name": "aks-prod", "type": "Microsoft.ContainerService/managedClusters"}
        ],
        impact_summary="운영 검토가 필요합니다.",
        recommendations=["구성을 검토합니다."],
        reference_docs=[],
        should_notify=True,
    )


def _document(update_id: str = "570120") -> ArchiveDocumentV1:
    return ArchiveDocumentV1(
        archive_id="8211694095999-0123456789abcdef0123456789abcdef",
        analyzed_at=datetime(2026, 8, 30, 12, tzinfo=UTC),
        source=ArchiveSource.SCHEDULED_DIGEST,
        run_id="run-1",
        hosted_agent_name="azbrief-analysis-hosted",
        trace_id="trace-1",
        report_language="ko",
        update=ArchiveUpdateV1(
            id=update_id,
            title="Azure Kubernetes Service update",
            description="A canonical update description.",
            link="https://azure.microsoft.com/updates?id=570120",
            published_date=datetime(2026, 8, 29, tzinfo=UTC),
            categories=["Compute"],
            azure_services=["Azure Kubernetes Service"],
            update_type="General Availability",
        ),
        result=ArchiveAnalysisResultV1.model_validate(
            _result(update_id).model_dump(mode="json", exclude={"job_relevance"})
        ),
    )


def test_archive_document_round_trips_as_strict_json():
    document = _document()

    restored = ArchiveDocumentV1.model_validate_json(document.model_dump_json())

    assert restored == document
    assert restored.analyzed_at.tzinfo is not None
    assert restored.schema_version == "1"


def test_archive_document_rejects_mismatched_update_identity():
    payload = _document().model_dump(mode="json")
    payload["result"]["update_id"] = "different"

    with pytest.raises(ValidationError, match="result.update_id must match update.id"):
        ArchiveDocumentV1.model_validate(payload)


def test_archive_summary_is_bounded_and_contains_no_subscriber_fields():
    summary = ArchiveSummary.from_document(_document())
    payload = summary.model_dump(mode="json")

    assert payload["update_id"] == "570120"
    assert payload["affected_resource_count"] == 1
    assert payload["action_item_count"] == 0
    assert payload["urgency"] == "high"
    assert payload["relevance"] == "relevant"
    assert "job_relevance" not in payload
    assert not ({"email", "subscriber", "recipient", "principal"} & set(payload))


def test_archive_document_excludes_personalized_job_relevance():
    payload = _document().model_dump(mode="json")
    assert "job_relevance" not in payload["result"]


def test_archive_document_rejects_unknown_fields():
    payload = _document().model_dump(mode="json")
    payload["subscriber_email"] = "reader@example.com"

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ArchiveDocumentV1.model_validate(payload)


def test_archive_document_rejects_future_nested_fields_without_a_version_bump():
    payload = _document().model_dump(mode="json")
    payload["result"]["future_runtime_field"] = "must require archive schema v2"

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ArchiveDocumentV1.model_validate(payload)


def test_archive_document_rejects_unknown_resource_and_reference_fields():
    payload = _document().model_dump(mode="json")
    payload["result"]["affected_resources"][0]["future_resource_field"] = True
    payload["result"]["reference_docs"] = [
        {"title": "Doc", "url": "https://learn.microsoft.com/doc", "future_doc_field": "x"}
    ]

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ArchiveDocumentV1.model_validate(payload)


class TestArchiveAuthorization:
    @pytest.mark.asyncio
    async def test_disabled_archive_is_hidden(self, monkeypatch):
        _configure(monkeypatch, ARCHIVE_UI_ENABLED="false")
        with pytest.raises(HTTPException) as exc:
            await require_archive_reader(_request())
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_empty_reader_lists_deny_authenticated_users(self, monkeypatch):
        _configure(monkeypatch, ARCHIVE_UI_ENABLED="true")
        with pytest.raises(HTTPException) as exc:
            await require_archive_reader(
                _request(**{"X_MS_CLIENT_PRINCIPAL_NAME": "reader@co.com"})
            )
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_group_and_admin_are_archive_readers(self, monkeypatch):
        _configure(
            monkeypatch,
            ARCHIVE_UI_ENABLED="true",
            ARCHIVE_ALLOWED_PRINCIPALS="group-a",
            ADMIN_ALLOWED_PRINCIPALS="admin@co.com",
        )
        group_blob = _principal_blob(
            [{"typ": "groups", "val": "group-a"}, {"typ": "oid", "val": "u1"}]
        )
        group_reader = await require_archive_reader(
            _request(**{"X_MS_CLIENT_PRINCIPAL": group_blob})
        )
        admin_reader = await require_archive_reader(
            _request(**{"X_MS_CLIENT_PRINCIPAL_NAME": "ADMIN@co.com"})
        )
        assert group_reader.id == "u1"
        assert admin_reader.name == "ADMIN@co.com"


class TestArchivePage:
    def test_page_uses_nonce_and_dom_text_rendering(self):
        page = render_archive_page(
            nonce="N0NCE",
            profile="enterprise",
            user="<img src=x onerror=alert(1)>",
            language="ko",
        )
        assert '<style nonce="N0NCE">' in page
        assert '<script nonce="N0NCE">' in page
        assert "<img src=x" not in page
        assert "&lt;img src=x" in page
        assert "innerHTML" not in page
        assert "textContent" in page
        assert "http://" not in page
        assert "advanced-filter" in page
        assert 'aria-expanded="false"' in page
        assert "renderImpact" in page
        assert "renderResources" in page
        assert "job_relevance" not in page
        assert "직무연관성" not in page


class TestArchiveRoutes:
    @pytest.fixture
    def client(self):
        from src.main import app

        return TestClient(app)

    @staticmethod
    def _install_service(monkeypatch):
        document = _document()

        class Service:
            async def list(self, query: ArchiveQuery):
                return ArchivePage(items=[ArchiveSummary.from_document(document)], scanned=1)

            async def get(self, archive_id: str):
                return document if archive_id == document.archive_id else None

        monkeypatch.setattr("src.archive.router.get_archive_service", lambda: Service())
        return document

    def test_archive_page_redirects_unauthenticated_browser(self, client, monkeypatch):
        _configure(
            monkeypatch,
            ARCHIVE_UI_ENABLED="true",
            ARCHIVE_ALLOWED_PRINCIPALS="reader@co.com",
        )
        response = client.get("/archive", follow_redirects=False)
        assert response.status_code == 302
        assert response.headers["location"].startswith("/.auth/login/aad")

    def test_archive_page_has_private_csp_response(self, client, monkeypatch):
        _configure(
            monkeypatch,
            ARCHIVE_UI_ENABLED="true",
            ARCHIVE_REQUIRE_AUTH="false",
        )
        response = client.get("/archive")
        assert response.status_code == 200
        assert response.headers["Cache-Control"] == "private, no-store"
        assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]

    def test_archive_api_lists_and_reads_without_storage_urls(self, client, monkeypatch):
        document = self._install_service(monkeypatch)
        _configure(
            monkeypatch,
            ARCHIVE_UI_ENABLED="true",
            ARCHIVE_REQUIRE_AUTH="false",
        )

        listing = client.get("/api/archive/analyses")
        detail = client.get(f"/api/archive/analyses/{document.archive_id}")

        assert listing.status_code == 200
        assert listing.headers["Cache-Control"] == "private, no-store"
        assert listing.json()["items"][0]["archive_id"] == document.archive_id
        assert detail.status_code == 200
        serialized = json.dumps(detail.json()).lower()
        assert "blob.core.windows.net" not in serialized
        assert "subscriber" not in serialized
        assert "job_relevance" not in serialized

    def test_archive_openapi_has_no_job_relevance_filter(self, client):
        operation = client.get("/openapi.json").json()["paths"]["/api/archive/analyses"]["get"]
        parameter_names = {parameter["name"] for parameter in operation["parameters"]}
        assert "job_relevance" not in parameter_names

    def test_archive_detail_returns_404_for_unknown_id(self, client, monkeypatch):
        self._install_service(monkeypatch)
        _configure(
            monkeypatch,
            ARCHIVE_UI_ENABLED="true",
            ARCHIVE_REQUIRE_AUTH="false",
        )
        unknown = "8211694095999-ffffffffffffffffffffffffffffffff"
        assert client.get(f"/api/archive/analyses/{unknown}").status_code == 404

    def test_archive_list_rejects_an_inverted_date_range(self, client, monkeypatch):
        self._install_service(monkeypatch)
        _configure(
            monkeypatch,
            ARCHIVE_UI_ENABLED="true",
            ARCHIVE_REQUIRE_AUTH="false",
        )
        response = client.get(
            "/api/archive/analyses",
            params={
                "analyzed_after": "2026-08-31T00:00:00Z",
                "analyzed_before": "2026-08-01T00:00:00Z",
            },
        )
        assert response.status_code == 422
