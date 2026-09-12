"""Tests for service layer with mocks."""

import asyncio
import json
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agent.scope import AnalysisScope, analysis_scope_context
from src.services.resource_graph import ResourceGraphQueryBuilder, ResourceGraphService

SUBSCRIPTION_A = "11111111-1111-1111-1111-111111111111"
SUBSCRIPTION_B = "22222222-2222-2222-2222-222222222222"


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

    def test_scoped_subscription_enrichment_never_discovers_tenant_subscriptions(self):
        with patch("src.services.resource_graph.get_settings") as mock:
            settings = MagicMock()
            settings.azure_subscription_id = None
            mock.return_value = settings
            service = ResourceGraphService()
        service._discover_accessible_subscriptions = MagicMock(
            side_effect=AssertionError("tenant-wide discovery must not run")
        )
        rows = [{"name": "resource", "subscriptionId": SUBSCRIPTION_A}]

        with analysis_scope_context(AnalysisScope(subscriptions=[SUBSCRIPTION_A])):
            result = service.enrich_subscription_names(rows)

        assert result[0]["subscriptionName"] == SUBSCRIPTION_A
        service._discover_accessible_subscriptions.assert_not_called()

    @pytest.mark.asyncio
    async def test_management_group_and_resource_group_scope_reaches_query_request(self):
        with patch("src.services.resource_graph.get_settings") as mock:
            settings = MagicMock()
            settings.azure_subscription_id = "deployment-sub"
            mock.return_value = settings
            service = ResourceGraphService()

        response = MagicMock(data=[], count=0, total_records=0)
        service._client = MagicMock()
        service._client.resources.return_value = response

        scope = AnalysisScope(
            management_groups=["/providers/Microsoft.Management/managementGroups/platform-mg"],
            resource_groups=["Production-RG"],
        )
        with analysis_scope_context(scope):
            await service.query_resources("Resources | project name, resourceGroup")

        request = service._client.resources.call_args.args[0]
        assert request.management_groups == ["platform-mg"]
        assert request.subscriptions is None
        assert "resourceGroup in~ ('Production-RG')" in request.query

    @pytest.mark.asyncio
    async def test_all_hierarchy_fields_are_intersected(self):
        with patch("src.services.resource_graph.get_settings") as mock:
            settings = MagicMock()
            settings.azure_subscription_id = None
            mock.return_value = settings
            service = ResourceGraphService()

        service._client = MagicMock()
        service._client.resources.return_value = MagicMock(data=[], count=0, total_records=0)
        scope = AnalysisScope(
            management_groups=["platform-mg"],
            subscriptions=[SUBSCRIPTION_A],
            resource_groups=["production-rg"],
        )

        with analysis_scope_context(scope):
            await service.query_resources("Resources | project name")

        request = service._client.resources.call_args.args[0]
        assert request.management_groups == ["platform-mg"]
        assert request.subscriptions is None
        assert f"subscriptionId in~ ('{SUBSCRIPTION_A}')" in request.query
        assert "resourceGroup in~ ('production-rg')" in request.query

    @pytest.mark.asyncio
    async def test_subscription_scope_overrides_the_deployment_default(self):
        with patch("src.services.resource_graph.get_settings") as mock:
            settings = MagicMock()
            settings.azure_subscription_id = "deployment-sub"
            mock.return_value = settings
            service = ResourceGraphService()

        response = MagicMock(data=[], count=0, total_records=0)
        service._client = MagicMock()
        service._client.resources.return_value = response

        with analysis_scope_context(AnalysisScope(subscriptions=[SUBSCRIPTION_A])):
            await service.query_resources("Resources | project name")

        request = service._client.resources.call_args.args[0]
        assert request.subscriptions == [SUBSCRIPTION_A]
        assert request.management_groups is None

    @pytest.mark.asyncio
    async def test_scoped_query_rejects_union_that_could_escape_the_boundary(self):
        with patch("src.services.resource_graph.get_settings") as mock:
            settings = MagicMock()
            settings.azure_subscription_id = "sub-1"
            mock.return_value = settings
            service = ResourceGraphService()

        with analysis_scope_context(AnalysisScope(resource_groups=["rg-1"])):
            with pytest.raises(ValueError, match="cannot contain union or join"):
                await service.query_resources("Resources | union ResourceContainers")

            with pytest.raises(ValueError, match="cannot contain union or join"):
                await service.query_resources(
                    "Resources | join kind=leftouter (ResourceContainers) on subscriptionId"
                )

    @pytest.mark.asyncio
    async def test_concurrent_scopes_do_not_leak_between_query_requests(self):
        with patch("src.services.resource_graph.get_settings") as mock:
            settings = MagicMock()
            settings.azure_subscription_id = "deployment-sub"
            mock.return_value = settings
            service = ResourceGraphService()

        requests = []

        def resources(request):
            requests.append(request)
            return MagicMock(data=[], count=0, total_records=0)

        service._client = MagicMock()
        service._client.resources.side_effect = resources

        async def run(scope):
            with analysis_scope_context(scope):
                await service.query_resources("Resources | project name, resourceGroup")

        await asyncio.gather(
            run(AnalysisScope(subscriptions=[SUBSCRIPTION_A], resource_groups=["rg-a"])),
            run(AnalysisScope(subscriptions=[SUBSCRIPTION_B], resource_groups=["rg-b"])),
        )

        observed = {(tuple(request.subscriptions or []), request.query) for request in requests}
        assert len(observed) == 2
        assert any(
            subscriptions == (SUBSCRIPTION_A,) and "'rg-a'" in query
            for subscriptions, query in observed
        )
        assert any(
            subscriptions == (SUBSCRIPTION_B,) and "'rg-b'" in query
            for subscriptions, query in observed
        )


