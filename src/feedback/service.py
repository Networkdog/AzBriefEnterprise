"""Application service for accepting and linking public feedback."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Awaitable, Callable, Optional
from urllib.parse import urlencode, urlparse

from structlog import get_logger

from src.config import Settings, get_settings
from src.feedback.models import FeedbackReceipt, FeedbackRequest, FeedbackSubmission
from src.i18n import normalize_language
from src.services.feedback import FeedbackStore, get_feedback_store

logger = get_logger()

FeedbackNotifier = Callable[[FeedbackSubmission], Awaitable[bool]]


def build_feedback_page_url(
    settings: Settings,
    language: str = "en",
    report_reference: str = "",
) -> str:
    """Build a browser URL from the configured trusted application origin."""
    if not getattr(settings, "feedback_ui_enabled", False):
        return ""
    base_url = (getattr(settings, "feedback_base_url", None) or "").strip()
    parsed = urlparse(base_url)
    if parsed.scheme != "https" or not parsed.netloc:
        return ""
    query = {"lang": normalize_language(language)}
    if report_reference.strip():
        query["report"] = report_reference.strip()[:500]
    return f"{base_url.rstrip('/')}/feedback?{urlencode(query)}"


class FeedbackService:
    """Create immutable feedback documents without exposing stored content."""

    def __init__(
        self,
        store: Optional[FeedbackStore] = None,
        settings: Optional[Settings] = None,
        clock: Optional[Callable[[], datetime]] = None,
        id_factory: Optional[Callable[[], str]] = None,
        notifier: Optional[FeedbackNotifier] = None,
    ):
        self.store = store or get_feedback_store()
        self.settings = settings or get_settings()
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._id_factory = id_factory or (lambda: uuid.uuid4().hex)
        self._notifier = notifier

    @property
    def configured(self) -> bool:
        return self.store.configured

    def set_notifier(self, notifier: FeedbackNotifier) -> None:
        """Attach the control-plane email notifier after application startup."""
        self._notifier = notifier

    async def submit(self, request: FeedbackRequest) -> FeedbackReceipt:
        """Persist one submission; honeypot traffic receives a silent acknowledgement."""
        if request.website:
            return FeedbackReceipt()
        created_at = self._clock().astimezone(timezone.utc)
        feedback_id = f"{created_at:%Y%m%dT%H%M%S%fZ}-{self._id_factory()}"
        submission = FeedbackSubmission(
            feedback_id=feedback_id,
            created_at=created_at,
            category=request.category,
            subject=request.subject,
            details=request.details,
            contact_email=request.contact_email,
            report_reference=request.report_reference,
            language=normalize_language(request.language),
        )
        await self.store.put(submission)
        notification_sent = False
        if self._notifier is not None:
            try:
                notification_sent = await self._notifier(submission)
            except Exception as exc:
                logger.error(
                    "feedback_notification_failed",
                    feedback_id=feedback_id,
                    category=request.category.value,
                    error=str(exc),
                )
        return FeedbackReceipt(
            feedback_id=feedback_id,
            notification_sent=notification_sent,
        )

    def page_url(self, language: str = "en", report_reference: str = "") -> str:
        """Build a browser URL from the configured trusted application origin."""
        return build_feedback_page_url(self.settings, language, report_reference)


_service: Optional[FeedbackService] = None


def get_feedback_service() -> FeedbackService:
    """Return the process-wide feedback application service."""
    global _service
    if _service is None:
        _service = FeedbackService()
    return _service


def reset_feedback_service() -> None:
    """Clear the cached service so changed settings take effect in tests."""
    global _service
    _service = None
