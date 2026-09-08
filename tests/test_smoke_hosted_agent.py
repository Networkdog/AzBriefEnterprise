"""Tests for the deployed Hosted Agent smoke CLI."""

from types import SimpleNamespace

import pytest

from scripts import smoke_hosted_agent
from src.rss.parser import AzureUpdate


def _update() -> AzureUpdate:
    return AzureUpdate(
        id="update-1",
        title="Update",
        description="Description",
        link="https://azure.microsoft.com/updates?id=update-1",
        published_date=None,
        categories=[],
        azure_services=["Storage"],
        update_type="General Availability",
        status=None,
    )


def _result(reason: str = "Complete analysis") -> SimpleNamespace:
    return SimpleNamespace(
        update_id="update-1",
        relevance_reason=reason,
        relevance=SimpleNamespace(value="relevant"),
        urgency=SimpleNamespace(value="medium"),
        importance="medium",
        impact_level="low",
        affected_resources=[],
        action_items=[],
        reference_docs=[],
        _hosted_trace_id="trace-1",
    )


class _Parser:
    async def get_updates(self):
        return [_update()]


class _Analyzer:
    def __init__(self, settings, result):
        self.settings = settings
        self.result = result
        self.closed = False

    async def analyze_update(self, update):
        assert update.id == "update-1"
        return self.result

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_smoke_uses_hosted_analyzer_and_returns_summary(monkeypatch):
    settings = SimpleNamespace(foundry_hosted_agent_name="hosted-agent")
    analyzer = _Analyzer(settings, _result())
    monkeypatch.setattr(smoke_hosted_agent, "get_settings", lambda: settings)
    monkeypatch.setattr(smoke_hosted_agent, "AzureUpdateParser", _Parser)
    monkeypatch.setattr(smoke_hosted_agent, "HostedAgentAnalyzer", lambda value: analyzer)

    summary = await smoke_hosted_agent.run_smoke()

    assert summary == {
        "status": "completed",
        "hosted_agent": "hosted-agent",
        "trace_id": "trace-1",
        "update_id": "update-1",
        "relevance": "relevant",
        "urgency": "medium",
        "importance": "medium",
        "impact_level": "low",
        "affected_resources": 0,
        "action_items": 0,
        "references": 0,
    }
    assert analyzer.closed is True


@pytest.mark.asyncio
async def test_smoke_rejects_report_generation_placeholder(monkeypatch):
    settings = SimpleNamespace(foundry_hosted_agent_name="hosted-agent")
    analyzer = _Analyzer(settings, _result("Report generation failed: Error code: 429"))
    monkeypatch.setattr(smoke_hosted_agent, "get_settings", lambda: settings)
    monkeypatch.setattr(smoke_hosted_agent, "AzureUpdateParser", _Parser)
    monkeypatch.setattr(smoke_hosted_agent, "HostedAgentAnalyzer", lambda value: analyzer)

    with pytest.raises(RuntimeError, match="failure placeholder"):
        await smoke_hosted_agent.run_smoke()

    assert analyzer.closed is True
