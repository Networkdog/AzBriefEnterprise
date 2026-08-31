"""Data-access backends for immutable analysis archive documents."""

from __future__ import annotations

import asyncio
import base64
import bisect
import hashlib
import json
import os
import re
import threading
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import quote, unquote

import httpx
from structlog import get_logger

from src.agent.resilience import calculate_backoff
from src.archive.models import (
    ARCHIVE_ID_PATTERN,
    ArchiveDocumentV1,
    ArchivePage,
    ArchiveQuery,
    ArchiveReceipt,
    ArchiveSummary,
)

logger = get_logger()

ARCHIVE_OBJECT_PREFIX = "entries/"
ARCHIVE_OBJECT_SUFFIX = ".json"
MAX_SCAN_RESULTS = 500
BLOB_API_VERSION = "2023-11-03"
STORAGE_SCOPE = "https://storage.azure.com/.default"

_REQUEST_TIMEOUT_S = 30
_MAX_WRITE_ATTEMPTS = 3
_LIST_BATCH_SIZE = 250
_TRANSIENT_STATUS_CODES = frozenset({408, 429, 500, 502, 503, 504})

_ARCHIVE_ID_RE = re.compile(ARCHIVE_ID_PATTERN)


class ArchiveConflictError(RuntimeError):
    """An immutable archive ID already contains different content."""


class ArchiveCursorError(ValueError):
    """A listing cursor is malformed or outside the archive namespace."""


class ArchiveIntegrityError(RuntimeError):
    """Stored archive bytes or metadata do not satisfy the archive contract."""


@dataclass(frozen=True)
class _BlobListItem:
    name: str
    metadata: dict[str, str]


def archive_object_name(archive_id: str) -> str:
    """Return the object name for a validated archive ID."""
    if not _ARCHIVE_ID_RE.fullmatch(archive_id):
        raise ValueError("invalid archive_id")
    return f"{ARCHIVE_OBJECT_PREFIX}{archive_id}{ARCHIVE_OBJECT_SUFFIX}"


def _encode_cursor(object_name: str) -> str:
    encoded = base64.urlsafe_b64encode(object_name.encode("ascii")).decode("ascii")
    return encoded.rstrip("=")


def _decode_cursor(cursor: str) -> str:
    if not cursor:
        return ""
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        object_name = base64.urlsafe_b64decode(padded).decode("ascii")
    except (ValueError, UnicodeDecodeError) as exc:
        raise ArchiveCursorError("invalid archive cursor") from exc
    archive_id = object_name.removeprefix(ARCHIVE_OBJECT_PREFIX).removesuffix(ARCHIVE_OBJECT_SUFFIX)
    if object_name != archive_object_name(archive_id):
        raise ArchiveCursorError("archive cursor is outside the entries namespace")
    return object_name


def _contains(value: str, expected: str) -> bool:
    return expected.casefold() in value.casefold()


