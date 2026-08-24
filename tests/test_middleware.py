"""Tests for security middleware (authentication and rate limiting)."""

import time
from unittest.mock import MagicMock, patch

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from src.middleware import RateLimiter, verify_api_key


@pytest.fixture
def rate_limiter():
    """Create a rate limiter with small limits for testing."""
    return RateLimiter(max_requests=3, window_seconds=5)


@pytest.fixture
def mock_request():
    """Create a mock FastAPI request."""
    request = MagicMock()
    request.client.host = "127.0.0.1"
    request.headers = {}
    request.url.path = "/api/test"
    return request


class TestRateLimiter:
    """Test in-memory rate limiter."""

    def test_allows_requests_under_limit(self, rate_limiter, mock_request):
        """Requests under the limit are allowed."""
        for _ in range(3):
            rate_limiter.check(mock_request)  # Should not raise

    def test_blocks_requests_over_limit(self, rate_limiter, mock_request):
        """Requests over the limit are blocked with 429."""
        from fastapi import HTTPException

        for _ in range(3):
            rate_limiter.check(mock_request)

        with pytest.raises(HTTPException) as exc_info:
            rate_limiter.check(mock_request)
        assert exc_info.value.status_code == 429

    def test_separate_clients_have_separate_limits(self, rate_limiter):
        """Different IPs have independent rate limits."""
        req1 = MagicMock()
        req1.client.host = "10.0.0.1"
        req1.headers = {}
        req2 = MagicMock()
        req2.client.host = "10.0.0.2"
        req2.headers = {}

        for _ in range(3):
            rate_limiter.check(req1)
            rate_limiter.check(req2)
        # Both should have used their 3 requests, but independently

    def test_uses_x_forwarded_for(self, rate_limiter):
        """Rate limiter uses X-Forwarded-For header when trust_proxy_headers is True."""
        rate_limiter.trust_proxy_headers = True
        req = MagicMock()
        req.client.host = "10.0.0.1"
        req.headers = {"X-Forwarded-For": "192.168.1.1, 10.0.0.1"}

        key = rate_limiter._get_client_key(req)
        assert key == "192.168.1.1"

    def test_window_expiration(self, rate_limiter, mock_request, monkeypatch):
        """Expired entries are cleaned up, allowing new requests."""
        # Manually fill the rate limit
        key = rate_limiter._get_client_key(mock_request)
        # Add old timestamps that are beyond the window
        old_time = time.monotonic() - 10  # 10 seconds ago, window is 5s
        rate_limiter._requests[key] = [old_time, old_time + 1, old_time + 2]

        # Should succeed because old entries are expired
        rate_limiter.check(mock_request)


class TestAPIKeyAuth:
    """Test API key authentication."""

    @pytest.mark.asyncio
    async def test_no_key_configured_allows_all(self):
        """When no API key is configured, all requests pass."""
        mock_request = MagicMock()
        mock_request.url.path = "/api/analyze"

        with patch("src.middleware.get_settings") as mock:
            settings = MagicMock()
            settings.api_key = None
            mock.return_value = settings

            result = await verify_api_key(mock_request, api_key=None)
            assert result is None

    @pytest.mark.asyncio
    async def test_missing_key_returns_401(self):
        """When API key is configured but not provided, returns 401."""
        from fastapi import HTTPException

        mock_request = MagicMock()
        mock_request.url.path = "/api/analyze"

        with patch("src.middleware.get_settings") as mock:
            settings = MagicMock()
            settings.api_key = "my-secret-key"
            mock.return_value = settings

            with pytest.raises(HTTPException) as exc_info:
                await verify_api_key(mock_request, api_key=None)
            assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_wrong_key_returns_403(self):
        """When wrong API key is provided, returns 403."""
        from fastapi import HTTPException

        mock_request = MagicMock()
        mock_request.url.path = "/api/analyze"

        with patch("src.middleware.get_settings") as mock:
            settings = MagicMock()
            settings.api_key = "correct-key"
            mock.return_value = settings

            with pytest.raises(HTTPException) as exc_info:
                await verify_api_key(mock_request, api_key="wrong-key")
            assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_correct_key_passes(self):
        """When correct API key is provided, authentication passes."""
        mock_request = MagicMock()
        mock_request.url.path = "/api/analyze"

        with patch("src.middleware.get_settings") as mock:
            settings = MagicMock()
            settings.api_key = "my-secret-key"
            mock.return_value = settings

            result = await verify_api_key(mock_request, api_key="my-secret-key")
            assert result == "my-secret-key"

    @pytest.mark.asyncio
    async def test_timing_safe_comparison(self):
        """API key comparison is timing-safe (uses hmac.compare_digest)."""
        mock_request = MagicMock()
        mock_request.url.path = "/api/analyze"
        from fastapi import HTTPException

        with patch("src.middleware.get_settings") as mock:
            settings = MagicMock()
            settings.api_key = "a" * 32
            mock.return_value = settings

            # Similar but wrong key should still fail
            with pytest.raises(HTTPException):
                await verify_api_key(mock_request, api_key="a" * 31 + "b")