class TestResourceGraphPaging:
    @staticmethod
    def make_service(responses):
        with patch("src.services.resource_graph.get_settings") as settings:
            settings.return_value.azure_subscription_id = SUBSCRIPTION_A
            service = ResourceGraphService()
        service._client = MagicMock()
        service._client.resources.side_effect = responses
        return service

    @pytest.mark.asyncio
    async def test_continuation_pages_keep_query_scope_and_object_array_format(self):
        service = self.make_service(
            [
                MagicMock(
                    data=[{"id": "first"}],
                    total_records=2,
                    skip_token="page-two",
                    result_truncated="true",
                ),
                MagicMock(
                    data=[{"id": "second"}],
                    total_records=2,
                    skip_token=None,
                    result_truncated="false",
                ),
            ]
        )
        with analysis_scope_context(
            AnalysisScope(subscriptions=[SUBSCRIPTION_A], resource_groups=["rg-a"])
        ):
            result = await service.query_resources("Resources | project id | order by id asc")

        assert result["data"] == [{"id": "first"}, {"id": "second"}]
        assert result["count"] == 2
        assert result["pages_fetched"] == 2
        assert result["result_truncated"] is False
        first, second = [call.args[0] for call in service._client.resources.call_args_list]
        assert first.query == second.query
        assert "resourceGroup in~ ('rg-a')" in second.query
        assert second.subscriptions == [SUBSCRIPTION_A]
        assert result["executed_query"] == second.query
        assert result["query_scope"] == {
            "subscriptions": [SUBSCRIPTION_A],
            "management_groups": [],
            "resource_groups": ["rg-a"],
        }
        assert result["queried_at"].endswith("+00:00")
        assert first.options.result_format == "objectArray"
        assert second.options.skip_token == "page-two"

    @pytest.mark.asyncio
    async def test_missing_continuation_token_is_explicit_partial_result(self):
        service = self.make_service(
            [
                MagicMock(
                    data=[{"id": "first"}],
                    total_records=1500,
                    skip_token=None,
                    result_truncated="true",
                )
            ]
        )
        result = await service.query_resources("Resources | project id | order by id asc")
        assert result["result_truncated"] is True
        assert "No continuation token" in result["truncation_reason"]
        assert result["count"] == 1
        assert result["total_records"] == 1500

    @pytest.mark.asyncio
    async def test_repeated_token_stops_paging(self):
        service = self.make_service(
            [
                MagicMock(
                    data=[{"id": "first"}],
                    total_records=3,
                    skip_token="repeated",
                    result_truncated="true",
                ),
                MagicMock(
                    data=[{"id": "second"}],
                    total_records=3,
                    skip_token="repeated",
                    result_truncated="true",
                ),
            ]
        )
        result = await service.query_resources("Resources | project id | order by id asc")
        assert result["pages_fetched"] == 2
        assert result["result_truncated"] is True
        assert "stalled" in result["truncation_reason"]

    @pytest.mark.asyncio
    async def test_page_budget_is_bounded_and_disclosed(self):
        service = self.make_service(
            [
                MagicMock(
                    data=[{"id": "first"}],
                    total_records=3,
                    skip_token="next",
                    result_truncated="true",
                )
            ]
        )
        with patch("src.services.resource_graph._MAX_QUERY_PAGES", 1):
            result = await service.query_resources("Resources | project id | order by id asc")
        assert result["pages_fetched"] == 1
        assert result["result_truncated"] is True
        assert "budget" in result["truncation_reason"]

    @pytest.mark.asyncio
    async def test_literal_keywords_and_comments_do_not_trigger_scope_rejection(self):
        service = self.make_service(
            [MagicMock(data=[], total_records=0, skip_token=None, result_truncated="false")]
        )
        query = "// join examples\nResources | where name == 'join top 10' | project id"
        with analysis_scope_context(AnalysisScope(resource_groups=["rg-a"])):
            await service.query_resources(query)
        executed = service._client.resources.call_args.args[0].query
        assert "name == 'join top 10'" in executed
        assert "resourceGroup in~ ('rg-a')" in executed


