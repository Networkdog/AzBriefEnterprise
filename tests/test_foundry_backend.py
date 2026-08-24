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


class TestCreateFoundryChatModel:
    """create_foundry_chat_model() graceful-degrade contract."""

    def test_none_without_endpoint(self):
        s = Settings(azure_tenant_id="test", llm_backend="foundry")
        assert foundry_backend.create_foundry_chat_model(s) is None


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


class TestExtractText:
    """_extract_text() handles the shapes a LangGraph node may return."""

    def test_string_passthrough(self):
        assert foundry_backend._extract_text("hello") == "hello"

    def test_dict_with_message_object(self):
        class _Msg:
            content = "enriched context"

        assert foundry_backend._extract_text({"messages": [_Msg()]}) == "enriched context"

    def test_dict_with_list_content(self):
        msg = {"content": [{"text": "a"}, {"text": "b"}]}
        assert foundry_backend._extract_text({"messages": [msg]}) == "a\nb"

    def test_unknown_shape_returns_empty(self):
        assert foundry_backend._extract_text(12345) == ""
