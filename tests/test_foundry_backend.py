"""Tests for the Microsoft Foundry backend integration.

These tests verify the backend switch and the graceful-degrade contract:
asking for Foundry without a reachable project must fall back to Azure OpenAI,
and every Foundry helper must return a safe fallback when the backend is off or
the SDK is absent.
"""

import pytest
from pydantic import ValidationError

from src.agent import foundry_backend
from src.config import FoundryMcpServer, Settings


class TestLlmBackendValidator:
    """Tests for the llm_backend field validator."""

    def test_default_is_foundry(self):
        s = Settings(azure_tenant_id="test")
        assert s.llm_backend == "foundry"

    def test_openai_accepted(self):
        s = Settings(azure_tenant_id="test", llm_backend="openai")
        assert s.llm_backend == "openai"

    def test_case_insensitive(self):
        s = Settings(azure_tenant_id="test", llm_backend="Foundry")
        assert s.llm_backend == "foundry"

    def test_invalid_backend_rejected(self):
        with pytest.raises(ValidationError, match="llm_backend"):
            Settings(azure_tenant_id="test", llm_backend="bedrock")


class TestUseFoundryProperty:
    """Tests for the use_foundry property gating."""

    def test_false_when_backend_openai(self):
        s = Settings(
            azure_tenant_id="test",
            llm_backend="openai",
            foundry_project_endpoint="https://x.services.ai.azure.com/api/projects/p",
        )
        assert s.use_foundry is False

    def test_false_when_no_endpoint(self):
        s = Settings(azure_tenant_id="test", llm_backend="foundry")
        assert s.use_foundry is False

    def test_true_when_backend_and_endpoint(self):
        s = Settings(
            azure_tenant_id="test",
            llm_backend="foundry",
            foundry_project_endpoint="https://x.services.ai.azure.com/api/projects/p",
        )
        assert s.use_foundry is True


class TestGetFoundryMcpServers:
    """Tests for FOUNDRY_MCP_SERVERS JSON parsing."""

    def test_none_returns_empty(self):
        s = Settings(azure_tenant_id="test")
        assert s.get_foundry_mcp_servers() == []

    def test_valid_json_parsed(self):
        s = Settings(
            azure_tenant_id="test",
            foundry_mcp_servers=(
                '[{"label":"azure","url":"https://h/mcp"},'
                '{"label":"learn","url":"https://learn.microsoft.com/api/mcp",'
                '"allowed_tools":["search"]}]'
            ),
        )
        servers = s.get_foundry_mcp_servers()
        assert len(servers) == 2
        assert all(isinstance(x, FoundryMcpServer) for x in servers)
        assert servers[0].label == "azure"
        assert servers[0].require_approval == "never"  # default
        assert servers[1].allowed_tools == ["search"]

    def test_malformed_json_returns_empty(self):
        s = Settings(azure_tenant_id="test", foundry_mcp_servers="{not json")
        assert s.get_foundry_mcp_servers() == []

    def test_non_list_returns_empty(self):
        s = Settings(azure_tenant_id="test", foundry_mcp_servers='{"label":"x"}')
        assert s.get_foundry_mcp_servers() == []


class TestFoundryAvailable:
    """foundry_available() must return a bool regardless of install state."""

    def test_returns_bool(self):
        assert isinstance(foundry_backend.foundry_available(), bool)


