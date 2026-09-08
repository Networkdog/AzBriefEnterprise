"""Create-only persistence backends for public feedback submissions."""

from __future__ import annotations

import asyncio
import os
import uuid
from pathlib import Path
from typing import Optional
from urllib.parse import quote, urlparse

import httpx

from src.agent.resilience import calculate_backoff
from src.feedback.models import FeedbackSubmission

BLOB_API_VERSION = "2023-11-03"
STORAGE_SCOPE = "https://storage.azure.com/.default"
FEEDBACK_OBJECT_PREFIX = "feedback/"

_MAX_DOCUMENT_BYTES = 65_536
_MAX_WRITE_ATTEMPTS = 3
_REQUEST_TIMEOUT_S = 30
_TRANSIENT_STATUS_CODES = frozenset({408, 429, 500, 502, 503, 504})


class FeedbackNotConfiguredError(RuntimeError):
    """No durable feedback backend is available."""


class FeedbackConflictError(RuntimeError):
    """A generated feedback ID already exists."""


def feedback_object_name(submission: FeedbackSubmission) -> str:
    """Build a partitioned object name from trusted submission fields."""
    created_at = submission.created_at
    return (
        f"{FEEDBACK_OBJECT_PREFIX}{created_at:%Y/%m}/"
        f"{quote(submission.feedback_id, safe='')}.json"
    )


def _serialize(submission: FeedbackSubmission) -> bytes:
    payload = submission.model_dump_json().encode("utf-8")
    if len(payload) > _MAX_DOCUMENT_BYTES:
        raise ValueError("feedback submission exceeds the 64 KB storage limit")
    return payload


def _container_url_from_checkpoint(checkpoint_url: str) -> str:
    """Return the state container URL that owns a configured checkpoint blob."""
    from src.config import normalize_archive_blob_container_url

    parsed = urlparse(checkpoint_url.strip())
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2:
        raise ValueError("checkpoint_blob_url must identify a blob inside a container")
    return normalize_archive_blob_container_url(f"{parsed.scheme}://{parsed.netloc}/{parts[0]}")


class FeedbackStore:
    """Inert backend used when durable state is not configured."""

    @property
    def configured(self) -> bool:
        return False

    async def put(self, submission: FeedbackSubmission) -> str:
        raise FeedbackNotConfiguredError("durable feedback storage is not configured")


class FileFeedbackStore(FeedbackStore):
    """Local development backend storing one immutable JSON file per submission."""

    def __init__(self, root: str):
        self._root = Path(root)

    @property
    def configured(self) -> bool:
        return True

    async def put(self, submission: FeedbackSubmission) -> str:
        object_name = feedback_object_name(submission)
        path = self._root / object_name.removeprefix(FEEDBACK_OBJECT_PREFIX)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("xb") as handle:
                handle.write(_serialize(submission))
                handle.flush()
                os.fsync(handle.fileno())
        except FileExistsError as exc:
            raise FeedbackConflictError(submission.feedback_id) from exc
        return object_name


class BlobFeedbackStore(FeedbackStore):
    """Azure Blob backend authenticated with the control-plane managed identity."""

    def __init__(self, container_url: str):
        from src.config import normalize_archive_blob_container_url

        self._container_url = normalize_archive_blob_container_url(container_url)
        self._credential = None

    @property
    def configured(self) -> bool:
        return True

    def _token(self) -> str:
        if self._credential is None:
            from src.config import get_azure_credential

            self._credential = get_azure_credential()
        return self._credential.get_token(STORAGE_SCOPE).token

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token()}",
            "x-ms-version": BLOB_API_VERSION,
            "x-ms-client-request-id": str(uuid.uuid4()),
            "x-ms-blob-type": "BlockBlob",
            "Content-Type": "application/json; charset=utf-8",
            "If-None-Match": "*",
        }

    async def put(self, submission: FeedbackSubmission) -> str:
        object_name = feedback_object_name(submission)
        object_url = f"{self._container_url}/{quote(object_name, safe='/')}"
        payload = _serialize(submission)
        for attempt in range(_MAX_WRITE_ATTEMPTS):
            try:
                async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_S) as client:
                    response = await client.put(
                        object_url,
                        headers=self._headers(),
                        content=payload,
                    )
            except httpx.RequestError:
                if attempt + 1 >= _MAX_WRITE_ATTEMPTS:
                    raise
                await asyncio.sleep(calculate_backoff(attempt))
                continue
            if response.status_code == 412:
                raise FeedbackConflictError(submission.feedback_id)
            if (
                response.status_code in _TRANSIENT_STATUS_CODES
                and attempt + 1 < _MAX_WRITE_ATTEMPTS
            ):
                retry_after = response.headers.get("Retry-After")
                await asyncio.sleep(
                    calculate_backoff(
                        attempt,
                        retry_after=float(retry_after) if retry_after else None,
                    )
                )
                continue
            response.raise_for_status()
            return object_name
        raise RuntimeError("feedback write attempts exhausted")


def build_feedback_store() -> FeedbackStore:
    """Place feedback in the configured private state container or local state folder."""
    from src.config import get_settings

    settings = get_settings()
    checkpoint_url = (settings.checkpoint_blob_url or "").strip()
    if checkpoint_url:
        return BlobFeedbackStore(_container_url_from_checkpoint(checkpoint_url))
    checkpoint_path = (settings.checkpoint_file_path or "").strip()
    if checkpoint_path:
        return FileFeedbackStore(str(Path(checkpoint_path).with_name("feedback")))
    return FeedbackStore()


_store: Optional[FeedbackStore] = None


def get_feedback_store() -> FeedbackStore:
    """Return the process-wide feedback store."""
    global _store
    if _store is None:
        _store = build_feedback_store()
    return _store


def reset_feedback_store() -> None:
    """Clear the cached backend so tests can change settings safely."""
    global _store
    _store = None
