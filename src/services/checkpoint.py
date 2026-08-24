"""Durable checkpoint for orchestrated digest runs.

The "analysed up to" watermark lives in a blob written with the workload's
managed identity, so a Container Apps Job and the Container App share one
source of truth without any local state.

Two invariants make a lost run cost duplicate work rather than a skipped update:

* only the **contiguous prefix** watermark ever reaches this store, and
* the stored value may only move **forward**, so a late writer with an older
  watermark cannot rewind the window.

Blob access goes through the REST API with an Entra token rather than
``azure-storage-blob``: the read/write pair happens twice per run, which does
not justify pulling in the storage SDK and its transitive dependencies.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import httpx
from structlog import get_logger

from src.config import get_settings

logger = get_logger()

BLOB_API_VERSION = "2021-08-06"
STORAGE_SCOPE = "https://storage.azure.com/.default"
CHECKPOINT_KEY = "last_successful_run_at"

_REQUEST_TIMEOUT_S = 30
_MAX_WRITE_ATTEMPTS = 3


class PreconditionFailed(Exception):
    """Another writer changed the blob between our read and our write."""


def _ensure_utc(value: datetime) -> datetime:
    """Normalize a datetime to timezone-aware UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_checkpoint(payload: str) -> Optional[datetime]:
    """Read the watermark out of a stored document, tolerating junk.

    A corrupt document must not stall the pipeline: returning None makes the
    next run fall back to its default window and rewrite a valid value.
    """
    try:
        raw = json.loads(payload)[CHECKPOINT_KEY]
    except (json.JSONDecodeError, KeyError, TypeError):
        return None
    if not isinstance(raw, str):
        return None
    try:
        return _ensure_utc(datetime.fromisoformat(raw.strip().replace("Z", "+00:00")))
    except ValueError:
        return None


def _serialize(watermark: datetime) -> str:
    return json.dumps(
        {
            CHECKPOINT_KEY: watermark.isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    )


class CheckpointStore:
    """Inert store used when no durable backend is configured.

    Reporting "no checkpoint" and refusing to advance is the safe direction:
    the caller falls back to its default window instead of trusting a value
    that was never persisted.
    """

    @property
    def configured(self) -> bool:
        return False

    async def get(self) -> Optional[datetime]:
        return None

    async def advance(self, watermark: datetime) -> bool:
        return False


class FileCheckpointStore(CheckpointStore):
    """Local-file backend for development and tests."""

    def __init__(self, path: str):
        self._path = Path(path)

    @property
    def configured(self) -> bool:
        return True

    async def get(self) -> Optional[datetime]:
        if not self._path.exists():
            return None
        return _parse_checkpoint(self._path.read_text(encoding="utf-8"))

    async def advance(self, watermark: datetime) -> bool:
        target = _ensure_utc(watermark)
        current = await self.get()
        if current is not None and target <= current:
            return False
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(_serialize(target), encoding="utf-8")
        return True


class BlobCheckpointStore(CheckpointStore):
    """Azure Blob backend authenticated with the workload's managed identity."""

    def __init__(self, blob_url: str):
        self._url = blob_url
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
        }

    async def _read(self) -> tuple[Optional[datetime], Optional[str]]:
        """Return the stored watermark and the blob's ETag."""
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_S) as client:
            response = await client.get(self._url, headers=self._headers())
        if response.status_code == 404:
            return None, None
        response.raise_for_status()
        return _parse_checkpoint(response.text), response.headers.get("ETag")

    async def _write(self, watermark: datetime, etag: Optional[str]) -> None:
        headers = self._headers()
        headers["x-ms-blob-type"] = "BlockBlob"
        headers["Content-Type"] = "application/json"
        # Without the guard two concurrent runs could interleave read and write.
        headers["If-Match" if etag else "If-None-Match"] = etag or "*"

        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_S) as client:
            response = await client.put(
                self._url, headers=headers, content=_serialize(watermark).encode("utf-8")
            )
        if response.status_code == 412:
            raise PreconditionFailed(self._url)
        response.raise_for_status()

    async def get(self) -> Optional[datetime]:
        watermark, _ = await self._read()
        return watermark

    async def advance(self, watermark: datetime) -> bool:
        target = _ensure_utc(watermark)
        for _ in range(_MAX_WRITE_ATTEMPTS):
            current, etag = await self._read()
            if current is not None and target <= current:
                return False
            try:
                await self._write(target, etag)
                return True
            except PreconditionFailed:
                continue
        logger.warning("checkpoint_write_contended", url=self._url)
        return False


_store: Optional[CheckpointStore] = None


def build_checkpoint_store() -> CheckpointStore:
    """Pick a backend from settings. Blob wins over file; neither means inert."""
    settings = get_settings()
    url = (settings.checkpoint_blob_url or "").strip()
    if url:
        if urlparse(url).scheme != "https":
            raise ValueError("checkpoint_blob_url must be an https URL")
        return BlobCheckpointStore(url)
    path = (settings.checkpoint_file_path or "").strip()
    if path:
        return FileCheckpointStore(path)
    return CheckpointStore()


def get_checkpoint_store() -> CheckpointStore:
    """Return the process-wide checkpoint store."""
    global _store
    if _store is None:
        _store = build_checkpoint_store()
    return _store


def reset_checkpoint_store() -> None:
    """Drop the cached store so a settings change takes effect (tests)."""
    global _store
    _store = None
