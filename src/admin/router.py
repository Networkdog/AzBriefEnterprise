"""Admin console routes.

Every route depends on :func:`src.admin.auth.require_admin`, which returns 404
while the console is disabled so a locked-down deployment does not even
advertise the surface. Responses are read-only summaries plus a single
run-trigger action; no secret value is ever returned.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field
from structlog import get_logger

from src.admin.auth import AdminPrincipal, extract_principal, require_admin
from src.admin.page import render_admin_page
from src.config import get_settings
from src.orchestrator import get_run_store, parse_iso_utc, services_ready, start_run

logger = get_logger()

router = APIRouter(tags=["admin"])

MAX_RECENT_UPDATES = 25

# Container Apps built-in authentication exposes the provider sign-in here.
SIGN_IN_PATH = "/.auth/login/aad?post_login_redirect_uri=/admin"


class StartRunRequest(BaseModel):
    """Body for a manually triggered orchestrated run."""

    since: Optional[str] = Field(
        None, description="ISO-8601 UTC instant; only later updates are analysed."
    )
    dry_run: bool = Field(False, description="Collect targets without analysing or emailing.")


def _parse_since(value: Optional[str]) -> Optional[datetime]:
    """Parse an ISO-8601 instant into aware UTC, or raise 400."""
    try:
        return parse_iso_utc(value)
    except ValueError:
        raise HTTPException(status_code=400, detail="since must be an ISO-8601 timestamp")


def _backend_label(settings) -> str:
    """Header badge naming the backend actually in force, not the one requested."""
    if not settings.use_foundry:
        return "Azure OpenAI"
    stages = len(settings.get_foundry_agents())
    return f"Foundry 멀티 에이전트 ({stages}단계)" if stages else "Foundry"


@router.get("/admin", response_class=HTMLResponse, include_in_schema=False)
async def admin_page(request: Request):
    """Serve the admin console shell.

    An unauthenticated browser is sent to the identity provider rather than
    shown a bare 401: platform auth runs in AllowAnonymous mode so that the
    API-key machine path stays reachable, which makes the redirect this
    application's job.
    """
    settings = get_settings()
    if not settings.admin_ui_enabled:
        raise HTTPException(status_code=404, detail="Not Found")
    if settings.admin_require_auth and extract_principal(request) is None:
        return RedirectResponse(url=SIGN_IN_PATH, status_code=302)

    principal = await require_admin(request)
    nonce = secrets.token_urlsafe(16)
    html = render_admin_page(
        nonce=nonce,
        profile=_backend_label(settings),
        user=principal.display,
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
        content=html,
        headers={"Content-Security-Policy": csp, "Cache-Control": "no-store"},
    )


@router.get("/api/admin/status")
async def admin_status(_: AdminPrincipal = Depends(require_admin)) -> dict:
    """Report the effective configuration. Values are flags and names, never secrets."""
    settings = get_settings()
    agents = settings.get_foundry_agents()
    if settings.communication_services_connection_string:
        email_transport = "Communication Services (연결 문자열)"
    elif settings.communication_services_endpoint:
        email_transport = "Communication Services (관리 ID)"
    else:
        email_transport = "콘솔 출력"

    return {
        "LLM 백엔드": settings.llm_backend,
        "Foundry 프로젝트": "설정됨" if settings.foundry_project_endpoint else "없음",
        "Foundry 에이전트": [f"{a.stage}: {a.name}" for a in agents],
        "모델 배포": settings.foundry_model_deployment or settings.azure_openai_deployment_name,
        "이메일 전송": email_transport,
        "구독자 수": len(settings.get_subscribers()),
        "리포트 언어": settings.report_language,
        "리포트 필터링": "켜짐" if settings.report_filtering_enabled else "꺼짐",
        "동시 분석 수": settings.max_concurrent_analyses,
        "실행 시간 예산(초)": settings.run_time_budget_s,
        "오케스트레이터 준비": "예" if services_ready() else "아니오",
    }


@router.get("/api/admin/subscribers")
async def admin_subscribers(_: AdminPrincipal = Depends(require_admin)) -> dict:
    """List the configured subscribers."""
    subscribers = get_settings().get_subscribers()
    return {
        "subscribers": [
            {
                "email": s.email,
                "name": s.name,
                "role": s.role,
                "language": s.language,
                "alert_level": s.alert_level,
            }
            for s in subscribers
        ]
    }


@router.get("/api/admin/updates")
async def admin_updates(
    _: AdminPrincipal = Depends(require_admin),
    limit: int = Query(default=10, ge=1, le=MAX_RECENT_UPDATES),
) -> dict:
    """List the most recent Azure Updates from the RSS feed."""
    from src.rss.parser import AzureUpdateParser

    try:
        updates = await AzureUpdateParser().get_updates()
    except Exception as exc:
        logger.warning("admin_updates_failed", error=str(exc))
        raise HTTPException(status_code=502, detail="Azure Update 피드를 읽지 못했습니다.")

    ordered = sorted(
        updates,
        key=lambda u: u.published_date or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )[:limit]
    return {
        "updates": [
            {
                "id": u.id,
                "title": u.title,
                "published_date": u.published_date.isoformat() if u.published_date else None,
                "update_type": u.update_type or "",
                "link": u.link,
            }
            for u in ordered
        ]
    }


@router.get("/api/admin/runs")
async def admin_runs(
    _: AdminPrincipal = Depends(require_admin),
    limit: int = Query(default=10, ge=1, le=50),
) -> dict:
    """List recent orchestrated runs."""
    return {"runs": [record.to_dict() for record in get_run_store().recent(limit)]}


@router.get("/api/admin/runs/{run_id}")
async def admin_run_detail(
    run_id: str,
    _: AdminPrincipal = Depends(require_admin),
) -> dict:
    """Return one run record."""
    record = get_run_store().get(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="실행 기록을 찾을 수 없습니다.")
    return record.to_dict()


@router.post("/api/admin/runs", status_code=202)
async def admin_start_run(
    request: StartRunRequest,
    principal: AdminPrincipal = Depends(require_admin),
) -> dict:
    """Start an orchestrated analysis run."""
    store = get_run_store()
    if store.active_count:
        raise HTTPException(status_code=409, detail="이미 실행 중인 작업이 있습니다.")

    try:
        record = start_run(since=_parse_since(request.since), dry_run=request.dry_run)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    logger.info("admin_run_triggered", run_id=record.run_id, principal=principal.display)
    return record.to_dict()
