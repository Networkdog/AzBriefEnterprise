"""Privacy and completeness contracts for exception diagnostics."""

import json

import httpx
import pytest
from pydantic import BaseModel, ValidationError

from src import error_logging
from src.error_logging import (
    configure_redaction,
    enrich_error_event,
    exception_fields,
    redact_fields,
    redact_text,
)


@pytest.fixture(autouse=True)
def isolated_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(error_logging, "_secret_values", ())


def test_active_warning_preserves_chained_failure_and_http_identifiers() -> None:
    request = httpx.Request(
        "GET", "https://example.com/resource?sig=private-signature&api-version=1"
    )
    response = httpx.Response(
        403,
        request=request,
        headers={"x-ms-request-id": "service-request-1", "Set-Cookie": "private-cookie"},
        json={"private_body": "must-not-be-logged"},
    )
    try:
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise RuntimeError("evidence query failed") from exc
    except RuntimeError:
        event = enrich_error_event(None, "warning", {"event": "task_failed", "trace_id": "t-1"})

    assert event["error_type"] == "RuntimeError"
    assert event["error_message"] == "evidence query failed"
    assert event["status_code"] == 403
    assert event["request_id"] == "service-request-1"
    assert event["trace_id"] == "t-1"
    assert "HTTPStatusError" in event["exception"]
    assert "Caused by:" in event["exception"]
    assert "test_active_warning_preserves_chained_failure" in event["exception"]
    assert "raise RuntimeError(" not in event["exception"]
    assert "private-signature" not in json.dumps(event)
    assert "private-cookie" not in json.dumps(event)
    assert "must-not-be-logged" not in json.dumps(event)


def test_redacts_nested_credentials_payloads_and_unlabelled_configured_secret() -> None:
    configure_redaction({"API_KEY": "opaque-secret-value"})
    fields = redact_fields(
        {
            "event": "send_failed",
            "trace_id": "trace-safe",
            "error": (
                "opaque-secret-value Bearer abc.def.ghi password=hunter2 "
                "AccountKey=storage-key; owner=admin@example.com "
                "https://user:password@example.com/path?sig=signed-token"
            ),
            "details": {
                "Authorization": "bearer-secret",
                "client_secret": "client-secret",
                "request_body": {"data": "customer-data"},
                "response_body": "response-data",
                "headers": {"X-Key": "private-header"},
                "status_code": 403,
            },
        }
    )
    encoded = json.dumps(fields)
    for secret in (
        "opaque-secret-value",
        "abc.def.ghi",
        "hunter2",
        "storage-key",
        "admin@example.com",
        "signed-token",
        "user:password",
        "bearer-secret",
        "client-secret",
        "customer-data",
        "response-data",
        "private-header",
    ):
        assert secret not in encoded
    assert fields["details"]["status_code"] == 403
    assert fields["trace_id"] == "trace-safe"


def test_validation_errors_keep_field_paths_and_types_not_input_or_context() -> None:
    class Payload(BaseModel):
        count: int

    try:
        Payload(count="PRIVATE_CUSTOMER_PAYLOAD")
    except ValidationError as exc:
        event = enrich_error_event(
            None, "exception", {"event": "invalid_payload", "exc_info": True, "error": str(exc)}
        )
    encoded = json.dumps(event)
    assert "count" in encoded
    assert "int_parsing" in encoded
    assert "PRIVATE_CUSTOMER_PAYLOAD" not in encoded
    assert "input_value" not in encoded
    assert "exc_info" not in event


def test_warning_without_exception_is_not_fabricated() -> None:
    event = enrich_error_event(None, "warning", {"event": "partial_run", "failed": 3})
    assert event == {"event": "partial_run", "failed": 3}


def test_stored_exception_retains_traceback_outside_except_block() -> None:
    try:
        raise ValueError("invalid response")
    except ValueError as exc:
        saved = exc
    event = enrich_error_event(None, "error", {"event": "delayed_failure", "exc_info": saved})
    assert event["error_type"] == "ValueError"
    assert "test_stored_exception_retains_traceback" in event["exception"]


def test_exception_chain_cycles_and_large_messages_are_bounded() -> None:
    error = RuntimeError("x" * 10000)
    error.__cause__ = error
    fields = exception_fields(error)
    assert len(fields["error_message"]) <= 4000
    assert len(fields["exception"]) <= 30000
    assert "TRUNCATED" in fields["error_message"]
    assert "exception chain truncated" in fields["exception"]


def test_suppressed_context_is_not_reintroduced() -> None:
    try:
        try:
            raise ValueError("suppressed-private-context")
        except ValueError:
            raise RuntimeError("public failure") from None
    except RuntimeError as exc:
        fields = exception_fields(exc)
    assert "suppressed-private-context" not in fields["exception"]


@pytest.mark.parametrize(
    "text",
    [
        "Authorization: Bearer live-token",
        '"api_key": "live-token"',
        "'client_secret': 'live-token'",
        "SharedAccessKey=live-token;",
        "https://example.com/path?token=live-token",
    ],
)
def test_secret_redaction_is_idempotent(text: str) -> None:
    sanitized = redact_text(text)
    assert "live-token" not in sanitized
    assert redact_text(sanitized) == sanitized
