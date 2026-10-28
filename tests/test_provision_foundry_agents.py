"""Tests for provisioning the AzBrief specialist Prompt Agent team."""

from types import SimpleNamespace
from unittest.mock import MagicMock, call

import pytest

from scripts.provision_foundry_agents import (
    _FoundryAdminClient,
    _managed_server_tools,
    _server_tool_drift,
    _server_tool_key,
    agent_instructions,
    provision,
    resolve_specialist_roster,
    runtime_skill_instructions,
    runtime_skill_names,
    specialist_instructions,
    validate_roster,
)
from src.agent.foundry_backend import (
    SPECIALIST_LOCAL_TOOL_NAMES,
    SPECIALIST_PROMPTS,
    build_specialist_text_options,
)
from src.config import EVIDENCE_SPECIALIST_ROLES, SPECIALIST_AGENT_ROLES, get_settings

_TENANT = "00000000-0000-0000-0000-000000000000"
_ENDPOINT = "https://r.services.ai.azure.com/api/projects/p"


def _agent(
    name: str,
    purpose: str,
    *,
    model: str = "gpt-4o",
    instructions: str | None = None,
    tools: list | None = None,
    version: str = "1",
):
    definition = MagicMock()
    definition.model = model
    definition.instructions = instructions or agent_instructions(purpose)
    definition.tools = tools or []
    definition.text = build_specialist_text_options(purpose)
    latest = MagicMock(version=version, definition=definition)
    agent = MagicMock()
    agent.name = name
    agent.versions.latest = latest
    return agent


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    for key in (
        "FOUNDRY_PROJECT_ENDPOINT",
        "FOUNDRY_MODEL_DEPLOYMENT",
        "FOUNDRY_COORDINATOR_AGENT_NAME",
        "FOUNDRY_RESOURCE_GRAPH_AGENT_NAME",
        "FOUNDRY_AZURE_MCP_AGENT_NAME",
        "FOUNDRY_AZURE_API_AGENT_NAME",
        "FOUNDRY_REPORT_WRITER_AGENT_NAME",
        "FOUNDRY_QUALITY_REVIEWER_AGENT_NAME",
        "FOUNDRY_COORDINATOR_WEB_SEARCH_ENABLED",
        "AZURE_MCP_SERVER_URL",
        "AZURE_MCP_PROJECT_CONNECTION_NAME",
    ):
        monkeypatch.setenv(key, "")
    monkeypatch.setenv("FOUNDRY_COORDINATOR_WEB_SEARCH_ENABLED", "false")
    monkeypatch.setenv("AZURE_TENANT_ID", _TENANT)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class TestSpecialistInstructions:
    def test_every_specialist_has_standing_instructions(self):
        for role in SPECIALIST_AGENT_ROLES:
            assert len(specialist_instructions(role)) > 100

    def test_dynamic_placeholders_are_stripped(self):
        for role in EVIDENCE_SPECIALIST_ROLES:
            text = specialist_instructions(role)
            assert "{update_context}" not in text
            assert "Azure Update under analysis" not in text

    def test_instructions_are_a_prefix_of_the_runtime_prompt(self):
        for role in EVIDENCE_SPECIALIST_ROLES:
            assert SPECIALIST_PROMPTS[role].startswith(specialist_instructions(role))

    def test_every_repository_skill_is_assigned_to_a_specialist(self):
        expected = {
            "azure-service-integration",
            "email-template",
            "foundry-agent-architecture",
            "kql-resource-graph",
            "language-naturalness",
            "report-evaluation",
            "report-quality",
        }
        assigned = {skill for role in SPECIALIST_AGENT_ROLES for skill in runtime_skill_names(role)}
        assert assigned == expected

    @pytest.mark.parametrize(
        ("role", "required", "excluded"),
        [
            ("coordinator", "foundry-agent-architecture", "email-template"),
            ("resource_graph", "kql-resource-graph", "report-quality"),
            ("azure_mcp", "foundry-agent-architecture", "language-naturalness"),
            ("azure_api", "azure-service-integration", "email-template"),
            ("report_writer", "email-template", "kql-resource-graph"),
            ("quality_reviewer", "report-evaluation", "email-template"),
        ],
    )
    def test_skill_guidance_is_role_scoped(self, role, required, excluded):
        instructions = runtime_skill_instructions(role)
        assert f"### Skill: {required}" in instructions
        assert f"### Skill: {excluded}" not in instructions

    def test_runtime_guidance_excludes_developer_procedures(self):
        for role in SPECIALIST_AGENT_ROLES:
            instructions = runtime_skill_instructions(role)
            assert "python -m" not in instructions
            assert "src/" not in instructions
            assert "tests/" not in instructions
            assert "apply_patch" not in instructions