def _encode_metadata_text(value: str, max_bytes: int) -> str:
    raw = value.encode("utf-8")
    if len(raw) > max_bytes:
        raw = raw[:max_bytes].decode("utf-8", errors="ignore").encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _encode_metadata_list(values: list[str], max_bytes: int) -> tuple[str, bool]:
    """Encode a complete JSON prefix and report whether values were omitted."""
    selected: list[str] = []
    for value in values:
        candidate = json.dumps(
            [*selected, value],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        if len(candidate.encode("utf-8")) > max_bytes:
            return (
                _encode_metadata_text(
                    json.dumps(selected, ensure_ascii=False, separators=(",", ":")),
                    max_bytes,
                ),
                True,
            )
        selected.append(value)
    serialized = json.dumps(selected, ensure_ascii=False, separators=(",", ":"))
    return _encode_metadata_text(serialized, max_bytes), False


def _decode_metadata_text(value: str) -> str:
    try:
        padded = value + "=" * (-len(value) % 4)
        return base64.b64decode(padded, altchars=b"-_", validate=True).decode("utf-8")
    except (ValueError, UnicodeDecodeError) as exc:
        raise ArchiveIntegrityError("archive metadata is not valid UTF-8 base64url") from exc


def _metadata_from_document(document: ArchiveDocumentV1, payload: bytes) -> dict[str, str]:
    summary = ArchiveSummary.from_document(document)
    services_b64, services_truncated = _encode_metadata_list(
        summary.azure_services,
        768,
    )
    projection_truncated = any(
        (
            len(summary.run_id.encode("utf-8")) > 128,
            len(summary.update_id.encode("utf-8")) > 512,
            len(summary.title.encode("utf-8")) > 768,
            services_truncated,
            len(summary.one_line_summary.encode("utf-8")) > 1_024,
        )
    )
    return {
        "schema_version": document.schema_version,
        "archive_id": document.archive_id,
        "analyzed_at": summary.analyzed_at.isoformat(),
        "source": summary.source.value,
        "run_id_b64": _encode_metadata_text(summary.run_id, 128),
        "update_id_b64": _encode_metadata_text(summary.update_id, 512),
        "title_b64": _encode_metadata_text(summary.title, 768),
        "published_at": summary.published_date.isoformat() if summary.published_date else "",
        "services_b64": services_b64,
        "projection_truncated": "true" if projection_truncated else "false",
        "update_category": summary.update_category,
        "urgency": summary.urgency,
        "importance": summary.importance,
        "impact_level": summary.impact_level,
        "relevance": summary.relevance,
        "summary_b64": _encode_metadata_text(summary.one_line_summary, 1_024),
        "affected_count": str(summary.affected_resource_count),
        "action_count": str(summary.action_item_count),
        "payload_sha256": hashlib.sha256(payload).hexdigest(),
    }


def _parse_optional_datetime(value: str) -> Optional[datetime]:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _summary_from_metadata(metadata: dict[str, str]) -> ArchiveSummary:
    if metadata.get("schema_version") != "1":
        raise ArchiveIntegrityError("unsupported archive metadata schema")
    try:
        services_raw = _decode_metadata_text(metadata["services_b64"])
        services = json.loads(services_raw)
        if not isinstance(services, list) or not all(isinstance(item, str) for item in services):
            raise ValueError("services must be a string list")
        return ArchiveSummary(
            archive_id=metadata["archive_id"],
            analyzed_at=metadata["analyzed_at"],
            source=metadata["source"],
            run_id=_decode_metadata_text(metadata.get("run_id_b64", "")),
            update_id=_decode_metadata_text(metadata["update_id_b64"]),
            title=_decode_metadata_text(metadata["title_b64"]),
            published_date=_parse_optional_datetime(metadata.get("published_at", "")),
            azure_services=services,
            update_category=metadata.get("update_category", ""),
            urgency=metadata.get("urgency", ""),
            importance=metadata.get("importance", ""),
            impact_level=metadata.get("impact_level", ""),
            relevance=metadata.get("relevance", ""),
            one_line_summary=_decode_metadata_text(metadata.get("summary_b64", "")),
            affected_resource_count=int(metadata.get("affected_count", "0")),
            action_item_count=int(metadata.get("action_count", "0")),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        if isinstance(exc, ArchiveIntegrityError):
            raise
        raise ArchiveIntegrityError("archive metadata is incomplete or malformed") from exc


def _matches_query(summary: ArchiveSummary, query: ArchiveQuery) -> bool:
    if query.q:
        searchable = " ".join(
            [
                summary.title,
                summary.one_line_summary,
                summary.update_id,
                *summary.azure_services,
            ]
        )
        if not _contains(searchable, query.q):
            return False
    if query.service and not any(
        _contains(service, query.service) for service in summary.azure_services
    ):
        return False
    exact_filters = (
        (summary.update_category, query.category),
        (summary.relevance, query.relevance),
        (summary.importance, query.importance),
        (summary.impact_level, query.impact_level),
        (summary.update_id, query.update_id),
    )
    if any(
        expected and actual.casefold() != expected.casefold() for actual, expected in exact_filters
    ):
        return False
    if query.source and summary.source != query.source:
        return False
    if query.analyzed_after and summary.analyzed_at < query.analyzed_after:
        return False
    if query.analyzed_before and summary.analyzed_at > query.analyzed_before:
        return False
    if query.published_after and (
        summary.published_date is None or summary.published_date < query.published_after
    ):
        return False
    if query.published_before and (
        summary.published_date is None or summary.published_date > query.published_before
    ):
        return False
    return True


class ArchiveStore:
    """Inert backend used when archive persistence is not configured."""

    @property
    def configured(self) -> bool:
        return False

    async def put(self, document: ArchiveDocumentV1) -> ArchiveReceipt:
        return ArchiveReceipt(archived=False)

    async def get(self, archive_id: str) -> Optional[ArchiveDocumentV1]:
        return None

    async def list(self, query: ArchiveQuery) -> ArchivePage:
        return ArchivePage()


class FileArchiveStore(ArchiveStore):
    """Local development backend storing one immutable JSON file per version."""

    def __init__(self, root: str):
        self._root = Path(root)
        self._names_lock = threading.RLock()
        self._known_names: Optional[set[str]] = None
        self._sorted_names: Optional[tuple[str, ...]] = None

    @property
    def configured(self) -> bool:
        return True

    def _path_for(self, archive_id: str) -> Path:
        return self._root / archive_object_name(archive_id)

    def _ensure_names_locked(self) -> set[str]:
        if self._known_names is None:
            self._known_names = {
                path.relative_to(self._root).as_posix()
                for path in (self._root / ARCHIVE_OBJECT_PREFIX).glob("*.json")
            }
        return self._known_names

    def _record_name_locked(self, object_name: str) -> None:
        names = self._ensure_names_locked()
        if object_name not in names:
            names.add(object_name)
            self._sorted_names = None

    def _names_snapshot(self) -> tuple[str, ...]:
        with self._names_lock:
            names = self._ensure_names_locked()
            if self._sorted_names is None:
                self._sorted_names = tuple(sorted(names))
            return self._sorted_names

    async def put(self, document: ArchiveDocumentV1) -> ArchiveReceipt:
        object_name = archive_object_name(document.archive_id)
        path = self._root / object_name
        payload = document.model_dump_json().encode("utf-8")
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._names_lock:
            try:
                with path.open("xb") as handle:
                    handle.write(payload)
                    handle.flush()
                    os.fsync(handle.fileno())
            except FileExistsError:
                if path.read_bytes() != payload:
                    raise ArchiveConflictError(document.archive_id)
            self._record_name_locked(object_name)
        return ArchiveReceipt(
            archived=True,
            archive_id=document.archive_id,
            object_name=object_name,
        )

    async def get(self, archive_id: str) -> Optional[ArchiveDocumentV1]:
        path = self._path_for(archive_id)
        if not path.exists():
            return None
        return ArchiveDocumentV1.model_validate_json(path.read_text(encoding="utf-8"))

    async def list(self, query: ArchiveQuery) -> ArchivePage:
        after = _decode_cursor(query.cursor)
        names = self._names_snapshot()
        start = bisect.bisect_right(names, after) if after else 0

        matches: list[tuple[str, ArchiveSummary]] = []
        scanned = 0
        last_scanned = ""
        exhausted = True
        for name in names[start:]:
            if scanned >= MAX_SCAN_RESULTS:
                exhausted = False
                break
            scanned += 1
            last_scanned = name
            try:
                document = ArchiveDocumentV1.model_validate_json(
                    (self._root / name).read_text(encoding="utf-8")
                )
            except (OSError, ValueError) as exc:
                logger.warning("archive_file_skipped", object_name=name, error=str(exc)[:200])
                continue
            summary = ArchiveSummary.from_document(document)
            if not _matches_query(summary, query):
                continue
            matches.append((name, summary))
            if len(matches) > query.limit:
                exhausted = False
                break

        page_items = matches[: query.limit]
        has_more = not exhausted
        if has_more and page_items:
            cursor_name = page_items[-1][0]
        elif has_more:
            cursor_name = last_scanned
        else:
            cursor_name = ""
        return ArchivePage(
            items=[summary for _, summary in page_items],
            next_cursor=_encode_cursor(cursor_name) if cursor_name else "",
            scanned=scanned,
            has_more=has_more,
        )


class BlobArchiveStore(ArchiveStore):
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
        }

    def _object_url(self, object_name: str) -> str:
        return f"{self._container_url}/{quote(object_name, safe='/')}"

    async def _put_once(
        self,
        object_name: str,
        payload: bytes,
        metadata: dict[str, str],
    ) -> httpx.Response:
        headers = self._headers()
        headers.update(
            {
                "x-ms-blob-type": "BlockBlob",
                "Content-Type": "application/json; charset=utf-8",
                "If-None-Match": "*",
                **{f"x-ms-meta-{key}": value for key, value in metadata.items()},
            }
        )
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_S) as client:
            return await client.put(self._object_url(object_name), headers=headers, content=payload)

    async def _read_raw(self, object_name: str) -> Optional[tuple[bytes, dict[str, str]]]:
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_S) as client:
            response = await client.get(self._object_url(object_name), headers=self._headers())
        if response.status_code == 404:
            return None
        response.raise_for_status()
        metadata = {
            key.removeprefix("x-ms-meta-"): value
            for key, value in response.headers.items()
            if key.startswith("x-ms-meta-")
        }
        return response.content, metadata

    async def put(self, document: ArchiveDocumentV1) -> ArchiveReceipt:
        object_name = archive_object_name(document.archive_id)
        payload = document.model_dump_json().encode("utf-8")
        metadata = _metadata_from_document(document, payload)
        for attempt in range(_MAX_WRITE_ATTEMPTS):
            try:
                response = await self._put_once(object_name, payload, metadata)
            except httpx.RequestError:
                if attempt + 1 >= _MAX_WRITE_ATTEMPTS:
                    raise
                await asyncio.sleep(calculate_backoff(attempt))
                continue
            if response.status_code == 412:
                existing = await self._read_raw(object_name)
                if existing is not None and existing[0] == payload:
                    return ArchiveReceipt(
                        archived=True,
                        archive_id=document.archive_id,
                        object_name=object_name,
                    )
                raise ArchiveConflictError(document.archive_id)
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
            return ArchiveReceipt(
                archived=True,
                archive_id=document.archive_id,
                object_name=object_name,
            )
        raise RuntimeError("archive write attempts exhausted")

    async def get(self, archive_id: str) -> Optional[ArchiveDocumentV1]:
        object_name = archive_object_name(archive_id)
        stored = await self._read_raw(object_name)
        if stored is None:
            return None
        payload, metadata = stored
        expected_hash = metadata.get("payload_sha256", "")
        actual_hash = hashlib.sha256(payload).hexdigest()
        if not expected_hash or expected_hash != actual_hash:
            raise ArchiveIntegrityError("archive payload hash does not match metadata")
        try:
            document = ArchiveDocumentV1.model_validate_json(payload)
        except ValueError as exc:
            raise ArchiveIntegrityError("archive payload does not match schema v1") from exc
        if document.archive_id != archive_id:
            raise ArchiveIntegrityError("archive payload ID does not match object name")
        return document

    async def _list_once(
        self,
        marker: str,
        start_from: str,
    ) -> tuple[list[_BlobListItem], str]:
        params = {
            "restype": "container",
            "comp": "list",
            "prefix": ARCHIVE_OBJECT_PREFIX,
            "include": "metadata",
            "maxresults": str(_LIST_BATCH_SIZE),
        }
        if marker:
            params["marker"] = marker
        elif start_from:
            params["startFrom"] = start_from
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_S) as client:
            response = await client.get(
                self._container_url,
                headers=self._headers(),
                params=params,
            )
        response.raise_for_status()
        try:
            root = ET.fromstring(response.text)
        except ET.ParseError as exc:
            raise ArchiveIntegrityError("Blob listing returned malformed XML") from exc
        items: list[_BlobListItem] = []
        for blob in root.findall("./Blobs/Blob"):
            name_node = blob.find("Name")
            if name_node is None or not name_node.text:
                continue
            name = unquote(name_node.text) if name_node.get("Encoded") == "true" else name_node.text
            metadata_node = blob.find("Metadata")
            metadata = (
                {child.tag: child.text or "" for child in metadata_node}
                if metadata_node is not None
                else {}
            )
            items.append(_BlobListItem(name=name, metadata=metadata))
        return items, root.findtext("NextMarker", default="")

    async def list(self, query: ArchiveQuery) -> ArchivePage:
        after = _decode_cursor(query.cursor)
        marker = ""
        start_from = after
        matches: list[tuple[str, ArchiveSummary]] = []
        scanned = 0
        last_scanned = ""
        has_more = False

        while scanned < MAX_SCAN_RESULTS:
            items, next_marker = await self._list_once(marker, start_from)
            start_from = ""
            for item in items:
                if item.name == after:
                    continue
                if not item.name.startswith(ARCHIVE_OBJECT_PREFIX):
                    continue
                scanned += 1
                last_scanned = item.name
                try:
                    summary = _summary_from_metadata(item.metadata)
                    if item.metadata.get("projection_truncated") == "true":
                        document = await self.get(summary.archive_id)
                        if document is None:
                            raise ArchiveIntegrityError(
                                "truncated archive projection has no source document"
                            )
                        summary = ArchiveSummary.from_document(document)
                except (ArchiveIntegrityError, ValueError) as exc:
                    logger.warning(
                        "archive_blob_skipped",
                        object_name=item.name,
                        error=str(exc)[:200],
                    )
                    continue
                if _matches_query(summary, query):
                    matches.append((item.name, summary))
                    if len(matches) > query.limit:
                        has_more = True
                        break
                if scanned >= MAX_SCAN_RESULTS:
                    has_more = bool(next_marker) or item != items[-1]
                    break
            if has_more or len(matches) > query.limit or scanned >= MAX_SCAN_RESULTS:
                break
            if not next_marker:
                break
            marker = next_marker

        page_items = matches[: query.limit]
        if has_more and page_items:
            cursor_name = page_items[-1][0]
        elif has_more:
            cursor_name = last_scanned
        else:
            cursor_name = ""
        return ArchivePage(
            items=[summary for _, summary in page_items],
            next_cursor=_encode_cursor(cursor_name) if cursor_name else "",
            scanned=scanned,
            has_more=has_more,
        )


_store: Optional[ArchiveStore] = None


def build_archive_store() -> ArchiveStore:
    """Select the archive backend; Blob wins over local file."""
    from src.config import get_settings

    settings = get_settings()
    url = (getattr(settings, "archive_blob_container_url", None) or "").strip()
    if url:
        return BlobArchiveStore(url)
    path = (getattr(settings, "archive_file_path", None) or "").strip()
    if path:
        return FileArchiveStore(path)
    return ArchiveStore()


def get_archive_store() -> ArchiveStore:
    """Return the process-wide archive store."""
    global _store
    if _store is None:
        _store = build_archive_store()
    return _store


def reset_archive_store() -> None:
    """Clear the process-wide store so changed settings take effect in tests."""
    global _store
    _store = None
