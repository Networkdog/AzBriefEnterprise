"""Tests for durable Admin-managed subscribers and principals."""

import json
from copy import deepcopy
from datetime import datetime, timezone

import pytest

from src.admin.configuration import (
    AdminConfigurationEntryExistsError,
    AdminConfigurationEntryNotFoundError,
    AdminConfigurationManager,
    AdminConfigurationProtectedEntryError,
)
from src.config import Subscriber, get_settings
from src.services.admin_config import (
    AdminConfigConflictError,
    AdminConfigSnapshot,
    AdminConfigStore,
    BlobAdminConfigStore,
    _blob_url_from_checkpoint,
)


class MemoryStore(AdminConfigStore):
    configured = True

    def __init__(self):
        self.payload = {}
        self.etag = None
        self.write_count = 0
        self.conflict_once = False

    async def read(self):
        return AdminConfigSnapshot(deepcopy(self.payload), self.etag)

    async def write(self, payload, etag):
        if self.conflict_once:
            self.conflict_once = False
            self.etag = "concurrent"
            raise AdminConfigConflictError("retry")
        if etag != self.etag:
            raise AdminConfigConflictError("stale")
        self.payload = deepcopy(payload)
        self.write_count += 1
        self.etag = f"etag-{self.write_count}"
        return self.etag


class HttpResponse:
    def __init__(self, status_code, payload=None, headers=None):
        self.status_code = status_code
        self._payload = payload or {}
        self.headers = headers or {}
        self.content = json.dumps(self._payload).encode("utf-8")

    def json(self):
        return deepcopy(self._payload)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class HttpClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def get(self, url, headers):
        self.requests.append(("GET", url, headers, None))
        return self.responses.pop(0)

    async def put(self, url, headers, content):
        self.requests.append(("PUT", url, headers, content))
        return self.responses.pop(0)