class TestRosterResolution:
    def test_defaults_to_the_complete_specialist_team(self):
        roster = resolve_specialist_roster(None)
        assert [role for _, role in roster] == list(SPECIALIST_AGENT_ROLES)
        assert [name for name, _ in roster] == [
            f"azbrief-{role.replace('_', '-')}" for role in SPECIALIST_AGENT_ROLES
        ]

    def test_configured_names_win(self, monkeypatch):
        monkeypatch.setenv("FOUNDRY_COORDINATOR_AGENT_NAME", "acme-coordinator")
        monkeypatch.setenv("FOUNDRY_RESOURCE_GRAPH_AGENT_NAME", "acme-rg")
        get_settings.cache_clear()

        names = {role: name for name, role in resolve_specialist_roster(None)}

        assert names["coordinator"] == "acme-coordinator"
        assert names["resource_graph"] == "acme-rg"
        assert names["azure_mcp"] == "azbrief-azure-mcp"

    def test_subset_of_roles(self):
        assert resolve_specialist_roster(["azure_mcp"]) == [("azbrief-azure-mcp", "azure_mcp")]

    def test_all_default_names_are_distinct(self):
        names = [name for name, _ in resolve_specialist_roster(None)]
        assert len(names) == len(set(names)) == len(SPECIALIST_AGENT_ROLES)


