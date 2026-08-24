"""Tests for shared subscription discovery utilities."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services import (
    _subscription_cache,
    _subscription_cache_lock,
    discover_subscriptions_sync,
)


class TestDiscoverSubscriptionsSync:
    """Tests for synchronous subscription discovery."""

    def setup_method(self):
        """Reset the module-level cache before each test."""
        import src.services

        src.services._subscription_cache = None

    def test_discovers_enabled_subscriptions(self):
        """Should return only enabled subscriptions."""
        mock_credential = MagicMock()
        mock_credential.get_token.return_value = MagicMock(token="test-token")

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "value": [
                {"subscriptionId": "sub-1", "state": "Enabled", "displayName": "Sub 1"},
                {"subscriptionId": "sub-2", "state": "Disabled", "displayName": "Sub 2"},
                {"subscriptionId": "sub-3", "state": "Enabled", "displayName": "Sub 3"},
            ]
        }

        with patch("src.services.httpx.get", return_value=mock_response):
            result = discover_subscriptions_sync(mock_credential)

        assert len(result) == 2
        assert result[0]["subscriptionId"] == "sub-1"
        assert result[1]["subscriptionId"] == "sub-3"

    def test_returns_cached_results(self):
        """Second call should return cached results without HTTP call."""
        import src.services

        src.services._subscription_cache = [
            {"subscriptionId": "cached-sub", "displayName": "Cached"}
        ]

        mock_credential = MagicMock()
        result = discover_subscriptions_sync(mock_credential)

        assert len(result) == 1
        assert result[0]["subscriptionId"] == "cached-sub"
        # Credential should NOT be called since cache is used
        mock_credential.get_token.assert_not_called()

    def test_handles_empty_response(self):
        """Should return empty list when no subscriptions found."""
        import src.services

        src.services._subscription_cache = None

        mock_credential = MagicMock()
        mock_credential.get_token.return_value = MagicMock(token="test-token")

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"value": []}

        with patch("src.services.httpx.get", return_value=mock_response):
            result = discover_subscriptions_sync(mock_credential)

        assert result == []
