"""Centralized logging configuration for AzBrief.

All entry points (test_local.py, main.py, scheduler.py) should call
``setup_logging()`` once at startup. This ensures a consistent log format,
level filtering, and optional Azure Monitor ingestion for both the
Container App, scheduler Job and Hosted Agent. Handled exceptions are redacted
and retained in console/file logs and workspace-based Application Insights.

Environment variables consumed
-------------------------------
LOG_LEVEL : str
    Root log level for application code (``src.*``).
    Values: DEBUG | INFO | WARNING | ERROR | CRITICAL (default: INFO)
LOG_FILE_ENABLED : str
    Enable/disable file logging. "true" / "false" (default: "true")
LOG_FILE_DIR : str
    Directory for log files (default: ``logs/``)
LOG_CONSOLE_LEVEL : str
    Override console handler level. Useful for CLI (default: same as LOG_LEVEL;
    ``test_local.py`` overrides to CRITICAL to keep terminal clean)
AZURE_MONITOR_INGESTION_ENDPOINT : str
    Azure Monitor DCR logs-ingestion endpoint or Data Collection Endpoint URL.
    If set together with the DCR fields, failure events are sent to Azure Monitor.
AZURE_MONITOR_DCR_RULE_ID : str
    Data Collection Rule (DCR) immutable ID (``dcr-...``).
AZURE_MONITOR_DCR_STREAM_NAME : str
    Stream name defined in the DCR (default: ``Custom-AzBriefFailures_CL``).
OTEL_ENABLED / APPLICATIONINSIGHTS_CONNECTION_STRING
    Enable traces and application warning/error export to Application Insights.
    OTEL_SDK_DISABLED=true prevents this export in offline tests.
"""

import copy
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import TracebackType
from typing import TYPE_CHECKING, Any, Optional

import structlog
from structlog import get_logger as _structlog_get_logger

from src.config import get_settings
from src.error_logging import (
    ERROR_LOGGER_NAME,
    configure_redaction,
    enrich_error_event,
    exception_fields,
    log_path,
    redact_fields,
    redact_text,
)

if TYPE_CHECKING:
    from azure.monitor.ingestion import LogsIngestionClient

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_THIRD_PARTY_LOGGERS = (
    "httpx",
    "httpcore",
    "httpx2",
    "httpcore2",
    "azure",
    "openai",
    "urllib3",
    "msal",
    "msal.token_cache",
    "msal.authority",
    "msal.application",
    "msal.telemetry",
)

_CONFIGURED = False  # guard against double-init
_FAILURE_STATUSES = frozenset(
    {
        "aborted",
        "critical",
        "error",
        "failed",
        "failure",
        "max_turns",
        "model_error",
        "partial",
        "prompt_too_long",
    }
)
_FAILURE_EVENT_SUFFIXES = ("_error", "_failed", "_failure", "_partial")
_FAILURE_COUNT_FIELDS = frozenset(
    {
        "archive_failed",
        "error_count",
        "errors",
        "failed",
        "failed_count",
        "failure_count",
    }
)
FAILURE_LOG_FIELD_TYPES = {
    "TimeGenerated": "datetime",
    "Level": "string",
    "Logger": "string",
    "Message": "string",
    "Source": "string",
    "Event": "string",
    "Status": "string",
    "FailureKind": "string",
    "Runtime": "string",
    "TraceId": "string",
    "RunId": "string",
    "UpdateId": "string",
    "TaskId": "string",
    "Operation": "string",
    "Phase": "string",
    "AgentRole": "string",
    "AgentName": "string",
    "ResponseId": "string",
    "StatusCode": "int",
    "ServiceErrorCode": "string",
    "RequestId": "string",
    "ErrorType": "string",
    "ErrorMessage": "string",
    "FailedCount": "int",
    "ArchiveFailedCount": "int",
    "PendingCount": "int",
    "DeferredCount": "int",
    "ExtendedProperties": "string",
}


class _RedactingFormatter(logging.Formatter):
    def formatException(
        self,
        exc_info: tuple[type[BaseException] | None, BaseException | None, TracebackType | None],
    ) -> str:
        return str(exception_fields(exc_info[1])["exception"]) if exc_info[1] is not None else ""

    def format(self, record: logging.LogRecord) -> str:
        safe_record = copy.copy(record)
        message = record.getMessage()
        try:
            event = json.loads(message)
        except (json.JSONDecodeError, ValueError):
            event = None
        safe_record.msg = (
            json.dumps(redact_fields(event), ensure_ascii=False)
            if isinstance(event, dict)
            else redact_text(message)
        )
        safe_record.args = ()
        safe_record.exc_text = None
        safe_record.stack_info = None
        return super().format(safe_record)


