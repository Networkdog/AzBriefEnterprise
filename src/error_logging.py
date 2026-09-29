"""Bounded, redacted exception diagnostics shared by logs and telemetry."""

import os
import re
import sys
import traceback
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from pydantic import ValidationError

ERROR_LOGGER_NAME = "azbrief.errors"
_ROOT = Path(__file__).resolve().parent.parent
_REDACTED = "[REDACTED]"
_MAX_MESSAGE_CHARS = 4000
_MAX_STACK_CHARS = 30000
_MAX_CHAIN_DEPTH = 8
_MAX_FRAMES = 32
_secret_values: tuple[str, ...] = ()
_SECRET_SUFFIXES = (
    "password",
    "passwd",
    "secret",
    "secretkey",
    "apikey",
    "accesstoken",
    "refreshtoken",
    "idtoken",
    "connectionstring",
    "authorization",
    "accountkey",
    "sharedaccesskey",
    "instrumentationkey",
    "sastoken",
    "sharedaccesssignature",
)
_PRIVATE_FIELDS = {
    "token",
    "sig",
    "signature",
    "cookie",
    "setcookie",
    "headers",
    "body",
    "payload",
    "content",
    "request",
    "response",
    "requestbody",
    "responsebody",
    "rawrequest",
    "rawresponse",
    "prompt",
    "messages",
    "input",
    "inputvalue",
    "subscriber",
    "subscribers",
    "toolarguments",
    "toolargs",
    "toolresult",
    "tooloutput",
    "arguments",
    "locals",
}
_CREDENTIAL_ASSIGNMENT = re.compile(r"""(?ix)
    (?P<key>["']?\b[\w-]*(?:password|passwd|secret|api[_-]?key|access[_-]?token|
    refresh[_-]?token|id[_-]?token|authorization|accountkey|sharedaccesskey|
    secret[_-]?key|sharedaccesssignature|instrumentationkey|sig|signature|cookie|token)
    \b["']?\s*[:=]\s*)
    (?:\[REDACTED\]|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|[^\s,;}\]]+)
    """)
_AUTHORIZATION = re.compile(r"(?i)\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]+")
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")
_EMAIL = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
_URL = re.compile(r"""https?://[^\s<>"']+""", re.IGNORECASE)


