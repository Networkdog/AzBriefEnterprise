"""Durable storage for mutable Admin console configuration.

The bootstrap configuration remains in Container Apps environment variables. Admin-managed
subscribers and principals live in one small JSON blob beside the digest checkpoint so the App
and scheduled Job share updates without mutating their own Azure resource definitions.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

import httpx

BLOB_API_VERSION = "2023-11-03"
STORAGE_SCOPE = "https://storage.azure.com/.default"
ADMIN_CONFIG_BLOB_NAME = "admin-config.json"

_REQUEST_TIMEOUT_S = 30
_MAX_DOCUMENT_BYTES = 256_000


class AdminConfigConflictError(Exception):
    """Another writer changed the configuration after it was read."""


class AdminConfigNotConfiguredError(RuntimeError):
    """No durable configuration backend is available."""


@dataclass(frozen=True)
class AdminConfigSnapshot:
    """One stored JSON object and the version used for conditional writes."""

    payload: dict[str, Any]
    etag: Optional[str]


def _serialize(payload: dict[str, Any]) -> bytes:
    content = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(content) > _MAX_DOCUMENT_BYTES:
        raise ValueError("admin configuration exceeds the 256 KB storage limit")
    return content


def _blob_url_from_checkpoint(checkpoint_url: str) -> str:
    parsed = urlparse(checkpoint_url.strip())
    parts = [part for part in parsed.path.split("/") if part]
    hostname = (parsed.hostname or "").lower()
    if (
        parsed.scheme != "https"
        or not hostname.endswith(".blob.core.windows.net")
        or len(parts) < 2
        or parsed.username
        or parsed.password
        or parsed.port
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("checkpoint_blob_url must identify an Azure Blob over HTTPS")
    return f"https://{hostname}/{parts[0]}/{ADMIN_CONFIG_BLOB_NAME}"


def _validate_blob_url(blob_url: str) -> str:
    parsed = urlparse(blob_url.strip())
    parts = [part for part in parsed.path.split("/") if part]
    hostname = (parsed.hostname or "").lower()
    if (
        parsed.scheme != "https"
        or not hostname.endswith(".blob.core.windows.net")
        or len(parts) != 2
        or parsed.username
        or parsed.password
        or parsed.port
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("admin_config_blob_url must identify one Azure Blob over HTTPS")
    return f"https://{hostname}/{parts[0]}/{parts[1]}"


class AdminConfigStore:
    """Inert backend used when durable state is not configured."""

    @property
    def configured(self) -> bool:
        return False

    async def read(self) -> AdminConfigSnapshot:
        return AdminConfigSnapshot(payload={}, etag=None)

    async def write(self, payload: dict[str, Any], etag: Optional[str]) -> str:
        raise AdminConfigNotConfiguredError("durable Admin configuration is not configured")


class FileAdminConfigStore(AdminConfigStore):
    """Local development backend with content-hash conditional writes."""

    def __init__(self, path: str):
        self._path = Path(path)

    @property
    def configured(self) -> bool:
        return True

    async def read(self) -> AdminConfigSnapshot:
        if not self._path.exists():
            return AdminConfigSnapshot(payload={}, etag=None)
        content = self._path.read_bytes()
        try:
            payload = json.loads(content)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError("stored Admin configuration is not valid UTF-8 JSON") from exc
        if not isinstance(payload, dict):
            raise ValueError("stored Admin configuration must be a JSON object")
        return AdminConfigSnapshot(payload=payload, etag=hashlib.sha256(content).hexdigest())

    async def write(self, payload: dict[str, Any], etag: Optional[str]) -> str:
        current = await self.read()
        if current.etag != etag:
            raise AdminConfigConflictError(str(self._path))
        content = _serialize(payload)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_bytes(content)
        return hashlib.sha256(content).hexdigest()


class BlobAdminConfigStore(AdminConfigStore):
    """Azure Blob backend authenticated with the control-plane managed identity."""

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

    async def read(self) -> AdminConfigSnapshot:
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_S) as client:
            response = await client.get(self._url, headers=self._headers())
        if response.status_code == 404:
            return AdminConfigSnapshot(payload={}, etag=None)
        response.raise_for_status()
        if len(response.content) > _MAX_DOCUMENT_BYTES:
            raise ValueError("stored Admin configuration exceeds the 256 KB storage limit")
        try:
            payload = response.json()
        except ValueError as exc:
            raise ValueError("stored Admin configuration is not valid JSON") from exc
        if not isinstance(payload, dict):
            raise ValueError("stored Admin configuration must be a JSON object")
        return AdminConfigSnapshot(payload=payload, etag=response.headers.get("ETag"))

    async def write(self, payload: dict[str, Any], etag: Optional[str]) -> str:
        headers = self._headers()
        headers.update(
            {
                "x-ms-blob-type": "BlockBlob",
                "Content-Type": "application/json; charset=utf-8",
                "If-Match" if etag else "If-None-Match": etag or "*",
            }
        )
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_S) as client:
            response = await client.put(self._url, headers=headers, content=_serialize(payload))
        if response.status_code == 412:
            raise AdminConfigConflictError(self._url)
        response.raise_for_status()
        return response.headers.get("ETag", "")


def build_admin_config_store() -> AdminConfigStore:
    """Place mutable configuration beside the configured digest checkpoint."""
    from src.config import get_settings

    settings = get_settings()
    configured_url = (settings.admin_config_blob_url or "").strip()
    if configured_url:
        return BlobAdminConfigStore(_validate_blob_url(configured_url))
    checkpoint_url = (settings.checkpoint_blob_url or "").strip()
    if checkpoint_url:
        return BlobAdminConfigStore(_blob_url_from_checkpoint(checkpoint_url))
    checkpoint_path = (settings.checkpoint_file_path or "").strip()
    if checkpoint_path:
        return FileAdminConfigStore(str(Path(checkpoint_path).with_name(ADMIN_CONFIG_BLOB_NAME)))
    return AdminConfigStore()


_store: Optional[AdminConfigStore] = None


def get_admin_config_store() -> AdminConfigStore:
    """Return the process-wide mutable configuration store."""
    global _store
    if _store is None:
        _store = build_admin_config_store()
    return _store


def reset_admin_config_store() -> None:
    """Drop the cached backend so tests can change settings safely."""
    global _store
    _store = None
