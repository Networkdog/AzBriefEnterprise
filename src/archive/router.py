"""Authenticated browser and JSON API for canonical analysis archives."""

from __future__ import annotations

import secrets
from datetime import datetime
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import ValidationError
from structlog import get_logger

from src.admin.auth import AdminPrincipal, extract_principal
from src.archive.auth import require_archive_reader
from src.archive.models import ArchiveQuery, ArchiveSource
from src.archive.page import render_archive_page
from src.archive.service import get_archive_service
from src.config import get_settings
from src.middleware import rate_limiter
from src.services.archive import ArchiveCursorError, ArchiveIntegrityError

logger = get_logger()
router = APIRouter(tags=["archive"])


def _sign_in_path(path: str) -> str:
    return f"/.auth/login/aad?post_login_redirect_uri={quote(path, safe='/')}"


def _page_response(request: Request, principal: AdminPrincipal) -> HTMLResponse:
    settings = get_settings()
    nonce = secrets.token_urlsafe(16)
    content = render_archive_page(
        nonce=nonce,
        profile="Microsoft Foundry Hosted Agent",
        user=principal.display,
        language=settings.report_language,
    )
    csp = (
        "default-src 'none'; "
        f"style-src 'nonce-{nonce}'; "
        f"script-src 'nonce-{nonce}'; "
        "connect-src 'self'; "
        "img-src 'self' data:; "
        "form-action 'none'; "
        "frame-ancestors 'none'; "
        "base-uri 'none'"
    )
    return HTMLResponse(
        content=content,
        headers={"Content-Security-Policy": csp, "Cache-Control": "private, no-store"},
    )


async def _authorize_page(request: Request):
    settings = get_settings()
    if not settings.archive_ui_enabled:
        raise HTTPException(status_code=404, detail="Not Found")
    if settings.archive_require_auth and extract_principal(request) is None:
        return RedirectResponse(url=_sign_in_path(request.url.path), status_code=302)
    return await require_archive_reader(request)


@router.get("/archive", response_class=HTMLResponse, include_in_schema=False)
async def archive_page(request: Request):
    """Serve the archive browser shell."""
    authorized = await _authorize_page(request)
    if isinstance(authorized, RedirectResponse):
        return authorized
    return _page_response(request, authorized)


@router.get("/archive/{archive_id}", response_class=HTMLResponse, include_in_schema=False)
async def archive_detail_page(archive_id: str, request: Request):
    """Serve the same shell with one archive ID selected by the client."""
    authorized = await _authorize_page(request)
    if isinstance(authorized, RedirectResponse):
        return authorized
    return _page_response(request, authorized)


@router.get("/api/archive/analyses")
async def list_archive_analyses(
    request: Request,
    response: Response,
    _: AdminPrincipal = Depends(require_archive_reader),
    q: str = Query(default="", max_length=200),
    service: str = Query(default="", max_length=200),
    category: str = Query(default="", max_length=100),
    relevance: str = Query(default="", max_length=50),
    importance: str = Query(default="", max_length=50),
    impact_level: str = Query(default="", max_length=50),
    source: Optional[ArchiveSource] = None,
    update_id: str = Query(default="", max_length=2_000),
    analyzed_after: Optional[datetime] = None,
    analyzed_before: Optional[datetime] = None,
    published_after: Optional[datetime] = None,
    published_before: Optional[datetime] = None,
    limit: int = Query(default=25, ge=1, le=50),
    cursor: str = Query(default="", max_length=512),
) -> dict:
    """Return a bounded metadata-only page of archive summaries."""
    rate_limiter.check(request)
    try:
        query = ArchiveQuery(
            q=q,
            service=service,
            category=category,
            relevance=relevance,
            importance=importance,
            impact_level=impact_level,
            source=source,
            update_id=update_id,
            analyzed_after=analyzed_after,
            analyzed_before=analyzed_before,
            published_after=published_after,
            published_before=published_before,
            limit=limit,
            cursor=cursor,
        )
        page = await get_archive_service().list(query)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail="Archive query range is invalid.") from exc
    except ArchiveCursorError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.error("archive_list_failed", error=str(exc))
        raise HTTPException(status_code=502, detail="Archive storage could not be read.") from exc
    response.headers["Cache-Control"] = "private, no-store"
    return page.model_dump(mode="json")


@router.get("/api/archive/analyses/{archive_id}")
async def get_archive_analysis(
    archive_id: str,
    request: Request,
    response: Response,
    _: AdminPrincipal = Depends(require_archive_reader),
) -> dict:
    """Return one validated canonical analysis document."""
    rate_limiter.check(request)
    try:
        document = await get_archive_service().get(archive_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Archive entry was not found.") from exc
    except ArchiveIntegrityError as exc:
        logger.error("archive_detail_corrupt", archive_id=archive_id, error=str(exc))
        raise HTTPException(
            status_code=502, detail="Archive entry failed integrity checks."
        ) from exc
    except Exception as exc:
        logger.error("archive_detail_failed", archive_id=archive_id, error=str(exc))
        raise HTTPException(status_code=502, detail="Archive storage could not be read.") from exc
    if document is None:
        raise HTTPException(status_code=404, detail="Archive entry was not found.")
    response.headers["Cache-Control"] = "private, no-store"
    return document.model_dump(mode="json")
