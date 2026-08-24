"""Tests for config validators (report_language, log_level)."""

import os
from unittest.mock import patch

import pytest
from pydantic import ValidationError


class TestReportLanguageValidator:
    """Tests for report_language field validator."""

    def test_valid_languages_accepted(self):
        """ko, en, ja should all be accepted."""
        from src.config import Settings, get_settings

        get_settings.cache_clear()

        for lang in ("ko", "en", "ja"):
            with patch.dict(os.environ, {"AZURE_TENANT_ID": "test", "REPORT_LANGUAGE": lang}):
                get_settings.cache_clear()
                s = Settings(azure_tenant_id="test", report_language=lang)
                assert s.report_language == lang

    def test_invalid_language_rejected(self):
        """Unsupported language codes should raise ValidationError."""
        from src.config import Settings

        with pytest.raises(ValidationError, match="report_language"):
            Settings(azure_tenant_id="test", report_language="de")

    def test_default_is_ko(self):
        """Default report_language should be 'ko'."""
        from src.config import Settings

        s = Settings(azure_tenant_id="test")
        assert s.report_language == "ko"


class TestLogLevelValidator:
    """Tests for log_level field validator."""

    def test_valid_levels_accepted(self):
        """Standard Python log levels should be accepted."""
        from src.config import Settings

        for level in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
            s = Settings(azure_tenant_id="test", log_level=level)
            assert s.log_level == level

    def test_case_insensitive(self):
        """log_level should accept lowercase and normalize to uppercase."""
        from src.config import Settings

        s = Settings(azure_tenant_id="test", log_level="info")
        assert s.log_level == "INFO"

    def test_invalid_level_rejected(self):
        """Non-standard log levels should raise ValidationError."""
        from src.config import Settings

        with pytest.raises(ValidationError, match="log_level"):
            Settings(azure_tenant_id="test", log_level="VERBOSE")

    def test_default_is_info(self):
        """Default log_level should be 'INFO'."""
        from src.config import Settings

        s = Settings(azure_tenant_id="test")
        assert s.log_level == "INFO"
