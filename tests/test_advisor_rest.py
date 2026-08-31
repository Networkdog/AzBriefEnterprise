"""Tests for Azure Advisor REST API mode in GetAdvisorRecommendationsTool."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agent.tools import (
    GetAdvisorRecommendationsInput,
    GetAdvisorRecommendationsTool,
)

# ---------------------------------------------------------------------------
# Sample REST API response fixtures
# ---------------------------------------------------------------------------

SAMPLE_REST_RECOMMENDATIONS = [
    {
        "id": "/subscriptions/sub1/providers/Microsoft.Advisor/recommendations/rec1",
        "name": "rec1",
        "properties": {
            "category": "Cost",
            "impact": "High",
            "risk": "Warning",
            "shortDescription": {
                "problem": "Right-size or shutdown underutilized virtual machines",
                "solution": "Resize the VM to a smaller SKU or shut it down",
            },
            "description": (
                "We've analyzed the usage patterns of your VM over the past 7 days "
                "and identified that it is underutilized."
            ),
            "impactedValue": "my-vm-01",
            "impactedType": "Microsoft.Compute/virtualMachines",
            "potentialBenefits": "Save up to 80% on compute costs",
            "learnMoreLink": "https://learn.microsoft.com/azure/advisor/advisor-cost-recommendations",
            "remediation": {
                "httpMethod": "POST",
                "uri": "/subscriptions/sub1/resourceGroups/rg1/providers/...",
                "details": "https://learn.microsoft.com/azure/advisor/advisor-cost-recommendations#resize",
            },
            "actions": [
                {
                    "actionType": "Document",
                    "caption": "Resize VM",
                    "link": "https://portal.azure.com/#blade/resize",
                    "description": "Navigate to resize blade",
                },
            ],
        },
    },
    {
        "id": "/subscriptions/sub1/providers/Microsoft.Advisor/recommendations/rec2",
        "name": "rec2",
        "properties": {
            "category": "Security",
            "impact": "Medium",
            "risk": "Error",
            "shortDescription": {
                "problem": "Enable Soft Delete for Blob Storage",
                "solution": "Turn on soft delete in storage account settings",
            },
            "description": "Soft delete protects your data from accidental deletion.",
            "impactedValue": "mystorageaccount",
            "impactedType": "Microsoft.Storage/storageAccounts",
            "potentialBenefits": "Save and recover your data when blobs are accidentally deleted",
            "learnMoreLink": "https://learn.microsoft.com/azure/storage/blobs/soft-delete",
            "remediation": {},
            "actions": [],
        },
    },
    {
        "id": "/subscriptions/sub1/providers/Microsoft.Advisor/recommendations/rec3",
        "name": "rec3",
        "properties": {
            "category": "Cost",
            "impact": "Low",
            "risk": "",
            "shortDescription": {
                "problem": "Consider reserved instances for stable workloads",
                "solution": "Purchase reserved instances",
            },
            "description": "",
            "impactedValue": "my-sql-server",
            "impactedType": "Microsoft.Sql/servers",
            "potentialBenefits": "Save up to 72% compared to pay-as-you-go pricing",
            "learnMoreLink": "",
            "remediation": {},
            "actions": [
                {
                    "actionType": "Document",
                    "caption": "Buy reservations",
                    "link": "https://portal.azure.com/#blade/reservations",
                },
                {
                    "actionType": "Document",
                    "caption": "Learn about RI",
                    "link": "https://learn.microsoft.com/azure/cost-management/reservations",
                },
            ],
        },
    },
]


class TestAdvisorInput:
    """Test GetAdvisorRecommendationsInput schema."""

    def test_default_values(self):
        inp = GetAdvisorRecommendationsInput()
        assert inp.category is None
        assert inp.impact is None

    def test_category_filter(self):
        inp = GetAdvisorRecommendationsInput(category="Cost")
        assert inp.category == "Cost"


class TestAdvisorRestFormat:
    """Test REST API result formatting."""

    def test_format_rest_results_groups_by_category(self):
        tool = GetAdvisorRecommendationsTool.__new__(GetAdvisorRecommendationsTool)
        result = tool._format_rest_results(SAMPLE_REST_RECOMMENDATIONS)

        assert "## Azure Advisor Recommendations — Detailed (3)" in result
        assert "### Cost (2)" in result
        assert "### Security (1)" in result

    def test_format_rest_results_includes_solution(self):
        tool = GetAdvisorRecommendationsTool.__new__(GetAdvisorRecommendationsTool)
        result = tool._format_rest_results(SAMPLE_REST_RECOMMENDATIONS)

        assert "Resize the VM to a smaller SKU" in result
        assert "Solution:" in result

    def test_format_rest_results_includes_benefits(self):
        tool = GetAdvisorRecommendationsTool.__new__(GetAdvisorRecommendationsTool)
        result = tool._format_rest_results(SAMPLE_REST_RECOMMENDATIONS)

        assert "Save up to 80% on compute costs" in result
        assert "Benefits:" in result

    def test_format_rest_results_includes_learn_more(self):
        tool = GetAdvisorRecommendationsTool.__new__(GetAdvisorRecommendationsTool)
        result = tool._format_rest_results(SAMPLE_REST_RECOMMENDATIONS)

        assert "Learn more:" in result
        assert "https://learn.microsoft.com/azure/advisor/advisor-cost-recommendations" in result

    def test_format_rest_results_includes_risk(self):
        tool = GetAdvisorRecommendationsTool.__new__(GetAdvisorRecommendationsTool)
        result = tool._format_rest_results(SAMPLE_REST_RECOMMENDATIONS)

        assert "Risk: Warning" in result
        assert "Risk: Error" in result

    def test_format_rest_results_includes_remediation(self):
        tool = GetAdvisorRecommendationsTool.__new__(GetAdvisorRecommendationsTool)
        result = tool._format_rest_results(SAMPLE_REST_RECOMMENDATIONS)

        assert "Remediation:" in result
        assert "#resize" in result

    def test_format_rest_results_includes_actions(self):
        tool = GetAdvisorRecommendationsTool.__new__(GetAdvisorRecommendationsTool)
        result = tool._format_rest_results(SAMPLE_REST_RECOMMENDATIONS)

        assert "Actions:" in result
        assert "[Resize VM]" in result

    def test_format_rest_results_multiple_actions(self):
        tool = GetAdvisorRecommendationsTool.__new__(GetAdvisorRecommendationsTool)
        result = tool._format_rest_results(SAMPLE_REST_RECOMMENDATIONS)

        assert "[Buy reservations]" in result
        assert "[Learn about RI]" in result

    def test_format_rest_results_impact_emoji(self):
        tool = GetAdvisorRecommendationsTool.__new__(GetAdvisorRecommendationsTool)
        result = tool._format_rest_results(SAMPLE_REST_RECOMMENDATIONS)

        assert "🔴" in result  # High
        assert "🟡" in result  # Medium
        assert "🟢" in result  # Low

    def test_format_rest_results_empty(self):
        tool = GetAdvisorRecommendationsTool.__new__(GetAdvisorRecommendationsTool)
        result = tool._format_rest_results([])

        assert "Detailed (0)" in result


class TestAdvisorRestApiMode:
    """Test REST API mode invocation."""

    def test_rest_api_mode_calls_rest_client(self):
        """REST API mode should call AzureRestClient.call_api."""
        mock_result = {
            "value": SAMPLE_REST_RECOMMENDATIONS,
            "count": 3,
        }

        tool = GetAdvisorRecommendationsTool()

        with patch(
            "src.services.azure_rest.AzureRestClient",
            return_value=MagicMock(call_api=AsyncMock(return_value=mock_result)),
        ) as mock_cls:
            result = asyncio.run(tool._arun())

            mock_cls.return_value.call_api.assert_called_once()
            call_kwargs = mock_cls.return_value.call_api.call_args
            assert "Microsoft.Advisor/recommendations" in call_kwargs.kwargs.get(
                "path", call_kwargs.args[0] if call_kwargs.args else ""
            )
            assert "2023-01-01" in str(call_kwargs)

        assert "Detailed (3)" in result
        assert "Cost" in result

    def test_rest_api_mode_with_category_filter(self):
        """REST API mode should pass category filter to $filter param."""
        mock_result = {"value": [SAMPLE_REST_RECOMMENDATIONS[0]], "count": 1}

        tool = GetAdvisorRecommendationsTool()

        with patch(
            "src.services.azure_rest.AzureRestClient",
            return_value=MagicMock(call_api=AsyncMock(return_value=mock_result)),
        ) as mock_cls:
            asyncio.run(tool._arun(category="Cost"))

            call_kwargs = mock_cls.return_value.call_api.call_args
            params = call_kwargs.kwargs.get("params", {})
            assert params is not None
            assert "Category eq 'Cost'" in params.get("$filter", "")

    def test_rest_api_error_is_preserved(self):
        """A REST permission failure remains an explicit Azure API evidence gap."""
        mock_rest_result = {"error": "API returned 403", "value": []}

        tool = GetAdvisorRecommendationsTool()

        with patch(
            "src.services.azure_rest.AzureRestClient",
            return_value=MagicMock(call_api=AsyncMock(return_value=mock_rest_result)),
        ):
            result = asyncio.run(tool._arun())

        assert result == "Advisor REST API error: API returned 403"

    def test_rest_api_exception_is_preserved(self):
        """A REST transport failure remains explicit instead of crossing roles."""
        tool = GetAdvisorRecommendationsTool()

        with patch(
            "src.services.azure_rest.AzureRestClient",
            return_value=MagicMock(call_api=AsyncMock(side_effect=Exception("Connection refused"))),
        ):
            result = asyncio.run(tool._arun())

        assert result == "Advisor REST API error: Connection refused"

    def test_rest_api_empty_results(self):
        """REST API returning empty should say no recommendations."""
        tool = GetAdvisorRecommendationsTool()

        with patch(
            "src.services.azure_rest.AzureRestClient",
            return_value=MagicMock(call_api=AsyncMock(return_value={"value": [], "count": 0})),
        ):
            result = asyncio.run(tool._arun())

        assert "No active Advisor recommendations found." in result
