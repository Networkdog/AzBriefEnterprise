"""Tests for the read-only runtime inventory data-access service."""

import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from src.services import runtime_inventory
from src.services.runtime_inventory import RuntimeInventoryService


class Credential:
    def __init__(self):
        self.closed = False

    def get_token(self, _scope):
        return SimpleNamespace(token="test-token")

    def close(self):
        self.closed = True


class Response:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


class HttpClient:
    def __init__(self, *_args, **_kwargs):
        self.requests = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def get(self, url, headers, params):
        self.requests.append((url, headers, params))
        if url.endswith("/missing"):
            return Response(404)
        return Response(200, {"name": "ready", "properties": {"provisioningState": "Succeeded"}})


@pytest.mark.asyncio
async def test_arm_inventory_isolates_missing_resources(monkeypatch):
    client = HttpClient()
    monkeypatch.setattr(runtime_inventory.httpx, "AsyncClient", lambda **_kwargs: client)
    service = RuntimeInventoryService(credential=Credential())

    result = await service.get_arm_resources(
        {
            "ready": ("/ready", "2025-01-01"),
            "missing": ("/missing", "2025-01-01"),
        }
    )

    assert result["ready"]["success"] is True
    assert result["ready"]["data"]["name"] == "ready"
    assert result["missing"] == {"success": False, "data": {}, "error": "HTTP 404"}
    assert len(client.requests) == 2
    assert all(request[2] == {"api-version": "2025-01-01"} for request in client.requests)


@pytest.mark.asyncio
async def test_foundry_inventory_projects_only_safe_agent_metadata(monkeypatch):
    class PromptDefinition:
        pass

    agent = SimpleNamespace(
        name="azbrief-coordinator",
        versions=SimpleNamespace(
            latest=SimpleNamespace(
                version="3",
                definition=PromptDefinition(),
                status="Ready",
            )
        ),
    )
    project = SimpleNamespace(
        agents=SimpleNamespace(list=lambda: [agent, SimpleNamespace(name="")]),
        close=lambda: None,
    )
    monkeypatch.setattr(
        "azure.ai.projects.AIProjectClient",
        lambda **_kwargs: project,
    )
    service = RuntimeInventoryService(credential=Credential())

    result = await service.list_foundry_agents("https://account.test/project")

    assert result == {
        "success": True,
        "data": {
            "azbrief-coordinator": {
                "version": "3",
                "kind": "PromptDefinition",
                "status": "Ready",
            }
        },
        "error": "",
    }


def test_inventory_closes_only_its_owned_credential(monkeypatch):
    credential = Credential()
    monkeypatch.setattr(runtime_inventory, "get_azure_credential", lambda: credential)
    owned = RuntimeInventoryService()
    owned._get_credential()

    owned.close()

    assert credential.closed is True

    external = Credential()
    RuntimeInventoryService(credential=external).close()
    assert external.closed is False


def test_inventory_initializes_owned_credential_once_across_threads(monkeypatch):
    creation_barrier = threading.Barrier(8, timeout=0.2)
    created: list[Credential] = []

    def create_credential():
        credential = Credential()
        created.append(credential)
        try:
            creation_barrier.wait()
        except threading.BrokenBarrierError:
            pass
        return credential

    monkeypatch.setattr(runtime_inventory, "get_azure_credential", create_credential)
    service = RuntimeInventoryService()

    with ThreadPoolExecutor(max_workers=8) as executor:
        credentials = list(executor.map(lambda _index: service._get_credential(), range(8)))

    assert created == [credentials[0]]
    assert all(credential is credentials[0] for credential in credentials)