class TestProvision:
    def test_without_endpoint_it_refuses(self, monkeypatch, capsys):
        monkeypatch.setattr(
            "scripts.provision_foundry_agents._client",
            lambda _endpoint: (_ for _ in ()).throw(
                AssertionError("missing endpoint must not create a live client")
            ),
        )
        assert provision([("a", "resource_graph")], "gpt-4o", False, False) == 1
        assert "FOUNDRY_PROJECT_ENDPOINT" in capsys.readouterr().out

    def test_dry_run_works_without_an_endpoint(self, capsys):
        assert provision([("a", "resource_graph")], "gpt-4o", True, False) == 0
        assert "Dry run" in capsys.readouterr().out

    def test_dry_run_makes_no_client(self, monkeypatch, capsys):
        monkeypatch.setenv("FOUNDRY_PROJECT_ENDPOINT", _ENDPOINT)
        get_settings.cache_clear()

        def _boom(_endpoint):
            raise AssertionError("dry run must not build a client")

        monkeypatch.setattr("scripts.provision_foundry_agents._client", _boom)
        assert provision([("a", "azure_api")], "gpt-4o", True, False) == 0
        assert "Dry run" in capsys.readouterr().out

    def test_creates_missing_and_updates_existing(self, monkeypatch):
        monkeypatch.setenv("FOUNDRY_PROJECT_ENDPOINT", _ENDPOINT)
        get_settings.cache_clear()
        existing = _agent("azbrief-azure-api", "azure_api", model="old-model")
        client = MagicMock()
        client.list_agents.return_value = [existing]
        client.create_version.side_effect = [MagicMock(version="1"), MagicMock(version="2")]
        monkeypatch.setattr("scripts.provision_foundry_agents._client", lambda _e: client)

        code = provision(
            [
                ("azbrief-resource-graph", "resource_graph"),
                ("azbrief-azure-api", "azure_api"),
            ],
            "gpt-4o",
            False,
            False,
        )

        assert code == 0
        create_call, update_call = client.create_version.call_args_list
        assert create_call.args[:3] == (
            "azbrief-resource-graph",
            "gpt-4o",
            agent_instructions("resource_graph"),
        )
        assert update_call.args[:3] == (
            "azbrief-azure-api",
            "gpt-4o",
            agent_instructions("azure_api"),
        )
        assert update_call.kwargs["previous_definition"] is existing.versions.latest.definition
        assert {tool.name for tool in create_call.kwargs["managed_tools"]} == (
            SPECIALIST_LOCAL_TOOL_NAMES["resource_graph"]
        )
        assert {tool.name for tool in update_call.kwargs["managed_tools"]} == (
            SPECIALIST_LOCAL_TOOL_NAMES["azure_api"]
        )
        assert create_call.kwargs["managed_text"].as_dict() == (
            build_specialist_text_options("resource_graph").as_dict()
        )

    def test_current_version_is_not_duplicated(self, monkeypatch, capsys):
        monkeypatch.setenv("FOUNDRY_PROJECT_ENDPOINT", _ENDPOINT)
        get_settings.cache_clear()
        current = _agent("azbrief-report-writer", "report_writer")
        client = MagicMock()
        client.list_agents.return_value = [current]
        monkeypatch.setattr("scripts.provision_foundry_agents._client", lambda _e: client)

        assert provision([("azbrief-report-writer", "report_writer")], "gpt-4o", False, False) == 0
        client.create_version.assert_not_called()
        assert "current azbrief-report-writer" in capsys.readouterr().out

    def test_one_failure_does_not_abort_the_rest(self, monkeypatch, capsys):
        monkeypatch.setenv("FOUNDRY_PROJECT_ENDPOINT", _ENDPOINT)
        get_settings.cache_clear()
        client = MagicMock()
        client.list_agents.return_value = []
        client.create_version.side_effect = [RuntimeError("quota"), MagicMock(version="1")]
        monkeypatch.setattr("scripts.provision_foundry_agents._client", lambda _e: client)

        code = provision([("a", "resource_graph"), ("b", "azure_api")], "gpt-4o", False, False)

        assert code == 1
        assert client.create_version.call_count == 2
        assert "FAILED" in capsys.readouterr().out

    def test_delete_skips_absent_agents(self, monkeypatch):
        monkeypatch.setenv("FOUNDRY_PROJECT_ENDPOINT", _ENDPOINT)
        get_settings.cache_clear()
        client = MagicMock()
        client.list_agents.return_value = [_agent("a", "resource_graph")]
        client.delete_agent.return_value = 1
        monkeypatch.setattr("scripts.provision_foundry_agents._client", lambda _e: client)

        assert provision([("a", "resource_graph"), ("b", "azure_api")], "gpt-4o", False, True) == 0
        client.delete_agent.assert_called_once_with("a")

    def test_rejects_one_agent_name_assigned_to_multiple_roles(self, monkeypatch, capsys):
        code = provision(
            [("shared", "coordinator"), ("shared", "resource_graph")],
            "gpt-4o",
            False,
            False,
        )

        assert code == 1
        assert "CONFLICT shared" in capsys.readouterr().out

    def test_rejects_case_only_duplicate_agent_names(self, capsys):
        code = provision(
            [("Shared", "coordinator"), ("shared", "resource_graph")],
            "gpt-4o",
            False,
            False,
        )

        assert code == 1
        assert "CONFLICT Shared/shared" in capsys.readouterr().out

    def test_azure_mcp_provision_requires_server_connection(self, monkeypatch, capsys):
        monkeypatch.setenv("FOUNDRY_PROJECT_ENDPOINT", _ENDPOINT)
        get_settings.cache_clear()
        client = MagicMock()
        client.list_agents.return_value = []
        monkeypatch.setattr("scripts.provision_foundry_agents._client", lambda _e: client)

        code = provision(
            [
                ("azbrief-coordinator", "coordinator"),
                ("azbrief-azure-mcp", "azure_mcp"),
            ],
            "gpt-4o",
            False,
            False,
        )

        assert code == 1
        client.list_agents.assert_not_called()
        client.create_version.assert_not_called()
        output = capsys.readouterr().out
        assert "AZURE_MCP_SERVER_URL" in output
        assert "AZURE_MCP_PROJECT_CONNECTION_NAME" in output