@pytest.fixture(autouse=True)
def _isolated_settings(monkeypatch):
    monkeypatch.delenv("SUBSCRIBERS", raising=False)
    monkeypatch.delenv("ADMIN_ALLOWED_PRINCIPALS", raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _configure(monkeypatch, **values):
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_managed_subscribers_merge_with_bootstrap_and_are_mutable(monkeypatch):
    _configure(
        monkeypatch,
        SUBSCRIBERS='[{"email":"base@example.com","name":"Base"}]',
    )
    store = MemoryStore()
    manager = AdminConfigurationManager(store, cache_ttl_s=0)

    added = await manager.add_subscriber(
        Subscriber(
            email="Managed@Example.com",
            name="Managed",
            language="en",
            management_groups=["platform-mg"],
            subscriptions=["11111111-1111-1111-1111-111111111111"],
            resource_groups=["production-rg"],
        )
    )
    subscribers = await manager.get_subscribers()

    assert added.email == "managed@example.com"
    assert added.management_groups == ["platform-mg"]
    assert added.subscriptions == ["11111111-1111-1111-1111-111111111111"]
    assert added.resource_groups == ["production-rg"]
    assert [subscriber.email for subscriber in subscribers] == [
        "base@example.com",
        "managed@example.com",
    ]
    assert await manager.get_managed_subscriber_emails() == {"managed@example.com"}

    updated = await manager.update_subscriber(
        "MANAGED@example.com",
        Subscriber(
            email="renamed@example.com",
            name="Renamed",
            role="Platform owner",
            alert_level="critical_only",
        ),
    )
    assert updated.email == "renamed@example.com"
    assert updated.role == "Platform owner"
    assert [subscriber.email for subscriber in await manager.get_subscribers()] == [
        "base@example.com",
        "renamed@example.com",
    ]

    await manager.remove_subscriber("RENAMED@example.com")
    assert [subscriber.email for subscriber in await manager.get_subscribers()] == [
        "base@example.com"
    ]


@pytest.mark.asyncio
async def test_duplicate_and_bootstrap_subscriber_changes_are_rejected(monkeypatch):
    _configure(
        monkeypatch,
        SUBSCRIBERS='[{"email":"base@example.com","name":"Base"}]',
    )
    manager = AdminConfigurationManager(MemoryStore(), cache_ttl_s=0)

    with pytest.raises(AdminConfigurationEntryExistsError):
        await manager.add_subscriber(Subscriber(email="BASE@example.com", name="Duplicate"))
    await manager.add_subscriber(Subscriber(email="managed@example.com", name="Managed"))
    with pytest.raises(AdminConfigurationEntryExistsError):
        await manager.update_subscriber(
            "managed@example.com",
            Subscriber(email="base@example.com", name="Collision"),
        )
    with pytest.raises(AdminConfigurationEntryNotFoundError):
        await manager.update_subscriber(
            "missing@example.com",
            Subscriber(email="renamed@example.com", name="Missing"),
        )
    with pytest.raises(AdminConfigurationProtectedEntryError):
        await manager.update_subscriber(
            "base@example.com",
            Subscriber(email="base@example.com", name="Protected"),
        )
    with pytest.raises(AdminConfigurationProtectedEntryError):
        await manager.remove_subscriber("base@example.com")


@pytest.mark.asyncio
async def test_managed_admins_merge_with_bootstrap_and_are_mutable(monkeypatch):
    _configure(monkeypatch, ADMIN_ALLOWED_PRINCIPALS="bootstrap-oid")
    manager = AdminConfigurationManager(MemoryStore(), cache_ttl_s=0)

    assert await manager.add_admin(" Managed-OID ") == "managed-oid"
    assert await manager.get_admin_principals() == {"bootstrap-oid", "managed-oid"}
    assert await manager.get_managed_admin_principals() == {"managed-oid"}

    await manager.remove_admin("MANAGED-OID")
    assert await manager.get_admin_principals() == {"bootstrap-oid"}
    with pytest.raises(AdminConfigurationProtectedEntryError):
        await manager.remove_admin("bootstrap-oid")


@pytest.mark.asyncio
async def test_automatic_run_times_merge_with_protected_deployment_cron(monkeypatch):
    _configure(monkeypatch, SCHEDULE_CRON_EXPRESSION="0 2 * * *")
    manager = AdminConfigurationManager(MemoryStore(), cache_ttl_s=0)

    added = await manager.add_automatic_run_time("14:30")
    schedules = await manager.get_automatic_run_schedules(
        datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc)
    )

    assert added.time_utc == "14:30"
    assert [(item.time_utc, item.managed) for item in schedules] == [
        ("02:00", False),
        ("14:30", True),
    ]
    with pytest.raises(AdminConfigurationEntryExistsError):
        await manager.add_automatic_run_time("02:00")
    with pytest.raises(AdminConfigurationProtectedEntryError):
        await manager.remove_automatic_run_time("02:00")

    await manager.remove_automatic_run_time("14:30")
    assert [item.time_utc for item in await manager.get_automatic_run_schedules()] == ["02:00"]


@pytest.mark.asyncio
async def test_due_automatic_run_is_claimed_once_and_released(monkeypatch):
    _configure(monkeypatch, SCHEDULE_CRON_EXPRESSION="0 2 * * *")
    store = MemoryStore()
    manager = AdminConfigurationManager(store, cache_ttl_s=0)
    await manager.add_automatic_run_time("14:30")
    now = datetime(2026, 9, 1, 14, 33, tzinfo=timezone.utc)

    lease = await manager.claim_due_automatic_run(now=now)

    assert lease is not None
    assert lease.schedule_key.startswith("admin:")
    assert lease.scheduled_for == datetime(2026, 9, 1, 14, 30, tzinfo=timezone.utc)
    assert await manager.claim_due_automatic_run(now=now) is None

    await manager.release_automatic_run(lease)
    assert store.payload["automatic_run_active"] is None
    assert await manager.claim_due_automatic_run(now=now) is None