def _export_failure(event: str, exc: Exception, **fields: Any) -> None:
    logger.warning(
        json.dumps(
            redact_fields({"event": event, **fields, **exception_fields(exc)}),
            ensure_ascii=False,
        ),
        extra={"_skip_cloud_export": True},
    )


def _is_application_record(record: logging.LogRecord) -> bool:
    return not getattr(record, "_skip_cloud_export", False) and (
        record.name in ("__main__", "src") or record.name.startswith(("src.", "scripts."))
    )


def _normalized_marker(value: Any) -> str:
    return str(value).strip().lower().replace("-", "_").replace(" ", "_")


def _has_positive_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value > 0
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"", "0", "false", "none", "null"}:
            return False
        try:
            return float(normalized) > 0
        except ValueError:
            return True
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return value is not None


def _classify_failure_record(record: logging.LogRecord, event: dict[str, Any] | None) -> str | None:
    """Return the custom-table failure category for an eligible record."""
    if event and event.get("error_type"):
        return "exception"
    if record.levelno >= logging.ERROR:
        return "error_log"
    if not event:
        return None

    for field in (
        "status",
        "result",
        "outcome",
        "state",
        "verdict",
        "transition",
        "transition_type",
        "terminal_reason",
    ):
        status = _normalized_marker(event.get(field, ""))
        if status in _FAILURE_STATUSES:
            return "partial_status" if status == "partial" else "failed_status"

    if event.get("success") is False:
        return "failed_result"

    event_name = _normalized_marker(event.get("event", ""))
    if event_name.endswith(_FAILURE_EVENT_SUFFIXES):
        return "failure_event"

    if _has_positive_value(event.get("error")):
        return "error_field"
    for field, value in event.items():
        normalized_field = _normalized_marker(field)
        if (
            normalized_field in _FAILURE_COUNT_FIELDS
            or normalized_field.endswith(("_failed", "_failure_count", "_error_count"))
        ) and _has_positive_value(value):
            return "failure_count"

    return None


def _event_text(event: dict[str, Any], *fields: str) -> str:
    for field in fields:
        value = event.get(field)
        if isinstance(value, (str, bool, int, float)):
            return str(value)
    return ""


def _event_count(event: dict[str, Any], field: str) -> int:
    value = event.get(field)
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return max(0, int(value))
    if isinstance(value, str):
        try:
            return max(0, int(value))
        except ValueError:
            pass
    return 0


class _ApplicationInsightsErrorHandler(logging.Handler):
    """Forward only application warnings/errors to the isolated OTel logger."""

    def emit(self, record: logging.LogRecord) -> None:
        if not _is_application_record(record):
            return
        try:
            message = record.getMessage()
            try:
                event = json.loads(message)
            except (json.JSONDecodeError, ValueError):
                event = {"event": message}
            if not isinstance(event, dict):
                event = {"event": message}
            if record.exc_info and record.exc_info[1] is not None:
                event.update(exception_fields(record.exc_info[1]))
            event = redact_fields(event)
            extra = {
                key: value
                for key, value in event.items()
                if key
                in {
                    "event",
                    "trace_id",
                    "run_id",
                    "update_id",
                    "task_id",
                    "operation",
                    "phase",
                    "agent_role",
                    "agent_name",
                    "response_id",
                    "status_code",
                    "service_error_code",
                    "request_id",
                    "http_request_id",
                    "error_type",
                    "error_message",
                }
                and isinstance(value, (str, bool, int, float))
            }
            extra["source_logger"] = record.name
            if event.get("error_type"):
                extra["exception.type"] = event["error_type"]
                extra["exception.message"] = event.get("error_message", "")
                if event.get("exception"):
                    extra["exception.stacktrace"] = event["exception"]
            target = logging.getLogger(ERROR_LOGGER_NAME)
            forwarded = target.makeRecord(
                ERROR_LOGGER_NAME,
                record.levelno,
                log_path(record.pathname),
                record.lineno,
                json.dumps(event, ensure_ascii=False),
                (),
                None,
                func=record.funcName,
                extra=extra,
            )
            forwarded.created = record.created
            target.handle(forwarded)
        except Exception as exc:
            _export_failure("applicationinsights_log_failed", exc)


