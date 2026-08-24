"""Security middleware for AzBrief FastAPI application.

Provides API key authentication and rate limiting for production use.
"""

import hmac
import time
from collections import defaultdict
from typing import Optional

from fastapi import HTTPException, Request, Security
from fastapi.security import APIKeyHeader
from structlog import get_logger

from src.config import get_settings

logger = get_logger()

# ---------------------------------------------------------------------------
# API Key Authentication
# ---------------------------------------------------------------------------

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def verify_api_key(
    request: Request,
    api_key: Optional[str] = Security(_api_key_header),
) -> Optional[str]:
    """Verify the API key from request header.

    If API_KEY is not configured, authentication is disabled (open access).
    When configured, all /api/* endpoints require a valid X-API-Key header.

    Args:
        request: FastAPI request object
        api_key: API key from X-API-Key header

    Returns:
        The verified API key, or None if auth is disabled

    Raises:
        HTTPException: 401 if key is missing, 403 if key is invalid
    """
    settings = get_settings()
    expected_key = getattr(settings, "api_key", None)

    if not expected_key:
        return None

    if not api_key:
        logger.warning("api_auth_missing", path=request.url.path)
        raise HTTPException(
            status_code=401,
            detail="API key required. Provide X-API-Key header.",
        )

    if not hmac.compare_digest(api_key, expected_key):
        logger.warning(
            "api_auth_invalid",
            path=request.url.path,
            key_prefix=api_key[:4] + "..." if len(api_key) > 4 else "***",
        )
        raise HTTPException(status_code=403, detail="Invalid API key.")

    return api_key


# ---------------------------------------------------------------------------
# Rate Limiting (in-memory token bucket)
# ---------------------------------------------------------------------------


class RateLimiter:
    """In-memory token bucket rate limiter.

    Tracks request counts per client IP with a sliding window.
    Suitable for single-instance deployments (Container App, Automation).

    Args:
        max_requests: Maximum requests allowed per window
        window_seconds: Time window in seconds
        trust_proxy_headers: Whether to trust X-Forwarded-For header (only behind reverse proxy)
        max_clients: Maximum number of tracked client keys (prevents memory exhaustion)
    """

    def __init__(
        self,
        max_requests: int = 30,
        window_seconds: int = 60,
        trust_proxy_headers: bool = False,
        max_clients: int = 10_000,
    ):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.trust_proxy_headers = trust_proxy_headers
        self.max_clients = max_clients
        self._requests: dict[str, list[float]] = defaultdict(list)

    def _get_client_key(self, request: Request) -> str:
        """Extract client identifier from request.

        Only trusts X-Forwarded-For when trust_proxy_headers is enabled
        (i.e., when deployed behind a known reverse proxy like Container Apps).
        """
        if self.trust_proxy_headers:
            forwarded = request.headers.get("X-Forwarded-For")
            if forwarded:
                ip = forwarded.split(",")[0].strip()
                return ip
        return request.client.host if request.client else "unknown"

    def _cleanup(self, key: str, now: float) -> None:
        """Remove expired entries for a client."""
        cutoff = now - self.window_seconds
        self._requests[key] = [t for t in self._requests[key] if t > cutoff]
        if not self._requests[key]:
            del self._requests[key]

    def _evict_stale_clients(self, now: float) -> None:
        """Evict oldest clients when max_clients is exceeded (memory bound)."""
        if len(self._requests) <= self.max_clients:
            return
        # Remove clients with oldest last-request time
        sorted_keys = sorted(
            self._requests.keys(),
            key=lambda k: self._requests[k][-1] if self._requests[k] else 0,
        )
        evict_count = len(self._requests) - self.max_clients
        for key in sorted_keys[:evict_count]:
            del self._requests[key]

    def check(self, request: Request) -> None:
        """Check rate limit for the request.

        Args:
            request: FastAPI request object

        Raises:
            HTTPException: 429 if rate limit exceeded
        """
        key = self._get_client_key(request)
        now = time.monotonic()
        self._cleanup(key, now)

        if len(self._requests[key]) >= self.max_requests:
            retry_after = int(self.window_seconds - (now - self._requests[key][0]))
            logger.warning(
                "rate_limit_exceeded",
                client=key,
                requests=len(self._requests[key]),
                max_requests=self.max_requests,
            )
            raise HTTPException(
                status_code=429,
                detail="Too many requests. Please retry later.",
                headers={"Retry-After": str(max(retry_after, 1))},
            )

        self._requests[key].append(now)
        self._evict_stale_clients(now)


# Singleton rate limiter instance
# trust_proxy_headers=False by default — set to True only when behind a known reverse proxy
import os as _os

rate_limiter = RateLimiter(
    max_requests=30,
    window_seconds=60,
    trust_proxy_headers=bool(_os.environ.get("TRUST_PROXY_HEADERS")),
)
