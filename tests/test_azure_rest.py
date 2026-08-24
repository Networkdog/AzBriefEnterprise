"""Tests for AzureRestClient and additional service integration tests."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.azure_rest import AzureRestClient


class TestAzureRestClientInit:
    """Test AzureRestClient initialization."""

    def test_default_init(self):
        """Client initializes with settings."""
        with patch("src.services.azure_rest.get_settings") as mock:
            settings = MagicMock()
            settings.azure_subscription_id = "sub-123"
            mock.return_value = settings
            client = AzureRestClient()
            assert client._settings == settings

    def test_credential_lazy_init(self):
        """Credential is not created until needed."""
        with patch("src.services.azure_rest.get_settings") as mock:
            mock.return_value = MagicMock()
            client = AzureRestClient()
            assert client._credential is None


class TestAzureRestClientCallApi:
    """Test call_api method."""

    @pytest.mark.asyncio
    async def test_successful_api_call(self):
        """Successful API call returns value list."""
        with patch("src.services.azure_rest.get_settings") as mock_settings:
            settings = MagicMock()
            settings.azure_subscription_id = "sub-123"
            mock_settings.return_value = settings

            client = AzureRestClient()

            mock_credential = MagicMock()
            mock_token = MagicMock()
            mock_token.token = "fake-token"
            mock_credential.get_token.return_value = mock_token
            client._credential = mock_credential

            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "value": [
                    {"name": "Standard_D2s_v3", "tier": "Standard"},
                    {"name": "Standard_D4s_v3", "tier": "Standard"},
                ]
            }

            with patch("httpx.AsyncClient") as MockAsyncClient:
                mock_client_instance = AsyncMock()
                mock_client_instance.request = AsyncMock(return_value=mock_response)
                mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
                mock_client_instance.__aexit__ = AsyncMock(return_value=False)
                MockAsyncClient.return_value = mock_client_instance

                result = await client.call_api(
                    path="/subscriptions/{subscriptionId}/providers/Microsoft.Compute/skus",
                    api_version="2021-07-01",
                )

            assert result["count"] == 2
            assert len(result["value"]) == 2
            assert result["value"][0]["name"] == "Standard_D2s_v3"

    @pytest.mark.asyncio
    async def test_no_subscription_returns_error(self):
        """Missing subscription ID returns error dict."""
        with patch("src.services.azure_rest.get_settings") as mock_settings:
            settings = MagicMock()
            settings.azure_subscription_id = None
            mock_settings.return_value = settings

            client = AzureRestClient()

            # Mock _get_subscription_id to return None
            with patch.object(client, "_get_subscription_id", return_value=None):
                result = await client.call_api(
                    path="/subscriptions/{subscriptionId}/test",
                )

            assert "error" in result
            assert result["value"] == []

    @pytest.mark.asyncio
    async def test_api_error_returns_status_code(self):
        """Non-200 response returns error with status code."""
        with patch("src.services.azure_rest.get_settings") as mock_settings:
            settings = MagicMock()
            settings.azure_subscription_id = "sub-123"
            mock_settings.return_value = settings

            client = AzureRestClient()
            mock_credential = MagicMock()
            mock_token = MagicMock()
            mock_token.token = "fake-token"
            mock_credential.get_token.return_value = mock_token
            client._credential = mock_credential

            mock_response = MagicMock()
            mock_response.status_code = 403
            mock_response.text = "Forbidden"

            with patch("httpx.AsyncClient") as MockAsyncClient:
                mock_client_instance = AsyncMock()
                mock_client_instance.request = AsyncMock(return_value=mock_response)
                mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
                mock_client_instance.__aexit__ = AsyncMock(return_value=False)
                MockAsyncClient.return_value = mock_client_instance

                result = await client.call_api(
                    path="/subscriptions/{subscriptionId}/test",
                )

            assert result["status_code"] == 403
            assert result["value"] == []

    @pytest.mark.asyncio
    async def test_max_results_limits_output(self):
        """Results are trimmed to max_results."""
        with patch("src.services.azure_rest.get_settings") as mock_settings:
            settings = MagicMock()
            settings.azure_subscription_id = "sub-123"
            mock_settings.return_value = settings

            client = AzureRestClient()
            mock_credential = MagicMock()
            mock_token = MagicMock()
            mock_token.token = "fake-token"
            mock_credential.get_token.return_value = mock_token
            client._credential = mock_credential

            # Return 10 items but limit to 3
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"value": [{"name": f"item-{i}"} for i in range(10)]}

            with patch("httpx.AsyncClient") as MockAsyncClient:
                mock_client_instance = AsyncMock()
                mock_client_instance.request = AsyncMock(return_value=mock_response)
                mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
                mock_client_instance.__aexit__ = AsyncMock(return_value=False)
                MockAsyncClient.return_value = mock_client_instance

                result = await client.call_api(
                    path="/subscriptions/{subscriptionId}/test",
                    max_results=3,
                )

            assert result["count"] == 3
            assert len(result["value"]) == 3

    def test_subscription_id_placeholder_replaced(self):
        """The {subscriptionId} placeholder is resolved."""
        with patch("src.services.azure_rest.get_settings") as mock_settings:
            settings = MagicMock()
            settings.azure_subscription_id = "abc-123"
            mock_settings.return_value = settings

            client = AzureRestClient()
            sub_id = client._get_subscription_id()
            assert sub_id == "abc-123"


class TestRateLimiterCleanup:
    """Test Rate Limiter memory cleanup (the fix)."""

    def test_empty_key_removed_after_cleanup(self):
        """Empty client keys are removed from dict after all timestamps expire."""
        import time

        from src.middleware import RateLimiter

        limiter = RateLimiter(max_requests=10, window_seconds=1)
        # Manually add an old entry
        limiter._requests["1.2.3.4"] = [time.monotonic() - 100]

        # Cleanup should remove expired timestamps AND the empty key
        limiter._cleanup("1.2.3.4", time.monotonic())
        assert "1.2.3.4" not in limiter._requests

    def test_active_key_preserved_after_cleanup(self):
        """Client keys with active timestamps are preserved."""
        import time

        from src.middleware import RateLimiter

        limiter = RateLimiter(max_requests=10, window_seconds=60)
        now = time.monotonic()
        limiter._requests["1.2.3.4"] = [now - 10, now - 5, now]

        limiter._cleanup("1.2.3.4", now)
        assert "1.2.3.4" in limiter._requests
        assert len(limiter._requests["1.2.3.4"]) == 3


class TestLoggingConfigTimezone:
    """Test that logging uses timezone-aware UTC."""

    def test_azure_monitor_handler_uses_utc(self):
        """_AzureMonitorHandler.emit uses datetime.now(timezone.utc)."""
        import logging
        from unittest.mock import patch as _patch

        from src.logging_config import _AzureMonitorHandler

        handler = _AzureMonitorHandler(
            endpoint="https://example.com",
            dcr_rule_id="dcr-test",
            flush_size=100,
        )

        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="test message",
            args=(),
            exc_info=None,
        )

        handler.emit(record)

        assert len(handler._buffer) == 1
        entry = handler._buffer[0]
        # Should end with Z (UTC) and NOT contain +00:00
        assert entry["TimeGenerated"].endswith("Z")
        assert "+00:00" not in entry["TimeGenerated"]
