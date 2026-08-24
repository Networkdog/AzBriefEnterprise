"""Tests for the admin console surface."""

import os

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from src.admin.auth import extract_principal, require_admin
from src.admin.page import render_admin_page
from src.config import get_settings

_ADMIN_ENV = (
    "ADMIN_UI_ENABLED",
    "ADMIN_REQUIRE_AUTH",
    "ADMIN_ALLOWED_PRINCIPALS",
    "LLM_BACKEND",
)


@pytest.fixture(autouse=True)
def _isolated_settings(monkeypatch):
    """Give every test a clean, cache-free settings environment."""
    for key in _ADMIN_ENV:
        monkeypatch.delenv(key, raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _configure(monkeypatch, **env: str) -> None:
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()


def _request(**headers: str) -> Request:
    raw = [(k.replace("_", "-").lower().encode(), v.encode()) for k, v in headers.items()]
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/admin",
            "raw_path": b"/admin",
            "query_string": b"",
            "headers": raw,
            "scheme": "https",
            "server": ("testserver", 443),
            "client": ("10.0.0.1", 5000),
        }
    )


def _principal_blob(claims: list[dict]) -> str:
    import base64
    import json

    return base64.b64encode(json.dumps({"claims": claims}).encode()).decode()


class TestExtractPrincipal:
    def test_unauthenticated_request_yields_none(self):
        assert extract_principal(_request()) is None

    def test_simple_headers_are_used(self):
        principal = extract_principal(
            _request(
                **{
                    "X_MS_CLIENT_PRINCIPAL_ID": "oid-1",
                    "X_MS_CLIENT_PRINCIPAL_NAME": "admin@co.com",
                }
            )
        )
        assert principal.id == "oid-1"
        assert principal.name == "admin@co.com"
        assert principal.identifiers == {"oid-1", "admin@co.com"}

    def test_claims_blob_is_decoded(self):
        blob = _principal_blob(
            [
                {"typ": "oid", "val": "claims-oid"},
                {"typ": "preferred_username", "val": "Claims@Co.com"},
                {"typ": "groups", "val": "group-a"},
            ]
        )
        principal = extract_principal(_request(**{"X_MS_CLIENT_PRINCIPAL": blob}))
        assert principal.id == "claims-oid"
        assert principal.name == "Claims@Co.com"
        assert "group-a" in principal.identifiers

    def test_corrupt_blob_does_not_raise(self):
        # A malformed header must look unauthenticated, never crash the route.
        assert extract_principal(_request(**{"X_MS_CLIENT_PRINCIPAL": "!!not-base64!!"})) is None


class TestRequireAdmin:
    @pytest.mark.asyncio
    async def test_disabled_console_reports_not_found(self, monkeypatch):
        # 404 rather than 403: a disabled console should not advertise itself.
        _configure(monkeypatch, ADMIN_UI_ENABLED="false")
        with pytest.raises(HTTPException) as exc:
            await require_admin(_request())
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_missing_identity_is_unauthorized(self, monkeypatch):
        _configure(monkeypatch, ADMIN_UI_ENABLED="true", ADMIN_ALLOWED_PRINCIPALS="admin@co.com")
        with pytest.raises(HTTPException) as exc:
            await require_admin(_request())
        assert exc.value.status_code == 401

    @pytest.mark.asyncio
    async def test_empty_allow_list_denies_everyone(self, monkeypatch):
        # Fail closed: an authenticated user is still not an administrator.
        _configure(monkeypatch, ADMIN_UI_ENABLED="true")
        with pytest.raises(HTTPException) as exc:
            await require_admin(_request(**{"X_MS_CLIENT_PRINCIPAL_NAME": "admin@co.com"}))
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_principal_outside_allow_list_is_forbidden(self, monkeypatch):
        _configure(monkeypatch, ADMIN_UI_ENABLED="true", ADMIN_ALLOWED_PRINCIPALS="owner@co.com")
        with pytest.raises(HTTPException) as exc:
            await require_admin(_request(**{"X_MS_CLIENT_PRINCIPAL_NAME": "intruder@co.com"}))
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_allow_list_match_is_case_insensitive(self, monkeypatch):
        _configure(monkeypatch, ADMIN_UI_ENABLED="true", ADMIN_ALLOWED_PRINCIPALS="Admin@Co.com")
        principal = await require_admin(_request(**{"X_MS_CLIENT_PRINCIPAL_NAME": "ADMIN@co.com"}))
        assert principal.name == "ADMIN@co.com"

    @pytest.mark.asyncio
    async def test_object_id_can_authorize(self, monkeypatch):
        _configure(monkeypatch, ADMIN_UI_ENABLED="true", ADMIN_ALLOWED_PRINCIPALS="oid-42")
        principal = await require_admin(_request(**{"X_MS_CLIENT_PRINCIPAL_ID": "oid-42"}))
        assert principal.id == "oid-42"

    @pytest.mark.asyncio
    async def test_auth_can_be_waived_for_local_development(self, monkeypatch):
        _configure(monkeypatch, ADMIN_UI_ENABLED="true", ADMIN_REQUIRE_AUTH="false")
        principal = await require_admin(_request())
        assert principal.name == "local-development"


