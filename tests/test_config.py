"""Tests for configuration management in src/config.py."""

import json
import os

import pytest

from src.config import SPECIALIST_AGENT_ROLES, Settings, Subscriber


class TestFoundryAgentRoles:
    """Per-role Foundry Agent Service name resolution."""

    def _settings(self, **overrides) -> Settings:
        base = {
            "azure_tenant_id": "00000000-0000-0000-0000-000000000000",
            "foundry_coordinator_agent_name": "azbrief-coordinator",
            "foundry_resource_graph_agent_name": "azbrief-resource-graph",
            "foundry_azure_mcp_agent_name": "azbrief-azure-mcp",
            "foundry_azure_api_agent_name": "azbrief-azure-api",
            "foundry_report_writer_agent_name": "azbrief-report-writer",
            "foundry_quality_reviewer_agent_name": "azbrief-quality-reviewer",
        }
        base.update(overrides)
        return Settings(_env_file=None, **base)

    def test_every_specialist_resolves_to_its_explicit_agent(self):
        settings = self._settings()
        assert settings.foundry_agent_for_role("coordinator") == "azbrief-coordinator"
        assert settings.foundry_agent_for_role("resource_graph") == "azbrief-resource-graph"
        assert settings.foundry_agent_for_role("azure_mcp") == "azbrief-azure-mcp"
        assert settings.foundry_agent_for_role("azure_api") == "azbrief-azure-api"
        assert settings.foundry_agent_for_role("report_writer") == "azbrief-report-writer"
        assert settings.foundry_agent_for_role("quality_reviewer") == ("azbrief-quality-reviewer")

    def test_unknown_role_is_rejected(self):
        with pytest.raises(ValueError, match="Unknown specialist role"):
            self._settings().foundry_agent_for_role("writer")

    def test_specialist_roster_contains_all_explicit_roles(self):
        settings = self._settings()
        assert {spec.role for spec in settings.get_foundry_specialist_agents()} == set(
            SPECIALIST_AGENT_ROLES
        )


class TestSubscriberModel:
    """Test Subscriber pydantic model."""

    def test_subscriber_defaults(self):
        sub = Subscriber(email="test@co.com", name="Test User")
        assert sub.role == ""
        assert sub.language == "ko"

    def test_subscriber_all_fields(self):
        sub = Subscriber(
            email="admin@co.com",
            name="Alice",
            role="Azure Infra Management",
            language="en",
        )
        assert sub.email == "admin@co.com"
        assert sub.language == "en"


class TestSettingsSubscribers:
    """Test subscribers JSON parsing."""

    def _make_settings_with_subscribers(self, subscribers_json):
        """Create a minimal Settings instance for subscriber testing."""
        old_env = os.environ.copy()
        try:
            os.environ["AZURE_TENANT_ID"] = "test-tenant-id"
            if subscribers_json is not None:
                os.environ["SUBSCRIBERS"] = subscribers_json
            else:
                os.environ.pop("SUBSCRIBERS", None)
            # Clear lru_cache to get fresh settings
            from src.config import Settings, get_settings

            get_settings.cache_clear()
            # Bypass .env file loading by passing env values directly
            return Settings(
                azure_tenant_id="test-tenant-id",
                subscribers=subscribers_json,
                _env_file=None,  # type: ignore[call-arg]
            )
        finally:
            os.environ.clear()
            os.environ.update(old_env)
            from src.config import get_settings

            get_settings.cache_clear()

    def test_parse_valid_subscribers_json(self):
        """Valid JSON array is parsed into Subscriber list."""
        settings = self._make_settings_with_subscribers(
            json.dumps(
                [
                    {"email": "a@co.com", "name": "Alice", "role": "Infra", "language": "ko"},
                    {"email": "b@co.com", "name": "Bob"},
                ]
            )
        )
        subs = settings.get_subscribers()
        assert len(subs) == 2
        assert subs[0].name == "Alice"
        assert subs[1].language == "ko"  # default

    def test_parse_empty_subscribers(self):
        """None or empty string returns empty list."""
        settings = self._make_settings_with_subscribers(None)
        assert settings.get_subscribers() == []

    def test_parse_invalid_json(self):
        """Invalid JSON returns empty list without crashing."""
        settings = self._make_settings_with_subscribers("not json")
        assert settings.get_subscribers() == []

    def test_parse_non_array_json(self):
        """Non-array JSON returns empty list."""
        settings = self._make_settings_with_subscribers('{"email": "test@co.com"}')
        assert settings.get_subscribers() == []

    def test_duplicate_email_deduplication(self):
        """Duplicate emails are deduplicated — last entry wins."""
        settings = self._make_settings_with_subscribers(
            json.dumps(
                [
                    {"email": "same@co.com", "name": "Alice", "role": "Infra", "language": "ko"},
                    {"email": "same@co.com", "name": "Alice-EN", "role": "Infra", "language": "en"},
                    {"email": "other@co.com", "name": "Bob"},
                ]
            )
        )
        subs = settings.get_subscribers()
        assert len(subs) == 2, "Duplicate email 'same@co.com' must be deduplicated to 1 entry"
        # Last entry wins
        same_sub = next(s for s in subs if s.email == "same@co.com")
        assert same_sub.name == "Alice-EN"
        assert same_sub.language == "en"

    def test_unique_emails_preserved(self):
        """Unique emails are all preserved."""
        settings = self._make_settings_with_subscribers(
            json.dumps(
                [
                    {"email": "a@co.com", "name": "A", "language": "ko"},
                    {"email": "b@co.com", "name": "B", "language": "en"},
                    {"email": "c@co.com", "name": "C", "language": "ja"},
                ]
            )
        )
        subs = settings.get_subscribers()
        assert len(subs) == 3
