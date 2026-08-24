"""Tests for service layer with mocks."""

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.resource_graph import ResourceGraphQueryBuilder, ResourceGraphService


class TestResourceGraphQueryBuilder:
    """Test KQL query construction."""

    def test_sanitize_kql_value_strips_dangerous_chars(self):
        """Sanitization removes injection-risk characters."""
        val = ResourceGraphQueryBuilder._sanitize_kql_value("vm' | take 1; --")
        assert "'" not in val
        assert "|" not in val
        assert ";" not in val

    def test_sanitize_kql_value_truncates_long_input(self):
        """Values over 200 chars are truncated."""
        val = ResourceGraphQueryBuilder._sanitize_kql_value("x" * 500)
        assert len(val) == 200

    def test_get_resource_types_summary_query(self):
        """Summary query groups by type."""
        q = ResourceGraphQueryBuilder.get_resource_types_summary()
        assert "summarize count() by type" in q

    def test_get_resource_regions_summary_query(self):
        """Regions summary groups by location."""
        q = ResourceGraphQueryBuilder.get_resource_regions_summary()
        assert "summarize count() by location" in q

    def test_find_related_resources_single_keyword(self):
        """Single keyword produces valid query."""
        q = ResourceGraphQueryBuilder.find_related_resources(["storage"])
        assert "type contains 'storage'" in q

    def test_find_related_resources_multiple_keywords(self):
        """Multiple keywords joined with OR."""
        q = ResourceGraphQueryBuilder.find_related_resources(["vm", "compute"])
        assert "type contains 'vm'" in q
        assert "type contains 'compute'" in q
        assert " or " in q

    def test_find_related_resources_empty_list(self):
        """Empty keywords produce valid fallback query."""
        q = ResourceGraphQueryBuilder.find_related_resources([])
        assert "where" in q.lower()

    def test_service_dispatcher_storage(self):
        """Storage services map to storage account query."""
        q = ResourceGraphQueryBuilder.get_query_for_update_service("Blob Storage")
        assert "Microsoft.Storage/storageAccounts" in q

    def test_service_dispatcher_vm(self):
        """VM services map to virtual machines query."""
        q = ResourceGraphQueryBuilder.get_query_for_update_service("Virtual Machines")
        assert "Microsoft.Compute/virtualMachines" in q

    def test_service_dispatcher_aks(self):
        """AKS services map to managed clusters query."""
        q = ResourceGraphQueryBuilder.get_query_for_update_service("AKS")
        assert "Microsoft.ContainerService/managedClusters" in q

    def test_service_dispatcher_unknown_falls_back(self):
        """Unknown service names fall back to keyword search."""
        q = ResourceGraphQueryBuilder.get_query_for_update_service("SomeNewService")
        assert "SomeNewService" in q


class TestResourceGraphServiceInit:
    """Test ResourceGraphService initialization."""

    def test_uses_settings_subscription(self):
        """Service uses subscription from settings if not provided."""
        with patch("src.services.resource_graph.get_settings") as mock:
            settings = MagicMock()
            settings.azure_subscription_id = "sub-123"
            mock.return_value = settings
            svc = ResourceGraphService()
            assert svc.subscription_id == "sub-123"

    def test_override_subscription(self):
        """Explicit subscription overrides settings."""
        with patch("src.services.resource_graph.get_settings") as mock:
            settings = MagicMock()
            settings.azure_subscription_id = "default-sub"
            mock.return_value = settings
            svc = ResourceGraphService(subscription_id="custom-sub")
            assert svc.subscription_id == "custom-sub"

    def test_enrich_subscription_names_adds_names(self):
        """enrich_subscription_names adds subscriptionName field."""
        with patch("src.services.resource_graph.get_settings") as mock:
            settings = MagicMock()
            settings.azure_subscription_id = "sub-1"
            mock.return_value = settings
            svc = ResourceGraphService()
            svc._subscription_name_map = {"sub-1": "MySubscription"}
            svc._discovered_subscriptions = ["sub-1"]

            data = [{"name": "res1", "subscriptionId": "sub-1"}]
            svc.enrich_subscription_names(data)
            assert data[0]["subscriptionName"] == "MySubscription"

    def test_enrich_subscription_names_handles_empty(self):
        """enrich_subscription_names handles empty data."""
        with patch("src.services.resource_graph.get_settings") as mock:
            settings = MagicMock()
            settings.azure_subscription_id = None
            mock.return_value = settings
            svc = ResourceGraphService()
            result = svc.enrich_subscription_names([])
            assert result == []