class TestAnalysisScope:
    def test_report_resource_must_match_configured_subscription_and_group(self):
        scope = AnalysisScope(
            subscriptions=[SUBSCRIPTION_A.upper()],
            resource_groups=["Production-RG"],
        )

        assert scope.contains_resource(
            {"subscriptionId": SUBSCRIPTION_A, "resourceGroup": "production-rg"}
        )
        assert not scope.contains_resource(
            {"subscriptionId": SUBSCRIPTION_B, "resourceGroup": "production-rg"}
        )
        assert not scope.contains_resource(
            {"subscriptionId": SUBSCRIPTION_A, "resourceGroup": "other-rg"}
        )
        assert not scope.contains_resource({"name": "missing-scope-fields"})

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("subscriptions", "11111111-1111-1111-1111-111111111111"),
            ("subscriptions", ["not-a-guid"]),
            ("management_groups", ["/"]),
            ("management_groups", ["bad value"]),
            ("resource_groups", ["rg' | take 1"]),
            ("resource_groups", ["ends-with-period."]),
        ],
    )
    def test_invalid_scope_values_fail_closed(self, field, value):
        with pytest.raises(ValueError):
            AnalysisScope(**{field: value})

    def test_unicode_resource_group_name_is_supported(self):
        assert AnalysisScope(resource_groups=["운영-RG"]).resource_groups == ("운영-RG",)

    def test_full_resource_group_id_derives_and_preserves_parent_subscription(self):
        scope = AnalysisScope(
            resource_groups=[f"/subscriptions/{SUBSCRIPTION_A}/resourceGroups/Production-RG"]
        )

        assert scope.subscriptions == (SUBSCRIPTION_A,)
        assert scope.resource_groups == (
            f"/subscriptions/{SUBSCRIPTION_A}/resourceGroups/Production-RG",
        )
        assert scope.contains_resource(
            {"subscriptionId": SUBSCRIPTION_A, "resourceGroup": "production-rg"}
        )
        assert not scope.contains_resource(
            {"subscriptionId": SUBSCRIPTION_B, "resourceGroup": "production-rg"}
        )

    def test_full_resource_group_id_rejects_conflicting_explicit_subscription(self):
        with pytest.raises(ValueError, match="outside the explicit subscription scope"):
            AnalysisScope(
                subscriptions=[SUBSCRIPTION_B],
                resource_groups=[f"/subscriptions/{SUBSCRIPTION_A}/resourceGroups/Production-RG"],
            )

    @pytest.mark.asyncio
    async def test_full_resource_group_id_injects_exact_subscription_pair(self):
        with patch("src.services.resource_graph.get_settings") as mock:
            settings = MagicMock()
            settings.azure_subscription_id = None
            mock.return_value = settings
            service = ResourceGraphService()
        service._client = MagicMock()
        service._client.resources.return_value = MagicMock(data=[], count=0, total_records=0)
        scope = AnalysisScope(
            resource_groups=[f"/subscriptions/{SUBSCRIPTION_A}/resourceGroups/Production-RG"]
        )

        with analysis_scope_context(scope):
            await service.query_resources("Resources | project name")

        request = service._client.resources.call_args.args[0]
        assert request.subscriptions == [SUBSCRIPTION_A]
        assert (
            f"subscriptionId =~ '{SUBSCRIPTION_A}' and "
            "resourceGroup =~ 'Production-RG'" in request.query
        )


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

    @pytest.mark.asyncio
    async def test_scoped_summary_never_reuses_or_overwrites_tenant_cache(self):
        import src.services.resource_graph as rg_module

        cached = {"data": [{"type": "tenant-wide"}], "count": 1}
        rg_module._resource_types_cache = cached
        rg_module._resource_types_cache_time = __import__("time").time()

        with patch("src.services.resource_graph.get_settings") as mock:
            settings = MagicMock()
            settings.azure_subscription_id = "deployment-sub"
            mock.return_value = settings
            service = ResourceGraphService()
        service._client = MagicMock()
        service._client.resources.return_value = MagicMock(
            data=[{"type": "scoped"}],
            count=1,
            total_records=1,
        )

        with analysis_scope_context(AnalysisScope(subscriptions=[SUBSCRIPTION_A])):
            result = await service.get_resource_types_summary()

        assert result["data"] == [{"type": "scoped"}]
        assert rg_module._resource_types_cache is cached

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

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("method_name", "dimension", "result_key", "item_key"),
        [
            ("get_cost_by_resource_type", "ResourceType", "costs_by_type", "resource_type"),
            ("get_cost_by_service", "ServiceName", "costs_by_service", "service"),
        ],
    )
    async def test_cost_totals_and_currency_survive_top_limit(
        self, method_name: str, dimension: str, result_key: str, item_key: str
    ):
        from src.services.cost_management import CostManagementService

        service = CostManagementService(subscription_id="00000000-0000-0000-0000-000000000001")
        columns = []
        for column_name in ("Cost", dimension, "Currency"):
            column = MagicMock()
            column.name = column_name
            columns.append(column)
        service._client = MagicMock()
        service._client.query.usage.return_value = MagicMock(
            columns=columns,
            rows=[[1, "Small", "KRW"], [10, "Largest", "KRW"], [2, "Other", "KRW"]],
            next_link=None,
        )

        result = await getattr(service, method_name)(days=7, top=1)

        assert result["success"] is True
        assert result["total_cost"] == 13
        assert result[result_key] == [{item_key: "Largest", "cost": 10, "currency": "KRW"}]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("tool_class_name", "filter_key", "filter_value", "dimension"),
        [
            (
                "GetCostByResourceTypeTool",
                "resource_type",
                "microsoft.storage/storageaccounts",
                "ResourceType",
            ),
            ("GetCostByServiceTool", "service_name", "Storage", "ServiceName"),
        ],
    )
    async def test_cost_tool_preserves_scope_filter_currency_and_period(
        self, tool_class_name: str, filter_key: str, filter_value: str, dimension: str
    ):
        from src.agent import tools
        from src.services.cost_management import CostManagementService

        service = CostManagementService(subscription_id=SUBSCRIPTION_A)
        columns = []
        for column_name in ("Currency", dimension, "PreTaxCost"):
            column = MagicMock()
            column.name = column_name
            columns.append(column)
        service._client = MagicMock()
        service._client.query.usage.return_value = MagicMock(
            columns=columns, rows=[["KRW", filter_value, 1200.25]], next_link=None
        )
        tool = getattr(tools, tool_class_name)(service=service)

        output = await tool.ainvoke(
            {"days": 7, "top": 1, "subscription_id": SUBSCRIPTION_B, filter_key: filter_value}
        )
        result = json.loads(output)
        query_args = service._client.query.usage.call_args.kwargs

        assert query_args["scope"] == f"/subscriptions/{SUBSCRIPTION_B}"
        assert query_args["parameters"].dataset.filter.serialize() == {
            "dimensions": {"name": dimension, "operator": "In", "values": [filter_value]}
        }
        assert service.subscription_id == SUBSCRIPTION_A
        assert result["scope"] == query_args["scope"]
        assert result["filter"] == {dimension: filter_value}
        assert result["currency"] == "KRW"
        assert result["cost_type"] == "ActualCost"
        assert result["has_cost_data"] is True
        assert result["total_cost"] == 1200.25
        assert result["period_days"] == 7
        assert result["start_date"] < result["end_date"]
        assert "$" not in output

    @pytest.mark.asyncio
    @pytest.mark.parametrize("failure", ["pagination", "mixed_currency", "missing_currency", "nan"])
    async def test_cost_query_rejects_unreliable_totals(self, failure: str):
        from src.services.cost_management import CostManagementService

        service = CostManagementService(subscription_id=SUBSCRIPTION_A)
        columns = []
        for column_name in ("Cost", "ResourceType", "Currency"):
            column = MagicMock()
            column.name = column_name
            columns.append(column)
        rows = [[10, "Storage", "KRW"]]
        if failure == "mixed_currency":
            rows.append([20, "Compute", "USD"])
        if failure == "missing_currency":
            rows[0][2] = ""
        if failure == "nan":
            rows[0][0] = float("nan")
        service._client = MagicMock()
        service._client.query.usage.return_value = MagicMock(
            columns=columns,
            rows=rows,
            next_link="https://management.azure.com/next" if failure == "pagination" else None,
        )

        result = await service.get_cost_by_resource_type()

        assert result["success"] is False
        assert "total_cost" not in result
        assert result["error"]

    @pytest.mark.asyncio
    async def test_cost_query_does_not_pick_first_of_multiple_subscriptions(self):
        from src.services.cost_management import CostManagementService

        service = CostManagementService(subscription_id=SUBSCRIPTION_A)
        service.subscription_id = None
        service._client = MagicMock()
        with (
            patch("src.config.get_azure_credential", return_value=MagicMock()),
            patch(
                "src.services.discover_subscriptions_async",
                new=AsyncMock(
                    return_value=[
                        {"subscriptionId": SUBSCRIPTION_A},
                        {"subscriptionId": SUBSCRIPTION_B},
                    ]
                ),
            ),
        ):
            result = await service.get_cost_by_resource_type()

        assert result["success"] is False
        assert "explicit or uniquely discoverable subscription" in result["error"]
        service._client.query.usage.assert_not_called()

    @pytest.mark.asyncio
    async def test_cost_query_never_broadens_bounded_analysis(self):
        from src.services.cost_management import CostManagementService

        service = CostManagementService(subscription_id=SUBSCRIPTION_A)
        service._client = MagicMock()
        with analysis_scope_context(AnalysisScope(subscriptions=[SUBSCRIPTION_A])):
            result = await service.get_cost_by_service(subscription_id=SUBSCRIPTION_B)

        assert result["success"] is False
        service._client.query.usage.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_cost_data_is_not_confirmed_zero(self):
        from src.agent.tools import GetCostByServiceTool
        from src.services.cost_management import CostManagementService

        service = CostManagementService(subscription_id=SUBSCRIPTION_A)
        service._client = MagicMock()
        service._client.query.usage.return_value = MagicMock(rows=[], next_link=None)

        output = await GetCostByServiceTool(service=service).ainvoke({"days": 30})
        result = json.loads(output)

        assert result["success"] is True
        assert result["has_cost_data"] is False
        assert result["currency"] == ""
        assert result["costs_by_service"] == []
        assert result["scope"] == f"/subscriptions/{SUBSCRIPTION_A}"


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

    @pytest.mark.asyncio
    async def test_fetch_page_content_extracts_only_descriptive_trusted_visuals(self):
        """Learn pages expose bounded email visuals without external or decorative images."""
        from src.services.microsoft_learn import MicrosoftLearnService

        response = MagicMock(
            status_code=200,
            url="https://learn.microsoft.com/azure/storage/example",
            text="""
                <main>
                  <h1>Configure storage</h1>
                  <figure>
                    <img src="media/example/portal-setting.png" alt="Storage configuration pane">
                    <figcaption>Set the minimum TLS version in the Azure portal.</figcaption>
                  </figure>
                  <img src="https://evil.example/screenshot.png" alt="External image">
                  <img src="/media/example/info.svg" alt="icon">
                  <p>Configuration guidance.</p>
                </main>
            """,
        )
        client = AsyncMock()
        client.is_closed = False
        client.get.return_value = response
        service = MicrosoftLearnService()
        service._client = client

        result = await service.fetch_page_content(
            "https://learn.microsoft.com/azure/storage/example"
        )

        assert result is not None
        assert result["visuals"] == [
            {
                "url": "https://learn.microsoft.com/azure/storage/media/example/portal-setting.png",
                "alt": "Storage configuration pane",
                "caption": "Set the minimum TLS version in the Azure portal.",
                "source_url": "https://learn.microsoft.com/azure/storage/example",
                "source_title": "Configure storage",
            }
        ]
