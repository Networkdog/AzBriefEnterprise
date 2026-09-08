"""Admin console routes.

Every route depends on :func:`src.admin.auth.require_admin`, which returns 404
while the console is disabled so a locked-down deployment does not even
advertise the surface. Responses expose operational summaries, bounded run
control, and durable subscriber/admin management. No secret value is ever returned.
"""

from __future__ import annotations

import secrets
from datetime import date, datetime, time, timezone
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from structlog import get_logger

from src.admin.auth import AdminPrincipal, extract_principal, require_admin
from src.admin.configuration import (
    AdminConfigurationEntryExistsError,
    AdminConfigurationEntryNotFoundError,
    AdminConfigurationProtectedEntryError,
    get_admin_configuration,
)
from src.admin.page import render_admin_page
from src.admin.readiness import collect_admin_readiness
from src.agent.scope import AnalysisScope
from src.archive.models import ArchiveSource
from src.config import Subscriber, get_settings
from src.orchestrator import (
    MAX_MANUAL_TARGETS,
    RunSelection,
    get_run_store,
    parse_iso_utc,
    start_run,
)
from src.services.admin_config import AdminConfigConflictError, AdminConfigNotConfiguredError
from src.web_fonts import WEB_FONT_CSP_SOURCE

logger = get_logger()

router = APIRouter(tags=["admin"])

MAX_RECENT_UPDATES = 25

# Container Apps built-in authentication exposes the provider sign-in here.
SIGN_IN_PATH = "/.auth/login/aad?post_login_redirect_uri=/admin"