class TestFoundryAdminClient:
    def test_create_version_preserves_latest_prompt_agent_model_configuration(self):
        from azure.ai.projects.models import PromptAgentDefinition, WebSearchPreviewTool

        tool = WebSearchPreviewTool()
        previous = SimpleNamespace(
            temperature=0.2,
            top_p=0.8,
            reasoning=None,
            tools=[tool],
            tool_choice="auto",
            text=None,
            structured_inputs=None,
        )
        project = MagicMock()
        project.agents.create_version.return_value = MagicMock(version="2")
        client = object.__new__(_FoundryAdminClient)
        client._project = project

        result = client.create_version(
            "azbrief-report-writer",
            "gpt-5-mini",
            "new instructions",
            previous_definition=previous,
        )

        assert result.version == "2"
        definition = project.agents.create_version.call_args.kwargs["definition"]
        assert isinstance(definition, PromptAgentDefinition)
        assert definition.model == "gpt-5-mini"
        assert definition.instructions == "new instructions"
        assert definition.temperature == 0.2
        assert definition.top_p == 0.8
        assert definition.tools == []
        assert definition.tool_choice == "auto"

    def test_create_version_replaces_app_functions_and_wrong_role_server_tools(self):
        from azure.ai.projects.models import FunctionTool, WebSearchPreviewTool

        old_function = FunctionTool(
            name="search_azure_docs",
            parameters={"type": "object", "properties": {}},
            description="retired",
            strict=False,
        )
        new_function = FunctionTool(
            name="query_azure_resources",
            parameters={"type": "object", "properties": {"query": {"type": "string"}}},
            description="new",
            strict=False,
        )
        external_function = FunctionTool(
            name="external_legacy_function",
            parameters={"type": "object", "properties": {}},
            description="not allowed by the specialist contract",
            strict=False,
        )
        web_search = WebSearchPreviewTool()
        previous = SimpleNamespace(
            temperature=None,
            top_p=None,
            reasoning=None,
            tools=[old_function, external_function, web_search],
            tool_choice=None,
            text=None,
            structured_inputs=None,
        )
        project = MagicMock()
        project.agents.create_version.return_value = MagicMock(version="2")
        client = object.__new__(_FoundryAdminClient)
        client._project = project

        client.create_version(
            "azbrief-resource-graph",
            "gpt-5-mini",
            "instructions",
            previous_definition=previous,
            managed_tools=[new_function],
        )

        definition = project.agents.create_version.call_args.kwargs["definition"]
        assert definition.tools == [new_function]

    def test_create_version_replaces_specialist_response_format(self):
        previous = SimpleNamespace(
            temperature=None,
            top_p=None,
            reasoning=None,
            tools=[],
            tool_choice=None,
            text=build_specialist_text_options("resource_graph"),
            structured_inputs=None,
        )
        project = MagicMock()
        project.agents.create_version.return_value = MagicMock(version="2")
        client = object.__new__(_FoundryAdminClient)
        client._project = project
        managed_text = build_specialist_text_options("azure_api")

        client.create_version(
            "azbrief-azure-api",
            "gpt-5-mini",
            "instructions",
            previous_definition=previous,
            managed_text=managed_text,
        )

        definition = project.agents.create_version.call_args.kwargs["definition"]
        assert definition.text.as_dict() == managed_text.as_dict()

    def test_non_evidence_specialist_clears_a_stale_response_format(self):
        previous = SimpleNamespace(
            temperature=None,
            top_p=None,
            reasoning=None,
            tools=[],
            tool_choice=None,
            text=build_specialist_text_options("resource_graph"),
            structured_inputs=None,
        )
        project = MagicMock()
        project.agents.create_version.return_value = MagicMock(version="2")
        client = object.__new__(_FoundryAdminClient)
        client._project = project

        client.create_version(
            "azbrief-report-writer",
            "gpt-5-mini",
            "instructions",
            previous_definition=previous,
            managed_text=None,
        )

        definition = project.agents.create_version.call_args.kwargs["definition"]
        assert definition.text is None

    def test_delete_agent_removes_every_version(self):
        project = MagicMock()
        project.agents.list_versions.return_value = [
            SimpleNamespace(version="1"),
            SimpleNamespace(version="2"),
        ]
        client = object.__new__(_FoundryAdminClient)
        client._project = project

        assert client.delete_agent("azbrief-resource-graph") == 2
        assert project.agents.delete_version.call_args_list == [
            call(agent_name="azbrief-resource-graph", agent_version="1", force=True),
            call(agent_name="azbrief-resource-graph", agent_version="2", force=True),
        ]

    def test_close_releases_project_and_credential(self):
        client = object.__new__(_FoundryAdminClient)
        client._project = MagicMock()
        client._credential = MagicMock()

        client.close()

        client._project.close.assert_called_once_with()
        client._credential.close.assert_called_once_with()


