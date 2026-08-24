"""Tests for Resource Health, Policy Compliance, and Service Health Events tools."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agent.tools import (
    GetPolicyComplianceInput,
    GetPolicyComplianceTool,
    GetResourceHealthInput,
    GetResourceHealthTool,
    GetServiceHealthEventsInput,
    GetServiceHealthEventsTool,
)

# ---------------------------------------------------------------------------
# Resource Health fixtures
# ---------------------------------------------------------------------------

SAMPLE_RESOURCE_HEALTH = [
    {
        "id": "/subscriptions/sub1/resourceGroups/rg1/providers/Microsoft.Compute/virtualMachines/vm1/providers/Microsoft.ResourceHealth/availabilityStatuses/current",
        "properties": {
            "availabilityState": "Available",
            "summary": "Resource is healthy",
            "resourceType": "Microsoft.Compute/virtualMachines",
        },
    },
    {
        "id": "/subscriptions/sub1/resourceGroups/rg1/providers/Microsoft.Web/sites/app1/providers/Microsoft.ResourceHealth/availabilityStatuses/current",
        "properties": {
            "availabilityState": "Degraded",
            "summary": "Performance degradation detected",
            "reasonType": "PlatformInitiated",
            "resourceType": "Microsoft.Web/sites",
            "recommendedActions": [{"action": "Check App Service diagnostics for more details"}],
        },
    },
    {
        "id": "/subscriptions/sub1/resourceGroups/rg1/providers/Microsoft.Sql/servers/sql1/providers/Microsoft.ResourceHealth/availabilityStatuses/current",
        "properties": {
            "availabilityState": "Unavailable",
            "summary": "Database failover in progress",
            "reasonType": "Unplanned",
            "resourceType": "Microsoft.Sql/servers",
        },
    },
]


# ---------------------------------------------------------------------------
# Policy Compliance fixtures
# ---------------------------------------------------------------------------

SAMPLE_POLICY_COMPLIANCE = [
    {
        "results": {
            "resourceDetails": [
                {"complianceState": "compliant", "count": 150},
                {"complianceState": "noncompliant", "count": 12},
            ]
        },
        "policyAssignments": [
            {
                "policyAssignmentId": "/subscriptions/sub1/providers/Microsoft.Authorization/policyAssignments/enforce-tls",
                "results": {
                    "resourceDetails": [
                        {"complianceState": "compliant", "count": 45},
                        {"complianceState": "noncompliant", "count": 5},
                    ]
                },
                "policyDefinitions": [
                    {
                        "policyDefinitionReferenceId": "TLS-1.2-required",
                        "results": {
                            "resourceDetails": [{"complianceState": "noncompliant", "count": 5}]
                        },
                    }
                ],
            },
            {
                "policyAssignmentId": "/subscriptions/sub1/providers/Microsoft.Authorization/policyAssignments/require-tags",
                "results": {
                    "resourceDetails": [
                        {"complianceState": "compliant", "count": 100},
                        {"complianceState": "noncompliant", "count": 7},
                    ]
                },
                "policyDefinitions": [],
            },
        ],
    }
]


# ---------------------------------------------------------------------------
# Service Health Events fixtures
# ---------------------------------------------------------------------------

SAMPLE_HEALTH_EVENTS = [
    {
        "properties": {
            "eventType": "ServiceIssue",
            "status": "Active",
            "title": "Virtual Machines - East US",
            "summary": "We are investigating reports of connectivity issues.",
            "description": "Customers may experience connectivity failures to VMs in East US region.",
            "impactStartTime": "2026-04-25T10:00:00Z",
            "impactMitigationTime": "",
            "impact": [
                {
                    "impactedService": "Virtual Machines",
                    "impactedRegions": [
                        {"impactedRegion": "East US"},
                        {"impactedRegion": "East US 2"},
                    ],
                }
            ],
            "recommendedActions": {
                "message": "If you are experiencing issues, consider failing over to another region.",
                "actions": [
                    {"actionText": "Check VM connectivity via Azure Portal"},
                ],
            },
            "faqs": [
                {
                    "question": "Is this affecting all VM sizes?",
                    "answer": "Currently the issue is limited to Dv4 and Ev4 series.",
                }
            ],
        },
    },
    {
        "properties": {
            "eventType": "PlannedMaintenance",
            "status": "Planned",
            "title": "App Service maintenance - Korea Central",
            "summary": "Planned maintenance for App Service in Korea Central.",
            "description": "",
            "impactStartTime": "2026-04-30T02:00:00Z",
            "impactMitigationTime": "2026-04-30T06:00:00Z",
            "impact": [
                {
                    "impactedService": "App Service",
                    "impactedRegions": [{"impactedRegion": "Korea Central"}],
                }
            ],
            "recommendedActions": {},
            "faqs": [],
        },
    },
]


# ============================================================================
# Resource Health Tests
# ============================================================================


class TestResourceHealthInput:
    def test_default_values(self):
        inp = GetResourceHealthInput()
        assert inp.resource_type is None

    def test_with_resource_type(self):
        inp = GetResourceHealthInput(resource_type="Microsoft.Compute/virtualMachines")
        assert inp.resource_type == "Microsoft.Compute/virtualMachines"


class TestResourceHealthFormat:
    def test_groups_by_status(self):
        tool = GetResourceHealthTool.__new__(GetResourceHealthTool)
        result = tool._format_results(SAMPLE_RESOURCE_HEALTH)

        assert "Resource Health Status (3 resources)" in result
        assert "🟢" in result  # Available
        assert "🟡" in result  # Degraded
        assert "🔴" in result  # Unavailable

    def test_shows_non_available_details(self):
        tool = GetResourceHealthTool.__new__(GetResourceHealthTool)
        result = tool._format_results(SAMPLE_RESOURCE_HEALTH)

        assert "Degraded" in result
        assert "Unavailable" in result
        assert "PlatformInitiated" in result
        assert "Performance degradation" in result

    def test_shows_recommended_actions(self):
        tool = GetResourceHealthTool.__new__(GetResourceHealthTool)
        result = tool._format_results(SAMPLE_RESOURCE_HEALTH)

        assert "App Service diagnostics" in result

    def test_empty_results(self):
        tool = GetResourceHealthTool.__new__(GetResourceHealthTool)
        result = tool._format_results([])
        assert "0 resources" in result


class TestResourceHealthApi:
    def test_calls_rest_api(self):
        tool = GetResourceHealthTool()
        mock_result = {"value": SAMPLE_RESOURCE_HEALTH, "count": 3}

        with patch(
            "src.services.azure_rest.AzureRestClient",
            return_value=MagicMock(call_api=AsyncMock(return_value=mock_result)),
        ):
            result = asyncio.run(tool._arun())

        assert "Resource Health Status" in result
        assert "Unavailable" in result

    def test_with_resource_type_filter(self):
        tool = GetResourceHealthTool()
        mock_result = {"value": [SAMPLE_RESOURCE_HEALTH[0]], "count": 1}

        with patch(
            "src.services.azure_rest.AzureRestClient",
            return_value=MagicMock(call_api=AsyncMock(return_value=mock_result)),
        ) as mock_cls:
            asyncio.run(tool._arun(resource_type="Microsoft.Compute/virtualMachines"))

            call_kwargs = mock_cls.return_value.call_api.call_args
            params = call_kwargs.kwargs.get("params", {})
            assert "resourceType" in params.get("$filter", "")

    def test_api_error_returns_message(self):
        tool = GetResourceHealthTool()

        with patch(
            "src.services.azure_rest.AzureRestClient",
            return_value=MagicMock(
                call_api=AsyncMock(return_value={"error": "403 Forbidden", "value": []})
            ),
        ):
            result = asyncio.run(tool._arun())

        assert "error" in result.lower()

    def test_exception_returns_message(self):
        tool = GetResourceHealthTool()

        with patch(
            "src.services.azure_rest.AzureRestClient",
            return_value=MagicMock(call_api=AsyncMock(side_effect=Exception("timeout"))),
        ):
            result = asyncio.run(tool._arun())

        assert "error" in result.lower()


# ============================================================================
# Policy Compliance Tests
# ============================================================================


class TestPolicyComplianceInput:
    def test_default_values(self):
        inp = GetPolicyComplianceInput()
        assert inp.resource_type is None
        assert inp.policy_category is None

    def test_with_filters(self):
        inp = GetPolicyComplianceInput(
            resource_type="Microsoft.Compute/virtualMachines",
            policy_category="Security",
        )
        assert inp.resource_type == "Microsoft.Compute/virtualMachines"
        assert inp.policy_category == "Security"


class TestPolicyComplianceFormat:
    def test_shows_overall_compliance(self):
        tool = GetPolicyComplianceTool.__new__(GetPolicyComplianceTool)
        result = tool._format_results(SAMPLE_POLICY_COMPLIANCE)

        assert "Policy Compliance Summary" in result
        assert "92.6%" in result  # 150/(150+12)
        assert "150 compliant" in result
        assert "12 non-compliant" in result

    def test_shows_non_compliant_policies(self):
        tool = GetPolicyComplianceTool.__new__(GetPolicyComplianceTool)
        result = tool._format_results(SAMPLE_POLICY_COMPLIANCE)

        assert "enforce-tls" in result
        assert "require-tags" in result
        assert "5 non-compliant" in result
        assert "7 non-compliant" in result

    def test_empty_results(self):
        tool = GetPolicyComplianceTool.__new__(GetPolicyComplianceTool)
        result = tool._format_results([])
        assert "Policy Compliance Summary" in result


class TestPolicyComplianceApi:
    def test_calls_rest_api(self):
        tool = GetPolicyComplianceTool()
        mock_result = {"value": SAMPLE_POLICY_COMPLIANCE, "count": 1}

        with patch(
            "src.services.azure_rest.AzureRestClient",
            return_value=MagicMock(call_api=AsyncMock(return_value=mock_result)),
        ):
            result = asyncio.run(tool._arun())

        assert "92.6%" in result

    def test_with_filters(self):
        tool = GetPolicyComplianceTool()
        mock_result = {"value": SAMPLE_POLICY_COMPLIANCE, "count": 1}

        with patch(
            "src.services.azure_rest.AzureRestClient",
            return_value=MagicMock(call_api=AsyncMock(return_value=mock_result)),
        ) as mock_cls:
            asyncio.run(
                tool._arun(
                    resource_type="Microsoft.Compute/virtualMachines",
                    policy_category="Security",
                )
            )

            call_kwargs = mock_cls.return_value.call_api.call_args
            params = call_kwargs.kwargs.get("params", {})
            f = params.get("$filter", "")
            assert "resourceType" in f
            assert "policyDefinitionCategory" in f

    def test_api_error_returns_message(self):
        tool = GetPolicyComplianceTool()

        with patch(
            "src.services.azure_rest.AzureRestClient",
            return_value=MagicMock(call_api=AsyncMock(return_value={"error": "403", "value": []})),
        ):
            result = asyncio.run(tool._arun())

        assert "error" in result.lower()


# ============================================================================
# Service Health Events Tests
# ============================================================================


class TestServiceHealthEventsInput:
    def test_default_values(self):
        inp = GetServiceHealthEventsInput()
        assert inp.event_type is None
        assert inp.service_name is None

    def test_with_filters(self):
        inp = GetServiceHealthEventsInput(
            event_type="ServiceIssue",
            service_name="Virtual Machines",
        )
        assert inp.event_type == "ServiceIssue"
        assert inp.service_name == "Virtual Machines"


class TestServiceHealthEventsFormat:
    def test_shows_event_details(self):
        tool = GetServiceHealthEventsTool.__new__(GetServiceHealthEventsTool)
        result = tool._format_results(SAMPLE_HEALTH_EVENTS)

        assert "Detailed (2)" in result
        assert "Virtual Machines - East US" in result
        assert "App Service maintenance" in result

    def test_shows_event_types_with_emoji(self):
        tool = GetServiceHealthEventsTool.__new__(GetServiceHealthEventsTool)
        result = tool._format_results(SAMPLE_HEALTH_EVENTS)

        assert "🚨" in result  # ServiceIssue
        assert "🔧" in result  # PlannedMaintenance

    def test_shows_affected_services_and_regions(self):
        tool = GetServiceHealthEventsTool.__new__(GetServiceHealthEventsTool)
        result = tool._format_results(SAMPLE_HEALTH_EVENTS)

        assert "Virtual Machines" in result
        assert "East US" in result
        assert "Korea Central" in result

    def test_shows_recommended_actions(self):
        tool = GetServiceHealthEventsTool.__new__(GetServiceHealthEventsTool)
        result = tool._format_results(SAMPLE_HEALTH_EVENTS)

        assert "failing over" in result
        assert "Check VM connectivity" in result

    def test_shows_faqs(self):
        tool = GetServiceHealthEventsTool.__new__(GetServiceHealthEventsTool)
        result = tool._format_results(SAMPLE_HEALTH_EVENTS)

        assert "VM sizes" in result
        assert "Dv4" in result

    def test_shows_status(self):
        tool = GetServiceHealthEventsTool.__new__(GetServiceHealthEventsTool)
        result = tool._format_results(SAMPLE_HEALTH_EVENTS)

        assert "Active" in result
        assert "Planned" in result

    def test_empty_results(self):
        tool = GetServiceHealthEventsTool.__new__(GetServiceHealthEventsTool)
        result = tool._format_results([])
        assert "Detailed (0)" in result


class TestServiceHealthEventsApi:
    def test_calls_rest_api(self):
        tool = GetServiceHealthEventsTool()
        mock_result = {"value": SAMPLE_HEALTH_EVENTS, "count": 2}

        with patch(
            "src.services.azure_rest.AzureRestClient",
            return_value=MagicMock(call_api=AsyncMock(return_value=mock_result)),
        ):
            result = asyncio.run(tool._arun())

        assert "Detailed (2)" in result
        assert "Virtual Machines" in result

    def test_with_event_type_filter(self):
        tool = GetServiceHealthEventsTool()
        mock_result = {"value": [SAMPLE_HEALTH_EVENTS[0]], "count": 1}

        with patch(
            "src.services.azure_rest.AzureRestClient",
            return_value=MagicMock(call_api=AsyncMock(return_value=mock_result)),
        ) as mock_cls:
            asyncio.run(tool._arun(event_type="ServiceIssue"))

            call_kwargs = mock_cls.return_value.call_api.call_args
            params = call_kwargs.kwargs.get("params", {})
            assert "eventType" in params.get("$filter", "")

    def test_no_events_returns_all_clear(self):
        tool = GetServiceHealthEventsTool()

        with patch(
            "src.services.azure_rest.AzureRestClient",
            return_value=MagicMock(call_api=AsyncMock(return_value={"value": [], "count": 0})),
        ):
            result = asyncio.run(tool._arun())

        assert "✅" in result
        assert "normally" in result

    def test_api_error_returns_message(self):
        tool = GetServiceHealthEventsTool()

        with patch(
            "src.services.azure_rest.AzureRestClient",
            return_value=MagicMock(call_api=AsyncMock(return_value={"error": "500", "value": []})),
        ):
            result = asyncio.run(tool._arun())

        assert "error" in result.lower()
