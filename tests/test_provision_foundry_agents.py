"""Tests for the Foundry agent provisioning script.

The script talks to a live project, so these cover the parts that must be right
before any network call: instructions derived from the runtime prompts, roster
resolution against Foundry settings, and per-agent fault isolation.
"""

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
    resolve_roster,
    resolve_runtime_roster,
    runtime_skill_instructions,
    runtime_skill_names,
    stage_instructions,
    validate_roster,
)
from src.agent.foundry_backend import (
    ENRICHMENT_LOCAL_TOOL_NAMES,
    build_stage_text_options,
)
from src.config import FOUNDRY_AGENT_STAGES, LLM_ROLES, get_settings

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
    definition.text = build_stage_text_options(purpose)
    latest = MagicMock(version=version, definition=definition)
    agent = MagicMock()
    agent.name = name
    agent.versions.latest = latest
    return agent


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    for key in (
        "FOUNDRY_ENRICHMENT_AGENTS",
        "FOUNDRY_PROJECT_ENDPOINT",
        "FOUNDRY_MODEL_DEPLOYMENT",
        "FOUNDRY_PRIMARY_AGENT_NAME",
        "FOUNDRY_PLANNER_AGENT_NAME",
        "FOUNDRY_EVALUATOR_AGENT_NAME",
        "FOUNDRY_REPORTER_AGENT_NAME",
        "FOUNDRY_CODEX_AGENT_NAME",
        "FOUNDRY_FAST_AGENT_NAME",
        "AZURE_MCP_SERVER_URL",
        "AZURE_MCP_PROJECT_CONNECTION_NAME",
    ):
        monkeypatch.setenv(key, "")
    monkeypatch.setenv("FOUNDRY_RESEARCH_WEB_SEARCH_ENABLED", "false")
    monkeypatch.setenv("AZURE_TENANT_ID", _TENANT)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class TestStageInstructions:
    def test_every_stage_has_instructions(self):
        for stage in FOUNDRY_AGENT_STAGES:
            assert len(stage_instructions(stage)) > 100

    def test_runtime_placeholders_are_stripped(self):
        """A standing instruction must not carry the per-run template holes."""
        for stage in FOUNDRY_AGENT_STAGES:
            text = stage_instructions(stage)
            assert "{update_context}" not in text
            assert "{prior_findings}" not in text
            assert "Azure Update under analysis" not in text

    def test_instructions_are_a_prefix_of_the_runtime_prompt(self):
        """Derived, not duplicated — the two can never drift."""
        from src.agent.foundry_backend import STAGE_PROMPTS

        for stage in FOUNDRY_AGENT_STAGES:
            assert STAGE_PROMPTS[stage].startswith(stage_instructions(stage))

    def test_every_repository_skill_is_assigned_to_an_agent(self):
        expected = {
            "azure-service-integration",
            "email-template",
            "foundry-agent-architecture",
            "kql-resource-graph",
            "language-naturalness",
            "report-evaluation",
            "report-quality",
        }
        assigned = {
            skill
            for purpose in (*LLM_ROLES, *FOUNDRY_AGENT_STAGES)
            for skill in runtime_skill_names(purpose)
        }
        assert assigned == expected

    @pytest.mark.parametrize(
        ("purpose", "required", "excluded"),
        [
            ("research", "foundry-agent-architecture", "kql-resource-graph"),
            ("impact", "kql-resource-graph", "email-template"),
            ("action", "report-quality", "report-evaluation"),
            ("review", "report-evaluation", "email-template"),
            ("planner", "azure-service-integration", "language-naturalness"),
            ("evaluator", "report-evaluation", "email-template"),
            ("reporter", "email-template", "kql-resource-graph"),
            ("codex", "kql-resource-graph", "report-quality"),
            ("fast", "language-naturalness", "azure-service-integration"),
        ],
    )
    def test_skill_guidance_is_role_scoped(self, purpose, required, excluded):
        instructions = runtime_skill_instructions(purpose)
        assert f"### Skill: {required}" in instructions
        assert f"### Skill: {excluded}" not in instructions

    def test_primary_carries_compact_fallback_guidance(self):
        instructions = runtime_skill_instructions("primary")
        assert len(runtime_skill_names("primary")) == 7
        assert len(instructions) < 6_000

    def test_runtime_guidance_excludes_developer_procedures(self):
        for purpose in (*LLM_ROLES, *FOUNDRY_AGENT_STAGES):
            instructions = runtime_skill_instructions(purpose)
            assert "python -m" not in instructions
            assert "src/" not in instructions
            assert "tests/" not in instructions
            assert "apply_patch" not in instructions


