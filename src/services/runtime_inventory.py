"""Read-only Azure and Foundry inventory used by operational readiness views."""

from __future__ import annotations

import asyncio
import threading
from typing import Any

import httpx
from structlog import get_logger

from src.config import get_azure_credential

logger = get_logger()

_ARM_SCOPE = "https://management.azure.com/.default"
_ARM_BASE_URL = "https://management.azure.com"
_REQUEST_TIMEOUT_S = 15.0


class RuntimeInventoryService:
    """Fetch safe runtime metadata without making readiness decisions."""

    def __init__(self, credential: Any = None) -> None:
        self._credential = credential
        self._owns_credential = credential is None
        self._credential_lock = threading.Lock()

    def _get_credential(self):
        if self._credential is None:
            with self._credential_lock:
                if self._credential is None:
                    self._credential = get_azure_credential()
        return self._credential

    async def get_arm_resources(
        self,
        requests: dict[str, tuple[str, str]],
    ) -> dict[str, dict[str, Any]]:
        """Fetch named ARM resources concurrently.

        Args:
            requests: Mapping of result key to ``(resource_id, api_version)``.

        Returns:
            Per-key success envelopes containing raw ARM resource documents.
        """
        if not requests:
            return {}
        try:
            token = await asyncio.to_thread(
                self._get_credential().get_token,
                _ARM_SCOPE,
            )
        except Exception as exc:
            error = type(exc).__name__
            logger.warning("admin_readiness_arm_auth_failed", error=error)
            return {key: {"success": False, "data": {}, "error": error} for key in requests}

        headers = {"Authorization": f"Bearer {token.token}"}

        async def fetch_one(
            client: httpx.AsyncClient,
            key: str,
            resource_id: str,
            api_version: str,
        ) -> tuple[str, dict[str, Any]]:
            try:
                response = await client.get(
                    f"{_ARM_BASE_URL}{resource_id}",
                    headers=headers,
                    params={"api-version": api_version},
                )
                if response.status_code != 200:
                    return key, {
                        "success": False,
                        "data": {},
                        "error": f"HTTP {response.status_code}",
                    }
                return key, {"success": True, "data": response.json(), "error": ""}
            except Exception as exc:
                return key, {
                    "success": False,
                    "data": {},
                    "error": type(exc).__name__,
                }

        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_S) as client:
            results = await asyncio.gather(
                *(
                    fetch_one(client, key, resource_id, api_version)
                    for key, (resource_id, api_version) in requests.items()
                )
            )
        return dict(results)

    async def list_foundry_agents(self, project_endpoint: str) -> dict[str, Any]:
        """List logical Foundry Agents with safe latest-version metadata."""
        try:
            agents = await asyncio.to_thread(
                self._list_foundry_agents_sync,
                project_endpoint,
            )
            return {"success": True, "data": agents, "error": ""}
        except Exception as exc:
            error = type(exc).__name__
            logger.warning("admin_readiness_foundry_agents_failed", error=error)
            return {"success": False, "data": {}, "error": error}

    def _list_foundry_agents_sync(self, project_endpoint: str) -> dict[str, dict[str, str]]:
        from azure.ai.projects import AIProjectClient

        project = AIProjectClient(
            endpoint=project_endpoint,
            credential=self._get_credential(),
        )
        try:
            result: dict[str, dict[str, str]] = {}
            for agent in project.agents.list():
                name = str(getattr(agent, "name", "") or "")
                if not name:
                    continue
                latest = getattr(getattr(agent, "versions", None), "latest", None)
                definition = getattr(latest, "definition", None)
                result[name] = {
                    "version": str(getattr(latest, "version", "") or ""),
                    "kind": type(definition).__name__ if definition is not None else "Agent",
                    "status": str(
                        getattr(latest, "status", "") or getattr(latest, "state", "") or ""
                    ),
                }
            return result
        finally:
            project.close()

    def close(self) -> None:
        """Close the credential owned by this short-lived inventory service."""
        if self._credential is not None and self._owns_credential:
            close = getattr(self._credential, "close", None)
            if callable(close):
                close()
        self._credential = None