@pytest.mark.asyncio
async def test_deployment_cron_is_claimable_without_managed_times(monkeypatch):
    _configure(monkeypatch, SCHEDULE_CRON_EXPRESSION="0 2 * * *")
    manager = AdminConfigurationManager(MemoryStore(), cache_ttl_s=0)

    lease = await manager.claim_due_automatic_run(
        now=datetime(2026, 9, 1, 2, 4, tzinfo=timezone.utc)
    )

    assert lease is not None
    assert lease.schedule_key.startswith("deployment:")


@pytest.mark.asyncio
async def test_overlapping_crons_consume_the_same_occurrence_once(monkeypatch):
    _configure(monkeypatch, SCHEDULE_CRON_EXPRESSION="30 14 * * 1-5")
    manager = AdminConfigurationManager(MemoryStore(), cache_ttl_s=0)
    await manager.add_automatic_run_time("14:30")
    now = datetime(2026, 9, 1, 14, 33, tzinfo=timezone.utc)

    lease = await manager.claim_due_automatic_run(now=now)
    assert lease is not None
    await manager.release_automatic_run(lease)

    assert await manager.claim_due_automatic_run(now=now) is None


@pytest.mark.asyncio
async def test_mutation_retries_one_etag_conflict():
    store = MemoryStore()
    store.conflict_once = True
    manager = AdminConfigurationManager(store, cache_ttl_s=0)

    await manager.add_admin("managed-oid")

    assert store.write_count == 1
    assert store.payload["admin_principals"] == ["managed-oid"]


def test_admin_blob_is_derived_inside_the_private_state_container():
    assert (
        _blob_url_from_checkpoint(
            "https://stexample.blob.core.windows.net/azbrief-state/checkpoint.json"
        )
        == "https://stexample.blob.core.windows.net/azbrief-state/admin-config.json"
    )

    with pytest.raises(ValueError):
        _blob_url_from_checkpoint("https://attacker.example/azbrief-state/checkpoint.json")


@pytest.mark.asyncio
async def test_blob_store_reads_json_and_etag(monkeypatch):
    client = HttpClient(
        [
            HttpResponse(
                200,
                {"schema_version": "1", "subscribers": [], "admin_principals": []},
                {"ETag": '"etag-1"'},
            )
        ]
    )
    monkeypatch.setattr(
        "src.services.admin_config.httpx.AsyncClient",
        lambda **_kwargs: client,
    )
    store = BlobAdminConfigStore(
        "https://stexample.blob.core.windows.net/azbrief-state/admin-config.json"
    )
    monkeypatch.setattr(store, "_headers", lambda: {"Authorization": "Bearer test"})

    snapshot = await store.read()

    assert snapshot.etag == '"etag-1"'
    assert snapshot.payload["schema_version"] == "1"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("etag", "expected_header"),
    [(None, ("If-None-Match", "*")), ('"etag-1"', ("If-Match", '"etag-1"'))],
)
async def test_blob_store_uses_conditional_writes(monkeypatch, etag, expected_header):
    client = HttpClient([HttpResponse(201, headers={"ETag": '"etag-2"'})])
    monkeypatch.setattr(
        "src.services.admin_config.httpx.AsyncClient",
        lambda **_kwargs: client,
    )
    store = BlobAdminConfigStore(
        "https://stexample.blob.core.windows.net/azbrief-state/admin-config.json"
    )
    monkeypatch.setattr(store, "_headers", lambda: {"Authorization": "Bearer test"})

    result = await store.write({"schema_version": "1"}, etag)

    headers = client.requests[0][2]
    assert headers[expected_header[0]] == expected_header[1]
    assert result == '"etag-2"'


@pytest.mark.asyncio
async def test_blob_store_reports_etag_conflict(monkeypatch):
    client = HttpClient([HttpResponse(412)])
    monkeypatch.setattr(
        "src.services.admin_config.httpx.AsyncClient",
        lambda **_kwargs: client,
    )
    store = BlobAdminConfigStore(
        "https://stexample.blob.core.windows.net/azbrief-state/admin-config.json"
    )
    monkeypatch.setattr(store, "_headers", lambda: {"Authorization": "Bearer test"})

    with pytest.raises(AdminConfigConflictError):
        await store.write({"schema_version": "1"}, '"stale"')