class TestAdminPage:
    def test_nonce_is_applied_to_inline_style_and_script(self):
        html = render_admin_page(nonce="N0NCE", profile="enterprise", user="admin@co.com")
        assert '<style nonce="N0NCE">' in html
        assert '<script nonce="N0NCE">' in html
        assert "__NONCE__" not in html

    def test_user_supplied_values_are_escaped(self):
        html = render_admin_page(nonce="n", profile="enterprise", user="<img src=x onerror=1>")
        assert "<img src=x" not in html
        assert "&lt;img src=x" in html

    def test_page_has_no_external_references(self):
        # Keeps the console usable behind a locked-down egress policy.
        html = render_admin_page(nonce="n", profile="enterprise", user="a")
        assert "http://" not in html
        assert "https://" not in html


class TestAdminRoutes:
    @pytest.fixture
    def client(self):
        from src.main import app

        return TestClient(app)

    def test_admin_page_hidden_while_disabled(self, client, monkeypatch):
        _configure(monkeypatch, ADMIN_UI_ENABLED="false")
        assert client.get("/admin").status_code == 404

    def test_admin_api_hidden_while_disabled(self, client, monkeypatch):
        _configure(monkeypatch, ADMIN_UI_ENABLED="false")
        assert client.get("/api/admin/status").status_code == 404

    def test_admin_page_renders_with_csp(self, client, monkeypatch):
        _configure(monkeypatch, ADMIN_UI_ENABLED="true", ADMIN_REQUIRE_AUTH="false")
        response = client.get("/admin")
        assert response.status_code == 200
        csp = response.headers["Content-Security-Policy"]
        assert "default-src 'none'" in csp
        assert "frame-ancestors 'none'" in csp
        assert "nonce-" in csp

    def test_unauthenticated_browser_is_sent_to_sign_in(self, client, monkeypatch):
        # Platform auth runs in AllowAnonymous mode so the API-key path stays
        # reachable, which makes the sign-in redirect the app's own job.
        _configure(
            monkeypatch,
            ADMIN_UI_ENABLED="true",
            ADMIN_REQUIRE_AUTH="true",
            ADMIN_ALLOWED_PRINCIPALS="admin@co.com",
        )
        response = client.get("/admin", follow_redirects=False)
        assert response.status_code == 302
        assert response.headers["location"].startswith("/.auth/login/aad")

    def test_admin_json_api_returns_401_not_a_redirect(self, client, monkeypatch):
        _configure(
            monkeypatch,
            ADMIN_UI_ENABLED="true",
            ADMIN_REQUIRE_AUTH="true",
            ADMIN_ALLOWED_PRINCIPALS="admin@co.com",
        )
        assert client.get("/api/admin/status").status_code == 401

    def test_signed_in_but_unlisted_principal_is_forbidden(self, client, monkeypatch):
        _configure(
            monkeypatch,
            ADMIN_UI_ENABLED="true",
            ADMIN_REQUIRE_AUTH="true",
            ADMIN_ALLOWED_PRINCIPALS="owner@co.com",
        )
        response = client.get(
            "/admin",
            headers={"X-MS-CLIENT-PRINCIPAL-NAME": "intruder@co.com"},
            follow_redirects=False,
        )
        assert response.status_code == 403

    def test_status_endpoint_exposes_no_secrets(self, client, monkeypatch):
        _configure(
            monkeypatch,
            ADMIN_UI_ENABLED="true",
            ADMIN_REQUIRE_AUTH="false",
            LLM_BACKEND="foundry",
        )
        payload = client.get("/api/admin/status").json()
        assert payload["LLM 백엔드"] == "foundry"
        serialized = str(payload).lower()
        for forbidden in ("accesskey", "api_key", "connection string=", "secret"):
            assert forbidden not in serialized

    def test_run_trigger_requires_initialized_services(self, client, monkeypatch):
        _configure(monkeypatch, ADMIN_UI_ENABLED="true", ADMIN_REQUIRE_AUTH="false")
        response = client.post("/api/admin/runs", json={"dry_run": True})
        # Without the app lifespan the orchestrator has no services registered.
        assert response.status_code in (202, 503)

    def test_security_headers_are_present(self, client):
        response = client.get("/health")
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert response.headers["X-Frame-Options"] == "DENY"


def test_admin_env_names_are_documented():
    """The template and the code must agree on the env var names."""
    template = os.path.join("infra", "azbrief-enterprise-deploy.json")
    with open(template, encoding="utf-8") as handle:
        content = handle.read()
    for name in ("ADMIN_UI_ENABLED", "ADMIN_ALLOWED_PRINCIPALS", "LLM_BACKEND"):
        assert name in content
