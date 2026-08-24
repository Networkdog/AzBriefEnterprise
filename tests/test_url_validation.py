"""Tests for URL validation in FastAPI endpoints."""

import pytest

from src.main import AnalyzeRequest


class TestUrlValidation:
    """Test SSRF prevention URL validation."""

    def test_valid_azure_url(self):
        """Valid Azure update URL is accepted."""
        req = AnalyzeRequest(update_url="https://azure.microsoft.com/updates?id=123456")
        assert req.update_url == "https://azure.microsoft.com/updates?id=123456"

    def test_valid_www_microsoft_url(self):
        """www.microsoft.com URLs are accepted."""
        req = AnalyzeRequest(
            update_url="https://www.microsoft.com/releasecommunications/api/v2/azure/123"
        )
        assert "microsoft.com" in req.update_url

    def test_valid_learn_microsoft_url(self):
        """learn.microsoft.com URLs are accepted."""
        req = AnalyzeRequest(update_url="https://learn.microsoft.com/azure/updates")
        assert "learn.microsoft.com" in req.update_url

    def test_invalid_domain_rejected(self):
        """Non-Microsoft domains are rejected."""
        with pytest.raises(ValueError, match="not allowed"):
            AnalyzeRequest(update_url="https://evil.com/steal-data")

    def test_internal_ip_rejected(self):
        """Internal IPs (SSRF) are rejected."""
        with pytest.raises(ValueError):
            AnalyzeRequest(update_url="http://169.254.169.254/metadata")

    def test_localhost_rejected(self):
        """Localhost URLs are rejected."""
        with pytest.raises(ValueError):
            AnalyzeRequest(update_url="http://localhost:8080/admin")

    def test_ftp_scheme_rejected(self):
        """Non-HTTP schemes are rejected."""
        with pytest.raises(ValueError, match="http"):
            AnalyzeRequest(update_url="ftp://azure.microsoft.com/file")

    def test_javascript_scheme_rejected(self):
        """JavaScript scheme is rejected."""
        with pytest.raises(ValueError):
            AnalyzeRequest(update_url="javascript:alert(1)")
