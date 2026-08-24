"""OpenTelemetry tracing for AzBrief — optional, graceful-degrading.

Adds distributed tracing of the analysis transaction and every tool call so the
non-deterministic agent loop can be inspected in Azure Monitor / Application
Insights (latency, token cost, tool-call tree, failures) — the observability
layer recommended for production agents.

Design constraints:
- **Absence-tolerant.** Every symbol here degrades to a no-op when the
  OpenTelemetry + Azure Monitor exporter packages are missing or telemetry is
  disabled, so a stripped-down environment still runs the analysis.
- **Never breaks the run.** All setup and span operations are wrapped so a
  misconfigured exporter can never fail an analysis.
- **structlog stays the source of truth** for structured event logs; OTel adds
  the span/trace view on top (top-level transaction + tool spans).
"""

from __future__ import annotations

import contextlib
from typing import Any, Iterator, Optional

import structlog

logger = structlog.get_logger(__name__)

# The OpenTelemetry *API* is a light package; the exporter is heavy. Import only
# the API here and treat its absence as "tracing unavailable".
_OTEL_API_AVAILABLE = False
try:  # pragma: no cover - import guard
    from opentelemetry import trace as _otel_trace
    from opentelemetry.trace import Status, StatusCode

    _OTEL_API_AVAILABLE = True
except Exception:  # pragma: no cover - packages not installed
    _otel_trace = None  # type: ignore[assignment]
    Status = None  # type: ignore[assignment]
    StatusCode = None  # type: ignore[assignment]

_TRACER_NAME = "azbrief"

# Module-level state (idempotent one-time configuration).
_configured = False
_enabled = False


def setup_telemetry(settings: Any) -> bool:
    """Configure the Azure Monitor OpenTelemetry exporter once, if requested.

    Idempotent: safe to call at the start of every analysis. Returns whether
    tracing is active. No-op (returns False) when telemetry is disabled, the
    connection string is missing, or the optional packages are not installed.

    Args:
        settings: The ``Settings`` object (reads ``otel_enabled`` and
            ``applicationinsights_connection_string``).

    Returns:
        True when spans will be exported, False otherwise.
    """
    global _configured, _enabled
    if _configured:
        return _enabled
    _configured = True

    if not getattr(settings, "otel_enabled", False):
        return False

    conn = getattr(settings, "applicationinsights_connection_string", None)
    if not _OTEL_API_AVAILABLE or not conn:
        logger.debug(
            "otel_not_configured",
            api_available=_OTEL_API_AVAILABLE,
            has_connection_string=bool(conn),
        )
        return False

    try:
        from azure.monitor.opentelemetry import configure_azure_monitor

        # Only export traces from AzBrief; disable auto-log capture so structlog
        # remains the single logging pipeline (avoids duplicate log ingestion).
        configure_azure_monitor(
            connection_string=conn,
            logger_name=_TRACER_NAME,
            disable_offline_storage=True,
        )
        _enabled = True
        logger.info("otel_configured", exporter="azure_monitor")
    except Exception as exc:  # pragma: no cover - exporter/runtime dependent
        logger.warning("otel_configure_failed", error=str(exc))
        _enabled = False

    return _enabled


def is_enabled() -> bool:
    """Return whether tracing is currently active."""
    return _enabled and _OTEL_API_AVAILABLE


def get_tracer():
    """Return the AzBrief tracer, or None when tracing is inactive."""
    if not is_enabled():
        return None
    try:  # pragma: no cover - trivial
        return _otel_trace.get_tracer(_TRACER_NAME)
    except Exception:
        return None


@contextlib.contextmanager
def traced_span(name: str, **attributes: Any) -> Iterator[Optional[Any]]:
    """Context manager that opens an OTel span, or a no-op when tracing is off.

    Records any exception on the span and marks it errored, then re-raises. When
    tracing is inactive this yields ``None`` with zero overhead beyond the
    context-manager machinery, so call sites need no conditional.

    Args:
        name: Span name (e.g. ``"azbrief.analyze"`` or ``"azbrief.tool.query_azure_resources"``).
        **attributes: Span attributes; ``None`` values are skipped.

    Yields:
        The active span, or ``None`` when tracing is inactive.
    """
    tracer = get_tracer()
    if tracer is None:
        yield None
        return

    with tracer.start_as_current_span(name) as span:
        try:
            for key, value in attributes.items():
                if value is not None:
                    span.set_attribute(key, value)
        except Exception:  # pragma: no cover - attribute setting is best-effort
            pass
        try:
            yield span
        except Exception as exc:
            try:  # pragma: no cover - error path
                span.record_exception(exc)
                if Status is not None and StatusCode is not None:
                    span.set_status(Status(StatusCode.ERROR, str(exc)))
            except Exception:
                pass
            raise
