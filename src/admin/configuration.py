"""Mutable subscriber and administrator configuration for the Admin console."""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Callable, Literal, Optional

from croniter import croniter
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.config import Subscriber, get_settings
from src.services.admin_config import (
    AdminConfigConflictError,
    AdminConfigNotConfiguredError,
    AdminConfigSnapshot,
    AdminConfigStore,
    get_admin_config_store,
)

_MAX_WRITE_ATTEMPTS = 3
_AUTOMATIC_RUN_LEASE_HOURS = 13


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _normalize_run_time(value: str) -> str:
    try:
        parsed = datetime.strptime(str(value).strip(), "%H:%M")
    except ValueError as exc:
        raise ValueError("automatic run time must use HH:MM in UTC") from exc
    return parsed.strftime("%H:%M")


def _daily_cron(value: str) -> str:
    normalized = _normalize_run_time(value)
    hour, minute = normalized.split(":")
    return f"{int(minute)} {int(hour)} * * *"


def _schedule_key(source: str, cron_expression: str) -> str:
    digest = hashlib.sha256(cron_expression.encode("utf-8")).hexdigest()[:16]
    return f"{source}:{digest}"


def _daily_time_from_cron(cron_expression: str) -> Optional[str]:
    parts = cron_expression.strip().split()
    if len(parts) != 5 or parts[2:] != ["*", "*", "*"]:
        return None
    try:
        minute = int(parts[0])
        hour = int(parts[1])
    except ValueError:
        return None
    if not 0 <= minute <= 59 or not 0 <= hour <= 23:
        return None
    return f"{hour:02d}:{minute:02d}"


class AutomaticRunLease(BaseModel):
    """Durable claim preventing duplicate starts for one schedule occurrence."""

    model_config = ConfigDict(extra="forbid")

    schedule_key: str
    scheduled_for: datetime
    claimed_at: datetime


class AutomaticRunSchedule(BaseModel):
    """Effective deployment or Admin-managed automatic schedule."""

    key: str
    cron_expression: str
    time_utc: Optional[str]
    managed: bool
    next_run_at: datetime


class AdminConfigurationEntryExistsError(ValueError):
    """A subscriber or principal already exists in the merged configuration."""


class AdminConfigurationEntryNotFoundError(ValueError):
    """A managed subscriber or principal does not exist."""


class AdminConfigurationProtectedEntryError(ValueError):
    """A bootstrap environment entry cannot be changed through the console."""


