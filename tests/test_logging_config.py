"""Offline checks for console, DCR and Application Insights error delivery."""

import io
import json
import logging
from collections.abc import Iterator
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
import structlog
from structlog.contextvars import bound_contextvars

from src import error_logging, logging_config
from src.agent import telemetry
from src.config import Settings
from src.error_logging import ERROR_LOGGER_NAME


@pytest.fixture
def logging_state(monkeypatch: pytest.MonkeyPatch) -> Iterator[Settings]:
    root = logging.getLogger()
    target = logging.getLogger(ERROR_LOGGER_NAME)
    saved_handlers, saved_level = root.handlers[:], root.level
    saved_app_level = logging.getLogger("src").level
    target_state = (target.handlers[:], target.level, target.propagate)
    structlog_state = structlog.get_config().copy()
    monkeypatch.setattr(logging_config, "_CONFIGURED", False)
    monkeypatch.setattr(error_logging, "_secret_values", ())
    monkeypatch.setattr(telemetry, "_configured", False)
    monkeypatch.setattr(telemetry, "_enabled", False)
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    monkeypatch.delenv("LOG_CONSOLE_LEVEL", raising=False)
    settings = Settings(
        _env_file=None,
        azure_tenant_id="00000000-0000-0000-0000-000000000000",
        otel_enabled=False,
        applicationinsights_connection_string=None,
        azure_monitor_ingestion_endpoint=None,
        azure_monitor_dcr_rule_id=None,
    )
    monkeypatch.setattr(logging_config, "get_settings", lambda: settings)
    try:
        yield settings
    finally:
        for handler in root.handlers[:]:
            if handler not in saved_handlers:
                handler.close()
        root.handlers[:] = saved_handlers
        root.setLevel(saved_level)
        logging.getLogger("src").setLevel(saved_app_level)
        target.handlers[:], target.level, target.propagate = target_state
        structlog.configure(**structlog_state)