class TestManagedServerTools:
    def test_coordinator_orders_learn_before_web_search(self, monkeypatch):
        monkeypatch.setenv("FOUNDRY_COORDINATOR_WEB_SEARCH_ENABLED", "true")
        get_settings.cache_clear()

        keys = [_server_tool_key(tool) for tool in _managed_server_tools("coordinator")]

        assert keys == [("mcp", "microsoft_learn"), ("web_search", "")]

    def test_azure_mcp_specialist_uses_read_only_server(self, monkeypatch):
        monkeypatch.setenv("AZURE_TENANT_ID", "test-tenant")
        monkeypatch.setenv("AZURE_SUBSCRIPTION_ID", "test-subscription")
        monkeypatch.setenv("AZURE_MCP_SERVER_URL", "https://mcp.example.com")
        monkeypatch.setenv("AZURE_MCP_PROJECT_CONNECTION_NAME", "azure-mcp-read-only")
        get_settings.cache_clear()

        tools = _managed_server_tools("azure_mcp")

        assert len(tools) == 1
        assert _server_tool_key(tools[0]) == ("mcp", "azure_read_only")
        assert tools[0].allowed_tools is None
        assert tools[0].require_approval == "never"
        assert "tenant `test-tenant` and subscription `test-subscription`" in (
            tools[0].server_description
        )

    def test_accepts_foundry_normalized_mcp_payload(self, monkeypatch):
        monkeypatch.setenv("AZURE_TENANT_ID", "test-tenant")
        monkeypatch.setenv("AZURE_SUBSCRIPTION_ID", "test-subscription")
        monkeypatch.setenv("AZURE_MCP_SERVER_URL", "https://mcp.example.com")
        monkeypatch.setenv("AZURE_MCP_PROJECT_CONNECTION_NAME", "azure-mcp-read-only")
        get_settings.cache_clear()
        required = _managed_server_tools("azure_mcp")[0]
        payload = required.as_dict()
        payload["server_url"] = "https://mcp.example.com/"
        deployed_tool = SimpleNamespace(
            type="mcp",
            server_label="azure_read_only",
            as_dict=lambda: payload,
        )
        version = SimpleNamespace(definition=SimpleNamespace(tools=[deployed_tool]))

        assert _server_tool_drift(version, "azure_mcp") == set()


