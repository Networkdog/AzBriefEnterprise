"""Fail-closed authorization for the authenticated analysis archive."""

from fastapi import HTTPException, Request
from structlog import get_logger

from src.admin.auth import AdminPrincipal, extract_principal
from src.config import get_settings

logger = get_logger()


async def require_archive_reader(request: Request) -> AdminPrincipal:
    """Authorize an archive reader from EasyAuth identity headers."""
    settings = get_settings()
    if not settings.archive_ui_enabled:
        raise HTTPException(status_code=404, detail="Not Found")
    if not settings.archive_require_auth:
        return AdminPrincipal(id="local", name="local-development")

    principal = extract_principal(request)
    if principal is None:
        logger.warning("archive_auth_missing", path=request.url.path)
        raise HTTPException(
            status_code=401,
            detail="Authentication required. Sign in through the configured Entra ID provider.",
        )

    allowed = settings.get_archive_allowed_principals()
    if not allowed or not (principal.identifiers & allowed):
        logger.warning(
            "archive_auth_forbidden",
            path=request.url.path,
            principal=principal.display,
            allow_list_configured=bool(allowed),
        )
        raise HTTPException(status_code=403, detail="This account cannot read the AzBrief archive.")
    logger.info("archive_auth_ok", path=request.url.path, principal=principal.display)
    return principal
