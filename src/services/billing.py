"""Read-only Azure Billing REST service."""

from typing import Any, Optional
from urllib.parse import quote

from structlog import get_logger

from src.services.azure_rest import AzureRestClient

logger = get_logger()

BILLING_API_VERSION = "2024-04-01"


class BillingService:
    """Query Azure Billing accounts and profiles visible to the current identity."""

    def __init__(self, client: Optional[AzureRestClient] = None):
        """Initialize the service.

        Args:
            client: Optional REST client override for tests.
        """
        self._client = client or AzureRestClient()

    async def list_billing_accounts(self, top: int = 20) -> dict[str, Any]:
        """List billing accounts that the current identity can read.

        Args:
            top: Maximum number of accounts to return.

        Returns:
            A success envelope with normalized billing account metadata.
        """
        result = await self._client.call_api(
            path="/providers/Microsoft.Billing/billingAccounts",
            api_version=BILLING_API_VERSION,
            params={"top": str(top)},
            max_results=top,
        )
        if result.get("error"):
            logger.warning(
                "billing_accounts_query_failed",
                status_code=result.get("status_code"),
                error=result["error"],
            )
            return {"success": False, "error": result["error"], "accounts": []}

        accounts = []
        for item in result.get("value", []):
            properties = item.get("properties") or {}
            accounts.append(
                {
                    "id": item.get("id", ""),
                    "name": item.get("name", ""),
                    "display_name": properties.get("displayName", ""),
                    "status": properties.get("accountStatus", ""),
                    "account_type": properties.get("accountType", ""),
                    "agreement_type": properties.get("agreementType", ""),
                    "has_read_access": properties.get("hasReadAccess"),
                }
            )
        return {
            "success": True,
            "api_version": BILLING_API_VERSION,
            "count": len(accounts),
            "accounts": accounts,
        }

    async def list_billing_profiles(
        self,
        billing_account_name: str,
        top: int = 50,
    ) -> dict[str, Any]:
        """List billing profiles under one accessible billing account.

        Args:
            billing_account_name: Billing account resource name returned by
                :meth:`list_billing_accounts`.
            top: Maximum number of profiles to return.

        Returns:
            A success envelope with normalized billing profile metadata.
        """
        account_segment = quote(billing_account_name, safe="")
        result = await self._client.call_api(
            path=(
                "/providers/Microsoft.Billing/billingAccounts/" f"{account_segment}/billingProfiles"
            ),
            api_version=BILLING_API_VERSION,
            params={"top": str(top)},
            max_results=top,
        )
        if result.get("error"):
            logger.warning(
                "billing_profiles_query_failed",
                billing_account=billing_account_name,
                status_code=result.get("status_code"),
                error=result["error"],
            )
            return {"success": False, "error": result["error"], "profiles": []}

        profiles = []
        for item in result.get("value", []):
            properties = item.get("properties") or {}
            profiles.append(
                {
                    "id": item.get("id", ""),
                    "name": item.get("name", ""),
                    "display_name": properties.get("displayName", ""),
                    "status": properties.get("status", ""),
                    "currency": properties.get("currency", ""),
                    "invoice_day": properties.get("invoiceDay"),
                    "purchase_order_number": properties.get("poNumber", ""),
                }
            )
        return {
            "success": True,
            "api_version": BILLING_API_VERSION,
            "billing_account_name": billing_account_name,
            "count": len(profiles),
            "profiles": profiles,
        }