def test_console_warning_captures_the_active_exception_as_one_json_record(
    logging_state: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    console = io.StringIO()
    monkeypatch.setattr("sys.stderr", console)
    logging_config.setup_logging(file_enabled=False)
    with bound_contextvars(trace_id="trace-1", update_id="update-1"):
        try:
            raise RuntimeError("backend rejected request; password=do-not-log")
        except RuntimeError:
            structlog.get_logger("src.synthetic").warning("operation_failed")
    lines = console.getvalue().splitlines()
    assert len(lines) == 1
    event = json.loads(lines[0])
    assert event["event"] == "operation_failed"
    assert event["error_type"] == "RuntimeError"
    assert "backend rejected request" in event["error_message"]
    assert "test_console_warning_captures" in event["exception"]
    assert event["trace_id"] == "trace-1"
    assert event["update_id"] == "update-1"
    assert "do-not-log" not in console.getvalue()


def test_structured_secret_fields_remain_valid_json_after_formatting(
    logging_state: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    console = io.StringIO()
    monkeypatch.setattr("sys.stderr", console)
    logging_config.setup_logging(file_enabled=False)
    structlog.get_logger("src.synthetic").error(
        "credentials_rejected", api_key="secret-api-key", password="secret-password"
    )
    event = json.loads(console.getvalue())
    assert event["api_key"] == "[REDACTED]"
    assert event["password"] == "[REDACTED]"


def test_error_export_starts_at_logging_setup_and_is_idempotent(
    logging_state: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    setup = MagicMock(return_value=True)
    monkeypatch.setattr(telemetry, "setup_telemetry", setup)
    target = logging.getLogger(ERROR_LOGGER_NAME)
    target.propagate = False
    received: list[logging.LogRecord] = []
    sink = logging.Handler()
    sink.emit = received.append
    target.handlers[:] = [sink]
    logging_config.setup_logging(file_enabled=False)
    logging_config.setup_logging(file_enabled=False)
    try:
        raise ValueError("synthetic detailed failure")
    except ValueError:
        structlog.get_logger("src.synthetic").error(
            "operation_failed", run_id="run-1", trace_id="trace-1"
        )
    structlog.get_logger("src.synthetic").info("ordinary_success")
    logging.getLogger("azure.monitor").warning("exporter diagnostic")
    assert setup.call_count == 1
    assert len(received) == 1
    record = received[0]
    assert record.exc_info is None
    assert record.__dict__["exception.type"] == "ValueError"
    assert "test_error_export_starts" in record.__dict__["exception.stacktrace"]
    assert record.__dict__["run_id"] == "run-1"
    assert record.__dict__["trace_id"] == "trace-1"
    assert "synthetic detailed failure" in record.getMessage()


def test_real_otel_sink_receives_exception_attributes_without_network(
    logging_state: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    from azure.monitor.opentelemetry.exporter.export.logs._exporter import (
        _convert_log_to_envelope,
    )
    from opentelemetry.instrumentation.logging.handler import LoggingHandler
    from opentelemetry.sdk._logs import LoggerProvider
    from opentelemetry.sdk._logs.export import (
        InMemoryLogRecordExporter,
        SimpleLogRecordProcessor,
    )

    monkeypatch.setenv("OTEL_SDK_DISABLED", "false")
    provider = LoggerProvider(shutdown_on_exit=False)
    exporter = InMemoryLogRecordExporter()
    provider.add_log_record_processor(SimpleLogRecordProcessor(exporter))
    sink = LoggingHandler(logger_provider=provider)
    target = logging.getLogger(ERROR_LOGGER_NAME)
    target.handlers[:] = [sink]
    target.propagate = False
    monkeypatch.setattr(telemetry, "setup_telemetry", lambda _: True)
    logging_config.setup_logging(file_enabled=False)
    try:
        try:
            raise OSError("transport down")
        except OSError as exc:
            raise RuntimeError("call failed; api_key=private-value") from exc
    except RuntimeError:
        structlog.get_logger("src.synthetic").warning(
            "foundry_hosted_analysis_failed", trace_id="trace-2", update_id="update-2"
        )
    records = exporter.get_finished_logs()
    assert len(records) == 1
    record = records[0].log_record
    assert record.attributes["exception.type"] == "RuntimeError"
    assert "OSError: transport down" in record.attributes["exception.stacktrace"]
    assert record.attributes["trace_id"] == "trace-2"
    assert "private-value" not in record.body
    assert "private-value" not in str(record.attributes)
    envelope = _convert_log_to_envelope(records[0])
    assert envelope.data.base_type == "ExceptionData"
    assert envelope.data.base_data.properties["trace_id"] == "trace-2"
    assert "OSError: transport down" in envelope.data.base_data.exceptions[0].stack
    provider.shutdown()


def test_dcr_error_flushes_immediately_and_uses_settings(
    logging_state: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    logging_state.azure_monitor_ingestion_endpoint = "https://test.ingest.monitor.azure.com"
    logging_state.azure_monitor_dcr_rule_id = "dcr-test"
    client = MagicMock()
    monkeypatch.setattr(logging_config._AzureMonitorHandler, "_get_client", lambda _: client)
    logging_config.setup_logging(file_enabled=False)
    try:
        raise RuntimeError("DCR failure; password=private-value")
    except RuntimeError:
        structlog.get_logger("src.synthetic").error("task_failed", trace_id="dcr-trace")
    client.upload.assert_called_once()
    batch = client.upload.call_args.kwargs["logs"]
    failure = next(item for item in batch if item.get("Event") == "task_failed")
    entry = json.loads(failure["ExtendedProperties"])
    assert entry["error_type"] == "RuntimeError"
    assert entry["trace_id"] == "dcr-trace"
    assert "private-value" not in failure["Message"]
    assert client.upload.call_args.kwargs["rule_id"] == "dcr-test"


def test_dcr_exports_only_errors_and_failure_markers(
    logging_state: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    logging_state.azure_monitor_ingestion_endpoint = "https://test.ingest.monitor.azure.com"
    logging_state.azure_monitor_dcr_rule_id = "dcr-test"
    client = MagicMock()
    monkeypatch.setenv("AZBRIEF_RUNTIME", "scheduler")
    monkeypatch.setattr(logging_config._AzureMonitorHandler, "_get_client", lambda _: client)
    logging_config.setup_logging(file_enabled=False)

    log = structlog.get_logger("src.synthetic")
    log.info("ordinary_success", status="completed", failed=0)
    log.warning("retry_scheduled", attempt=1)
    client.upload.assert_not_called()

    log.warning("tool_failed", trace_id="trace-1", update_id="update-1")
    log.info(
        "orchestrator_run_complete",
        status="partial",
        run_id="run-1",
        failed=2,
        archive_failed=1,
        pending=3,
        deferred=4,
    )
    log.info("batch_progress", status="running", failed=1)
    log.warning("analysis_iteration_stopped", transition="max_turns")

    assert client.upload.call_count == 4
    first = client.upload.call_args_list[0].kwargs["logs"][0]
    assert set(first) == set(logging_config.FAILURE_LOG_FIELD_TYPES)
    assert first["Event"] == "tool_failed"
    assert first["FailureKind"] == "failure_event"
    assert first["Runtime"] == "scheduler"
    assert first["TraceId"] == "trace-1"
    assert first["UpdateId"] == "update-1"

    second = client.upload.call_args_list[1].kwargs["logs"][0]
    assert second["Event"] == "orchestrator_run_complete"
    assert second["FailureKind"] == "partial_status"
    assert second["Status"] == "partial"
    assert second["RunId"] == "run-1"
    assert second["FailedCount"] == 2
    assert second["ArchiveFailedCount"] == 1
    assert second["PendingCount"] == 3
    assert second["DeferredCount"] == 4

    third = client.upload.call_args_list[2].kwargs["logs"][0]
    assert third["FailureKind"] == "failure_count"
    assert third["Status"] == "running"
    assert third["FailedCount"] == 1

    fourth = client.upload.call_args_list[3].kwargs["logs"][0]
    assert fourth["Event"] == "analysis_iteration_stopped"
    assert fourth["FailureKind"] == "failed_status"
    assert fourth["Status"] == "max_turns"


def test_failure_history_timestamp_is_the_event_time_not_the_upload_time(
    logging_state: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    handler = logging_config._AzureMonitorHandler("https://test.example", "dcr-test")
    client = MagicMock()
    monkeypatch.setattr(handler, "_get_client", lambda: client)
    record = logging.LogRecord(
        "src.synthetic", logging.ERROR, __file__, 1, "synthetic failure", (), None
    )
    record.created = datetime(2026, 9, 28, 1, 2, 3, tzinfo=timezone.utc).timestamp()

    handler.emit(record)

    assert client.upload.call_args.kwargs["logs"][0]["TimeGenerated"] == "2026-09-28T01:02:03Z"
    handler.close()


def test_dcr_exports_info_record_with_failed_status(
    logging_state: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    logging_state.azure_monitor_ingestion_endpoint = "https://test.ingest.monitor.azure.com"
    logging_state.azure_monitor_dcr_rule_id = "dcr-test"
    client = MagicMock()
    monkeypatch.setattr(logging_config._AzureMonitorHandler, "_get_client", lambda _: client)
    logging_config.setup_logging(file_enabled=False)

    structlog.get_logger("src.synthetic").info(
        "hosted_request_completed",
        status="failed",
        operation="analyze_update",
        response_id="response-1",
    )

    entry = client.upload.call_args.kwargs["logs"][0]
    assert entry["FailureKind"] == "failed_status"
    assert entry["Status"] == "failed"
    assert entry["Operation"] == "analyze_update"
    assert entry["ResponseId"] == "response-1"


def test_incomplete_dcr_configuration_is_reported_locally(
    logging_state: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    console = io.StringIO()
    monkeypatch.setattr("sys.stderr", console)
    logging_state.azure_monitor_ingestion_endpoint = "https://test.ingest.monitor.azure.com"

    logging_config.setup_logging(file_enabled=False)

    assert "azure_monitor_configuration_incomplete" in console.getvalue()
    assert not any(
        isinstance(handler, logging_config._AzureMonitorHandler)
        for handler in logging.getLogger().handlers
    )


def test_dcr_upload_failure_is_reported_without_recursive_export(
    logging_state: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    console = io.StringIO()
    monkeypatch.setattr("sys.stderr", console)
    logging_state.azure_monitor_ingestion_endpoint = "https://test.ingest.monitor.azure.com"
    logging_state.azure_monitor_dcr_rule_id = "dcr-test"
    client = MagicMock()

    def fail_upload(**kwargs):
        logging.getLogger("azure.core.pipeline").error("exporter failed; token=private-value")
        raise RuntimeError("upload failed; token=private-value")

    client.upload.side_effect = fail_upload
    monkeypatch.setattr(logging_config._AzureMonitorHandler, "_get_client", lambda _: client)
    logging_config.setup_logging(file_enabled=False)
    structlog.get_logger("src.synthetic").error("original_failure")
    assert client.upload.call_count == 1
    assert "azure_monitor_upload_failed" in console.getvalue()
    assert "private-value" not in console.getvalue()


def test_dcr_extended_properties_are_redacted_for_stdlib_json(
    logging_state: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    logging_state.azure_monitor_ingestion_endpoint = "https://test.ingest.monitor.azure.com"
    logging_state.azure_monitor_dcr_rule_id = "dcr-test"
    client = MagicMock()
    monkeypatch.setattr(logging_config._AzureMonitorHandler, "_get_client", lambda _: client)
    logging_config.setup_logging(file_enabled=False)
    logging.getLogger("src.synthetic").error(
        json.dumps({"event": "stdlib_failure", "api_key": "private-value"})
    )
    failure = next(
        item
        for item in client.upload.call_args.kwargs["logs"]
        if item.get("Event") == "stdlib_failure"
    )
    assert "private-value" not in json.dumps(failure)
    assert json.loads(failure["ExtendedProperties"])["api_key"] == "[REDACTED]"


def test_disabling_log_export_is_visible_and_does_not_attach_a_bridge(
    logging_state: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    console = io.StringIO()
    monkeypatch.setattr("sys.stderr", console)
    monkeypatch.setenv("OTEL_LOGS_EXPORTER", "None")
    monkeypatch.setattr(telemetry, "setup_telemetry", lambda _: True)
    logging_config.setup_logging(file_enabled=False)
    assert "applicationinsights_error_logs_disabled" in console.getvalue()
    assert not any(
        isinstance(handler, logging_config._ApplicationInsightsErrorHandler)
        for handler in logging.getLogger().handlers
    )


def test_stdlib_exception_formatter_does_not_capture_source_or_locals() -> None:
    formatter = logging_config._RedactingFormatter("%(message)s")
    try:
        raise RuntimeError("failed; client_secret=private-value")
    except RuntimeError:
        import sys

        record = logging.LogRecord(
            "src.synthetic", logging.ERROR, __file__, 1, "operation_failed", (), sys.exc_info()
        )
    rendered = formatter.format(record)
    assert "RuntimeError: failed" in rendered
    assert "test_stdlib_exception_formatter" in rendered
    assert "raise RuntimeError" not in rendered
    assert "private-value" not in rendered
    assert record.exc_text is None


@pytest.mark.asyncio
async def test_hosted_error_details_reach_sink_but_not_the_wire_contract(
    logging_state: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(telemetry, "setup_telemetry", lambda _: True)
    target = logging.getLogger(ERROR_LOGGER_NAME)
    target.propagate = False
    records: list[logging.LogRecord] = []
    sink = logging.Handler()
    sink.emit = records.append
    target.handlers[:] = [sink]
    logging_config.setup_logging(file_enabled=False)

    from src.agent.hosted_contract import HostedAnalysisRequest, HostedUpdate
    from src.hosted_agent import execute_request

    class BrokenAnalyzer:
        async def analyze_update(self, update, trace_id=None, scope=None):
            try:
                raise OSError("upstream connection failed; api_key=private-value")
            except OSError:
                structlog.get_logger("src.synthetic").warning("tool_failed")
                raise

    request = HostedAnalysisRequest(
        update=HostedUpdate(id="test-update", title="Synthetic update"),
        trace_id="test-trace",
    )
    response = await execute_request(request.model_dump_json(), BrokenAnalyzer())
    assert response.status == "failed"
    assert response.error == "Hosted analysis failed (OSError)"
    assert "upstream" not in response.model_dump_json()
    assert "private-value" not in response.model_dump_json()
    failures = {record.__dict__["event"]: record for record in records}
    for event in ("tool_failed", "hosted_analysis_failed"):
        record = failures[event]
        assert record.__dict__["trace_id"] == "test-trace"
        assert record.__dict__["update_id"] == "test-update"
        assert "upstream connection failed" in record.__dict__["exception.message"]
        assert "analyze_update" in record.__dict__["exception.stacktrace"]
        assert "private-value" not in record.getMessage()
