"""General-purpose Azure Management REST API client.

Allows the AI agent to call any Azure Management API endpoint
to check resource availability, SKUs, capabilities, and other
metadata that is not available through Azure Resource Graph.

Common use cases:
- VM size availability: GET /subscriptions/{sub}/providers/Microsoft.Compute/skus?$filter=location eq '{region}'
- VM sizes per location: GET /subscriptions/{sub}/providers/Microsoft.Compute/locations/{loc}/vmSizes
- Resource provider features: GET /subscriptions/{sub}/providers/{namespace}?api-version=2021-04-01
- Service availability by region: GET /subscriptions/{sub}/locations
"""

from typing import Any, Optional

import httpx
from structlog import get_logger

from src.config import get_settings

logger = get_logger()


class AzureRestClient:
    """Lightweight client for calling Azure Management REST APIs with proper authentication."""

    BASE_URL = "https://management.azure.com"

    def __init__(self):
        self._credential = None
        self._settings = get_settings()

    def _get_credential(self):
        if self._credential is None:
            from src.config import get_azure_credential

            self._credential = get_azure_credential()
        return self._credential

    def _get_token(self) -> str:
        credential = self._get_credential()
        return credential.get_token("https://management.azure.com/.default").token

    def _get_subscription_id(self) -> Optional[str]:
        """Get subscription ID — use configured or discover first accessible."""
        sub_id = self._settings.azure_subscription_id
        if sub_id:
            return sub_id
        from src.services import discover_subscriptions_sync

        credential = self._get_credential()
        subs = discover_subscriptions_sync(credential)
        return subs[0]["subscriptionId"] if subs else None

    async def call_api(
        self,
        path: str,
        api_version: str = "2021-07-01",
        method: str = "GET",
        params: Optional[dict[str, str]] = None,
        max_results: int = 200,
    ) -> dict[str, Any]:
        """Call an Azure Management REST API endpoint.

        Args:
            path: API path (e.g., "/subscriptions/{subscriptionId}/providers/Microsoft.Compute/skus").
                  The placeholder {subscriptionId} is auto-replaced with the active subscription.
            api_version: API version string (e.g., "2021-07-01")
            method: HTTP method (GET, POST, etc.)
            params: Additional query parameters (e.g., {"$filter": "location eq 'koreacentral'"})
            max_results: Maximum number of items to return from paginated results

        Returns:
            Dict with 'value' (list of results), 'count', and metadata
        """
        import time as _time

        resolved_path = path
        if "{subscriptionId}" in path:
            subscription_id = self._get_subscription_id()
            if not subscription_id:
                return {"error": "No subscription ID available", "value": []}
            resolved_path = path.replace("{subscriptionId}", subscription_id)

        # Build URL
        url = f"{self.BASE_URL}{resolved_path}"

        # Build query params
        query_params = {"api-version": api_version}
        if params:
            query_params.update(params)

        token = self._get_token()
        headers = {"Authorization": f"Bearer {token}"}

        _t0 = _time.time()
        logger.info("azure_rest_call_start", path=resolved_path[:100], method=method)

        all_values: list[dict] = []

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                current_url = url
                page = 0

                while current_url and len(all_values) < max_results:
                    page += 1
                    if page == 1:
                        response = await client.request(
                            method,
                            current_url,
                            headers=headers,
                            params=query_params,
                        )
                    else:
                        # nextLink includes full URL with params
                        response = await client.request(method, current_url, headers=headers)

                    if response.status_code != 200:
                        _elapsed = _time.time() - _t0
                        logger.warning(
                            "azure_rest_call_non_200",
                            path=resolved_path[:100],
                            status=response.status_code,
                            elapsed_s=round(_elapsed, 2),
                            body=response.text[:300],
                        )
                        return {
                            "error": f"API returned {response.status_code}",
                            "status_code": response.status_code,
                            "body": response.text[:500],
                            "value": [],
                        }

                    data = response.json()
                    values = data.get("value", [])
                    all_values.extend(values)

                    # Handle pagination
                    current_url = data.get("nextLink")

            _elapsed = _time.time() - _t0

            # Trim to max_results
            if len(all_values) > max_results:
                all_values = all_values[:max_results]

            logger.info(
                "azure_rest_call_ok",
                path=resolved_path[:100],
                count=len(all_values),
                pages=page,
                elapsed_s=round(_elapsed, 2),
            )

            return {
                "path": resolved_path,
                "api_version": api_version,
                "count": len(all_values),
                "value": all_values,
            }

        except Exception as e:
            _elapsed = _time.time() - _t0
            logger.error(
                "azure_rest_call_error",
                path=resolved_path[:100],
                error=str(e),
                elapsed_s=round(_elapsed, 2),
            )
            return {
                "error": str(e),
                "value": [],
            }

    async def get_resource(
        self,
        path: str,
        api_version: str = "2021-04-01",
        params: Optional[dict[str, str]] = None,
    ) -> dict[str, Any]:
        """Call an Azure Management REST API endpoint that returns a single object.

        Unlike :meth:`call_api`, this does not expect a paginated ``value`` array.
        It returns the raw JSON body, which is required for metadata endpoints such
        as ``/providers/{namespace}`` that return a single resource object (with a
        ``resourceTypes`` list carrying per-type ``locations``).

        Args:
            path: API path (e.g., "/subscriptions/{subscriptionId}/providers/Microsoft.Databricks").
                  The placeholder {subscriptionId} is auto-replaced with the active subscription.
            api_version: API version string (e.g., "2021-04-01")
            params: Additional query parameters

        Returns:
            Raw JSON response as a dict, or {"error": str} on failure
        """
        import time as _time

        resolved_path = path
        if "{subscriptionId}" in path:
            subscription_id = self._get_subscription_id()
            if not subscription_id:
                return {"error": "No subscription ID available"}
            resolved_path = path.replace("{subscriptionId}", subscription_id)
        url = f"{self.BASE_URL}{resolved_path}"

        query_params = {"api-version": api_version}
        if params:
            query_params.update(params)

        token = self._get_token()
        headers = {"Authorization": f"Bearer {token}"}

        _t0 = _time.time()
        logger.info("azure_rest_get_start", path=resolved_path[:100])

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(url, headers=headers, params=query_params)

            _elapsed = _time.time() - _t0
            if response.status_code != 200:
                logger.warning(
                    "azure_rest_get_non_200",
                    path=resolved_path[:100],
                    status=response.status_code,
                    elapsed_s=round(_elapsed, 2),
                    body=response.text[:300],
                )
                return {
                    "error": f"API returned {response.status_code}",
                    "status_code": response.status_code,
                    "body": response.text[:500],
                }

            logger.info(
                "azure_rest_get_ok",
                path=resolved_path[:100],
                elapsed_s=round(_elapsed, 2),
            )
            return response.json()

        except Exception as e:
            _elapsed = _time.time() - _t0
            logger.error(
                "azure_rest_get_error",
                path=resolved_path[:100],
                error=str(e),
                elapsed_s=round(_elapsed, 2),
            )
            return {"error": str(e)}