class TestResourceGraphServiceCache:
    """Test thread-safe caching."""

    @pytest.mark.asyncio
    async def test_cache_hit(self):
        """Second call within TTL returns cached result."""
        import src.services.resource_graph as rg_module

        # Reset cache state
        rg_module._resource_types_cache = {"data": [{"type": "cached"}], "count": 1}
        rg_module._resource_types_cache_time = __import__("time").time()

        with patch("src.services.resource_graph.get_settings") as mock:
            settings = MagicMock()
            settings.azure_subscription_id = "sub-1"
            mock.return_value = settings
            svc = ResourceGraphService()
            result = await svc.get_resource_types_summary()
            assert result["data"][0]["type"] == "cached"

        # Cleanup
        rg_module._resource_types_cache = None
        rg_module._resource_types_cache_time = 0


class TestKQLSanitize:
    """Test sanitize_kql function from tools."""

    def test_top_without_by_becomes_take(self):
        """'| top N' without ORDER BY becomes '| take N'."""
        from src.agent.tools import sanitize_kql

        q = sanitize_kql("Resources | top 50")
        assert "take 50" in q
        assert "top 50" not in q

    def test_top_with_by_preserved(self):
        """'| top N by col' is preserved."""
        from src.agent.tools import sanitize_kql

        q = sanitize_kql("Resources | top 50 by name asc")
        assert "top 50 by name asc" in q

    def test_let_statements_removed(self):
        """let statements are stripped (not supported in Resource Graph)."""
        from src.agent.tools import sanitize_kql

        q = sanitize_kql("let x = 5;\nResources | take 10")
        assert "let" not in q
        assert "Resources" in q

    def test_render_removed(self):
        """render operator is removed."""
        from src.agent.tools import sanitize_kql

        q = sanitize_kql("Resources | summarize count() by type | render barchart")
        assert "render" not in q

    def test_trailing_semicolons_stripped(self):
        """Trailing semicolons are stripped."""
        from src.agent.tools import sanitize_kql

        q = sanitize_kql("Resources | take 10;")
        assert not q.endswith(";")

    def test_project_except_to_project_away(self):
        """project-except is replaced with project-away."""
        from src.agent.tools import sanitize_kql

        q = sanitize_kql("Resources | project-except kind")
        assert "project-away" in q
        assert "project-except" not in q


class TestCostManagementService:
    """Test CostManagementService."""

    @pytest.mark.asyncio
    async def test_get_cost_by_resource_type_no_subscription(self):
        """Returns error when no subscription."""
        with patch("src.services.cost_management.get_settings") as mock:
            settings = MagicMock()
            settings.azure_subscription_id = None
            mock.return_value = settings

            from src.services.cost_management import CostManagementService

            svc = CostManagementService()
            svc._subscription_discovered = True
            svc.subscription_id = None

            # Mock the client to fail
            mock_client = MagicMock()
            mock_client.query.usage.side_effect = Exception("No subscription")
            svc._client = mock_client

            result = await svc.get_cost_by_resource_type(days=7)
            assert result["success"] is False


class TestLogAnalyticsService:
    """Test LogAnalyticsService."""

    @pytest.mark.asyncio
    async def test_query_logs_no_workspace(self):
        """Returns error when workspace not configured."""
        with patch("src.services.log_analytics.get_settings") as mock:
            settings = MagicMock()
            settings.log_analytics_workspace_id = None
            mock.return_value = settings

            from src.services.log_analytics import LogAnalyticsService

            svc = LogAnalyticsService()
            result = await svc.query_logs("AzureActivity | take 10")
            assert result["success"] is False
            assert "not configured" in result["error"]


class TestMicrosoftLearnService:
    """Test MicrosoftLearnService."""

    @pytest.mark.asyncio
    async def test_close_releases_client(self):
        """close() releases the HTTP client."""
        from src.services.microsoft_learn import MicrosoftLearnService

        svc = MicrosoftLearnService()
        # Get a client first
        client = await svc._get_client()
        assert client is not None
        assert not client.is_closed

        # Close should release it
        await svc.close()
        # After close, getting a new client should create a fresh one
        assert client.is_closed

    @pytest.mark.asyncio
    async def test_fallback_search_returns_results(self):
        """Fallback search generates relevant URLs."""
        from src.services.microsoft_learn import MicrosoftLearnService

        svc = MicrosoftLearnService()
        result = await svc._fallback_search("blob storage sftp")
        assert result["count"] > 0
        assert any("storage" in r.get("url", "").lower() for r in result["results"])
        await svc.close()

    @pytest.mark.asyncio
    async def test_fallback_search_unknown_term(self):
        """Fallback search returns generic Azure docs for unknown terms."""
        from src.services.microsoft_learn import MicrosoftLearnService

        svc = MicrosoftLearnService()
        result = await svc._fallback_search("xyznonexistent")
        assert result["count"] > 0
        await svc.close()
