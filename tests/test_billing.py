"""Tests for the read-only Azure Billing REST service."""

from unittest.mock import AsyncMock

import pytest

from src.agent.tools import ListBillingAccountsTool, ListBillingProfilesTool
from src.services.billing import BILLING_API_VERSION, BillingService


@pytest.mark.asyncio
async def test_list_billing_accounts_uses_tenant_scope_and_normalizes_metadata():
    client = AsyncMock()
    client.call_api.return_value = {
        "value": [
            {
                "id": "/providers/Microsoft.Billing/billingAccounts/account-1",
                "name": "account-1",
                "properties": {
                    "displayName": "Enterprise Account",
                    "accountStatus": "Active",
                    "accountType": "Enterprise",
                    "agreementType": "EnterpriseAgreement",
                    "hasReadAccess": True,
                },
            }
        ]
    }
    service = BillingService(client=client)

    result = await service.list_billing_accounts(top=10)

    assert result == {
        "success": True,
        "api_version": BILLING_API_VERSION,
        "count": 1,
        "accounts": [
            {
                "id": "/providers/Microsoft.Billing/billingAccounts/account-1",
                "name": "account-1",
                "display_name": "Enterprise Account",
                "status": "Active",
                "account_type": "Enterprise",
                "agreement_type": "EnterpriseAgreement",
                "has_read_access": True,
            }
        ],
    }
    client.call_api.assert_awaited_once_with(
        path="/providers/Microsoft.Billing/billingAccounts",
        api_version="2024-04-01",
        params={"top": "10"},
        max_results=10,
    )


@pytest.mark.asyncio
async def test_list_billing_accounts_preserves_permission_failure():
    client = AsyncMock()
    client.call_api.return_value = {"error": "API returned 403", "status_code": 403}

    result = await BillingService(client=client).list_billing_accounts()

    assert result == {
        "success": False,
        "error": "API returned 403",
        "accounts": [],
    }


@pytest.mark.asyncio
async def test_list_billing_profiles_encodes_account_name_and_normalizes_metadata():
    client = AsyncMock()
    client.call_api.return_value = {
        "value": [
            {
                "id": "/providers/Microsoft.Billing/billingAccounts/a/billingProfiles/p",
                "name": "profile-1",
                "properties": {
                    "displayName": "Production",
                    "status": "Active",
                    "currency": "KRW",
                    "invoiceDay": 5,
                    "poNumber": "PO-123",
                },
            }
        ]
    }
    service = BillingService(client=client)

    result = await service.list_billing_profiles("account:tenant/date", top=25)

    assert result["success"] is True
    assert result["profiles"][0]["currency"] == "KRW"
    client.call_api.assert_awaited_once_with(
        path=(
            "/providers/Microsoft.Billing/billingAccounts/"
            "account%3Atenant%2Fdate/billingProfiles"
        ),
        api_version="2024-04-01",
        params={"top": "25"},
        max_results=25,
    )


@pytest.mark.asyncio
async def test_list_billing_profiles_preserves_api_failure():
    client = AsyncMock()
    client.call_api.return_value = {"error": "API returned 400", "status_code": 400}

    result = await BillingService(client=client).list_billing_profiles("unsupported")

    assert result == {
        "success": False,
        "error": "API returned 400",
        "profiles": [],
    }


@pytest.mark.asyncio
async def test_billing_accounts_tool_emits_evidence_identifier():
    service = AsyncMock()
    service.list_billing_accounts.return_value = {
        "success": True,
        "api_version": "2024-04-01",
        "count": 1,
        "accounts": [
            {
                "id": "/providers/Microsoft.Billing/billingAccounts/a",
                "name": "a",
                "display_name": "Account A",
                "status": "Active",
                "account_type": "Enterprise",
                "agreement_type": "EnterpriseAgreement",
                "has_read_access": True,
            }
        ],
    }

    output = await ListBillingAccountsTool(service=service).ainvoke({"top": 10})

    assert "Evidence: billing:/providers/Microsoft.Billing/billingAccounts/a" in output
    assert "Agreement type: EnterpriseAgreement" in output


@pytest.mark.asyncio
async def test_billing_profiles_tool_preserves_api_error():
    service = AsyncMock()
    service.list_billing_profiles.return_value = {
        "success": False,
        "error": "API returned 403",
        "profiles": [],
    }

    output = await ListBillingProfilesTool(service=service).ainvoke(
        {"billing_account_name": "a", "top": 10}
    )

    assert output == "Billing profile API error: API returned 403"
