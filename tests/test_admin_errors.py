"""Offline coverage for the Admin failure-log reader."""

from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from azure.monitor.query import LogsQueryStatus, LogsTable
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.admin.router import router
from src.config import Settings
from src.error_logging import configure_redaction
from src.services.log_analytics import LogAnalyticsService

RUN_ID = "a" * 32
TRACE_ID = "b" * 32


def _response(rows: list[dict[str, object]]) -> SimpleNamespace:
    columns = list(rows[0]) if rows else ["TimeGenerated"]
    table = LogsTable(
        name="PrimaryResult",
        columns=columns,
        columns_types=["datetime" if key == "TimeGenerated" else "string" for key in columns],
        rows=[[item.get(key) for key in columns] for item in rows],
    )
    return SimpleNamespace(status=LogsQueryStatus.SUCCESS, tables=[table])


def _event(**overrides: object) -> dict[str, object]:
    return {
        "TimeGenerated": "2026-09-28T01:02:03Z",
        "Level": "WARNING",
        "Runtime": "hosted",
        "Event": "hosted_analysis_failed",
        "ErrorType": "TimeoutError",
        "ErrorMessage": "The upstream operation timed out.",
        "RunId": RUN_ID,
        "TraceId": TRACE_ID,
        "UpdateId": "123456",
        **overrides,
    }


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    value = Settings(
        _env_file=None,
        azure_tenant_id="00000000-0000-0000-0000-000000000000",
        admin_ui_enabled=True,
        admin_require_auth=False,
        log_analytics_workspace_id="00000000-0000-0000-0000-000000000001",
    )
    for module in ("src.admin.auth", "src.admin.router", "src.services.log_analytics"):
        monkeypatch.setattr(module + ".get_settings", lambda: value)
    monkeypatch.setattr("src.error_logging._secret_values", ())
    return value