class TestRosterResolution:
    def test_defaults_to_all_four_stages(self):
        roster = resolve_roster(None)
        assert [stage for _, stage in roster] == list(FOUNDRY_AGENT_STAGES)
        assert [name for name, _ in roster] == [f"azbrief-{s}" for s in FOUNDRY_AGENT_STAGES]

    def test_configured_names_win(self, monkeypatch):
        """Provisioned names must match what the running app looks up."""
        monkeypatch.setenv(
            "FOUNDRY_ENRICHMENT_AGENTS",
            '[{"name":"acme-research","stage":"research"}]',
        )
        get_settings.cache_clear()
        names = dict((stage, name) for name, stage in resolve_roster(None))
        assert names["research"] == "acme-research"
        assert names["impact"] == "azbrief-impact"  # unconfigured stage keeps the default

    def test_subset_of_stages(self):
        assert [s for _, s in resolve_roster(["impact"])] == ["impact"]

    def test_primary_runtime_agent_is_required_by_configuration(self, monkeypatch):
        monkeypatch.setenv("FOUNDRY_PRIMARY_AGENT_NAME", "azbrief-primary")
        get_settings.cache_clear()
        assert resolve_runtime_roster(None) == [("azbrief-primary", "primary")]

    def test_distinct_runtime_agents_are_provisioned(self, monkeypatch):
        monkeypatch.setenv("FOUNDRY_PRIMARY_AGENT_NAME", "azbrief-primary")
        monkeypatch.setenv("FOUNDRY_PLANNER_AGENT_NAME", "azbrief-planner")
        monkeypatch.setenv("FOUNDRY_EVALUATOR_AGENT_NAME", "azbrief-evaluator")
        monkeypatch.setenv("FOUNDRY_REPORTER_AGENT_NAME", "azbrief-reporter")
        monkeypatch.setenv("FOUNDRY_CODEX_AGENT_NAME", "azbrief-codex")
        monkeypatch.setenv("FOUNDRY_FAST_AGENT_NAME", "azbrief-fast")
        get_settings.cache_clear()
        assert resolve_runtime_roster(None) == [
            ("azbrief-primary", "primary"),
            ("azbrief-planner", "planner"),
            ("azbrief-evaluator", "evaluator"),
            ("azbrief-reporter", "reporter"),
            ("azbrief-codex", "codex"),
            ("azbrief-fast", "fast"),
        ]

    def test_every_runtime_role_has_standing_instructions(self):
        for role in LLM_ROLES:
            assert len(agent_instructions(role)) > 100


