"""Tests for the Container Apps MCP control plane."""

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from mcp import Client
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Mount, Route
from starlette.testclient import TestClient

from src.agent.analyzer import AnalysisResult, RelevanceStatus
from src.archive.models import ArchiveReceipt
from src.mcp_server import MCPApiKeyMiddleware, mcp, mcp_http_app, register_mcp_services
from src.rss.parser import AzureUpdate


def _update() -> AzureUpdate:
    return AzureUpdate(
        id="update-1",
        title="Azure Update",
        description="Description",
        link="https://azure.microsoft.com/updates/update-1",
        published_date=datetime(2026, 8, 27, tzinfo=timezone.utc),
        categories=[],
        azure_services=[],
        update_type=None,
        status=None,
    )


def _result() -> AnalysisResult:
    return AnalysisResult(
        update_id="update-1",
        update_title="Azure Update",
        relevance=RelevanceStatus.RELEVANT,
        relevance_reason="Relevant",
        affected_resources=[],
        impact_summary="Impact",
        recommendations=[],
        reference_docs=[],
        should_notify=True,
    )


@pytest.mark.anyio
async def test_mcp_tools_delegate_analysis_to_registered_hosted_proxy():
    class Analyzer:
        async def analyze_update(self, update):
            assert update.id == "update-1"
            return _result()

    parser = SimpleNamespace(
        get_updates=lambda: None,
        get_update_by_url=lambda _url: None,
    )

    async def get_update_by_url(_url):
        return _update()

    parser.get_update_by_url = get_update_by_url

    class Archive:
        configured = True

        def __init__(self):
            self.sources = []

        async def archive_analysis(self, update, result, source):
            assert update.id == result.update_id
            self.sources.append(source.value)
            return ArchiveReceipt(
                archived=True,
                archive_id="8211694095999-0123456789abcdef0123456789abcdef",
                object_name="entries/item.json",
            )

    archive = Archive()
    register_mcp_services(Analyzer(), parser, archive)

    async with Client(mcp, raise_exceptions=True) as client:
        tools = (await client.list_tools()).tools
        result = await client.call_tool(
            "analyze_azure_update",
            {"update_url": "https://azure.microsoft.com/updates/update-1"},
        )

    assert {tool.name for tool in tools} == {
        "analyze_azure_update",
        "get_recent_digest_runs",
        "list_recent_azure_updates",
    }
    assert result.structured_content["update_id"] == "update-1"
    assert archive.sources == ["mcp"]


@pytest.mark.anyio
async def test_mcp_analysis_returns_tool_error_when_archive_fails():
    class Analyzer:
        async def analyze_update(self, _update):
            return _result()

    class Archive:
        configured = True

        async def archive_analysis(self, *_args):
            raise RuntimeError("archive unavailable")

    async def get_update_by_url(_url):
        return _update()

    parser = SimpleNamespace(get_update_by_url=get_update_by_url)
    register_mcp_services(Analyzer(), parser, Archive())

    async with Client(mcp, raise_exceptions=False) as client:
        result = await client.call_tool(
            "analyze_azure_update",
            {"update_url": "https://azure.microsoft.com/updates/update-1"},
        )

    assert result.is_error is True
    assert "archive unavailable" in str(result.content)


def _wrapped_app(monkeypatch, expected_key):
    async def endpoint(_request):
        return PlainTextResponse("ok")

    settings = SimpleNamespace(api_key=expected_key)
    monkeypatch.setattr("src.mcp_server.get_settings", lambda: settings)
    return MCPApiKeyMiddleware(Starlette(routes=[Route("/", endpoint, methods=["POST"])]))


def test_mcp_auth_fails_closed_without_configured_key(monkeypatch):
    with TestClient(_wrapped_app(monkeypatch, None)) as client:
        response = client.post("/")

    assert response.status_code == 503


def test_mcp_auth_rejects_missing_and_invalid_keys(monkeypatch):
    with TestClient(_wrapped_app(monkeypatch, "expected")) as client:
        missing = client.post("/")
        invalid = client.post("/", headers={"X-API-Key": "wrong"})

    assert missing.status_code == 401
    assert invalid.status_code == 403


def test_mcp_auth_accepts_valid_key(monkeypatch):
    checked = []
    monkeypatch.setattr(
        "src.mcp_server.rate_limiter.check",
        lambda request: checked.append(request.url.path),
    )
    with TestClient(_wrapped_app(monkeypatch, "expected")) as client:
        response = client.post("/", headers={"X-API-Key": "expected"})

    assert response.status_code == 200
    assert response.text == "ok"
    assert checked == ["/"]


def test_container_app_mounts_mcp_surface():
    from src.main import app

    assert any(getattr(route, "path", None) == "/mcp" for route in app.routes)


def test_mounted_streamable_http_initializes(monkeypatch):
    monkeypatch.setattr(
        "src.mcp_server.get_settings",
        lambda: SimpleNamespace(api_key="expected"),
    )
    monkeypatch.setattr("src.mcp_server.rate_limiter.check", lambda _request: None)

    @asynccontextmanager
    async def lifespan(_app):
        async with mcp.session_manager.run():
            yield

    host = Starlette(
        routes=[Mount("/mcp", app=mcp_http_app)],
        lifespan=lifespan,
    )
    with TestClient(host) as client:
        response = client.post(
            "/mcp",
            headers={
                "X-API-Key": "expected",
                "Accept": "application/json, text/event-stream",
                "MCP-Protocol-Version": "2025-11-25",
            },
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "test-client", "version": "1.0"},
                },
            },
        )

    assert response.status_code == 200
    assert response.json()["result"]["serverInfo"]["name"] == "AzBrief Enterprise"
