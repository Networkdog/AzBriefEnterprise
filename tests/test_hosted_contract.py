"""Tests for the Container Apps to Hosted Agent wire contract."""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from src.agent.hosted_contract import (
    HOSTED_AGENT_REQUEST_ADAPTER,
    HostedAgentResponse,
    HostedAnalysisRequest,
    HostedCustomizationRequest,
    HostedSubscriber,
    HostedUpdate,
)


def _update() -> HostedUpdate:
    return HostedUpdate(
        id="update-1",
        title="Azure Update",
        description="Description",
        link="https://azure.microsoft.com/updates/update-1",
        published_date=datetime(2026, 8, 27, tzinfo=timezone.utc),
        categories=["Compute"],
        azure_services=["Virtual Machines"],
        update_type="Feature Update",
        status="In development",
    )


def test_request_round_trip_is_versioned_and_strict():
    request = HostedAnalysisRequest(update=_update(), trace_id="trace-1")

    restored = HOSTED_AGENT_REQUEST_ADAPTER.validate_json(request.model_dump_json())

    assert restored.contract_version == "2"
    assert restored.operation == "analyze_update"
    assert restored.update.title == "Azure Update"
    assert restored.trace_id == "trace-1"


def test_request_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        HostedAnalysisRequest.model_validate(
            {"update": _update().model_dump(), "trace_id": "trace-1", "unexpected": True}
        )


def test_customization_request_uses_discriminated_operation():
    request = HostedCustomizationRequest(
        update=_update(),
        result={"update_id": "update-1"},
        subscriber=HostedSubscriber(email="admin@example.com", name="Admin"),
        trace_id="trace-2",
    )

    restored = HOSTED_AGENT_REQUEST_ADAPTER.validate_json(request.model_dump_json())

    assert isinstance(restored, HostedCustomizationRequest)
    assert restored.subscriber.email == "admin@example.com"


@pytest.mark.parametrize(
    "payload",
    [
        {
            "operation": "analyze_update",
            "status": "completed",
            "trace_id": "trace-1",
        },
        {
            "operation": "analyze_update",
            "status": "failed",
            "trace_id": "trace-1",
        },
        {
            "operation": "analyze_update",
            "status": "completed",
            "trace_id": "trace-1",
            "result": {},
            "error": "not empty",
        },
        {
            "operation": "analyze_update",
            "status": "failed",
            "trace_id": "trace-1",
            "result": {},
            "error": "failed",
        },
    ],
)
def test_response_rejects_ambiguous_status_payloads(payload):
    with pytest.raises(ValidationError):
        HostedAgentResponse.model_validate(payload)


def test_completed_response_round_trip():
    response = HostedAgentResponse(
        operation="analyze_update",
        status="completed",
        result={"update_id": "update-1"},
        trace_id="trace-1",
    )

    restored = HostedAgentResponse.model_validate_json(response.model_dump_json())

    assert restored == response
