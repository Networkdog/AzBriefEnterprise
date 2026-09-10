"""Tests for the admin console surface."""

import os

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from src.admin.auth import extract_principal, require_admin
from src.admin.page import render_admin_page
from src.config import get_settings
from src.web_design import CONTROL_SURFACE_BASE_CSS
from src.web_fonts import (
    PRETENDARD_VERSION,
    WEB_FONT_CSP_SOURCE,
    WEB_FONT_FACE_CSS,
    WEB_FONT_STACK,
    WEB_FONT_URL,
)

_ADMIN_ENV = (
    "ADMIN_UI_ENABLED",
    "ADMIN_REQUIRE_AUTH",
    "ADMIN_ALLOWED_PRINCIPALS",
    "FOUNDRY_PROJECT_ENDPOINT",
    "FOUNDRY_HOSTED_AGENT_NAME",
    "FOUNDRY_COORDINATOR_AGENT_NAME",
    "FOUNDRY_RESOURCE_GRAPH_AGENT_NAME",
    "FOUNDRY_AZURE_MCP_AGENT_NAME",
    "FOUNDRY_AZURE_API_AGENT_NAME",
    "FOUNDRY_REPORT_WRITER_AGENT_NAME",
    "FOUNDRY_QUALITY_REVIEWER_AGENT_NAME",
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
    async def test_managed_object_id_can_authorize(self, monkeypatch):
        class Configuration:
            async def get_admin_principals(self):
                return {"managed-oid"}

        _configure(monkeypatch, ADMIN_UI_ENABLED="true", ADMIN_ALLOWED_PRINCIPALS="bootstrap")
        monkeypatch.setattr("src.admin.auth.get_admin_configuration", lambda: Configuration())

        principal = await require_admin(_request(**{"X_MS_CLIENT_PRINCIPAL_ID": "managed-oid"}))

        assert principal.id == "managed-oid"

    @pytest.mark.asyncio
    async def test_auth_can_be_waived_for_local_development(self, monkeypatch):
        _configure(monkeypatch, ADMIN_UI_ENABLED="true", ADMIN_REQUIRE_AUTH="false")
        principal = await require_admin(_request())
        assert principal.name == "local-development"


class TestAdminPage:
    def test_offline_preview_uses_validated_synthetic_archive_and_no_live_runs(self):
        from scripts.preview_web import create_app
        from src.archive.models import ArchiveDocumentV1, ArchivePage

        with TestClient(create_app()) as client:
            for path in ("/admin", "/archive", "/feedback"):
                response = client.get(path)
                assert response.status_code == 200
                assert "SYNTHETIC PREVIEW" in response.text
                assert "default-src 'none'" in response.headers["content-security-policy"]
            listing = ArchivePage.model_validate(client.get("/api/archive/analyses").json())
            assert len(listing.items) == 25
            assert listing.has_more
            detail = client.get("/api/archive/analyses/" + listing.items[0].archive_id).json()
            assert ArchiveDocumentV1.model_validate(detail).report_language == "en"
            assert "job_relevance" not in str(detail)
            assert (
                client.post(
                    "/api/admin/runs", json={"mode": "recent", "recent_count": 1}
                ).status_code
                == 409
            )

    def test_shared_control_surface_design_uses_accessible_light_tokens(self):
        assert "--canvas: #f4f6f7" in CONTROL_SURFACE_BASE_CSS
        assert "--surface: #ffffff" in CONTROL_SURFACE_BASE_CSS
        assert "--ink: #172126" in CONTROL_SURFACE_BASE_CSS
        assert "--primary: #0f766e" in CONTROL_SURFACE_BASE_CSS
        assert "--link: #0b64a0" in CONTROL_SURFACE_BASE_CSS
        assert "outline: 2px solid var(--focus)" in CONTROL_SURFACE_BASE_CSS
        assert "prefers-reduced-motion: reduce" in CONTROL_SURFACE_BASE_CSS

    def test_shared_shell_keeps_navigation_and_focus_usable_on_mobile(self):
        assert "flex-wrap: wrap" in CONTROL_SURFACE_BASE_CSS
        assert ".primary-nav { order: 3; width: 100%" in CONTROL_SURFACE_BASE_CSS
        assert "max-width: 44%" in CONTROL_SURFACE_BASE_CSS
        assert "textarea:focus-visible" in CONTROL_SURFACE_BASE_CSS
        assert "animation: none !important" in CONTROL_SURFACE_BASE_CSS
        assert "font-variant-numeric: tabular-nums" in CONTROL_SURFACE_BASE_CSS

    def test_webfont_policy_is_pinned_and_uses_apple_local_first(self):
        assert PRETENDARD_VERSION == "1.3.9"
        assert WEB_FONT_URL == (
            "https://cdn.jsdelivr.net/npm/pretendard@1.3.9/"
            "dist/web/variable/woff2/PretendardVariable.woff2"
        )
        assert WEB_FONT_CSP_SOURCE == WEB_FONT_URL
        assert "font-display: swap" in WEB_FONT_FACE_CSS
        assert "format('woff2-variations')" in WEB_FONT_FACE_CSS
        assert WEB_FONT_STACK.index("'Apple SD Gothic Neo'") < WEB_FONT_STACK.index(
            "'Pretendard Variable'"
        )
        assert "AppleSDGothicNeo-Regular" in WEB_FONT_STACK
        assert "apple" not in WEB_FONT_URL.lower()

    def test_nonce_is_applied_to_inline_style_and_script(self):
        html = render_admin_page(nonce="N0NCE", profile="enterprise", user="admin@co.com")
        assert '<style nonce="N0NCE">' in html
        assert '<script nonce="N0NCE">' in html
        assert "__NONCE__" not in html

    def test_user_supplied_values_are_escaped(self):
        html = render_admin_page(nonce="n", profile="enterprise", user="<img src=x onerror=1>")
        assert "<img src=x" not in html
        assert "&lt;img src=x" in html

    def test_page_allows_only_the_pinned_external_webfont(self):
        html = render_admin_page(nonce="n", profile="enterprise", user="a")
        assert "http://" not in html
        assert WEB_FONT_URL in html
        assert html.count("https://") == 1
        assert "@import" not in html
        assert '<link rel="icon" href="data:image/svg+xml,' in html
        assert 'rel="stylesheet"' not in html
        assert "'Apple SD Gothic Neo'" in html
        assert "'AppleSDGothicNeo-Regular'" in html
        assert "font-display: swap" in html

    def test_archive_link_is_rendered_only_when_enabled(self):
        disabled = render_admin_page(nonce="n", profile="enterprise", user="a")
        enabled = render_admin_page(
            nonce="n",
            profile="enterprise",
            user="a",
            archive_enabled=True,
        )
        assert 'href="/archive"' not in disabled
        assert 'href="/archive"' in enabled

    def test_page_uses_shared_operations_shell(self):
        html = render_admin_page(
            nonce="n", profile="enterprise", user="admin", archive_enabled=True
        )

        assert '<a class="skip-link" href="#main-content">' in html
        assert '<header class="app-header">' in html
        assert '<span class="brand-mark" aria-hidden="true">AZ</span>' in html
        assert '<a class="nav-link" href="/admin" aria-current="page">Admin</a>' in html
        assert '<a class="nav-link" href="/archive">Archive</a>' in html
        assert '<main id="main-content">' in html
        assert '<html lang="en">' in html
        assert '<p class="page-kicker">Control plane</p><h1>Admin console</h1>' in html

    def test_page_exposes_subscriber_and_administrator_management(self):
        html = render_admin_page(nonce="n", profile="enterprise", user="admin")

        assert "main { width: 100%; max-width: 1180px; margin: 0 auto;" in html
        assert html.count('class="panel ') == 6
        assert html.count('class="panel-header"') == 6
        assert "function initializeCollapsiblePanels()" in html
        assert "document.querySelectorAll('section.panel')" in html
        assert "button.setAttribute('aria-expanded', 'true')" in html
        assert "button.setAttribute('aria-controls', body.id)" in html
        assert "body.hidden = collapsed; caption.hidden = !collapsed;" in html
        assert "initializeCollapsiblePanels(); updateRunFields();" in html
        assert html.count("setPanelCaption(") == 7
        assert "'status', data.ready" in html
        assert "setPanelCaption('run'" in html
        assert "setPanelCaption('subscriber'" in html
        assert "setPanelCaption('administrator'" in html
        assert "'schedule'," in html
        assert "setPanelCaption('updates'" in html
        assert html.count('action-surface"') == 4
        assert html.count('action-table"') == 5
        assert ".action-table th:first-child, .action-table td:first-child" in html
        assert "<thead><tr><th>Details</th><th>Run ID</th>" in html
        assert "<thead><tr><th>Actions</th><th>Email</th>" in html
        assert '<th id="runs-start-time">Started (Local)</th>' in html
        assert '<th id="schedule-run-time">Run time (Local)</th>' in html
        assert '<th id="schedule-next-time">Next run (Local)</th>' in html
        assert '<th id="updates-published-time">Published (Local)</th>' in html
        assert 'aria-labelledby="run-title"' in html
        assert '<h2 id="run-title">Manual run</h2>' in html
        assert html.index('id="updates-title"') < html.index('id="run-title"')
        assert 'aria-label="Manual run settings"' in html
        assert "runPanel.classList.contains('collapsed')" in html
        assert 'aria-labelledby="schedule-title"' in html
        assert '<form id="run-form" class="action-surface"' in html
        assert '<button id="run" type="submit">Start run</button>' in html
        assert '<button id="schedule-add" type="submit">Add schedule</button>' in html
        assert 'id="schedule-basis-local"' in html
        assert 'name="schedule-basis" value="local" checked' in html
        assert 'id="schedule-basis-utc"' in html
        assert 'id="since" step="60"' in html
        assert 'id="since-basis-local"' in html
        assert 'name="since-basis" value="local" checked' in html
        assert 'id="since-basis-utc"' in html
        assert "function dateTimeInputToUtc(value, utcBasis)" in html
        assert "const since = selectedSinceUtc(); if (since) body.since = since;" in html
        assert "function localTimeToUtc(value)" in html
        assert "body: JSON.stringify({time_utc: selectedScheduleTimeUtc()})" in html
        assert "function dateTimeTextForBasis(value, basis)" in html
        assert "function convertTimeInputs(nextBasis)" in html
        assert "dateTimeInputValue(instant, nextBasis === 'utc')" in html
        assert "function setTimeBasis(value, reload)" in html
        assert "input.checked = input.value === timeBasis" in html
        assert "Promise.allSettled([loadRuns(), loadSchedules(), loadUpdates()])" in html
        assert "displayedDateTime(run.started_at)" in html
        assert "displayedScheduleTime(schedule.time_utc, schedule.next_run_at)" in html
        assert "displayedDateTime(schedule.next_run_at)" in html
        assert "displayedDateTime(u.published_date)" in html
        assert 'id="send-email"' in html
        assert 'id="sub-cancel"' in html
        assert 'id="sub-management-groups"' in html
        assert "management_groups: commaList($('sub-management-groups').value)" in html
        assert 'id="run-detail"' in html
        assert 'id="run-summary"' in html
        assert "api('/api/admin/runs/'" in html
        assert "tableButton('View'" in html
        assert "tableButton('Select'" in html
        assert "editSubscriber(s)" in html
        assert "method: editingEmail ? 'PUT' : 'POST'" in html
        assert "send_email: $('send-email').checked" in html
        assert ".run-field[hidden] { display: none !important; }" in html
        assert "Run history" in html
        assert "Scheduled times" in html
        assert "Registered subscribers" in html
        assert "Access list" in html
        assert 'id="sub-form"' in html
        assert 'id="admin-form"' in html
        assert 'id="schedule-form"' in html
        assert 'id="run-mode"' in html
        assert 'data-run-mode="date_range"' in html
        assert 'data-run-mode="recent"' in html
        assert 'data-run-mode="update_id"' in html
        assert 'data-run-mode="update_url"' in html
        assert "/api/admin/subscribers" in html
        assert "/api/admin/administrators" in html
        assert html.count('class="table-wrap"') == 5
        assert html.count('class="compact action-table"') == 1
        assert ".table-wrap table { min-width: 720px; }" in html
        assert "td.empty { padding: 16px 11px; text-align: left; }" in html
        assert "innerHTML" not in html

    def test_workspace_navigation_filters_and_run_validation_are_wired(self):
        page = render_admin_page("nonce", "enterprise", "admin", feedback_enabled=True)

        assert 'href="/feedback"' in page
        assert 'aria-label="Console sections"' in page
        assert page.count('data-section="') == 6
        assert "window.addEventListener('hashchange'" in page
        assert "function filterTable(id)" in page
        assert "tr.dataset.status = r.status" in page
        assert "$('run-form').reportValidity()" in page
        assert "$('start-date').value > $('end-date').value" in page
        assert "Could not load data. Refresh to retry." in page
        assert 'id="ui-icon-search"' in page
        assert 'data-icon="refresh-cw"' in page
        assert 'href="/feedback"' not in render_admin_page("nonce", "enterprise", "admin")

    def test_fields_expose_requirements_placeholders_and_shared_dimensions(self):
        html = render_admin_page(nonce="n", profile="enterprise", user="admin")

        assert html.count('class="field-requirement required">Required</span>') >= 10
        assert html.count('class="field-requirement">Optional</span>') >= 6
        assert 'placeholder="Required • name@company.com"' in html
        assert 'placeholder="Optional • e.g. Cloud Architect"' in html
        assert 'placeholder="Required • numeric update ID"' in html
        assert 'placeholder="Optional • date and time"' in html
        assert 'aria-required="true"' in html
        assert "control.required = required" in html
        assert "control.disabled = !active" in html
        assert "--control-height: 40px" in CONTROL_SURFACE_BASE_CSS
        assert "--command-width: 144px" in CONTROL_SURFACE_BASE_CSS
        assert "--time-basis-width: 320px" in CONTROL_SURFACE_BASE_CSS
        assert "height: var(--control-height)" in CONTROL_SURFACE_BASE_CSS
        assert "gap: 10px; align-items: start; }" in html
        assert "max-width: var(--time-basis-width)" in html
        assert "white-space: nowrap; cursor: pointer;" in html
        assert ".action-row > button { width: var(--command-width); }" in html
        assert ".action-table button, .button-group button" in html

    def test_readiness_panel_uses_compact_rows(self):
        html = render_admin_page(nonce="n", profile="enterprise", user="admin")

        assert "#status-sections { display: grid" in html
        assert "grid-template-columns: minmax(125px, .8fr)" in html
        assert ".check-head { display: contents; }" in html
        assert ".check-state { grid-column: 3; grid-row: 1; white-space: nowrap;" in html
        assert ".check-detail { grid-column: 1 / -1; grid-row: 2; }" in html
        assert "min-height: 118px" not in html

    def test_page_renders_readiness_checklist_without_html_injection(self):
        html = render_admin_page(nonce="n", profile="enterprise", user="admin")

        assert 'id="status-summary"' in html
        assert 'id="status-sections"' in html
        assert 'id="status-refresh"' in html
        assert "checkData.ok ? 'Ready' : 'Attention'" in html
        assert "checkData.action" in html
        assert "innerHTML" not in html


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
        assert f"font-src 'self' {WEB_FONT_CSP_SOURCE}" in csp
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

    def test_status_endpoint_exposes_structured_checks_without_secrets(self, client, monkeypatch):
        from src.admin.readiness import (
            ReadinessCheck,
            ReadinessReport,
            ReadinessSection,
        )

        async def fake_readiness():
            check = ReadinessCheck(
                id="hosted_agent",
                name="Hosted Agent",
                ok=True,
                detail="azbrief-analysis-hosted · v11",
            )
            return ReadinessReport(
                ok=True,
                ready=1,
                total=1,
                checked_at="2026-09-01T00:00:00Z",
                sections=[
                    ReadinessSection(
                        id="foundry", title="Microsoft Foundry", ok=True, checks=[check]
                    )
                ],
            )

        _configure(
            monkeypatch,
            ADMIN_UI_ENABLED="true",
            ADMIN_REQUIRE_AUTH="false",
        )
        monkeypatch.setattr("src.admin.router.collect_admin_readiness", fake_readiness)
        payload = client.get("/api/admin/status").json()
        assert payload["ok"] is True
        assert payload["ready"] == payload["total"] == 1
        assert payload["sections"][0]["checks"][0]["id"] == "hosted_agent"
        serialized = str(payload).lower()
        for forbidden in ("accesskey", "api_key", "connection string=", "secret"):
            assert forbidden not in serialized

    def test_run_trigger_requires_initialized_services(self, client, monkeypatch):
        _configure(monkeypatch, ADMIN_UI_ENABLED="true", ADMIN_REQUIRE_AUTH="false")
        response = client.post("/api/admin/runs", json={"dry_run": True})
        # Without the app lifespan the orchestrator has no services registered.
        assert response.status_code in (202, 503)

    def test_admin_run_sets_archive_source(self, client, monkeypatch):
        import importlib

        from src.orchestrator import RunRecord

        captured = {}
        router_module = importlib.import_module("src.admin.router")

        def fake_start_run(
            since,
            dry_run,
            source,
            selection,
            commit_checkpoint,
            send_email,
        ):
            captured.update(
                source=source,
                selection=selection,
                commit_checkpoint=commit_checkpoint,
                send_email=send_email,
            )
            return RunRecord(
                run_id="admin-run",
                source=source,
                since=since,
                dry_run=dry_run,
                selection=selection,
                commit_checkpoint=commit_checkpoint,
                send_email=send_email,
            )

        _configure(monkeypatch, ADMIN_UI_ENABLED="true", ADMIN_REQUIRE_AUTH="false")
        monkeypatch.setattr(router_module, "start_run", fake_start_run)
        monkeypatch.setattr(
            router_module,
            "get_run_store",
            lambda: type("Store", (), {"active_count": 0})(),
        )

        response = client.post(
            "/api/admin/runs",
            json={"mode": "recent", "recent_count": 7, "dry_run": True},
        )

        assert response.status_code == 202
        assert captured["source"] == "admin_run"
        assert captured["selection"].mode == "recent"
        assert captured["selection"].recent_count == 7
        assert captured["commit_checkpoint"] is False
        assert captured["send_email"] is False

        response = client.post(
            "/api/admin/runs",
            json={
                "mode": "update_id",
                "update_id": "12345",
                "send_email": True,
            },
        )
        assert response.status_code == 202
        assert captured["send_email"] is True

    @pytest.mark.parametrize(
        "payload",
        [
            {"mode": "date_range", "start_date": "2026-09-02", "end_date": "2026-09-01"},
            {"mode": "recent", "recent_count": 101},
            {"mode": "update_id", "update_id": "not-a-number"},
            {"mode": "update_url", "update_url": "https://attacker.example/updates/1"},
            {"mode": "recent", "recent_count": 3, "update_id": "123"},
            {"mode": "recent", "recent_count": 3, "dry_run": True, "send_email": True},
        ],
    )
    def test_manual_run_rejects_invalid_or_ambiguous_selectors(self, client, monkeypatch, payload):
        _configure(monkeypatch, ADMIN_UI_ENABLED="true", ADMIN_REQUIRE_AUTH="false")

        assert client.post("/api/admin/runs", json=payload).status_code == 422

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("subscriptions", ["not-a-guid"]),
            ("subscriptions", "11111111-1111-1111-1111-111111111111"),
            ("management_groups", ["bad value"]),
            ("resource_groups", ["rg' | take 1"]),
        ],
    )
    def test_subscriber_rejects_invalid_hierarchy_scope(self, client, monkeypatch, field, value):
        _configure(monkeypatch, ADMIN_UI_ENABLED="true", ADMIN_REQUIRE_AUTH="false")
        payload = {
            "email": "scope@example.com",
            "name": "Scope",
            field: value,
        }

        assert client.post("/api/admin/subscribers", json=payload).status_code == 422

    def test_full_resource_group_id_derives_parent_subscription_in_admin_payload(
        self, client, monkeypatch
    ):
        from src.config import Subscriber

        parent = "11111111-1111-1111-1111-111111111111"

        class Configuration:
            configured = True

            async def add_subscriber(self, subscriber):
                return subscriber

            async def get_subscribers(self):
                return [
                    Subscriber(
                        email="scope@example.com",
                        name="Scope",
                        resource_groups=[f"/subscriptions/{parent}/resourceGroups/Production-RG"],
                    )
                ]

        _configure(monkeypatch, ADMIN_UI_ENABLED="true", ADMIN_REQUIRE_AUTH="false")
        monkeypatch.setattr(
            "src.admin.router.get_admin_configuration",
            lambda: Configuration(),
        )

        response = client.post(
            "/api/admin/subscribers",
            json={
                "email": "scope@example.com",
                "name": "Scope",
                "resource_groups": [f"/subscriptions/{parent}/resourceGroups/Production-RG"],
            },
        )

        assert response.status_code == 201
        assert response.json()["subscriptions"] == [parent]

    def test_full_resource_group_id_rejects_conflicting_subscription(self, client, monkeypatch):
        _configure(monkeypatch, ADMIN_UI_ENABLED="true", ADMIN_REQUIRE_AUTH="false")

        response = client.post(
            "/api/admin/subscribers",
            json={
                "email": "scope@example.com",
                "name": "Scope",
                "subscriptions": ["22222222-2222-2222-2222-222222222222"],
                "resource_groups": [
                    "/subscriptions/11111111-1111-1111-1111-111111111111/"
                    "resourceGroups/Production-RG"
                ],
            },
        )

        assert response.status_code == 422

    def test_subscriber_and_administrator_crud(self, client, monkeypatch):
        import importlib

        from src.config import Subscriber

        class Configuration:
            configured = True

            def __init__(self):
                self.subscribers = []
                self.administrators = {"bootstrap"}

            async def get_subscribers(self):
                return self.subscribers

            async def get_managed_subscriber_emails(self):
                return {subscriber.email for subscriber in self.subscribers}

            async def add_subscriber(self, subscriber):
                self.subscribers.append(subscriber)
                return subscriber

            async def update_subscriber(self, email, subscriber):
                self.subscribers = [
                    subscriber if item.email == email else item for item in self.subscribers
                ]
                return subscriber

            async def remove_subscriber(self, email):
                self.subscribers = [item for item in self.subscribers if item.email != email]

            async def get_admin_principals(self):
                return self.administrators

            async def get_managed_admin_principals(self):
                return self.administrators - {"bootstrap"}

            async def add_admin(self, principal):
                self.administrators.add(principal)
                return principal

            async def remove_admin(self, principal):
                self.administrators.remove(principal)

        configuration = Configuration()
        router_module = importlib.import_module("src.admin.router")
        _configure(monkeypatch, ADMIN_UI_ENABLED="true", ADMIN_REQUIRE_AUTH="false")
        monkeypatch.setattr(router_module, "get_admin_configuration", lambda: configuration)

        subscriber = client.post(
            "/api/admin/subscribers",
            json={
                "email": "USER@example.com",
                "name": "User",
                "role": "Platform",
                "language": "en",
                "alert_level": "important_and_above",
                "management_groups": ["platform-mg"],
                "subscriptions": ["11111111-1111-1111-1111-111111111111"],
                "resource_groups": ["production-rg"],
            },
        )
        assert subscriber.status_code == 201
        assert subscriber.json()["email"] == "user@example.com"
        assert subscriber.json()["managed"] is True
        assert client.get("/api/admin/subscribers").json()["subscribers"] == [
            {
                **Subscriber(
                    email="user@example.com",
                    name="User",
                    role="Platform",
                    language="en",
                    alert_level="important_and_above",
                    management_groups=["platform-mg"],
                    subscriptions=["11111111-1111-1111-1111-111111111111"],
                    resource_groups=["production-rg"],
                ).model_dump(mode="json"),
                "managed": True,
            }
        ]
        updated = client.put(
            "/api/admin/subscribers/user@example.com",
            json={
                "email": "renamed@example.com",
                "name": "Renamed",
                "role": "Owner",
                "language": "ko",
                "alert_level": "critical_only",
            },
        )
        assert updated.status_code == 200
        assert updated.json()["email"] == "renamed@example.com"
        assert updated.json()["role"] == "Owner"
        assert client.delete("/api/admin/subscribers/renamed@example.com").status_code == 204

        administrator = client.post(
            "/api/admin/administrators",
            json={"principal": " Managed-OID "},
        )
        assert administrator.status_code == 201
        assert administrator.json() == {"principal": "managed-oid", "managed": True}
        administrators = client.get("/api/admin/administrators").json()["administrators"]
        assert administrators == [
            {"principal": "bootstrap", "managed": False},
            {"principal": "managed-oid", "managed": True},
        ]
        assert client.delete("/api/admin/administrators/managed-oid").status_code == 204

    def test_automatic_schedule_crud(self, client, monkeypatch):
        import importlib

        from src.admin.configuration import AutomaticRunSchedule

        class Configuration:
            configured = True

            def __init__(self):
                self.times = []

            async def get_automatic_run_schedules(self):
                return [
                    AutomaticRunSchedule(
                        key="deployment:1",
                        cron_expression="0 2 * * *",
                        time_utc="02:00",
                        managed=False,
                        next_run_at="2026-09-02T02:00:00Z",
                    )
                ]

            async def add_automatic_run_time(self, run_time):
                self.times.append(run_time)
                return AutomaticRunSchedule(
                    key="admin:1",
                    cron_expression="30 14 * * *",
                    time_utc=run_time,
                    managed=True,
                    next_run_at="2026-09-01T14:30:00Z",
                )

            async def remove_automatic_run_time(self, run_time):
                self.times.remove(run_time)

        configuration = Configuration()
        router_module = importlib.import_module("src.admin.router")
        _configure(monkeypatch, ADMIN_UI_ENABLED="true", ADMIN_REQUIRE_AUTH="false")
        monkeypatch.setattr(router_module, "get_admin_configuration", lambda: configuration)

        listed = client.get("/api/admin/schedules")
        added = client.post("/api/admin/schedules", json={"time_utc": "14:30"})
        removed = client.delete("/api/admin/schedules/14%3A30")

        assert listed.status_code == 200
        assert listed.json()["schedules"][0]["managed"] is False
        assert added.status_code == 201
        assert added.json()["time_utc"] == "14:30"
        assert removed.status_code == 204
        assert configuration.times == []
        assert client.post("/api/admin/schedules", json={"time_utc": "25:00"}).status_code == 422

    def test_management_requests_validate_input_and_prevent_self_removal(self, client, monkeypatch):
        _configure(monkeypatch, ADMIN_UI_ENABLED="true", ADMIN_REQUIRE_AUTH="false")

        assert (
            client.post(
                "/api/admin/subscribers",
                json={"email": "not-an-email", "name": "Invalid"},
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/api/admin/administrators",
                json={"principal": "a,b"},
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/api/admin/subscribers",
                json={"email": "valid@example.com", "name": "   "},
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/api/admin/administrators",
                json={"principal": "   "},
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/api/admin/administrators",
                json={"principal": "guest_user#EXT#@tenant.onmicrosoft.com"},
            ).status_code
            == 503
        )
        assert client.delete("/api/admin/administrators/local-development").status_code == 409

    def test_security_headers_are_present(self, client):
        response = client.get("/health")
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert response.headers["X-Frame-Options"] == "DENY"


def test_admin_env_names_are_documented():
    """The template and the code must agree on the env var names."""
    template = os.path.join("infra", "azbrief-enterprise-deploy.json")
    with open(template, encoding="utf-8") as handle:
        content = handle.read()
    for name in (
        "ADMIN_UI_ENABLED",
        "ADMIN_ALLOWED_PRINCIPALS",
        "FOUNDRY_PROJECT_ENDPOINT",
        "FOUNDRY_HOSTED_AGENT_NAME",
        "SCHEDULE_CRON_EXPRESSION",
        "SCHEDULE_DISPATCH_ENABLED",
    ):
        assert f'"name": "{name}"' in content
    for removed in (
        "LLM_BACKEND",
        "AZURE_OPENAI_ENDPOINT",
        "AZURE_OPENAI_DEPLOYMENT_NAME",
        "FOUNDRY_PRIMARY_AGENT_NAME",
        "FOUNDRY_ENRICHMENT_AGENTS",
    ):
        assert f'"name": "{removed}"' not in content