class TestProvision:
    def test_without_endpoint_it_refuses(self, monkeypatch, capsys):
        monkeypatch.setattr(
            "scripts.provision_foundry_agents._client",
            lambda _endpoint: (_ for _ in ()).throw(
                AssertionError("missing endpoint must not create a live client")
            ),
        )
        assert provision([("a", "research")], "gpt-4o", dry_run=False, delete=False) == 1
        assert "FOUNDRY_PROJECT_ENDPOINT" in capsys.readouterr().out

    def test_dry_run_works_without_an_endpoint(self, capsys):
        """Reviewing the instructions must not require a provisioned project."""
        assert provision([("a", "research")], "gpt-4o", dry_run=True, delete=False) == 0
        assert "Dry run" in capsys.readouterr().out

    def test_dry_run_makes_no_client(self, monkeypatch, capsys):
        monkeypatch.setenv("FOUNDRY_PROJECT_ENDPOINT", _ENDPOINT)
        get_settings.cache_clear()

        def _boom(_endpoint):
            raise AssertionError("dry run must not build a client")

        monkeypatch.setattr("scripts.provision_foundry_agents._client", _boom)
        assert provision([("a", "research")], "gpt-4o", dry_run=True, delete=False) == 0
        assert "Dry run" in capsys.readouterr().out

    def test_creates_missing_and_updates_existing(self, monkeypatch):
        monkeypatch.setenv("FOUNDRY_PROJECT_ENDPOINT", _ENDPOINT)
        get_settings.cache_clear()

        existing = _agent("azbrief-impact", "impact", model="old-model")
        client = MagicMock()
        client.list_agents.return_value = [existing]
        client.create_version.side_effect = [
            MagicMock(version="1"),
            MagicMock(version="2"),
        ]
        monkeypatch.setattr("scripts.provision_foundry_agents._client", lambda _e: client)

        code = provision(
            [("azbrief-research", "research"), ("azbrief-impact", "impact")],
            "gpt-4o",
            dry_run=False,
            delete=False,
        )
        assert code == 0
        assert client.create_version.call_count == 2
        create_call, update_call = client.create_version.call_args_list
        assert create_call.args[:3] == (
            "azbrief-research",
            "gpt-4o",
            agent_instructions("research"),
        )
        assert update_call.args[:3] == (
            "azbrief-impact",
            "gpt-4o",
            agent_instructions("impact"),
        )
        assert update_call.kwargs["previous_definition"] is existing.versions.latest.definition
        assert {
            tool.name for tool in create_call.kwargs["managed_tools"] if getattr(tool, "name", None)
        } == (ENRICHMENT_LOCAL_TOOL_NAMES["research"])
        assert _server_tool_key(create_call.kwargs["managed_tools"][0]) == (
            "mcp",
            "microsoft_learn",
        )
        assert {
            tool.name for tool in update_call.kwargs["managed_tools"] if getattr(tool, "name", None)
        } == (ENRICHMENT_LOCAL_TOOL_NAMES["impact"])
        assert create_call.kwargs["managed_text"].as_dict() == (
            build_stage_text_options("research").as_dict()
        )
        assert update_call.kwargs["managed_text"].as_dict() == (
            build_stage_text_options("impact").as_dict()
        )

    def test_current_version_is_not_duplicated(self, monkeypatch, capsys):
        monkeypatch.setenv("FOUNDRY_PROJECT_ENDPOINT", _ENDPOINT)
        get_settings.cache_clear()
        current = _agent("azbrief-primary", "primary")
        client = MagicMock()
        client.list_agents.return_value = [current]
        monkeypatch.setattr("scripts.provision_foundry_agents._client", lambda _e: client)

        assert provision([("azbrief-primary", "primary")], "gpt-4o", False, False) == 0
        client.create_version.assert_not_called()
        assert "current azbrief-primary (version 1)" in capsys.readouterr().out

    def test_one_failure_does_not_abort_the_rest(self, monkeypatch, capsys):
        monkeypatch.setenv("FOUNDRY_PROJECT_ENDPOINT", _ENDPOINT)
        get_settings.cache_clear()

        client = MagicMock()
        client.list_agents.return_value = []
        client.create_version.side_effect = [RuntimeError("quota"), MagicMock(version="1")]
        monkeypatch.setattr("scripts.provision_foundry_agents._client", lambda _e: client)

        code = provision(
            [("a", "research"), ("b", "impact")], "gpt-4o", dry_run=False, delete=False
        )
        assert code == 1  # reported
        assert client.create_version.call_count == 2  # but the second still ran
        assert "FAILED" in capsys.readouterr().out

    def test_delete_skips_absent_agents(self, monkeypatch):
        monkeypatch.setenv("FOUNDRY_PROJECT_ENDPOINT", _ENDPOINT)
        get_settings.cache_clear()

        client = MagicMock()
        present = _agent("a", "research")
        client.list_agents.return_value = [present]
        client.delete_agent.return_value = 1
        monkeypatch.setattr("scripts.provision_foundry_agents._client", lambda _e: client)

        assert provision([("a", "research"), ("b", "impact")], "gpt-4o", False, True) == 0
        client.delete_agent.assert_called_once_with("a")

    def test_rejects_one_agent_name_assigned_to_multiple_roles(self, monkeypatch, capsys):
        monkeypatch.setattr(
            "scripts.provision_foundry_agents._client",
            lambda _endpoint: (_ for _ in ()).throw(
                AssertionError("conflicting roster must not create a live client")
            ),
        )

        code = provision(
            [("shared", "primary"), ("shared", "research")],
            "gpt-4o",
            dry_run=False,
            delete=False,
        )

        assert code == 1
        assert "CONFLICT shared" in capsys.readouterr().out