class ManagedAdminConfiguration(BaseModel):
    """Versioned mutable configuration stored in the private state container."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1"] = "1"
    subscribers: list[Subscriber] = Field(default_factory=list)
    admin_principals: list[str] = Field(default_factory=list)
    automatic_run_times_utc: list[str] = Field(default_factory=list)
    automatic_run_last_occurrences: dict[str, datetime] = Field(default_factory=dict)
    automatic_run_active: Optional[AutomaticRunLease] = None
    updated_at: Optional[datetime] = None

    @field_validator("admin_principals", mode="before")
    @classmethod
    def normalize_admin_principals(cls, value) -> list[str]:
        """Normalize identifiers and preserve the first occurrence."""
        seen: set[str] = set()
        normalized: list[str] = []
        for item in value or []:
            principal = str(item).strip().lower()
            if principal and principal not in seen:
                seen.add(principal)
                normalized.append(principal)
        return normalized

    @field_validator("automatic_run_times_utc", mode="before")
    @classmethod
    def normalize_automatic_run_times(cls, value) -> list[str]:
        seen: set[str] = set()
        normalized: list[str] = []
        for item in value or []:
            run_time = _normalize_run_time(str(item))
            if run_time not in seen:
                seen.add(run_time)
                normalized.append(run_time)
        return sorted(normalized)

    @model_validator(mode="after")
    def deduplicate_subscribers(self) -> "ManagedAdminConfiguration":
        """Keep one profile per case-insensitive email address."""
        seen: set[str] = set()
        unique: list[Subscriber] = []
        for subscriber in self.subscribers:
            key = subscriber.email.strip().lower()
            if key in seen:
                raise ValueError(f"duplicate managed subscriber email: {subscriber.email}")
            seen.add(key)
            unique.append(subscriber.model_copy(update={"email": key}))
        self.subscribers = unique
        return self


class AdminConfigurationManager:
    """Merge immutable bootstrap settings with ETag-protected mutable entries."""

    def __init__(
        self,
        store: Optional[AdminConfigStore] = None,
        cache_ttl_s: float = 5.0,
    ):
        self._store = store or get_admin_config_store()
        self._cache_ttl_s = cache_ttl_s
        self._cached: Optional[ManagedAdminConfiguration] = None
        self._cache_expires_at = 0.0

    @property
    def configured(self) -> bool:
        """Return whether UI mutations have a durable destination."""
        return self._store.configured

    @staticmethod
    def _parse(snapshot: AdminConfigSnapshot) -> ManagedAdminConfiguration:
        if not snapshot.payload:
            return ManagedAdminConfiguration()
        return ManagedAdminConfiguration.model_validate(snapshot.payload)

    async def _load(self, force: bool = False) -> ManagedAdminConfiguration:
        import time

        now = time.monotonic()
        if not force and self._cached is not None and now < self._cache_expires_at:
            return self._cached.model_copy(deep=True)
        configuration = self._parse(await self._store.read())
        self._cached = configuration
        self._cache_expires_at = now + self._cache_ttl_s
        return configuration.model_copy(deep=True)

    def _invalidate(self) -> None:
        self._cached = None
        self._cache_expires_at = 0.0

    async def _mutate(
        self,
        update: Callable[[ManagedAdminConfiguration], ManagedAdminConfiguration],
    ) -> ManagedAdminConfiguration:
        if not self._store.configured:
            raise AdminConfigNotConfiguredError(
                "durable Admin configuration requires CHECKPOINT_BLOB_URL or CHECKPOINT_FILE_PATH"
            )
        for _ in range(_MAX_WRITE_ATTEMPTS):
            snapshot = await self._store.read()
            current = self._parse(snapshot)
            changed = update(current.model_copy(deep=True))
            changed.updated_at = datetime.now(timezone.utc)
            try:
                await self._store.write(changed.model_dump(mode="json"), snapshot.etag)
                self._cached = changed
                self._cache_expires_at = 0.0
                return changed.model_copy(deep=True)
            except AdminConfigConflictError:
                self._invalidate()
        raise AdminConfigConflictError("Admin configuration remained contended")

    @staticmethod
    def _bootstrap_subscribers() -> list[Subscriber]:
        return get_settings().get_subscribers()

    @staticmethod
    def _bootstrap_admins() -> set[str]:
        return get_settings().get_admin_allowed_principals()

    @staticmethod
    def _bootstrap_schedule(now: datetime) -> AutomaticRunSchedule:
        expression = get_settings().schedule_cron_expression.strip()
        if not croniter.is_valid(expression):
            raise ValueError("SCHEDULE_CRON_EXPRESSION is not a valid five-field cron")
        next_run = croniter(expression, now).get_next(datetime)
        return AutomaticRunSchedule(
            key=_schedule_key("deployment", expression),
            cron_expression=expression,
            time_utc=_daily_time_from_cron(expression),
            managed=False,
            next_run_at=_ensure_utc(next_run),
        )

    @staticmethod
    def _managed_schedule(run_time: str, now: datetime) -> AutomaticRunSchedule:
        expression = _daily_cron(run_time)
        next_run = croniter(expression, now).get_next(datetime)
        return AutomaticRunSchedule(
            key=_schedule_key("admin", expression),
            cron_expression=expression,
            time_utc=_normalize_run_time(run_time),
            managed=True,
            next_run_at=_ensure_utc(next_run),
        )

    def _effective_schedules(
        self,
        configuration: ManagedAdminConfiguration,
        now: datetime,
    ) -> list[AutomaticRunSchedule]:
        schedules = [self._bootstrap_schedule(now)]
        schedules.extend(
            self._managed_schedule(run_time, now)
            for run_time in configuration.automatic_run_times_utc
        )
        return schedules

    @staticmethod
    def _latest_occurrence(
        schedule: AutomaticRunSchedule,
        now: datetime,
    ) -> datetime:
        scheduled_for = croniter(
            schedule.cron_expression,
            now.replace(second=0, microsecond=0) + timedelta(minutes=1),
        ).get_prev(datetime)
        return _ensure_utc(scheduled_for)

    def _due_schedule(
        self,
        configuration: ManagedAdminConfiguration,
        now: datetime,
        lookback_minutes: int,
    ) -> Optional[AutomaticRunLease]:
        active = configuration.automatic_run_active
        if active and _ensure_utc(active.claimed_at) > now - timedelta(
            hours=_AUTOMATIC_RUN_LEASE_HOURS
        ):
            return None

        due: list[AutomaticRunLease] = []
        earliest = now - timedelta(minutes=lookback_minutes)
        for schedule in self._effective_schedules(configuration, now):
            scheduled_for = self._latest_occurrence(schedule, now)
            if not earliest <= scheduled_for <= now:
                continue
            last = configuration.automatic_run_last_occurrences.get(schedule.key)
            if last and _ensure_utc(last) >= scheduled_for:
                continue
            due.append(
                AutomaticRunLease(
                    schedule_key=schedule.key,
                    scheduled_for=scheduled_for,
                    claimed_at=now,
                )
            )
        return min(due, key=lambda item: item.scheduled_for) if due else None

    async def get_subscribers(self) -> list[Subscriber]:
        """Return bootstrap and managed subscribers, rejecting shadowed profiles."""
        merged = {
            subscriber.email.strip().lower(): subscriber
            for subscriber in self._bootstrap_subscribers()
        }
        managed = await self._load()
        for subscriber in managed.subscribers:
            merged.setdefault(subscriber.email.strip().lower(), subscriber)
        return list(merged.values())

    async def get_managed_subscriber_emails(self) -> set[str]:
        configuration = await self._load()
        return {subscriber.email.strip().lower() for subscriber in configuration.subscribers}

    async def add_subscriber(self, subscriber: Subscriber) -> Subscriber:
        """Persist a new subscriber unless either configuration source already owns it."""
        normalized = subscriber.model_copy(update={"email": subscriber.email.strip().lower()})
        key = normalized.email
        bootstrap = {item.email.strip().lower() for item in self._bootstrap_subscribers()}

        def add(configuration: ManagedAdminConfiguration) -> ManagedAdminConfiguration:
            managed = {item.email.strip().lower() for item in configuration.subscribers}
            if key in bootstrap or key in managed:
                raise AdminConfigurationEntryExistsError(f"subscriber already exists: {key}")
            configuration.subscribers.append(normalized)
            return configuration

        await self._mutate(add)
        return normalized

    async def update_subscriber(self, email: str, subscriber: Subscriber) -> Subscriber:
        """Replace one managed subscriber without allowing configuration shadowing."""
        current_key = email.strip().lower()
        normalized = subscriber.model_copy(update={"email": subscriber.email.strip().lower()})
        bootstrap = {item.email.strip().lower() for item in self._bootstrap_subscribers()}
        if current_key in bootstrap:
            raise AdminConfigurationProtectedEntryError(
                "deployment subscribers cannot be changed from the console"
            )

        def replace(configuration: ManagedAdminConfiguration) -> ManagedAdminConfiguration:
            index = next(
                (
                    index
                    for index, item in enumerate(configuration.subscribers)
                    if item.email.strip().lower() == current_key
                ),
                None,
            )
            if index is None:
                raise AdminConfigurationEntryNotFoundError(f"subscriber not found: {current_key}")
            managed = {
                item.email.strip().lower()
                for position, item in enumerate(configuration.subscribers)
                if position != index
            }
            if normalized.email in bootstrap or normalized.email in managed:
                raise AdminConfigurationEntryExistsError(
                    f"subscriber already exists: {normalized.email}"
                )
            configuration.subscribers[index] = normalized
            return configuration

        await self._mutate(replace)
        return normalized

    async def remove_subscriber(self, email: str) -> None:
        """Delete a managed subscriber while preserving deployment bootstrap entries."""
        key = email.strip().lower()
        if key in {item.email.strip().lower() for item in self._bootstrap_subscribers()}:
            raise AdminConfigurationProtectedEntryError(
                "deployment subscribers cannot be removed from the console"
            )

        def remove(configuration: ManagedAdminConfiguration) -> ManagedAdminConfiguration:
            remaining = [
                item for item in configuration.subscribers if item.email.strip().lower() != key
            ]
            if len(remaining) == len(configuration.subscribers):
                raise AdminConfigurationEntryNotFoundError(f"subscriber not found: {key}")
            configuration.subscribers = remaining
            return configuration

        await self._mutate(remove)

    async def get_admin_principals(self) -> set[str]:
        """Return bootstrap and managed administrator identifiers."""
        configuration = await self._load()
        return self._bootstrap_admins() | set(configuration.admin_principals)

    async def get_managed_admin_principals(self) -> set[str]:
        configuration = await self._load()
        return set(configuration.admin_principals)

    async def add_admin(self, principal: str) -> str:
        """Persist one normalized principal identifier."""
        normalized = principal.strip().lower()
        bootstrap = self._bootstrap_admins()

        def add(configuration: ManagedAdminConfiguration) -> ManagedAdminConfiguration:
            if normalized in bootstrap or normalized in configuration.admin_principals:
                raise AdminConfigurationEntryExistsError(
                    f"administrator already exists: {normalized}"
                )
            configuration.admin_principals.append(normalized)
            return configuration

        await self._mutate(add)
        return normalized

    async def remove_admin(self, principal: str) -> None:
        """Delete a managed administrator while preserving bootstrap access."""
        normalized = principal.strip().lower()
        if normalized in self._bootstrap_admins():
            raise AdminConfigurationProtectedEntryError(
                "deployment administrators cannot be removed from the console"
            )

        def remove(configuration: ManagedAdminConfiguration) -> ManagedAdminConfiguration:
            if normalized not in configuration.admin_principals:
                raise AdminConfigurationEntryNotFoundError(f"administrator not found: {normalized}")
            configuration.admin_principals.remove(normalized)
            return configuration

        await self._mutate(remove)

    async def get_automatic_run_schedules(
        self,
        now: Optional[datetime] = None,
    ) -> list[AutomaticRunSchedule]:
        """Return the protected deployment cron plus Admin-managed daily times."""
        current = _ensure_utc(now or datetime.now(timezone.utc))
        configuration = await self._load()
        return self._effective_schedules(configuration, current)

    async def add_automatic_run_time(self, run_time: str) -> AutomaticRunSchedule:
        """Add one daily UTC execution time without shadowing an existing schedule."""
        normalized = _normalize_run_time(run_time)
        expression = _daily_cron(normalized)

        def add(configuration: ManagedAdminConfiguration) -> ManagedAdminConfiguration:
            expressions = {
                schedule.cron_expression
                for schedule in self._effective_schedules(
                    configuration,
                    datetime.now(timezone.utc),
                )
            }
            if expression in expressions:
                raise AdminConfigurationEntryExistsError(
                    f"automatic run time already exists: {normalized}"
                )
            configuration.automatic_run_times_utc.append(normalized)
            configuration.automatic_run_times_utc.sort()
            return configuration

        await self._mutate(add)
        return self._managed_schedule(normalized, datetime.now(timezone.utc))

    async def remove_automatic_run_time(self, run_time: str) -> None:
        """Remove one Admin-managed daily UTC execution time."""
        normalized = _normalize_run_time(run_time)
        if _daily_time_from_cron(get_settings().schedule_cron_expression) == normalized:
            raise AdminConfigurationProtectedEntryError(
                "deployment automatic run time cannot be removed from the console"
            )

        def remove(configuration: ManagedAdminConfiguration) -> ManagedAdminConfiguration:
            if normalized not in configuration.automatic_run_times_utc:
                raise AdminConfigurationEntryNotFoundError(
                    f"automatic run time not found: {normalized}"
                )
            configuration.automatic_run_times_utc.remove(normalized)
            key = _schedule_key("admin", _daily_cron(normalized))
            configuration.automatic_run_last_occurrences.pop(key, None)
            return configuration

        await self._mutate(remove)

    async def claim_due_automatic_run(
        self,
        now: Optional[datetime] = None,
        lookback_minutes: int = 7,
    ) -> Optional[AutomaticRunLease]:
        """Atomically claim one due schedule occurrence across Job executions."""
        current = _ensure_utc(now or datetime.now(timezone.utc))
        preview = await self._load(force=True)
        if self._due_schedule(preview, current, lookback_minutes) is None:
            return None

        claimed: list[AutomaticRunLease] = []

        def claim(configuration: ManagedAdminConfiguration) -> ManagedAdminConfiguration:
            claimed.clear()
            candidate = self._due_schedule(configuration, current, lookback_minutes)
            if candidate is None:
                return configuration
            claimed.append(candidate)
            configuration.automatic_run_active = candidate
            for schedule in self._effective_schedules(configuration, current):
                if self._latest_occurrence(schedule, current) == candidate.scheduled_for:
                    configuration.automatic_run_last_occurrences[schedule.key] = (
                        candidate.scheduled_for
                    )
            return configuration

        await self._mutate(claim)
        return claimed[-1] if claimed else None

    async def release_automatic_run(self, lease: AutomaticRunLease) -> None:
        """Release the active lease after the claimed Job execution exits."""

        def release(configuration: ManagedAdminConfiguration) -> ManagedAdminConfiguration:
            active = configuration.automatic_run_active
            if (
                active
                and active.schedule_key == lease.schedule_key
                and _ensure_utc(active.scheduled_for) == _ensure_utc(lease.scheduled_for)
            ):
                configuration.automatic_run_active = None
            return configuration

        await self._mutate(release)


_manager: Optional[AdminConfigurationManager] = None


def get_admin_configuration() -> AdminConfigurationManager:
    """Return the process-wide mutable configuration manager."""
    global _manager
    if _manager is None:
        _manager = AdminConfigurationManager()
    return _manager


def reset_admin_configuration() -> None:
    """Drop the cached manager after a settings/backend change in tests."""
    global _manager
    _manager = None