def _private_key(key: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", key.lower())
    return normalized in _PRIVATE_FIELDS or normalized.endswith(_SECRET_SUFFIXES)


def configure_redaction(settings: Mapping[str, Any]) -> None:
    """Remember configured secrets for redaction even in unlabelled messages."""
    global _secret_values
    values = {**os.environ, **settings}
    _secret_values = tuple(
        sorted(
            {
                value
                for key, value in values.items()
                if _private_key(key) and isinstance(value, str) and len(value) >= 8
            },
            key=len,
            reverse=True,
        )
    )


def redact_text(text: str) -> str:
    """Remove credentials, email addresses and URL credentials/query strings."""
    for secret in _secret_values:
        text = text.replace(secret, _REDACTED)
    text = _AUTHORIZATION.sub(lambda match: f"{match[1]} {_REDACTED}", text)
    text = _CREDENTIAL_ASSIGNMENT.sub(lambda match: match["key"] + _REDACTED, text)
    text = _JWT.sub(_REDACTED, text)

    def clean_url(match: re.Match[str]) -> str:
        try:
            parts = urlsplit(match[0])
        except ValueError:
            return "[REDACTED_URL]"
        netloc = parts.netloc.rsplit("@", 1)[-1]
        return urlunsplit((parts.scheme, netloc, parts.path, _REDACTED if parts.query else "", ""))

    text = _URL.sub(clean_url, text)
    return _EMAIL.sub("[REDACTED_EMAIL]", text)


def redact_fields(fields: Mapping[str, Any]) -> dict[str, Any]:
    """Redact structured data without inspecting arbitrary object attributes."""

    def clean(value: Any, depth: int = 0) -> Any:
        if depth >= 8:
            return "[TRUNCATED]"
        if isinstance(value, Mapping):
            return {
                str(key): _REDACTED if _private_key(str(key)) else clean(item, depth + 1)
                for key, item in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [clean(item, depth + 1) for item in value]
        if isinstance(value, str):
            return redact_text(value)
        if value is None or isinstance(value, (bool, int, float)):
            return value
        return f"<{type(value).__name__}>"

    return {key: _REDACTED if _private_key(key) else clean(value) for key, value in fields.items()}


def _bounded(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 14] + "...[TRUNCATED]"


def log_path(filename: str) -> str:
    """Keep useful source locations without exposing host-specific home paths."""
    path = Path(filename)
    try:
        return str(path.relative_to(_ROOT))
    except ValueError:
        return path.name


def _exception_message(exc: BaseException) -> str:
    if isinstance(exc, ValidationError):
        details = [
            {"loc": error["loc"], "type": error["type"]}
            for error in exc.errors(include_input=False, include_context=False, include_url=False)
        ]
        return _bounded(redact_text(f"Validation failed: {details}"), _MAX_MESSAGE_CHARS)
    return _bounded(redact_text(str(exc)), _MAX_MESSAGE_CHARS)


def exception_fields(exc: BaseException) -> dict[str, Any]:
    """Describe an exception and its causes, excluding source lines and locals."""
    fields: dict[str, Any] = {
        "error_type": type(exc).__name__,
        "error_message": _exception_message(exc),
    }
    chain: list[str] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen and len(seen) < _MAX_CHAIN_DEPTH:
        seen.add(id(current))
        frames = traceback.extract_tb(current.__traceback__)
        lines = [
            f'  File "{log_path(frame.filename)}", line {frame.lineno}, in {frame.name}'
            for frame in frames[-_MAX_FRAMES:]
        ]
        if len(frames) > _MAX_FRAMES:
            lines.insert(0, "  ...[earlier frames truncated]")
        lines.append(f"{type(current).__name__}: {_exception_message(current)}")
        chain.append("\n".join(lines))

        response = getattr(current, "response", None)
        status = getattr(current, "status_code", None)
        if status is None:
            status = getattr(response, "status_code", None)
        if isinstance(status, int):
            fields.setdefault("status_code", status)
        code = getattr(current, "code", None)
        if code is None:
            code = getattr(getattr(current, "error", None), "code", None)
        if isinstance(code, str):
            fields.setdefault("service_error_code", _bounded(redact_text(code), 256))
        request_id = getattr(current, "request_id", None)
        headers = getattr(response, "headers", None)
        if not isinstance(request_id, str) and isinstance(headers, Mapping):
            for name in ("x-ms-request-id", "x-request-id", "apim-request-id", "request-id"):
                request_id = headers.get(name)
                if isinstance(request_id, str):
                    break
        if isinstance(request_id, str):
            fields.setdefault("request_id", _bounded(redact_text(request_id), 256))
        current = current.__cause__ or (
            None if current.__suppress_context__ else current.__context__
        )

    if current is not None:
        chain.append("...[exception chain truncated]")
    fields["exception"] = _bounded("\nCaused by:\n".join(chain), _MAX_STACK_CHARS)
    return fields


def enrich_error_event(_logger: Any, method_name: str, event: dict[str, Any]) -> dict[str, Any]:
    """Capture active handled exceptions before structlog renders an event."""
    exc_info = event.pop("exc_info", None)
    exc: BaseException | None = None
    if isinstance(exc_info, BaseException):
        exc = exc_info
    elif (
        isinstance(exc_info, tuple)
        and len(exc_info) == 3
        and isinstance(exc_info[1], BaseException)
    ):
        exc = exc_info[1]
    elif exc_info is not False and (
        exc_info or method_name in ("warning", "warn", "error", "exception", "critical")
    ):
        exc = sys.exc_info()[1]
    if exc is None and isinstance(event.get("error"), BaseException):
        exc = event["error"]
    if exc is not None:
        event.update(exception_fields(exc))
        if "error" in event:
            event["error"] = event["error_message"]
    if event.pop("stack_info", False):
        event["stack"] = "\n".join(
            f'  File "{log_path(frame.filename)}", line {frame.lineno}, in {frame.name}'
            for frame in traceback.extract_stack(limit=_MAX_FRAMES)
        )
    return redact_fields(event)