# ---------------------------------------------------------------------------
# Azure Monitor handler (optional)
# ---------------------------------------------------------------------------


class _AzureMonitorHandler(logging.Handler):
    """Send failure records to an Azure Monitor custom table.

    Uses the ``azure-monitor-ingestion`` SDK to upload structured JSON logs
    to a Data Collection Rule (DCR) stream. Ordinary informational and warning
    records stay in the standard sinks; errors and failure-marked records are
    flushed immediately.
    """

    def __init__(
        self,
        endpoint: str,
        dcr_rule_id: str,
        stream_name: str = "Custom-AzBriefFailures_CL",
    ) -> None:
        super().__init__()
        self._endpoint = endpoint
        self._dcr_rule_id = dcr_rule_id
        self._stream_name = stream_name
        self._buffer: list[dict[str, Any]] = []
        self._client: Optional["LogsIngestionClient"] = None
        self.setFormatter(_RedactingFormatter("%(message)s"))

    def _get_client(self) -> Optional["LogsIngestionClient"]:
        if self._client is None:
            try:
                from azure.monitor.ingestion import LogsIngestionClient

                from src.config import get_azure_credential

                credential = get_azure_credential()
                self._client = LogsIngestionClient(
                    endpoint=self._endpoint,
                    credential=credential,
                )
            except Exception as exc:
                _export_failure("azure_monitor_client_failed", exc)
        return self._client

    def emit(self, record: logging.LogRecord) -> None:
        if getattr(record, "_skip_cloud_export", False) or any(
            record.name == name or record.name.startswith(name + ".")
            for name in (*_THIRD_PARTY_LOGGERS, "opentelemetry", ERROR_LOGGER_NAME)
        ):
            return
        try:
            message = self.format(record)
            entry = {
                "TimeGenerated": datetime.fromtimestamp(record.created, timezone.utc)
                .isoformat()
                .replace("+00:00", "Z"),
                "Level": record.levelname,
                "Logger": record.name,
                "Message": message,
                "Source": "AzBrief",
            }
            # structlog JSON이면 구조화 필드 추가
            try:
                data = json.loads(message)
                if not isinstance(data, dict):
                    data = None
            except (json.JSONDecodeError, ValueError):
                data = None

            failure_kind = _classify_failure_record(record, data)
            if failure_kind is None:
                return

            event = data or {}
            entry.update(
                {
                    "Event": _event_text(event, "event") or message,
                    "Status": _event_text(
                        event,
                        "status",
                        "result",
                        "outcome",
                        "state",
                        "verdict",
                        "transition",
                        "transition_type",
                        "terminal_reason",
                    ),
                    "FailureKind": failure_kind,
                    "Runtime": os.environ.get("AZBRIEF_RUNTIME", "unknown"),
                    "TraceId": _event_text(event, "trace_id"),
                    "RunId": _event_text(event, "run_id"),
                    "UpdateId": _event_text(event, "update_id"),
                    "TaskId": _event_text(event, "task_id"),
                    "Operation": _event_text(event, "operation"),
                    "Phase": _event_text(event, "phase"),
                    "AgentRole": _event_text(event, "agent_role"),
                    "AgentName": _event_text(event, "agent_name"),
                    "ResponseId": _event_text(event, "response_id"),
                    "StatusCode": _event_count(event, "status_code"),
                    "ServiceErrorCode": _event_text(event, "service_error_code"),
                    "RequestId": _event_text(event, "request_id", "http_request_id"),
                    "ErrorType": _event_text(event, "error_type"),
                    "ErrorMessage": _event_text(event, "error_message", "error"),
                    "FailedCount": _event_count(event, "failed"),
                    "ArchiveFailedCount": _event_count(event, "archive_failed"),
                    "PendingCount": _event_count(event, "pending"),
                    "DeferredCount": _event_count(event, "deferred"),
                    "ExtendedProperties": message,
                }
            )
            self._buffer.append(entry)
            self.flush()
        except Exception as exc:
            _export_failure("azure_monitor_log_failed", exc)

    def flush(self) -> None:
        self.acquire()
        try:
            if not self._buffer:
                return
            batch, self._buffer = self._buffer, []
        finally:
            self.release()
        client = self._get_client()
        if client is None:
            return
        try:
            client.upload(
                rule_id=self._dcr_rule_id,
                stream_name=self._stream_name,
                logs=batch,
            )
        except Exception as exc:
            _export_failure("azure_monitor_upload_failed", exc, record_count=len(batch))

    def close(self) -> None:
        self.flush()
        if self._client:
            self._client.close()
        super().close()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def setup_logging(
    *,
    console_level: Optional[str] = None,
    file_enabled: Optional[bool] = None,
    file_dir: Optional[str] = None,
) -> Optional[Path]:
    """Initialize the centralized logging system.

    Call once at application startup. Safe to call multiple times (idempotent).

    Args:
        console_level: Override console log level (e.g., "CRITICAL" for CLI).
                       If None, reads LOG_CONSOLE_LEVEL env or falls back to LOG_LEVEL.
        file_enabled:  Override file logging toggle.
                       If None, reads LOG_FILE_ENABLED env (default: True).
        file_dir:      Override log file directory.
                       If None, reads LOG_FILE_DIR env (default: "logs/").

    Returns:
        Path to the log file, or None if file logging is disabled.
    """
    global _CONFIGURED
    if _CONFIGURED:
        return None
    _CONFIGURED = True

    settings = get_settings()
    configure_redaction(settings.model_dump())

    # --- Resolve configuration ---
    app_level_str = os.environ.get("LOG_LEVEL", settings.log_level).upper()
    app_level = getattr(logging, app_level_str, logging.INFO)

    console_level_str = (
        console_level or os.environ.get("LOG_CONSOLE_LEVEL", app_level_str)
    ).upper()
    console_log_level = getattr(logging, console_level_str, app_level)

    if file_enabled is None:
        file_enabled = os.environ.get("LOG_FILE_ENABLED", "true").lower() == "true"

    if file_dir is None:
        file_dir = os.environ.get("LOG_FILE_DIR", "logs")

    # --- Root logger ---
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)  # 핸들러에서 세밀하게 필터
    root.handlers.clear()

    # --- Console handler ---
    console = logging.StreamHandler(sys.stderr)
    console.setLevel(console_log_level)
    console.setFormatter(_RedactingFormatter("%(message)s"))
    root.addHandler(console)

    # --- File handler ---
    log_file_path: Optional[Path] = None
    if file_enabled:
        log_dir = Path(file_dir)
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file_path = log_dir / f"azbrief_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

        file_handler = logging.FileHandler(log_file_path, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(
            _RedactingFormatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        )
        root.addHandler(file_handler)

    # --- Azure Monitor handler (optional) ---
    ingestion_endpoint = settings.azure_monitor_ingestion_endpoint
    dcr_rule_id = settings.azure_monitor_dcr_rule_id
    dcr_stream = settings.azure_monitor_dcr_stream_name

    if ingestion_endpoint and dcr_rule_id and dcr_stream:
        try:
            az_handler = _AzureMonitorHandler(
                endpoint=ingestion_endpoint,
                dcr_rule_id=dcr_rule_id,
                stream_name=dcr_stream,
            )
            az_handler.setLevel(logging.INFO)  # INFO 이상만 Azure Monitor로 전송
            root.addHandler(az_handler)
            logger.info(
                "Azure Monitor failure logging enabled (endpoint=%s, dcr=%s)",
                ingestion_endpoint[:40] + "...",
                dcr_rule_id[:20] + "...",
            )
        except Exception as exc:
            _export_failure("azure_monitor_setup_failed", exc)
    elif ingestion_endpoint or dcr_rule_id:
        logger.warning(
            json.dumps(
                {
                    "event": "azure_monitor_configuration_incomplete",
                    "endpoint_configured": bool(ingestion_endpoint),
                    "dcr_rule_id_configured": bool(dcr_rule_id),
                    "stream_configured": bool(dcr_stream),
                }
            )
        )

    # --- Third-party log suppression ---
    for name in _THIRD_PARTY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)

    # --- Application logger level ---
    logging.getLogger("src").setLevel(app_level)

    # --- structlog configuration ---
    structlog.configure(
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=False,
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            enrich_error_event,
            structlog.processors.UnicodeDecoder(),
            structlog.processors.JSONRenderer(ensure_ascii=False),
        ],
    )

    from src.agent.telemetry import setup_telemetry

    if setup_telemetry(settings):
        if os.environ.get("OTEL_LOGS_EXPORTER", "").strip().lower() == "none":
            _structlog_get_logger(__name__).warning("applicationinsights_error_logs_disabled")
        else:
            root.addHandler(_ApplicationInsightsErrorHandler(level=logging.WARNING))

    return log_file_path