class TestValidateRoster:
    def test_fails_for_missing_agent_and_required_tools(self, monkeypatch, capsys):
        monkeypatch.setenv("FOUNDRY_PROJECT_ENDPOINT", _ENDPOINT)
        get_settings.cache_clear()
        resource_graph = _agent("azbrief-resource-graph", "resource_graph")
        client = MagicMock()
        client.list_agents.return_value = [resource_graph]
        monkeypatch.setattr("scripts.provision_foundry_agents._client", lambda _e: client)

        code = validate_roster(
            [
                ("azbrief-coordinator", "coordinator"),
                ("azbrief-resource-graph", "resource_graph"),
            ]
        )

        assert code == 1
        output = capsys.readouterr().out
        assert "MISSING azbrief-coordinator" in output
        assert "NO-TOOL azbrief-resource-graph" in output
        assert "OK      azbrief-resource-graph" not in output

    def test_passes_with_current_instructions_and_required_tools(self, monkeypatch, capsys):
        monkeypatch.setenv("FOUNDRY_PROJECT_ENDPOINT", _ENDPOINT)
        get_settings.cache_clear()
        resource_graph = _agent(
            "azbrief-resource-graph",
            "resource_graph",
            tools=[
                SimpleNamespace(name=name) for name in SPECIALIST_LOCAL_TOOL_NAMES["resource_graph"]
            ],
        )
        client = MagicMock()
        client.list_agents.return_value = [resource_graph]
        monkeypatch.setattr("scripts.provision_foundry_agents._client", lambda _e: client)

        assert validate_roster([("azbrief-resource-graph", "resource_graph")]) == 0
        assert "roster check passed" in capsys.readouterr().out

    def test_fails_when_deployed_instructions_are_stale(self, monkeypatch, capsys):
        monkeypatch.setenv("FOUNDRY_PROJECT_ENDPOINT", _ENDPOINT)
        get_settings.cache_clear()
        agent = _agent("azbrief-report-writer", "report_writer", instructions="old")
        client = MagicMock()
        client.list_agents.return_value = [agent]
        monkeypatch.setattr("scripts.provision_foundry_agents._client", lambda _e: client)

        assert validate_roster([("azbrief-report-writer", "report_writer")]) == 1
        assert "STALE" in capsys.readouterr().out

    def test_fails_when_specialist_response_format_is_missing(self, monkeypatch, capsys):
        monkeypatch.setenv("FOUNDRY_PROJECT_ENDPOINT", _ENDPOINT)
        get_settings.cache_clear()
        resource_graph = _agent(
            "azbrief-resource-graph",
            "resource_graph",
            tools=[
                SimpleNamespace(name=name) for name in SPECIALIST_LOCAL_TOOL_NAMES["resource_graph"]
            ],
        )
        resource_graph.versions.latest.definition.text = None
        client = MagicMock()
        client.list_agents.return_value = [resource_graph]
        monkeypatch.setattr("scripts.provision_foundry_agents._client", lambda _e: client)

        assert validate_roster([("azbrief-resource-graph", "resource_graph")]) == 1
        assert "NO-FORMAT" in capsys.readouterr().out

    def test_fails_when_any_function_remains_on_mcp_only_specialist(self, monkeypatch, capsys):
        from azure.ai.projects.models import FunctionTool

        monkeypatch.setenv("FOUNDRY_PROJECT_ENDPOINT", _ENDPOINT)
        monkeypatch.setenv("AZURE_MCP_SERVER_URL", "https://mcp.example.com")
        monkeypatch.setenv("AZURE_MCP_PROJECT_CONNECTION_NAME", "azure-mcp-read-only")
        get_settings.cache_clear()
        azure_mcp = _agent(
            "azbrief-azure-mcp",
            "azure_mcp",
            tools=[
                FunctionTool(
                    name="external_legacy_function",
                    parameters={"type": "object", "properties": {}},
                    description="not allowed",
                    strict=False,
                )
            ],
        )
        client = MagicMock()
        client.list_agents.return_value = [azure_mcp]
        monkeypatch.setattr("scripts.provision_foundry_agents._client", lambda _e: client)

        assert validate_roster([("azbrief-azure-mcp", "azure_mcp")]) == 1
        assert "EXTRA-TOOL" in capsys.readouterr().out

    def test_fails_when_app_server_tool_is_attached_to_wrong_role(self, monkeypatch, capsys):
        from azure.ai.projects.models import WebSearchTool

        monkeypatch.setenv("FOUNDRY_PROJECT_ENDPOINT", _ENDPOINT)
        get_settings.cache_clear()
        reviewer = _agent(
            "azbrief-quality-reviewer",
            "quality_reviewer",
            tools=[WebSearchTool()],
        )
        client = MagicMock()
        client.list_agents.return_value = [reviewer]
        monkeypatch.setattr("scripts.provision_foundry_agents._client", lambda _e: client)

        assert validate_roster([("azbrief-quality-reviewer", "quality_reviewer")]) == 1
        assert "STALE-SERVER-TOOL" in capsys.readouterr().out