class TestFoundryAdminClient:
    def test_create_version_preserves_latest_prompt_agent_configuration(self):
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
            "azbrief-research",
            "gpt-5-mini",
            "new instructions",
            previous_definition=previous,
        )

        assert result.version == "2"
        call = project.agents.create_version.call_args
        assert call.kwargs["agent_name"] == "azbrief-research"
        definition = call.kwargs["definition"]
        assert isinstance(definition, PromptAgentDefinition)
        assert definition.model == "gpt-5-mini"
        assert definition.instructions == "new instructions"
        assert definition.temperature == 0.2
        assert definition.top_p == 0.8
        assert definition.tools == [tool]
        assert definition.tool_choice == "auto"

    def test_create_version_replaces_app_functions_but_preserves_managed_tools(self):
        from azure.ai.projects.models import FunctionTool, WebSearchPreviewTool

        old_function = FunctionTool(
            name="search_azure_docs",
            parameters={"type": "object", "properties": {}},
            description="old",
            strict=False,
        )
        retired_function = FunctionTool(
            name="get_policy_compliance",
            parameters={"type": "object", "properties": {}},
            description="retired from pre-analysis",
            strict=False,
        )
        new_function = FunctionTool(
            name="search_azure_docs",
            parameters={
                "type": "object",
                "properties": {"query": {"type": "string"}},
            },
            description="new",
            strict=False,
        )
        web_search = WebSearchPreviewTool()
        previous = SimpleNamespace(
            temperature=None,
            top_p=None,
            reasoning=None,
            tools=[old_function, retired_function, web_search],
            tool_choice=None,
            text=None,
            structured_inputs=None,
        )
        project = MagicMock()
        project.agents.create_version.return_value = MagicMock(version="2")
        client = object.__new__(_FoundryAdminClient)
        client._project = project

        client.create_version(
            "azbrief-research",
            "gpt-5-mini",
            "instructions",
            previous_definition=previous,
            managed_tools=[new_function],
        )

        definition = project.agents.create_version.call_args.kwargs["definition"]
        assert len(definition.tools) == 2
        assert web_search in definition.tools
        assert new_function in definition.tools
        assert old_function not in definition.tools
        assert retired_function not in definition.tools

    def test_create_version_replaces_stage_response_format(self):
        previous = SimpleNamespace(
            temperature=None,
            top_p=None,
            reasoning=None,
            tools=[],
            tool_choice=None,
            text=build_stage_text_options("research"),
            structured_inputs=None,
        )
        project = MagicMock()
        project.agents.create_version.return_value = MagicMock(version="2")
        client = object.__new__(_FoundryAdminClient)
        client._project = project
        managed_text = build_stage_text_options("impact")

        client.create_version(
            "azbrief-impact",
            "gpt-5-mini",
            "instructions",
            previous_definition=previous,
            managed_text=managed_text,
        )

        definition = project.agents.create_version.call_args.kwargs["definition"]
        assert definition.text.as_dict() == managed_text.as_dict()

    def test_delete_agent_removes_every_version(self):
        project = MagicMock()
        project.agents.list_versions.return_value = [
            SimpleNamespace(version="1"),
            SimpleNamespace(version="2"),
        ]
        client = object.__new__(_FoundryAdminClient)
        client._project = project

        assert client.delete_agent("azbrief-primary") == 2
        assert project.agents.delete_version.call_args_list == [
            call(
                agent_name="azbrief-primary",
                agent_version="1",
                force=True,
            ),
            call(
                agent_name="azbrief-primary",
                agent_version="2",
                force=True,
            ),
        ]

    def test_close_releases_project_and_credential(self):
        client = object.__new__(_FoundryAdminClient)
        client._project = MagicMock()
        client._credential = MagicMock()

        client.close()

        client._project.close.assert_called_once_with()
        client._credential.close.assert_called_once_with()


class TestManagedServerTools:
    def test_research_orders_learn_before_web_search(self, monkeypatch):
        monkeypatch.setenv("FOUNDRY_RESEARCH_WEB_SEARCH_ENABLED", "true")
        get_settings.cache_clear()

        keys = [_server_tool_key(tool) for tool in _managed_server_tools("research")]

        assert keys == [("mcp", "microsoft_learn"), ("web_search", "")]

    def test_impact_uses_configured_read_only_azure_mcp(self, monkeypatch):
        monkeypatch.setenv("AZURE_TENANT_ID", "test-tenant")
        monkeypatch.setenv("AZURE_SUBSCRIPTION_ID", "test-subscription")
        monkeypatch.setenv("AZURE_MCP_SERVER_URL", "https://mcp.example.com")
        monkeypatch.setenv("AZURE_MCP_PROJECT_CONNECTION_NAME", "azure-mcp-read-only")
        get_settings.cache_clear()

        tools = _managed_server_tools("impact")

        assert len(tools) == 1
        assert _server_tool_key(tools[0]) == ("mcp", "azure_read_only")
        assert tools[0].allowed_tools is None
        assert tools[0].require_approval == "never"
        assert "tenant `test-tenant` and subscription `test-subscription`" in (
            tools[0].server_description
        )

    def test_impact_accepts_foundry_normalized_mcp_payload(self, monkeypatch):
        monkeypatch.setenv("AZURE_TENANT_ID", "test-tenant")
        monkeypatch.setenv("AZURE_SUBSCRIPTION_ID", "test-subscription")
        monkeypatch.setenv("AZURE_MCP_SERVER_URL", "https://mcp.example.com")
        monkeypatch.setenv("AZURE_MCP_PROJECT_CONNECTION_NAME", "azure-mcp-read-only")
        get_settings.cache_clear()
        deployed_tool = SimpleNamespace(
            type="mcp",
            server_label="azure_read_only",
            as_dict=lambda: {
                "type": "mcp",
                "server_label": "azure_read_only",
                "server_url": "https://mcp.example.com/",
                "project_connection_id": "azure-mcp-read-only",
                "require_approval": "never",
                "server_description": (
                    "Read-only Azure MCP Server exposing direct resource-group, Resource Health, "
                    "and Advisor tools. Use these tools as the primary source for live tenant "
                    "evidence; there is no single `azure` proxy tool. Always pass tenant "
                    "`test-tenant` and subscription `test-subscription`."
                ),
            },
        )
        version = SimpleNamespace(
            definition=SimpleNamespace(tools=[deployed_tool]),
        )

        assert _server_tool_drift(version, "impact") == set()


