"""Application service for creating and browsing canonical analysis archives."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Callable, Optional
from urllib.parse import quote, urlparse

from src.agent.analyzer import AnalysisResult
from src.archive.models import (
    ArchiveAnalysisResultV1,
    ArchiveDocumentV1,
    ArchivePage,
    ArchiveQuery,
    ArchiveReceipt,
    ArchiveSource,
    ArchiveUpdateV1,
)
from src.config import Settings, get_settings
from src.rss.parser import AzureUpdate
from src.services.archive import ArchiveStore, get_archive_store

_MAX_EPOCH_MILLISECONDS = 9_999_999_999_999


def create_archive_id(analyzed_at: datetime, random_hex: str) -> str:
    """Create a lexicographically newest-first immutable archive ID."""
    if analyzed_at.tzinfo is None:
        analyzed_at = analyzed_at.replace(tzinfo=timezone.utc)
    epoch_ms = int(analyzed_at.astimezone(timezone.utc).timestamp() * 1000)
    reverse_epoch = _MAX_EPOCH_MILLISECONDS - epoch_ms
    normalized_random = random_hex.lower().replace("-", "")
    if reverse_epoch < 0 or len(normalized_random) != 32:
        raise ValueError("archive ID components are out of range")
    return f"{reverse_epoch:013d}-{normalized_random}"


class ArchiveService:
    """Own archive document construction while delegating I/O to a store."""

    def __init__(
        self,
        store: Optional[ArchiveStore] = None,
        settings: Optional[Settings] = None,
        clock: Optional[Callable[[], datetime]] = None,
        id_factory: Optional[Callable[[], str]] = None,
    ):
        self.store = store or get_archive_store()
        self.settings = settings or get_settings()
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._id_factory = id_factory or (lambda: uuid.uuid4().hex)

    @property
    def configured(self) -> bool:
        return self.store.configured

    async def archive_analysis(
        self,
        update: AzureUpdate,
        result: AnalysisResult,
        source: ArchiveSource,
        run_id: str = "",
    ) -> ArchiveReceipt:
        """Commit one canonical analysis, or no-op when no store is configured."""
        if not self.store.configured:
            return ArchiveReceipt(archived=False)
        analyzed_at = self._clock()
        document = ArchiveDocumentV1(
            archive_id=create_archive_id(analyzed_at, self._id_factory()),
            analyzed_at=analyzed_at,
            source=source,
            run_id=run_id,
            hosted_agent_name=self.settings.foundry_hosted_agent_name or "",
            trace_id=getattr(result, "_hosted_trace_id", ""),
            report_language=self.settings.report_language,
            update=ArchiveUpdateV1.model_validate(update.to_dict()),
            result=ArchiveAnalysisResultV1.model_validate(
                result.model_dump(mode="json", exclude={"job_relevance"})
            ),
        )
        return await self.store.put(document)

    async def get(self, archive_id: str) -> Optional[ArchiveDocumentV1]:
        return await self.store.get(archive_id)

    async def list(self, query: ArchiveQuery) -> ArchivePage:
        return await self.store.list(query)

    def detail_url(self, archive_id: str) -> str:
        """Return the authenticated browser URL without exposing the blob URL."""
        if not self.settings.archive_ui_enabled:
            return ""
        base_url = (getattr(self.settings, "archive_base_url", None) or "").strip()
        parsed = urlparse(base_url)
        if parsed.scheme != "https" or not parsed.netloc:
            return ""
        return f"{base_url.rstrip('/')}/archive/{quote(archive_id, safe='')}"


_service: Optional[ArchiveService] = None


def get_archive_service() -> ArchiveService:
    """Return the process-wide archive application service."""
    global _service
    if _service is None:
        _service = ArchiveService()
    return _service


def reset_archive_service() -> None:
    """Clear the cached service so changed settings take effect in tests."""
    global _service
    _service = None