class StartRunRequest(BaseModel):
    """Body for a manually triggered orchestrated run."""

    model_config = ConfigDict(extra="forbid")

    mode: Literal["checkpoint", "date_range", "recent", "update_id", "update_url"] = "checkpoint"
    since: Optional[str] = Field(
        None, description="ISO-8601 UTC instant; only later updates are analysed."
    )
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    recent_count: Optional[int] = Field(default=None, ge=1, le=MAX_MANUAL_TARGETS)
    update_id: str = Field(default="", max_length=32)
    update_url: str = Field(default="", max_length=2048)
    send_email: bool = Field(
        False,
        description="Send the completed manual run as a digest after analysis.",
    )
    dry_run: bool = Field(False, description="Collect targets without analysing or emailing.")

    @field_validator("update_id", "update_url")
    @classmethod
    def strip_selector_text(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def validate_selector(self) -> "StartRunRequest":
        if self.dry_run and self.send_email:
            raise ValueError("dry_run cannot send email")
        fields = {
            "date_range": bool(self.start_date or self.end_date),
            "recent": self.recent_count is not None,
            "update_id": bool(self.update_id),
            "update_url": bool(self.update_url),
        }
        if self.mode == "checkpoint":
            if any(fields.values()):
                raise ValueError("checkpoint mode accepts only since")
            _parse_since(self.since)
            return self

        if self.since:
            raise ValueError("since is available only in checkpoint mode")
        if not fields[self.mode]:
            raise ValueError(f"{self.mode} selector value is required")
        if any(value for key, value in fields.items() if key != self.mode):
            raise ValueError("provide values for exactly one selector mode")
        self.to_selection()
        return self

    def to_selection(self) -> RunSelection:
        """Translate validated request fields to the orchestrator contract."""
        if self.mode == "date_range":
            if self.start_date is None or self.end_date is None:
                raise ValueError("date_range requires start_date and end_date")
            return RunSelection(
                mode=self.mode,
                start_date=datetime.combine(self.start_date, time.min, tzinfo=timezone.utc),
                end_date=datetime.combine(self.end_date, time.min, tzinfo=timezone.utc),
            )
        if self.mode == "recent":
            return RunSelection(mode=self.mode, recent_count=self.recent_count)
        if self.mode == "update_id":
            return RunSelection(mode=self.mode, update_id=self.update_id)
        if self.mode == "update_url":
            return RunSelection(mode=self.mode, update_url=self.update_url)
        return RunSelection()


class SubscriberRequest(BaseModel):
    """Validated subscriber profile accepted from the Admin console."""

    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=320)
    name: str = Field(min_length=1, max_length=200)
    role: str = Field(default="", max_length=500)
    language: str = Field(default="ko", min_length=2, max_length=35)
    management_groups: list[str] = Field(default_factory=list, max_length=100)
    subscriptions: list[str] = Field(default_factory=list, max_length=100)
    resource_groups: list[str] = Field(default_factory=list, max_length=100)
    focus_services: list[str] = Field(default_factory=list, max_length=100)
    alert_level: Literal["critical_only", "important_and_above", "all"] = "all"

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        normalized = value.strip().lower()
        if (
            normalized.count("@") != 1
            or any(character.isspace() for character in normalized)
            or normalized.startswith("@")
            or normalized.endswith("@")
            or "." not in normalized.rsplit("@", 1)[1]
        ):
            raise ValueError("Enter a valid email address.")
        return normalized

    @field_validator("name", "language")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Enter a required value.")
        return normalized

    @field_validator("role")
    @classmethod
    def strip_optional_text(cls, value: str) -> str:
        return value.strip()

    @field_validator(
        "management_groups",
        "subscriptions",
        "resource_groups",
        mode="before",
    )
    @classmethod
    def normalize_hierarchy_scopes(cls, value, info) -> list[str]:
        scope = AnalysisScope(**{info.field_name: value})
        return list(getattr(scope, info.field_name))

    @field_validator("focus_services", mode="before")
    @classmethod
    def normalize_focus_services(cls, value) -> list[str]:
        if isinstance(value, (str, bytes)):
            raise ValueError("focus_services must be an array")
        seen: set[str] = set()
        normalized: list[str] = []
        for item in value or []:
            text = str(item).strip()
            if text and text.lower() not in seen:
                seen.add(text.lower())
                normalized.append(text)
        return normalized

    @model_validator(mode="after")
    def validate_hierarchy_scope(self) -> "SubscriberRequest":
        scope = AnalysisScope(
            management_groups=self.management_groups,
            subscriptions=self.subscriptions,
            resource_groups=self.resource_groups,
        )
        self.management_groups = list(scope.management_groups)
        self.subscriptions = list(scope.subscriptions)
        self.resource_groups = list(scope.resource_groups)
        return self

    def to_subscriber(self) -> Subscriber:
        return Subscriber.model_validate(self.model_dump())


class AdministratorRequest(BaseModel):
    """One EasyAuth claim identifier granted administrator access."""

    model_config = ConfigDict(extra="forbid")

    principal: str = Field(min_length=3, max_length=320)

    @field_validator("principal")
    @classmethod
    def validate_principal(cls, value: str) -> str:
        normalized = value.strip().lower()
        if (
            not normalized
            or any(character in normalized for character in ",/\\")
            or any(ord(character) < 32 for character in normalized)
        ):
            raise ValueError("Provide one administrator object ID, UPN, or group ID.")
        return normalized


class AutomaticRunTimeRequest(BaseModel):
    """One Admin-managed daily execution time in UTC."""

    model_config = ConfigDict(extra="forbid")

    time_utc: str = Field(min_length=5, max_length=5)

    @field_validator("time_utc")
    @classmethod
    def validate_time_utc(cls, value: str) -> str:
        normalized = value.strip()
        try:
            parsed = datetime.strptime(normalized, "%H:%M")
        except ValueError as exc:
            raise ValueError("UTC run time must use HH:MM format.") from exc
        return parsed.strftime("%H:%M")


def _subscriber_payload(subscriber: Subscriber, managed: bool) -> dict:
    return {**subscriber.model_dump(mode="json"), "managed": managed}


