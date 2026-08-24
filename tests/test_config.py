"""Tests for configuration management in src/config.py."""

import json
import os

import pytest

from src.config import LLM_ROLES, Settings, Subscriber


class TestLlmProfile:
    """Per-role LLM deployment resolution."""

    @pytest.fixture(autouse=True)
    def _isolate_env(self, monkeypatch):
        # Settings reads AZURE_OPENAI_* from the developer's real environment,
        # which would otherwise leak into the role-fallback assertions.
        for key in list(os.environ):
            if key.upper().startswith("AZURE_OPENAI_"):
                monkeypatch.delenv(key, raising=False)

    def _settings(self, **overrides) -> Settings:
        base = {
            "azure_tenant_id": "00000000-0000-0000-0000-000000000000",
            "azure_openai_endpoint": "https://main.openai.azure.com",
            "azure_openai_api_key": "main-key",
            "azure_openai_api_version": "2024-02-15-preview",
            "azure_openai_deployment_name": "gpt-main",
        }
        base.update(overrides)
        # _env_file=None keeps the repo's .env out of the fixture.
        return Settings(_env_file=None, **base)

    def test_primary_uses_main_fields(self):
        profile = self._settings().llm_profile("primary")
        assert profile["deployment"] == "gpt-main"
        assert profile["endpoint"] == "https://main.openai.azure.com"
        assert profile["api_key"] == "main-key"

    def test_unset_role_falls_back_to_primary(self):
        # An unconfigured role must behave exactly like the primary model.
        settings = self._settings()
        assert settings.llm_profile("fast") == settings.llm_profile("primary")
        assert settings.llm_profile("codex") == settings.llm_profile("primary")

    def test_fast_role_uses_its_own_deployment(self):
        # Regression: the azure_openai_fast_* fields were documented and
        # configurable but never read, so the fast model silently ran on the
        # primary deployment.
        profile = self._settings(azure_openai_fast_deployment_name="gpt-mini").llm_profile("fast")
        assert profile["deployment"] == "gpt-mini"
        assert profile["endpoint"] == "https://main.openai.azure.com"
        assert profile["api_key"] == "main-key"

    def test_role_can_override_every_field(self):
        profile = self._settings(
            azure_openai_codex_endpoint="https://codex.openai.azure.com",
            azure_openai_codex_api_key="codex-key",
            azure_openai_codex_api_version="2025-01-01-preview",
            azure_openai_codex_deployment_name="gpt-codex",
        ).llm_profile("codex")
        assert profile == {
            "endpoint": "https://codex.openai.azure.com",
            "api_key": "codex-key",
            "api_version": "2025-01-01-preview",
            "deployment": "gpt-codex",
        }

    def test_partial_override_inherits_the_rest(self):
        profile = self._settings(azure_openai_codex_deployment_name="gpt-codex").llm_profile(
            "codex"
        )
        assert profile["deployment"] == "gpt-codex"
        assert profile["endpoint"] == "https://main.openai.azure.com"
        assert profile["api_version"] == "2024-02-15-preview"

    def test_unknown_role_rejected(self):
        with pytest.raises(ValueError, match="Unknown LLM role"):
            self._settings().llm_profile("writer")

    def test_all_declared_roles_resolve(self):
        settings = self._settings()
        for role in LLM_ROLES:
            assert settings.llm_profile(role)["deployment"]


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
        import os

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
