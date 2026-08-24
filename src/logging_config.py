"""Centralized logging configuration for AzBrief.

All entry points (test_local.py, main.py, scheduler.py) should call
``setup_logging()`` once at startup. This ensures a consistent log format,
level filtering, and optional Azure Monitor ingestion for both the
Container App and the scheduler Job.

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
    Azure Monitor Data Collection Endpoint (DCE) URL.
    If set together with the DCR fields, logs are sent to Azure Monitor.
AZURE_MONITOR_DCR_RULE_ID : str
    Data Collection Rule (DCR) immutable ID (``dcr-...``).
AZURE_MONITOR_DCR_STREAM_NAME : str
    Stream name defined in the DCR (default: ``Custom-AzBrief_CL``).
"""

import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import structlog
from structlog import get_logger as _structlog_get_logger

from src.config import get_settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_THIRD_PARTY_LOGGERS = (
    "httpx",
    "httpcore",
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


# ---------------------------------------------------------------------------
# Azure Monitor handler (optional)
# ---------------------------------------------------------------------------


class _AzureMonitorHandler(logging.Handler):
    """Buffered handler that sends log records to Azure Monitor via ingestion API.

    Uses the ``azure-monitor-ingestion`` SDK to upload structured JSON logs
    to a Data Collection Rule (DCR) stream.  Logs are buffered and flushed
    every ``flush_interval`` records or when the handler is closed.
    """

    def __init__(
        self,
        endpoint: str,
        dcr_rule_id: str,
        stream_name: str = "Custom-AzBrief_CL",
        flush_size: int = 50,
    ):
        super().__init__()
        self._endpoint = endpoint
        self._dcr_rule_id = dcr_rule_id
        self._stream_name = stream_name
        self._flush_size = flush_size
        self._buffer: list[dict] = []
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                from azure.identity import DefaultAzureCredential
                from azure.monitor.ingestion import LogsIngestionClient

                credential = DefaultAzureCredential()
                self._client = LogsIngestionClient(
                    endpoint=self._endpoint,
                    credential=credential,
                )
            except Exception as exc:
                logging.getLogger(__name__).warning(
                    "Azure Monitor ingestion client init failed: %s", exc
                )
        return self._client

    def emit(self, record: logging.LogRecord):
        try:
            entry = {
                "TimeGenerated": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "Level": record.levelname,
                "Logger": record.name,
                "Message": self.format(record),
                "Source": "AzBrief",
            }
            # structlog JSON이면 구조화 필드 추가
            if hasattr(record, "msg") and isinstance(record.msg, str):
                try:
                    import json

                    data = json.loads(record.msg)
                    if isinstance(data, dict):
                        entry["Event"] = data.get("event", "")
                        entry["UpdateId"] = data.get("update_id", "")
                        entry["Phase"] = data.get("phase", "")
                        # 전체 structlog JSON을 ExtendedProperties에 보존
                        entry["ExtendedProperties"] = record.msg
                except (json.JSONDecodeError, ValueError):
                    pass

            self._buffer.append(entry)

            if len(self._buffer) >= self._flush_size:
                self.flush()
        except Exception:
            self.handleError(record)

    def flush(self):
        if not self._buffer:
            return
        client = self._get_client()
        if client is None:
            self._buffer.clear()
            return
        try:
            client.upload(
                rule_id=self._dcr_rule_id,
                stream_name=self._stream_name,
                logs=self._buffer,
            )
        except Exception as exc:
            logging.getLogger(__name__).debug("Azure Monitor upload failed: %s", exc)
        finally:
            self._buffer.clear()

    def close(self):
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
    import os

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
    console.setFormatter(logging.Formatter("%(message)s"))
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
            logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        )
        root.addHandler(file_handler)

    # --- Azure Monitor handler (optional) ---
    ingestion_endpoint = os.environ.get("AZURE_MONITOR_INGESTION_ENDPOINT", "")
    dcr_rule_id = os.environ.get("AZURE_MONITOR_DCR_RULE_ID", "")
    dcr_stream = os.environ.get("AZURE_MONITOR_DCR_STREAM_NAME", "Custom-AzBrief_CL")

    if ingestion_endpoint and dcr_rule_id:
        try:
            az_handler = _AzureMonitorHandler(
                endpoint=ingestion_endpoint,
                dcr_rule_id=dcr_rule_id,
                stream_name=dcr_stream,
            )
            az_handler.setLevel(logging.INFO)  # INFO 이상만 Azure Monitor로 전송
            root.addHandler(az_handler)
            logger.info(
                "Azure Monitor logging enabled (endpoint=%s, dcr=%s)",
                ingestion_endpoint[:40] + "...",
                dcr_rule_id[:20] + "...",
            )
        except Exception as exc:
            logger.warning("Failed to set up Azure Monitor handler: %s", exc)

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
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.UnicodeDecoder(),
            structlog.processors.JSONRenderer(ensure_ascii=False),
        ],
    )

    return log_file_path