def _raise_configuration_error(exc: Exception) -> None:
    if isinstance(exc, AdminConfigurationEntryNotFoundError):
        raise HTTPException(status_code=404, detail=str(exc))
    if isinstance(
        exc,
        (
            AdminConfigurationEntryExistsError,
            AdminConfigurationProtectedEntryError,
            AdminConfigConflictError,
        ),
    ):
        raise HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, AdminConfigNotConfiguredError):
        raise HTTPException(status_code=503, detail=str(exc))
    logger.error("admin_configuration_operation_failed", error=type(exc).__name__)
    raise HTTPException(status_code=503, detail="Could not save Admin configuration.")


def _parse_since(value: Optional[str]) -> Optional[datetime]:
    """Parse an ISO-8601 instant into aware UTC, or raise 400."""
    try:
        return parse_iso_utc(value)
    except ValueError:
        raise HTTPException(status_code=400, detail="since must be an ISO-8601 timestamp")


def _backend_label(settings) -> str:
    """Header badge naming the configured Hosted Agent profile."""
    if not settings.use_hosted_agent:
        return "Hosted Agent configuration error"
    return "Microsoft Foundry Hosted Agent"


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
        archive_enabled=settings.archive_ui_enabled,
    )
    csp = (
        "default-src 'none'; "
        f"style-src 'nonce-{nonce}'; "
        f"script-src 'nonce-{nonce}'; "
        "connect-src 'self'; "
        f"font-src 'self' {WEB_FONT_CSP_SOURCE}; "
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
    """Report actionable live readiness without exposing secrets."""
    report = await collect_admin_readiness()
    return report.model_dump(mode="json")


@router.get("/api/admin/subscribers")
async def admin_subscribers(_: AdminPrincipal = Depends(require_admin)) -> dict:
    """List the configured subscribers."""
    configuration = get_admin_configuration()
    try:
        subscribers = await configuration.get_subscribers()
        managed = await configuration.get_managed_subscriber_emails()
    except Exception as exc:
        _raise_configuration_error(exc)
    return {
        "configuration_writable": configuration.configured,
        "subscribers": [
            _subscriber_payload(subscriber, subscriber.email.lower() in managed)
            for subscriber in subscribers
        ],
    }


@router.post("/api/admin/subscribers", status_code=201)
async def admin_add_subscriber(
    request: SubscriberRequest,
    principal: AdminPrincipal = Depends(require_admin),
) -> dict:
    """Persist one subscriber profile without exposing the backing Blob."""
    configuration = get_admin_configuration()
    try:
        subscriber = await configuration.add_subscriber(request.to_subscriber())
        count = len(await configuration.get_subscribers())
    except Exception as exc:
        _raise_configuration_error(exc)
    logger.info(
        "admin_subscriber_added",
        principal=principal.display,
        subscriber_count=count,
    )
    return _subscriber_payload(subscriber, managed=True)


@router.put("/api/admin/subscribers/{email}")
async def admin_update_subscriber(
    email: str,
    request: SubscriberRequest,
    principal: AdminPrincipal = Depends(require_admin),
) -> dict:
    """Replace one console-managed subscriber profile."""
    try:
        subscriber = await get_admin_configuration().update_subscriber(
            email,
            request.to_subscriber(),
        )
    except Exception as exc:
        _raise_configuration_error(exc)
    logger.info("admin_subscriber_updated", principal=principal.display)
    return _subscriber_payload(subscriber, managed=True)


@router.delete("/api/admin/subscribers/{email}", status_code=204)
async def admin_remove_subscriber(
    email: str,
    principal: AdminPrincipal = Depends(require_admin),
) -> None:
    """Remove one console-managed subscriber."""
    try:
        await get_admin_configuration().remove_subscriber(email)
    except Exception as exc:
        _raise_configuration_error(exc)
    logger.info("admin_subscriber_removed", principal=principal.display)


@router.get("/api/admin/administrators")
async def admin_administrators(_: AdminPrincipal = Depends(require_admin)) -> dict:
    """List bootstrap and console-managed administrator identifiers."""
    configuration = get_admin_configuration()
    try:
        principals = await configuration.get_admin_principals()
        managed = await configuration.get_managed_admin_principals()
    except Exception as exc:
        _raise_configuration_error(exc)
    return {
        "configuration_writable": configuration.configured,
        "administrators": [
            {"principal": item, "managed": item in managed} for item in sorted(principals)
        ],
    }


@router.post("/api/admin/administrators", status_code=201)
async def admin_add_administrator(
    request: AdministratorRequest,
    principal: AdminPrincipal = Depends(require_admin),
) -> dict:
    """Grant Admin and Archive access to one EasyAuth claim identifier."""
    try:
        added = await get_admin_configuration().add_admin(request.principal)
    except Exception as exc:
        _raise_configuration_error(exc)
    logger.info("admin_principal_added", principal=principal.display)
    return {"principal": added, "managed": True}


@router.delete("/api/admin/administrators/{administrator}", status_code=204)
async def admin_remove_administrator(
    administrator: str,
    principal: AdminPrincipal = Depends(require_admin),
) -> None:
    """Remove one console-managed administrator without allowing self-lockout."""
    normalized = administrator.strip().lower()
    if normalized in principal.identifiers:
        raise HTTPException(
            status_code=409,
            detail="The currently signed-in administrator cannot be deleted.",
        )
    try:
        await get_admin_configuration().remove_admin(normalized)
    except Exception as exc:
        _raise_configuration_error(exc)
    logger.info("admin_principal_removed", principal=principal.display)


@router.get("/api/admin/schedules")
async def admin_schedules(_: AdminPrincipal = Depends(require_admin)) -> dict:
    """List the protected deployment schedule and Admin-managed daily times."""
    configuration = get_admin_configuration()
    try:
        schedules = await configuration.get_automatic_run_schedules()
    except Exception as exc:
        _raise_configuration_error(exc)
    return {
        "configuration_writable": configuration.configured,
        "schedules": [schedule.model_dump(mode="json") for schedule in schedules],
    }


@router.post("/api/admin/schedules", status_code=201)
async def admin_add_schedule(
    request: AutomaticRunTimeRequest,
    principal: AdminPrincipal = Depends(require_admin),
) -> dict:
    """Persist one additional daily UTC execution time."""
    try:
        schedule = await get_admin_configuration().add_automatic_run_time(request.time_utc)
    except Exception as exc:
        _raise_configuration_error(exc)
    logger.info(
        "admin_schedule_added",
        principal=principal.display,
        time_utc=request.time_utc,
    )
    return schedule.model_dump(mode="json")


@router.delete("/api/admin/schedules/{time_utc}", status_code=204)
async def admin_remove_schedule(
    time_utc: str,
    principal: AdminPrincipal = Depends(require_admin),
) -> None:
    """Delete one console-managed daily UTC execution time."""
    try:
        await get_admin_configuration().remove_automatic_run_time(time_utc)
    except Exception as exc:
        _raise_configuration_error(exc)
    logger.info("admin_schedule_removed", principal=principal.display, time_utc=time_utc)


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
        raise HTTPException(status_code=502, detail="Could not read the Azure Update feed.")

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
        raise HTTPException(status_code=404, detail="Run record not found.")
    return record.to_dict()


@router.post("/api/admin/runs", status_code=202)
async def admin_start_run(
    request: StartRunRequest,
    principal: AdminPrincipal = Depends(require_admin),
) -> dict:
    """Start an orchestrated analysis run."""
    store = get_run_store()
    if store.active_count:
        raise HTTPException(status_code=409, detail="A run is already in progress.")

    try:
        record = start_run(
            since=_parse_since(request.since),
            dry_run=request.dry_run,
            source=ArchiveSource.ADMIN_RUN.value,
            selection=request.to_selection(),
            commit_checkpoint=False,
            send_email=request.send_email,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    logger.info(
        "admin_run_triggered",
        run_id=record.run_id,
        selector=record.selection.mode,
        principal=principal.display,
    )
    return record.to_dict()