class TestValidateRoster:
    def test_fails_for_missing_agent_and_required_tools(self, monkeypatch, capsys):
        monkeypatch.setenv("FOUNDRY_PROJECT_ENDPOINT", _ENDPOINT)
        get_settings.cache_clear()
        research = _agent("azbrief-research", "research")
        client = MagicMock()
        client.list_agents.return_value = [research]
        monkeypatch.setattr("scripts.provision_foundry_agents._client", lambda _e: client)

        code = validate_roster([("azbrief-primary", "primary"), ("azbrief-research", "research")])

        assert code == 1
        output = capsys.readouterr().out
        assert "MISSING azbrief-primary" in output
        assert "NO-TOOL azbrief-research" in output

    def test_passes_with_current_instructions_and_required_tools(self, monkeypatch, capsys):
        monkeypatch.setenv("FOUNDRY_PROJECT_ENDPOINT", _ENDPOINT)
        get_settings.cache_clear()
        primary = _agent("azbrief-primary", "primary")
        research = _agent(
            "azbrief-research",
            "research",
            tools=[
                *_managed_server_tools("research"),
                *[SimpleNamespace(name=name) for name in ENRICHMENT_LOCAL_TOOL_NAMES["research"]],
            ],
        )
        client = MagicMock()
        client.list_agents.return_value = [primary, research]
        monkeypatch.setattr("scripts.provision_foundry_agents._client", lambda _e: client)

        assert (
            validate_roster([("azbrief-primary", "primary"), ("azbrief-research", "research")]) == 0
        )
        assert "roster check passed" in capsys.readouterr().out

    def test_fails_when_deployed_instructions_are_stale(self, monkeypatch, capsys):
        monkeypatch.setenv("FOUNDRY_PROJECT_ENDPOINT", _ENDPOINT)
        get_settings.cache_clear()
        agent = _agent("azbrief-primary", "primary", instructions="old instructions")
        client = MagicMock()
        client.list_agents.return_value = [agent]
        monkeypatch.setattr("scripts.provision_foundry_agents._client", lambda _e: client)

        assert validate_roster([("azbrief-primary", "primary")]) == 1
        assert "STALE" in capsys.readouterr().out

    def test_fails_when_stage_response_format_is_missing(self, monkeypatch, capsys):
        monkeypatch.setenv("FOUNDRY_PROJECT_ENDPOINT", _ENDPOINT)
        get_settings.cache_clear()
        research = _agent(
            "azbrief-research",
            "research",
            tools=[SimpleNamespace(name=name) for name in ENRICHMENT_LOCAL_TOOL_NAMES["research"]],
        )
        research.versions.latest.definition.text = None
        client = MagicMock()
        client.list_agents.return_value = [research]
        monkeypatch.setattr("scripts.provision_foundry_agents._client", lambda _e: client)

        assert validate_roster([("azbrief-research", "research")]) == 1
        assert "NO-FORMAT" in capsys.readouterr().out

    def test_fails_when_retired_app_function_remains(self, monkeypatch, capsys):
        monkeypatch.setenv("FOUNDRY_PROJECT_ENDPOINT", _ENDPOINT)
        get_settings.cache_clear()
        impact = _agent(
            "azbrief-impact",
            "impact",
            tools=[
                *[SimpleNamespace(name=name) for name in ENRICHMENT_LOCAL_TOOL_NAMES["impact"]],
                SimpleNamespace(name="get_policy_compliance"),
            ],
        )
        client = MagicMock()
        client.list_agents.return_value = [impact]
        monkeypatch.setattr("scripts.provision_foundry_agents._client", lambda _e: client)

        assert validate_roster([("azbrief-impact", "impact")]) == 1
        assert "EXTRA-TOOL" in capsys.readouterr().out
