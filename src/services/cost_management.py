"""Azure Cost Management Service using Azure SDK."""

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Optional
from uuid import UUID

from azure.core.exceptions import HttpResponseError
from azure.mgmt.costmanagement import CostManagementClient
from azure.mgmt.costmanagement.models import (
    QueryAggregation,
    QueryComparisonExpression,
    QueryDataset,
    QueryDefinition,
    QueryFilter,
    QueryGrouping,
    QueryTimePeriod,
)
from structlog import get_logger

from src.config import get_settings

logger = get_logger()

MAX_RETRIES = 3
RETRY_BASE_DELAY = 10  # seconds


class CostManagementService:
    """Service for Azure Cost Management queries."""

    def __init__(self, subscription_id: Optional[str] = None):
        """Initialize Cost Management service.

        Args:
            subscription_id: Azure subscription ID (uses config if not provided)
        """
        settings = get_settings()
        self.subscription_id = subscription_id or settings.azure_subscription_id
        self._client: Optional[CostManagementClient] = None
        self._credential = None
        self._subscription_discovered = False

    async def _ensure_subscription(self) -> None:
        """Lazily discover subscription if not configured (async-safe)."""
        if self.subscription_id or self._subscription_discovered:
            return
        self._subscription_discovered = True
        try:
            from src.config import get_azure_credential
            from src.services import discover_subscriptions_async

            credential = get_azure_credential()
            subs = await discover_subscriptions_async(credential)
            if len(subs) == 1:
                self.subscription_id = subs[0]["subscriptionId"]
                logger.info(
                    "cost_subscription_auto_discovered",
                    subscription_id=self.subscription_id,
                )
        except Exception as e:
            logger.warning("cost_subscription_discovery_failed", error=str(e))

    async def _resolve_scope(self, subscription_id: Optional[str]) -> str:
        """Resolve one explicit subscription without broadening a bounded analysis."""
        from src.agent.scope import current_analysis_scope

        if current_analysis_scope().is_bounded:
            raise ValueError("Cost Management cannot enforce the full subscriber resource scope")
        if not subscription_id:
            await self._ensure_subscription()
        resolved = subscription_id or self.subscription_id
        if not resolved:
            raise ValueError(
                "Cost query requires an explicit or uniquely discoverable subscription"
            )
        return f"/subscriptions/{UUID(resolved)}"

    def _get_client(self) -> CostManagementClient:
        """Get or create Cost Management client."""
        if self._client is None:
            from src.config import get_azure_credential

            self._credential = get_azure_credential()
            self._client = CostManagementClient(
                credential=self._credential, subscription_id=self.subscription_id
            )
        return self._client

    async def _query_with_retry(self, scope: str, query_definition: QueryDefinition) -> Any:
        """Execute a Cost Management query with retry on 429 rate limit errors.

        Args:
            scope: Azure scope (e.g., /subscriptions/{id})
            query_definition: The query definition to execute

        Returns:
            Query result from Cost Management API

        Raises:
            HttpResponseError: If all retries are exhausted
        """
        client = self._get_client()
        last_error = None

        for attempt in range(MAX_RETRIES):
            try:
                return await asyncio.to_thread(
                    client.query.usage, scope=scope, parameters=query_definition
                )
            except HttpResponseError as e:
                if e.status_code == 429:
                    last_error = e
                    delay = RETRY_BASE_DELAY * (2**attempt)
                    logger.warning(
                        "Cost Management rate limited (429), retrying",
                        attempt=attempt + 1,
                        max_retries=MAX_RETRIES,
                        delay_seconds=delay,
                    )
                    await asyncio.sleep(delay)
                else:
                    raise

        raise last_error  # type: ignore[misc]

    @staticmethod
    def _parse_cost_result(
        result: Any, dimension: str, item_key: str
    ) -> tuple[list[dict[str, Any]], float, str]:
        """Parse named columns without summing currencies or incomplete pages."""
        if result.next_link:
            raise ValueError("Cost query is incomplete: additional result pages are available")
        if not result.rows:
            return [], 0.0, ""
        columns = {column.name.lower(): index for index, column in enumerate(result.columns)}
        cost_column = next(
            (columns[name] for name in ("cost", "pretaxcost", "totalcost") if name in columns),
            None,
        )
        if cost_column is None or dimension.lower() not in columns or "currency" not in columns:
            raise ValueError("Cost response is missing cost, dimension, or currency columns")

        costs = []
        currencies = set()
        total = Decimal("0")
        for row in result.rows:
            amount = Decimal(str(row[cost_column]))
            currency = str(row[columns["currency"]] or "").strip()
            if not amount.is_finite() or not currency:
                raise ValueError("Cost response contains an invalid amount or missing currency")
            currencies.add(currency)
            total += amount
            costs.append(
                {
                    item_key: row[columns[dimension.lower()]],
                    "cost": float(amount),
                    "currency": currency,
                }
            )
        if len(currencies) != 1:
            raise ValueError(
                "Cost response contains multiple currencies; no combined total is valid"
            )
        costs.sort(key=lambda cost: cost["cost"], reverse=True)
        for cost in costs:
            cost["cost"] = round(cost["cost"], 2)
        return costs, float(round(total, 2)), currencies.pop()

    async def get_cost_by_resource_type(
        self,
        days: int = 30,
        top: int = 20,
        resource_type: Optional[str] = None,
        subscription_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Get cost breakdown by resource type.

        Args:
            days: Number of days to look back
            top: Number of top resource types to return
            resource_type: Exact ARM resource type to filter before aggregation
            subscription_id: Explicit subscription, otherwise the configured or unique subscription

        Returns:
            Cost breakdown by resource type
        """
        try:
            scope = await self._resolve_scope(subscription_id)
            if not 1 <= days <= 365 or not 1 <= top <= 100:
                raise ValueError("Cost query requires days in 1-365 and top in 1-100")
            resource_type = resource_type.strip().lower() if resource_type else None

            end_date = datetime.now(timezone.utc)
            start_date = end_date - timedelta(days=days)

            query_definition = QueryDefinition(
                type="ActualCost",
                timeframe="Custom",
                time_period=QueryTimePeriod(from_property=start_date, to=end_date),
                dataset=QueryDataset(
                    granularity="None",
                    aggregation={"totalCost": QueryAggregation(name="Cost", function="Sum")},
                    grouping=[QueryGrouping(type="Dimension", name="ResourceType")],
                    filter=(
                        QueryFilter(
                            dimensions=QueryComparisonExpression(
                                name="ResourceType", operator="In", values=[resource_type]
                            )
                        )
                        if resource_type
                        else None
                    ),
                ),
            )

            logger.info(
                "cost_query_start",
                query_type="by_resource_type",
                scope=scope,
                days=days,
            )

            import time as _time

            _t0 = _time.time()
            result = await self._query_with_retry(scope=scope, query_definition=query_definition)
            _elapsed = _time.time() - _t0

            costs, total_cost, currency = self._parse_cost_result(
                result, "ResourceType", "resource_type"
            )

            logger.info(
                "cost_query_ok",
                query_type="by_resource_type",
                row_count=len(costs),
                total_cost=round(total_cost, 2),
                elapsed_s=round(_elapsed, 2),
            )

            return {
                "success": True,
                "scope": scope,
                "source": "Azure Cost Management Query API",
                "filter": {"ResourceType": resource_type} if resource_type else {},
                "has_cost_data": bool(costs),
                "cost_type": "ActualCost",
                "currency": currency,
                "period_days": days,
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "total_cost": round(total_cost, 2),
                "row_count": len(costs),
                "costs_by_type": costs[:top],
            }

        except Exception as e:
            logger.error("cost_query_error", query_type="by_resource_type", error=str(e))
            return {"success": False, "error": str(e), "costs_by_type": []}

    async def get_cost_by_service(
        self,
        days: int = 30,
        top: int = 20,
        service_name: Optional[str] = None,
        subscription_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Get cost breakdown by Azure service (meter category).

        Args:
            days: Number of days to look back
            top: Number of top services to return
            service_name: Exact Cost Management ServiceName value to filter
            subscription_id: Explicit subscription, otherwise the configured or unique subscription

        Returns:
            Cost breakdown by service
        """
        try:
            scope = await self._resolve_scope(subscription_id)
            if not 1 <= days <= 365 or not 1 <= top <= 100:
                raise ValueError("Cost query requires days in 1-365 and top in 1-100")

            end_date = datetime.now(timezone.utc)
            start_date = end_date - timedelta(days=days)

            query_definition = QueryDefinition(
                type="ActualCost",
                timeframe="Custom",
                time_period=QueryTimePeriod(from_property=start_date, to=end_date),
                dataset=QueryDataset(
                    granularity="None",
                    aggregation={"totalCost": QueryAggregation(name="Cost", function="Sum")},
                    grouping=[QueryGrouping(type="Dimension", name="ServiceName")],
                    filter=(
                        QueryFilter(
                            dimensions=QueryComparisonExpression(
                                name="ServiceName", operator="In", values=[service_name]
                            )
                        )
                        if service_name
                        else None
                    ),
                ),
            )

            logger.info(
                "cost_query_start",
                query_type="by_service",
                scope=scope,
                days=days,
            )

            import time as _time

            _t0 = _time.time()
            result = await self._query_with_retry(scope=scope, query_definition=query_definition)
            _elapsed = _time.time() - _t0

            costs, total_cost, currency = self._parse_cost_result(result, "ServiceName", "service")

            logger.info(
                "cost_query_ok",
                query_type="by_service",
                row_count=len(costs),
                total_cost=round(total_cost, 2),
                elapsed_s=round(_elapsed, 2),
            )

            return {
                "success": True,
                "scope": scope,
                "source": "Azure Cost Management Query API",
                "filter": {"ServiceName": service_name} if service_name else {},
                "has_cost_data": bool(costs),
                "cost_type": "ActualCost",
                "currency": currency,
                "period_days": days,
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "total_cost": round(total_cost, 2),
                "row_count": len(costs),
                "costs_by_service": costs[:top],
            }

        except Exception as e:
            logger.error("cost_query_error", query_type="by_service", error=str(e))
            return {"success": False, "error": str(e), "costs_by_service": []}
