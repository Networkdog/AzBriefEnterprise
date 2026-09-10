"""Public feedback page and bounded submission API."""

from __future__ import annotations

import secrets
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from structlog import get_logger

from src.config import get_settings
from src.feedback.models import FeedbackReceipt, FeedbackRequest
from src.feedback.page import render_feedback_page
from src.feedback.service import get_feedback_service
from src.middleware import RateLimiter
from src.services.feedback import FeedbackNotConfiguredError
from src.web_design import DEFAULT_WEB_UI_LANGUAGE
from src.web_fonts import WEB_FONT_CSP_SOURCE

logger = get_logger()
router = APIRouter(tags=["feedback"])
feedback_rate_limiter = RateLimiter(max_requests=5, window_seconds=60)


def _require_feedback_enabled() -> None:
    if not get_settings().feedback_ui_enabled:
        raise HTTPException(status_code=404, detail="Not Found")


def _require_same_origin(request: Request) -> None:
    """Reject explicit cross-site browser posts while allowing non-browser probes."""
    fetch_site = request.headers.get("Sec-Fetch-Site", "").casefold()
    if fetch_site and fetch_site not in {"same-origin", "none"}:
        raise HTTPException(status_code=403, detail="Cross-site submissions are not allowed")
    origin = request.headers.get("Origin", "").strip()
    if not origin:
        return
    parsed = urlparse(origin)
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.netloc.casefold() != request.url.netloc.casefold()
    ):
        raise HTTPException(status_code=403, detail="Cross-site submissions are not allowed")


@router.get("/feedback", response_class=HTMLResponse, include_in_schema=False)
async def feedback_page(
    lang: str = Query(default=DEFAULT_WEB_UI_LANGUAGE, max_length=16),
    report: str = Query(default="", max_length=500),
):
    """Serve the public feedback form."""
    _require_feedback_enabled()
    nonce = secrets.token_urlsafe(16)
    settings = get_settings()
    content = render_feedback_page(
        nonce=nonce,
        language=lang,
        report_reference=report,
        admin_enabled=settings.admin_ui_enabled,
        archive_enabled=settings.archive_ui_enabled,
    )
    csp = (
        "default-src 'none'; "
        f"style-src 'nonce-{nonce}'; "
        f"script-src 'nonce-{nonce}'; "
        "connect-src 'self'; "
        f"font-src 'self' {WEB_FONT_CSP_SOURCE}; "
        "img-src 'self' data:; "
        "form-action 'self'; "
        "frame-ancestors 'none'; "
        "base-uri 'none'"
    )
    return HTMLResponse(
        content=content,
        headers={"Content-Security-Policy": csp, "Cache-Control": "no-store"},
    )


@router.post("/api/feedback", response_model=FeedbackReceipt, status_code=201)
async def submit_feedback(payload: FeedbackRequest, request: Request) -> FeedbackReceipt:
    """Persist one feedback item and notify the configured developer mailbox."""
    _require_feedback_enabled()
    _require_same_origin(request)
    feedback_rate_limiter.check(request)
    service = get_feedback_service()
    if not service.configured:
        raise HTTPException(status_code=503, detail="Feedback storage is not configured")
    try:
        receipt = await service.submit(payload)
    except FeedbackNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail="Feedback storage is not configured") from exc
    except Exception as exc:
        logger.error(
            "feedback_submission_failed",
            category=payload.category.value,
            has_contact=bool(payload.contact_email),
            has_report_reference=bool(payload.report_reference),
            error=str(exc),
        )
        raise HTTPException(status_code=503, detail="Feedback could not be stored") from exc
    logger.info(
        "feedback_submission_accepted",
        feedback_id=receipt.feedback_id,
        category=payload.category.value,
        has_contact=bool(payload.contact_email),
        has_report_reference=bool(payload.report_reference),
        notification_sent=receipt.notification_sent,
    )
    return receipt
