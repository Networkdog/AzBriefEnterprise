"""Tests for FastAPI endpoints."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.agent.analyzer import AnalysisResult, RelevanceStatus, UrgencyLevel
from src.rss.parser import AzureUpdate


@pytest.fixture
def app():
    """Create test FastAPI app with mocked globals."""
    from src.main import app

    return app


@pytest.fixture
def client(app):
    """Create test client."""
    return TestClient(app)


@pytest.fixture
def mock_update():
    """Create a mock AzureUpdate."""
    return AzureUpdate(
        id="123456",
        title="Test Update",
        description="Test description",
        link="https://azure.microsoft.com/updates?id=123456",
        published_date=datetime(2026, 3, 10, 18, 0, tzinfo=timezone.utc),
        categories=["Storage"],
        azure_services=["Blob Storage"],
        update_type="General Availability",
        status=None,
    )


@pytest.fixture
def mock_analysis_result():
    """Create a mock AnalysisResult."""
    return AnalysisResult(
        update_id="123456",
        update_title="Test Update",
        relevance=RelevanceStatus.RELEVANT,
        urgency=UrgencyLevel.MEDIUM,
        one_line_summary="Test summary",
        relevance_reason="Test reason",
        affected_resources=[],
        impact_summary="Test impact",
        recommendations=["Test recommendation"],
        reference_docs=[],
        should_notify=True,
    )


class TestHealthEndpoint:
    """Test /health endpoint."""

    def test_health_returns_200(self, client):
        """Health endpoint returns 200."""
        with patch("src.main.get_settings") as mock_settings:
            settings = MagicMock()
            settings.use_hosted_agent = True
            settings.foundry_hosted_agent_name = "azbrief-analysis-hosted"
            mock_settings.return_value = settings
            with patch("src.config.get_azure_credential") as mock_cred:
                mock_cred.return_value.get_token.return_value = MagicMock()
                response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] in ("healthy", "degraded")
        assert "version" in data

    def test_health_degraded_when_credential_fails(self, client):
        """Health returns degraded when Azure credential fails."""
        with patch("src.main.get_settings") as mock_settings:
            settings = MagicMock()
            settings.use_hosted_agent = True
            settings.foundry_hosted_agent_name = "azbrief-analysis-hosted"
            mock_settings.return_value = settings
            with patch("src.config.get_azure_credential", side_effect=Exception("Auth failed")):
                response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "degraded"


class TestRootEndpoint:
    """Test / endpoint."""

    def test_root_returns_info(self, client):
        """Root endpoint returns app info."""
        response = client.get("/")
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "AzBrief"
        assert "version" in data


class TestAnalyzeEndpoint:
    """Test /api/analyze endpoint."""

    def test_analyze_requires_valid_url(self, client):
        """Analyze rejects invalid URLs."""
        response = client.post(
            "/api/analyze",
            json={"update_url": "https://evil.com/malware"},
        )
        assert response.status_code == 422

    def test_analyze_rejects_non_https(self, client):
        """Analyze rejects non-HTTPS URLs."""
        response = client.post(
            "/api/analyze",
            json={"update_url": "ftp://azure.microsoft.com/updates?id=1"},
        )
        assert response.status_code == 422

    def test_analyze_accepts_valid_url(self, client, mock_update, mock_analysis_result):
        """Analyze accepts valid Azure URLs when services are initialized."""
        import src.main as main_module

        mock_rss = AsyncMock()
        mock_rss.get_update_by_url = AsyncMock(return_value=mock_update)
        mock_analyzer = AsyncMock()
        mock_analyzer.analyze_update = AsyncMock(return_value=mock_analysis_result)

        original_analyzer = main_module.analyzer
        original_rss = main_module.rss_parser
        try:
            main_module.analyzer = mock_analyzer
            main_module.rss_parser = mock_rss

            response = client.post(
                "/api/analyze",
                json={
                    "update_url": "https://azure.microsoft.com/updates?id=123",
                    "send_email": False,
                },
            )
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "success"
            assert data["is_relevant"] is True
        finally:
            main_module.analyzer = original_analyzer
            main_module.rss_parser = original_rss

    def test_analyze_returns_500_when_not_initialized(self, client):
        """Analyze returns 500 when services not initialized."""
        import src.main as main_module

        original = main_module.analyzer
        try:
            main_module.analyzer = None
            response = client.post(
                "/api/analyze",
                json={"update_url": "https://azure.microsoft.com/updates?id=1"},
            )
            assert response.status_code == 500
        finally:
            main_module.analyzer = original


class TestBatchAnalyzeEndpoint:
    """Test /api/batch/analyze endpoint."""

    def test_batch_rejects_empty_list(self, client):
        """Batch rejects empty URL list."""
        response = client.post("/api/batch/analyze", json=[])
        assert response.status_code == 400

    def test_batch_rejects_too_many_urls(self, client):
        """Batch rejects more than 10 URLs."""
        urls = [f"https://azure.microsoft.com/updates?id={i}" for i in range(11)]
        response = client.post("/api/batch/analyze", json=urls)
        assert response.status_code == 400

    def test_batch_validates_urls(self, client):
        """Batch validates all URLs before processing."""
        urls = [
            "https://azure.microsoft.com/updates?id=1",
            "https://evil.com/malware",
        ]
        response = client.post("/api/batch/analyze", json=urls)
        assert response.status_code == 400
        assert "evil.com" in response.json()["detail"]


class TestRSSCheckEndpoint:
    """Test /api/rss/check endpoint."""

    def test_rss_check_returns_500_when_not_initialized(self, client):
        """RSS check returns 500 when parser not initialized."""
        import src.main as main_module

        original = main_module.rss_parser
        try:
            main_module.rss_parser = None
            response = client.post(
                "/api/rss/check",
                json={"processed_ids": []},
            )
            assert response.status_code == 500
        finally:
            main_module.rss_parser = original


class TestAPIAuthentication:
    """Test API key authentication."""

    def test_api_endpoints_open_when_no_key_configured(
        self, client, mock_update, mock_analysis_result
    ):
        """When AZBRIEF_API_KEY is not set, endpoints are open."""
        import src.main as main_module

        mock_rss = AsyncMock()
        mock_rss.get_update_by_url = AsyncMock(return_value=mock_update)
        mock_analyzer = AsyncMock()
        mock_analyzer.analyze_update = AsyncMock(return_value=mock_analysis_result)

        original_analyzer = main_module.analyzer
        original_rss = main_module.rss_parser
        try:
            main_module.analyzer = mock_analyzer
            main_module.rss_parser = mock_rss

            with patch("src.middleware.get_settings") as mock_settings:
                settings = MagicMock()
                settings.api_key = None
                mock_settings.return_value = settings

                response = client.post(
                    "/api/analyze",
                    json={
                        "update_url": "https://azure.microsoft.com/updates?id=1",
                        "send_email": False,
                    },
                )
                assert response.status_code == 200
        finally:
            main_module.analyzer = original_analyzer
            main_module.rss_parser = original_rss

    def test_api_rejects_missing_key(self, client):
        """When API key is configured, missing key returns 401."""
        with patch("src.middleware.get_settings") as mock_settings:
            settings = MagicMock()
            settings.api_key = "secret-key-123"
            mock_settings.return_value = settings

            response = client.post(
                "/api/analyze",
                json={"update_url": "https://azure.microsoft.com/updates?id=1"},
            )
            assert response.status_code == 401

    def test_api_rejects_wrong_key(self, client):
        """When API key is configured, wrong key returns 403."""
        with patch("src.middleware.get_settings") as mock_settings:
            settings = MagicMock()
            settings.api_key = "secret-key-123"
            mock_settings.return_value = settings

            response = client.post(
                "/api/analyze",
                json={"update_url": "https://azure.microsoft.com/updates?id=1"},
                headers={"X-API-Key": "wrong-key"},
            )
            assert response.status_code == 403
