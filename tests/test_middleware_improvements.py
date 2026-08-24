"""Tests for middleware improvements — RateLimiter and API key auth."""

from unittest.mock import MagicMock

import pytest

from src.middleware import RateLimiter


class TestRateLimiterXForwardedFor:
    """Test X-Forwarded-For handling with trust_proxy_headers flag."""

    def test_ignores_forwarded_header_by_default(self):
        """Default: should NOT trust X-Forwarded-For header."""
        limiter = RateLimiter(trust_proxy_headers=False)
        request = MagicMock()
        request.headers = {"X-Forwarded-For": "1.2.3.4"}
        request.client = MagicMock()
        request.client.host = "10.0.0.1"

        key = limiter._get_client_key(request)
        assert key == "10.0.0.1"

    def test_trusts_forwarded_header_when_enabled(self):
        """When trust_proxy_headers=True, should use X-Forwarded-For."""
        limiter = RateLimiter(trust_proxy_headers=True)
        request = MagicMock()
        request.headers = {"X-Forwarded-For": "1.2.3.4, 10.0.0.1"}
        request.client = MagicMock()
        request.client.host = "10.0.0.1"

        key = limiter._get_client_key(request)
        assert key == "1.2.3.4"

    def test_falls_back_to_client_host_without_header(self):
        """When no X-Forwarded-For even with trust enabled, use client.host."""
        limiter = RateLimiter(trust_proxy_headers=True)
        request = MagicMock()
        request.headers = {}
        request.client = MagicMock()
        request.client.host = "192.168.1.1"

        key = limiter._get_client_key(request)
        assert key == "192.168.1.1"


class TestRateLimiterMemoryBounds:
    """Test that RateLimiter properly bounds memory usage."""

    def test_evicts_stale_clients_when_max_exceeded(self):
        """Should evict oldest clients when max_clients is exceeded."""
        import time

        limiter = RateLimiter(max_requests=100, window_seconds=60, max_clients=3)

        # Add 5 different client keys
        now = time.monotonic()
        for i in range(5):
            limiter._requests[f"client-{i}"] = [now + i]

        limiter._evict_stale_clients(now + 10)

        # Should have at most 3 clients
        assert len(limiter._requests) <= 3
        # Newest clients should be kept
        assert "client-4" in limiter._requests
        assert "client-3" in limiter._requests

    def test_no_eviction_when_under_limit(self):
        """Should not evict when under max_clients."""
        import time

        limiter = RateLimiter(max_clients=100)
        now = time.monotonic()
        limiter._requests["client-1"] = [now]
        limiter._requests["client-2"] = [now]

        limiter._evict_stale_clients(now)
        assert len(limiter._requests) == 2
