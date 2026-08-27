"""Authenticated MCP control plane hosted by the Container App."""

import hmac
from typing import Any, Optional
from urllib.parse import urlparse

from fastapi import HTTPException
from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse
from structlog import get_logger

from src.config import get_settings
from src.middleware import rate_limiter
from src.orchestrator import get_run_store
from src.rss.parser import AzureUpdate

logger = get_logger()

_ALLOWED_UPDATE_DOMAINS = frozenset(
    {
        "azure.microsoft.com",
        "www.microsoft.com",
        "learn.microsoft.com",
        "azure.com",
    }
)

_analyzer: Optional[Any] = None
_rss_parser: Optional[Any] = None

mcp = MCPServer(
    "AzBrief Enterprise",
    version="1.0.0",
    instructions=(
        "Read Azure Update announcements and request environment-grounded analysis. "
        "All analysis executes in the Microsoft Foundry Hosted Agent."
    ),
)


def register_mcp_services(analyzer: Any, rss_parser: Any) -> None:
    """Register control-plane dependencies initialized by FastAPI lifespan."""
    global _analyzer, _rss_parser
    _analyzer = analyzer
    _rss_parser = rss_parser


def _require_services() -> tuple[Any, Any]:
    if _analyzer is None or _rss_parser is None:
        raise RuntimeError("AzBrief MCP services are not initialized")
    return _analyzer, _rss_parser


def _validate_update_url(update_url: str) -> str:
    parsed = urlparse(update_url)
    hostname = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not any(
        hostname == domain or hostname.endswith(f".{domain}") for domain in _ALLOWED_UPDATE_DOMAINS
    ):
        raise ValueError("update_url must be an HTTPS Microsoft Azure URL")
    return update_url


async def _resolve_update(update_url: str) -> AzureUpdate:
    _, rss_parser = _require_services()
    update = await rss_parser.get_update_by_url(_validate_update_url(update_url))
    if update is not None:
        return update

    details = await rss_parser.fetch_update_details(update_url)
    return AzureUpdate(
        id=update_url,
        title=details.get("title", "Unknown Update"),
        description=details.get("content", ""),
        link=update_url,
        published_date=None,
        categories=[],
        azure_services=[],
        update_type=None,
        status=None,
    )


@mcp.tool()
async def list_recent_azure_updates(limit: int = 10) -> list[dict[str, Any]]:
    """List recent Azure Update announcements without analyzing them."""
    if not 1 <= limit <= 50:
        raise ValueError("limit must be between 1 and 50")
    _, rss_parser = _require_services()
    updates = await rss_parser.get_updates()
    return [update.to_dict() for update in updates[:limit]]


@mcp.tool()
async def analyze_azure_update(update_url: str) -> dict[str, Any]:
    """Run complete environment-grounded analysis for one Azure Update URL."""
    analyzer, _ = _require_services()
    update = await _resolve_update(update_url)
    result = await analyzer.analyze_update(update)
    return result.model_dump(mode="json")


@mcp.tool()
def get_recent_digest_runs(limit: int = 10) -> list[dict[str, Any]]:
    """Read recent digest-run status records from this Container App replica."""
    if not 1 <= limit <= 50:
        raise ValueError("limit must be between 1 and 50")
    return [record.to_dict() for record in get_run_store().recent(limit)]


class MCPApiKeyMiddleware:
    """Require the Container App API key before parsing any MCP payload."""

    def __init__(self, app: Any):
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        expected = get_settings().api_key
        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        provided = headers.get(b"x-api-key", b"").decode("utf-8", errors="ignore")
        if not expected:
            logger.error("mcp_auth_not_configured")
            response = JSONResponse(
                {"error": "MCP authentication is not configured"},
                status_code=503,
            )
            await response(scope, receive, send)
            return
        if not provided:
            logger.warning("mcp_auth_missing")
            response = JSONResponse({"error": "X-API-Key is required"}, status_code=401)
            await response(scope, receive, send)
            return
        if not hmac.compare_digest(provided, expected):
            logger.warning("mcp_auth_invalid")
            response = JSONResponse({"error": "Invalid API key"}, status_code=403)
            await response(scope, receive, send)
            return
        try:
            rate_limiter.check(Request(scope))
        except HTTPException as exc:
            response = JSONResponse(
                {"error": "Too many requests"},
                status_code=exc.status_code,
                headers=exc.headers,
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


# Azure Container Apps ingress owns host routing, so the MCP transport can trust
# the reverse proxy's normalized Host header. The server remains protected by the
# API-key middleware and the Container App's network/IP restrictions.
_transport_security = TransportSecuritySettings(enable_dns_rebinding_protection=False)
_streamable_http_app = mcp.streamable_http_app(
    streamable_http_path="/",
    json_response=True,
    stateless_http=True,
    transport_security=_transport_security,
)
mcp_http_app = MCPApiKeyMiddleware(_streamable_http_app)
