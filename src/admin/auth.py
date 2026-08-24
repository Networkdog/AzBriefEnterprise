"""Authentication and authorization for the AzBrief admin surface.

Identity comes from the Container Apps built-in authentication (EasyAuth)
sidecar, which validates the Entra ID token before the request reaches this
process and injects ``X-MS-CLIENT-PRINCIPAL*`` headers. The sidecar strips any
inbound copy of those headers, so they can only originate from the platform —
provided ingress is the only path to the container, which the enterprise
template enforces.

Everything here fails closed: the admin surface is off unless explicitly
enabled, and an empty allow-list denies every principal rather than defaulting
to open access.
"""

from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass, field
from typing import Optional

from fastapi import HTTPException, Request
from structlog import get_logger

from src.config import get_settings

logger = get_logger()

PRINCIPAL_HEADER = "X-MS-CLIENT-PRINCIPAL"
PRINCIPAL_ID_HEADER = "X-MS-CLIENT-PRINCIPAL-ID"
PRINCIPAL_NAME_HEADER = "X-MS-CLIENT-PRINCIPAL-NAME"

# Claim types that carry a usable identifier, most specific first.
_NAME_CLAIMS = (
    "preferred_username",
    "upn",
    "email",
    "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress",
    "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/name",
    "name",
)
_ID_CLAIMS = (
    "oid",
    "http://schemas.microsoft.com/identity/claims/objectidentifier",
    "sub",
)


@dataclass
class AdminPrincipal:
    """Authenticated caller of an admin route."""

    id: str = ""
    name: str = ""
    groups: list[str] = field(default_factory=list)

    @property
    def identifiers(self) -> set[str]:
        """Lowercased values that may appear in the allow-list."""
        values = {self.id, self.name, *self.groups}
        return {v.lower() for v in values if v}

    @property
    def display(self) -> str:
        return self.name or self.id or "unknown"


def _decode_principal_header(raw: str) -> tuple[str, str, list[str]]:
    """Decode the base64 EasyAuth principal blob into (id, name, groups)."""
    try:
        padded = raw + "=" * (-len(raw) % 4)
        payload = json.loads(base64.b64decode(padded).decode("utf-8"))
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return "", "", []

    claims = payload.get("claims") or []
    by_type: dict[str, list[str]] = {}
    for claim in claims:
        if not isinstance(claim, dict):
            continue
        claim_type = str(claim.get("typ", ""))
        claim_value = str(claim.get("val", ""))
        if claim_type and claim_value:
            by_type.setdefault(claim_type, []).append(claim_value)

    def first(candidates: tuple[str, ...]) -> str:
        for key in candidates:
            values = by_type.get(key)
            if values:
                return values[0]
        return ""

    return first(_ID_CLAIMS), first(_NAME_CLAIMS), by_type.get("groups", [])


def extract_principal(request: Request) -> Optional[AdminPrincipal]:
    """Build an ``AdminPrincipal`` from the request, or None when unauthenticated."""
    principal_id = request.headers.get(PRINCIPAL_ID_HEADER, "").strip()
    principal_name = request.headers.get(PRINCIPAL_NAME_HEADER, "").strip()
    groups: list[str] = []

    raw = request.headers.get(PRINCIPAL_HEADER)
    if raw:
        claim_id, claim_name, claim_groups = _decode_principal_header(raw)
        principal_id = principal_id or claim_id
        principal_name = principal_name or claim_name
        groups = claim_groups

    if not principal_id and not principal_name:
        return None
    return AdminPrincipal(id=principal_id, name=principal_name, groups=groups)


async def require_admin(request: Request) -> AdminPrincipal:
    """FastAPI dependency guarding every admin route.

    Raises:
        HTTPException: 404 when the admin surface is disabled (the route should
            not advertise its existence), 401 when unauthenticated, 403 when the
            principal is not on the allow-list.
    """
    settings = get_settings()

    if not settings.admin_ui_enabled:
        raise HTTPException(status_code=404, detail="Not Found")

    if not settings.admin_require_auth:
        return AdminPrincipal(id="local", name="local-development")

    principal = extract_principal(request)
    if principal is None:
        logger.warning("admin_auth_missing", path=request.url.path)
        raise HTTPException(
            status_code=401,
            detail="Authentication required. Sign in through the configured Entra ID provider.",
        )

    allowed = settings.get_admin_allowed_principals()
    if not allowed or not (principal.identifiers & allowed):
        logger.warning(
            "admin_auth_forbidden",
            path=request.url.path,
            principal=principal.display,
            allow_list_configured=bool(allowed),
        )
        raise HTTPException(status_code=403, detail="This account is not an AzBrief administrator.")

    logger.info("admin_auth_ok", path=request.url.path, principal=principal.display)
    return principal
