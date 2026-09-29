"""Azure Monitor initialization and shutdown tests without remote exports."""

import logging
from collections.abc import Iterator
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.agent import telemetry
from src.error_logging import ERROR_LOGGER_NAME


@pytest.fixture(autouse=True)
def telemetry_state(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    target = logging.getLogger(ERROR_LOGGER_NAME)
    saved = (target.level, target.propagate)
    monkeypatch.setattr(telemetry, "_configured", False)
    monkeypatch.setattr(telemetry, "_enabled", False)
    monkeypatch.setattr(telemetry, "_OTEL_API_AVAILABLE", True)
    monkeypatch.setattr(telemetry.atexit, "register", MagicMock())
    monkeypatch.setenv("OTEL_SDK_DISABLED", "false")
    try:
        yield
    finally:
        target.level, target.propagate = saved


def _settings(enabled: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        otel_enabled=enabled,
        applicationinsights_connection_string="InstrumentationKey=synthetic-test",
    )


def test_setup_keeps_entra_auth_and_isolates_error_export(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure = MagicMock()
    credential = object()
    monkeypatch.setattr("azure.monitor.opentelemetry.configure_azure_monitor", configure)
    monkeypatch.setattr("src.config.get_azure_credential", lambda: credential)
    assert telemetry.setup_telemetry(_settings()) is True
    assert telemetry.setup_telemetry(_settings()) is True
    configure.assert_called_once()
    assert configure.call_args.kwargs["credential"] is credential
    assert configure.call_args.kwargs["logger_name"] == ERROR_LOGGER_NAME
    assert configure.call_args.kwargs["disable_offline_storage"] is True
    assert configure.call_args.kwargs["enable_trace_based_sampling_for_logs"] is False
    assert logging.getLogger(ERROR_LOGGER_NAME).propagate is False
    telemetry.atexit.register.assert_called_once_with(telemetry.flush_telemetry)


@pytest.mark.parametrize("sdk_disabled,enabled", [("true", True), ("TRUE", True), ("false", False)])
def test_disabled_telemetry_never_initializes_exporters(
    monkeypatch: pytest.MonkeyPatch, sdk_disabled: str, enabled: bool
) -> None:
    configure = MagicMock()
    monkeypatch.setenv("OTEL_SDK_DISABLED", sdk_disabled)
    monkeypatch.setattr("azure.monitor.opentelemetry.configure_azure_monitor", configure)
    assert telemetry.setup_telemetry(_settings(enabled)) is False
    configure.assert_not_called()


def test_requested_telemetry_without_connection_string_reports_configuration_gap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logger = MagicMock()
    monkeypatch.setattr(telemetry, "logger", logger)
    settings = _settings()
    settings.applicationinsights_connection_string = None
    assert telemetry.setup_telemetry(settings) is False
    logger.warning.assert_called_once_with(
        "otel_not_configured", api_available=True, has_connection_string=False
    )


def test_failed_exporter_setup_does_not_break_the_application(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "azure.monitor.opentelemetry.configure_azure_monitor",
        MagicMock(side_effect=RuntimeError("exporter unavailable")),
    )
    monkeypatch.setattr("src.config.get_azure_credential", lambda: object())
    assert telemetry.setup_telemetry(_settings()) is False
    assert telemetry.is_enabled() is False


def test_flush_drains_both_providers_with_a_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(telemetry, "_enabled", True)
    logs = MagicMock()
    traces = MagicMock()
    monkeypatch.setattr("opentelemetry._logs.get_logger_provider", lambda: logs)
    monkeypatch.setattr(telemetry._otel_trace, "get_tracer_provider", lambda: traces)
    telemetry.flush_telemetry()
    logs.force_flush.assert_called_once_with(timeout_millis=5000)
    traces.force_flush.assert_called_once_with(timeout_millis=5000)


def test_flush_reports_failure_without_replacing_the_original_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(telemetry, "_enabled", True)
    logs = MagicMock()
    logs.force_flush.side_effect = RuntimeError("export failed")
    logger = MagicMock()
    monkeypatch.setattr(telemetry, "logger", logger)
    monkeypatch.setattr("opentelemetry._logs.get_logger_provider", lambda: logs)
    telemetry.flush_telemetry()
    logger.warning.assert_called_once_with("otel_flush_failed", error_type="RuntimeError")


def test_trace_exception_is_redacted_and_not_recorded_twice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracer = MagicMock()
    span = tracer.start_as_current_span.return_value.__enter__.return_value
    monkeypatch.setattr(telemetry, "get_tracer", lambda: tracer)
    with pytest.raises(RuntimeError):
        with telemetry.traced_span("azbrief.synthetic", trace_id="trace-1"):
            raise RuntimeError("failed; api_key=private-value")
    tracer.start_as_current_span.assert_called_once_with(
        "azbrief.synthetic", record_exception=False, set_status_on_exception=False
    )
    span.record_exception.assert_not_called()
    span.add_event.assert_called_once()
    attributes = span.add_event.call_args.kwargs["attributes"]
    assert attributes["exception.type"] == "RuntimeError"
    assert "test_trace_exception_is_redacted" in attributes["exception.stacktrace"]
    assert "private-value" not in str(attributes)
    assert "private-value" not in span.set_status.call_args.args[0].description
