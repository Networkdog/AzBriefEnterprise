"""Azure services package.

Shared utilities for subscription discovery used across multiple services.
"""

import threading
from typing import Optional

import httpx
from structlog import get_logger

logger = get_logger()

# Module-level cache for discovered subscriptions (shared across all services)
_subscription_cache: Optional[list[dict]] = None
_subscription_cache_lock = threading.Lock()


async def discover_subscriptions_async(credential) -> list[dict]:
    """Discover all enabled Azure subscriptions accessible by the credential (async).

    Returns cached results if already discovered. Each entry is a dict with
    'subscriptionId' and 'displayName'.

    Args:
        credential: Azure credential instance

    Returns:
        List of dicts with 'subscriptionId' and 'displayName'
    """
    global _subscription_cache
    if _subscription_cache is not None:
        return _subscription_cache

    token = credential.get_token("https://management.azure.com/.default").token
    headers = {"Authorization": f"Bearer {token}"}
    url: Optional[str] = "https://management.azure.com/subscriptions?api-version=2022-12-01"
    discovered: list[dict] = []

    async with httpx.AsyncClient(timeout=30.0) as client:
        while url:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            payload = response.json()

            for sub in payload.get("value", []):
                sub_id = sub.get("subscriptionId")
                state = str(sub.get("state", "")).lower()
                if not sub_id or (state and state != "enabled"):
                    continue
                discovered.append(
                    {
                        "subscriptionId": sub_id,
                        "displayName": sub.get("displayName", ""),
                    }
                )

            url = payload.get("nextLink")

    with _subscription_cache_lock:
        _subscription_cache = discovered

    logger.info("subscriptions_discovered", count=len(discovered))
    return discovered


def discover_subscriptions_sync(credential) -> list[dict]:
    """Discover all enabled Azure subscriptions (sync version for non-async callers).

    Returns cached results if already discovered.

    Args:
        credential: Azure credential instance

    Returns:
        List of dicts with 'subscriptionId' and 'displayName'
    """
    global _subscription_cache
    if _subscription_cache is not None:
        return _subscription_cache

    token = credential.get_token("https://management.azure.com/.default").token
    headers = {"Authorization": f"Bearer {token}"}
    url: Optional[str] = "https://management.azure.com/subscriptions?api-version=2022-12-01"
    discovered: list[dict] = []

    while url:
        response = httpx.get(url, headers=headers, timeout=30.0)
        response.raise_for_status()
        payload = response.json()

        for sub in payload.get("value", []):
            sub_id = sub.get("subscriptionId")
            state = str(sub.get("state", "")).lower()
            if not sub_id or (state and state != "enabled"):
                continue
            discovered.append(
                {
                    "subscriptionId": sub_id,
                    "displayName": sub.get("displayName", ""),
                }
            )

        url = payload.get("nextLink")

    with _subscription_cache_lock:
        _subscription_cache = discovered

    logger.info("subscriptions_discovered", count=len(discovered))
    return discovered