class TestBuildEnrichmentNode:
    """build_enrichment_node() must return None unless fully configured."""

    def test_none_for_openai_backend(self):
        s = Settings(azure_tenant_id="test")
        assert foundry_backend.build_enrichment_node(s) is None

    def test_none_without_agent_name(self):
        s = Settings(
            azure_tenant_id="test",
            llm_backend="foundry",
            foundry_project_endpoint="https://x.services.ai.azure.com/api/projects/p",
        )
        assert foundry_backend.build_enrichment_node(s) is None

    def test_none_when_sdk_missing(self, monkeypatch):
        monkeypatch.setattr(foundry_backend, "foundry_available", lambda: False)
        s = Settings(
            azure_tenant_id="test",
            llm_backend="foundry",
            foundry_project_endpoint="https://x.services.ai.azure.com/api/projects/p",
            foundry_enrichment_agent_name="azbrief-enrichment",
        )
        assert foundry_backend.build_enrichment_node(s) is None

    def test_returns_callable_when_configured(self, monkeypatch):
        monkeypatch.setattr(foundry_backend, "foundry_available", lambda: True)
        s = Settings(
            azure_tenant_id="test",
            llm_backend="foundry",
            foundry_project_endpoint="https://x.services.ai.azure.com/api/projects/p",
            foundry_enrichment_agent_name="azbrief-enrichment",
        )
        node = foundry_backend.build_enrichment_node(s)
        assert callable(node)

    @pytest.mark.asyncio
    async def test_node_degrades_on_failure(self, monkeypatch):
        """When the live SDK call fails, the node returns {} (state unchanged)."""
        monkeypatch.setattr(foundry_backend, "foundry_available", lambda: True)
        s = Settings(
            azure_tenant_id="test",
            llm_backend="foundry",
            foundry_project_endpoint="https://x.services.ai.azure.com/api/projects/p",
            foundry_enrichment_agent_name="azbrief-enrichment",
        )
        node = foundry_backend.build_enrichment_node(s)
        # No langchain-azure-ai installed → the inner import/call raises → {} returned.
        result = await node({"update_context": "some update"})
        assert result == {}


class _FakeText:
    def __init__(self, value: str) -> None:
        self.value = value


class _FakePart:
    def __init__(self, value: str) -> None:
        self.text = _FakeText(value)


class _FakeMessage:
    def __init__(self, role: str, *values: str) -> None:
        self.role = role
        self.content = [_FakePart(v) for v in values]


class _FakeMessages:
    def __init__(self, messages: list) -> None:
        self._messages = messages

    def list(self, thread_id: str):
        return list(self._messages)


class _FakeAgent:
    def __init__(self, name: str, agent_id: str) -> None:
        self.name = name
        self.id = agent_id


class _FakeAgentsClient:
    def __init__(self, agents: list, messages: list) -> None:
        self._agents = agents
        self.messages = _FakeMessages(messages)
        self.list_calls = 0

    def list_agents(self):
        self.list_calls += 1
        return list(self._agents)


class TestEnumName:
    """_enum_name() normalizes SDK enums and their str forms."""

    def test_dotted_str_form(self):
        assert foundry_backend._enum_name("RunStatus.COMPLETED") == "completed"

    def test_plain_str(self):
        assert foundry_backend._enum_name("completed") == "completed"

    def test_object_with_value(self):
        class _E:
            value = "MessageRole.AGENT"

        assert foundry_backend._enum_name(_E()) == "agent"


class TestLatestAgentText:
    """_latest_agent_text() returns the newest assistant text, skipping the user."""

    def test_picks_first_agent_message(self):
        client = _FakeAgentsClient(
            [],
            [
                _FakeMessage("MessageRole.AGENT", "pong"),
                _FakeMessage("MessageRole.USER", "ping"),
            ],
        )
        assert foundry_backend._latest_agent_text(client, "t1") == "pong"

    def test_joins_multiple_parts(self):
        client = _FakeAgentsClient([], [_FakeMessage("agent", "a", "b")])
        assert foundry_backend._latest_agent_text(client, "t1") == "a\nb"

    def test_returns_empty_without_agent_message(self):
        client = _FakeAgentsClient([], [_FakeMessage("MessageRole.USER", "ping")])
        assert foundry_backend._latest_agent_text(client, "t1") == ""


class TestAgentRoster:
    """_agent_roster() lists once per project endpoint and reuses the result."""

    def test_caches_per_endpoint(self):
        endpoint = "https://cache-probe.example/api/projects/p"
        foundry_backend._AGENT_ROSTER_CACHE.pop(endpoint, None)
        client = _FakeAgentsClient([_FakeAgent("azbrief-research", "asst_1")], [])
        try:
            first = foundry_backend._agent_roster(client, endpoint)
            second = foundry_backend._agent_roster(client, endpoint)
            assert first == {"azbrief-research": "asst_1"}
            assert second == first
            assert client.list_calls == 1
        finally:
            foundry_backend._AGENT_ROSTER_CACHE.pop(endpoint, None)
