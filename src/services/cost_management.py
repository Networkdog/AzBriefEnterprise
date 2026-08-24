"""Azure Cost Management Service using Azure SDK."""

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from azure.core.exceptions import HttpResponseError
from azure.mgmt.costmanagement import CostManagementClient
from azure.mgmt.costmanagement.models import (
    QueryAggregation,
    QueryDataset,
    QueryDefinition,
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
            if subs:
                self.subscription_id = subs[0]["subscriptionId"]
                logger.info(
                    "cost_subscription_auto_discovered",
                    subscription_id=self.subscription_id,
                )
        except Exception as e:
            logger.warning("cost_subscription_discovery_failed", error=str(e))

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

    async def get_cost_by_resource_type(self, days: int = 30, top: int = 20) -> dict[str, Any]:
        """Get cost breakdown by resource type.

        Args:
            days: Number of days to look back
            top: Number of top resource types to return

        Returns:
            Cost breakdown by resource type
        """
        try:
            await self._ensure_subscription()
            scope = f"/subscriptions/{self.subscription_id}"

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

            # Parse results
            costs = []
            if result.rows:
                for row in result.rows[:top]:
                    if len(row) >= 2:
                        costs.append(
                            {
                                "resource_type": row[1] if len(row) > 1 else "Unknown",
                                "cost": round(float(row[0]), 2) if row[0] else 0,
                                "currency": result.columns[0].name if result.columns else "USD",
                            }
                        )

            # Sort by cost descending
            costs.sort(key=lambda x: x["cost"], reverse=True)

            total_cost = sum(c["cost"] for c in costs)

            logger.info(
                "cost_query_ok",
                query_type="by_resource_type",
                row_count=len(costs),
                total_cost=round(total_cost, 2),
                elapsed_s=round(_elapsed, 2),
            )

            return {
                "success": True,
                "period_days": days,
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "total_cost": round(total_cost, 2),
                "costs_by_type": costs[:top],
            }

        except Exception as e:
            logger.error("cost_query_error", query_type="by_resource_type", error=str(e))
            return {"success": False, "error": str(e), "costs_by_type": []}

    async def get_cost_by_service(self, days: int = 30, top: int = 20) -> dict[str, Any]:
        """Get cost breakdown by Azure service (meter category).

        Args:
            days: Number of days to look back
            top: Number of top services to return

        Returns:
            Cost breakdown by service
        """
        try:
            await self._ensure_subscription()
            scope = f"/subscriptions/{self.subscription_id}"

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

            costs = []
            if result.rows:
                for row in result.rows[:top]:
                    if len(row) >= 2:
                        costs.append(
                            {
                                "service": row[1] if len(row) > 1 else "Unknown",
                                "cost": round(float(row[0]), 2) if row[0] else 0,
                            }
                        )

            costs.sort(key=lambda x: x["cost"], reverse=True)
            total_cost = sum(c["cost"] for c in costs)

            logger.info(
                "cost_query_ok",
                query_type="by_service",
                row_count=len(costs),
                total_cost=round(total_cost, 2),
                elapsed_s=round(_elapsed, 2),
            )

            return {
                "success": True,
                "period_days": days,
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "total_cost": round(total_cost, 2),
                "costs_by_service": costs[:top],
            }

        except Exception as e:
            logger.error("cost_query_error", query_type="by_service", error=str(e))
            return {"success": False, "error": str(e), "costs_by_service": []}