@pytest.fixture
def sdk(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> MagicMock:
    client = MagicMock()
    client.query_workspace.return_value = _response([_event()])
    monkeypatch.setattr(LogAnalyticsService, "_get_client", lambda _: client)
    return client


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as test_client:
        yield test_client


@pytest.mark.asyncio
async def test_log_query_reads_real_sdk_string_columns(sdk: MagicMock) -> None:
    result = await LogAnalyticsService().query_logs("AzBriefFailures_CL | take 1")

    assert result["success"] is True
    assert result["data"][0]["ErrorMessage"] == "The upstream operation timed out."
    assert result["data"][0]["TimeGenerated"] == datetime(2026, 9, 28, 1, 2, 3, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_failure_query_is_bounded_and_correlates_only_run_traces(sdk: MagicMock) -> None:
    result = await LogAnalyticsService().get_failure_events(hours=24, limit=50, run_id=RUN_ID)

    assert result["success"] is True
    call = sdk.query_workspace.call_args.kwargs
    query = call["query"]
    assert "union *" not in query
    assert "ExtendedProperties" not in query
    assert "Source == 'AzBrief'" in query
    assert "ago(24h)" in query
    assert f"RunId == '{RUN_ID}'" in query
    assert "TraceId in (run_traces)" in query
    assert "isnotempty(TraceId)" in query
    assert "UpdateId in" not in query
    assert query.index("order by TimeGenerated desc") < query.index("take 51")
    assert call["timespan"] == timedelta(hours=24)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "arguments",
    [
        {"hours": 0},
        {"hours": 721},
        {"limit": 0},
        {"limit": 101},
        {"run_id": "'; union * //"},
    ],
)
async def test_failure_query_rejects_unbounded_or_injected_filters(
    sdk: MagicMock, arguments: dict[str, object]
) -> None:
    result = await LogAnalyticsService().get_failure_events(**arguments)

    assert result["success"] is False
    sdk.query_workspace.assert_not_called()


def test_admin_failure_history_returns_timestamped_redacted_projection(
    client: TestClient, sdk: MagicMock
) -> None:
    configure_redaction({"api_key": "configured-private-key"})
    sdk.query_workspace.return_value = _response(
        [
            _event(
                ErrorMessage=(
                    "Timeout; configured-private-key; Bearer abc.def.ghi; user@example.com "
                    "https://service.example/path?sig=private-sas"
                ),
                ExtendedProperties='{"body":"private evidence","exception":"private stack"}',
                Message="private payload",
            )
        ]
    )

    response = client.get("/api/admin/errors")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    payload = response.json()
    assert payload["period_hours"] == 24
    assert payload["has_more"] is False
    entry = payload["events"][0]
    assert entry["occurred_at"] == "2026-09-28T01:02:03Z"
    assert entry["message"].startswith("Timeout;")
    assert entry["error_type"] == "TimeoutError"
    assert entry["trace_id"] == TRACE_ID
    assert entry["run_id"] == RUN_ID
    for private in (
        "configured-private-key",
        "abc.def.ghi",
        "user@example.com",
        "private-sas",
        "private evidence",
        "private stack",
        "private payload",
        "ExtendedProperties",
    ):
        assert private not in response.text


def test_admin_failure_history_survives_an_empty_local_run_registry(
    client: TestClient, sdk: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = MagicMock()
    store.get.return_value = None
    store.recent.return_value = []
    monkeypatch.setattr("src.admin.router.get_run_store", lambda: store)

    response = client.get("/api/admin/errors", params={"run_id": RUN_ID})

    assert response.status_code == 200
    assert response.json()["events"][0]["run_id"] == RUN_ID
    store.get.assert_not_called()
    store.recent.assert_not_called()


def test_admin_failure_history_marks_more_records_and_bounds_messages(
    client: TestClient, sdk: MagicMock
) -> None:
    sdk.query_workspace.return_value = _response([_event(ErrorMessage="x" * 5000)] * 3)

    payload = client.get("/api/admin/errors?limit=2").json()

    assert len(payload["events"]) == 2
    assert payload["has_more"] is True
    assert len(payload["events"][0]["message"]) <= 2000
    assert payload["events"][0]["message"].endswith("[TRUNCATED]")


def test_admin_failure_history_reports_a_genuinely_empty_result(
    client: TestClient, sdk: MagicMock
) -> None:
    sdk.query_workspace.return_value = _response([])

    response = client.get("/api/admin/errors")

    assert response.status_code == 200
    assert response.json()["events"] == []
    assert response.json()["has_more"] is False


def test_admin_failure_history_reports_missing_configuration(
    client: TestClient, sdk: MagicMock, settings: Settings
) -> None:
    settings.log_analytics_workspace_id = None

    response = client.get("/api/admin/errors")

    assert response.status_code == 503
    assert "LOG_ANALYTICS_WORKSPACE_ID" in response.json()["detail"]
    sdk.query_workspace.assert_not_called()


@pytest.mark.parametrize("partial", [False, True])
def test_admin_failure_history_never_turns_query_failure_into_empty_success(
    client: TestClient, sdk: MagicMock, partial: bool
) -> None:
    if partial:
        sdk.query_workspace.return_value = SimpleNamespace(
            status=LogsQueryStatus.PARTIAL,
            partial_data=[_response([_event()]).tables[0]],
            partial_error=SimpleNamespace(message="private query diagnostics"),
        )
    else:
        sdk.query_workspace.side_effect = RuntimeError("private query diagnostics")

    response = client.get("/api/admin/errors")

    assert response.status_code == 502
    assert "private query diagnostics" not in response.text
    assert "events" not in response.json()


@pytest.mark.parametrize("timestamp", ["not-a-date", "2026-09-28T01:02:03"])
def test_admin_failure_history_rejects_invalid_or_ambiguous_event_times(
    client: TestClient, sdk: MagicMock, timestamp: str
) -> None:
    sdk.query_workspace.return_value = SimpleNamespace(
        status=LogsQueryStatus.SUCCESS,
        tables=[
            SimpleNamespace(
                columns=list(_event()), rows=[list(_event(TimeGenerated=timestamp).values())]
            )
        ],
    )

    response = client.get("/api/admin/errors")

    assert response.status_code == 502
    assert "events" not in response.json()


@pytest.mark.parametrize(
    "query",
    ["hours=0", "hours=721", "limit=101", "limit=0", "run_id=invalid", "run_id=%27%3Bunion"],
)
def test_admin_failure_history_validates_filters_before_querying(
    client: TestClient, sdk: MagicMock, query: str
) -> None:
    assert client.get("/api/admin/errors?" + query).status_code == 422
    sdk.query_workspace.assert_not_called()


@pytest.mark.parametrize("status", [404, 401, 403])
def test_admin_failure_history_requires_enabled_authenticated_admin(
    client: TestClient,
    sdk: MagicMock,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    status: int,
) -> None:
    settings.admin_require_auth = True
    settings.admin_ui_enabled = status != 404
    headers = {}
    if status == 403:
        headers = {"X-MS-CLIENT-PRINCIPAL-ID": "not-an-admin"}
        configuration = SimpleNamespace(get_admin_principals=AsyncMock(return_value=set()))
        monkeypatch.setattr("src.admin.auth.get_admin_configuration", lambda: configuration)

    assert client.get("/api/admin/errors", headers=headers).status_code == status
    sdk.query_workspace.assert_not_called()
