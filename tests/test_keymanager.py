"""Key manager tests: refresh propagation, single-flight, error policy, and
the background scheduler (specs 0007, 0010)."""

from __future__ import annotations

import asyncio
import base64
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.danalock_ble.const import (
    CONF_REFRESH_JITTER_HOURS,
    CONF_REFRESH_PERIOD_HOURS,
    DEFAULT_REFRESH_JITTER_HOURS,
    DEFAULT_REFRESH_PERIOD_HOURS,
    DOMAIN,
    LEGACY_REFRESH_JITTER_MIN,
    LEGACY_REFRESH_PERIOD_MIN,
    option_refresh_hours,
)
from pydanalock.cloud import ApiError, AuthError, DeviceKey
from custom_components.danalock_ble.keymanager import DanalockKeyManager
from tests.conftest import (
    SERIAL_NORMALIZED,
    CloudHandler,
    inject_advertisement,
    load_fixture,
    make_broadcast_payload,
    make_service_info,
    setup_entry,
)
from tests.test_sensors import entity_state

SECOND_SERIAL = "aabbccddeeff"


def make_device_key(
    serial: str = SERIAL_NORMALIZED,
    *,
    blob: bytes = b"synthetic-login-blob",
    broadcast_key: bytes | None = None,
    valid_to: datetime | None = datetime(2099, 1, 1, tzinfo=UTC),
) -> DeviceKey:
    """A synthetic device key."""
    return DeviceKey(
        serial=serial,
        login_blob=blob,
        broadcast_key=broadcast_key or bytes(range(16)),
        valid_from=None,
        valid_to=valid_to,
        permissions=(),
    )


class StubClient:
    """Cloud client test double recording get_key calls."""

    def __init__(
        self,
        keys: dict[str, DeviceKey],
        errors: dict[str, Exception] | None = None,
        delay: float = 0.0,
    ) -> None:
        self.keys = keys
        self.errors = errors or {}
        self.delay = delay
        self.calls: list[tuple[str, bool]] = []
        self.in_flight = 0
        self.max_in_flight = 0

    async def get_key(self, serial: str, *, force: bool = False) -> DeviceKey:
        self.calls.append((serial, force))
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            await asyncio.sleep(self.delay)
            if serial in self.errors:
                raise self.errors[serial]
            return self.keys[serial]
        finally:
            self.in_flight -= 1


class StubControl:
    """Control test double recording set_key calls."""

    def __init__(self) -> None:
        self.keys: list[DeviceKey] = []

    async def set_key(self, key: DeviceKey) -> None:
        self.keys.append(key)


class BlockingClient:
    """Client test double whose get_key blocks on a gate."""

    def __init__(self, key: DeviceKey) -> None:
        self.key = key
        self.gate = asyncio.Event()
        self.calls: list[tuple[str, bool]] = []

    async def get_key(self, serial: str, *, force: bool = False) -> DeviceKey:
        self.calls.append((serial, force))
        await self.gate.wait()
        return self.key


def make_entry(hass: HomeAssistant, **kwargs: Any) -> MockConfigEntry:
    """A registered danalock entry for manager-level tests."""
    entry = MockConfigEntry(domain=DOMAIN, unique_id="user@example.com", **kwargs)
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
def managers_to_stop() -> list[DanalockKeyManager]:
    """Managers created in a test are stopped afterwards (no stray timers)."""
    managers: list[DanalockKeyManager] = []
    yield managers
    for manager in managers:
        manager.stop()


async def test_refresh_forces_client_and_updates_shared_dict(
    hass: HomeAssistant,
) -> None:
    """refresh hits the client with force=True and rewrites the shared dict
    entry in place (spec 0007 R5/R6)."""
    entry = make_entry(hass)
    keys = {SERIAL_NORMALIZED: make_device_key()}
    client = StubClient({SERIAL_NORMALIZED: make_device_key(blob=b"new-blob")})
    manager = DanalockKeyManager(hass, entry, client, keys)
    observer = keys

    key = await manager.refresh(SERIAL_NORMALIZED, force=True)

    assert client.calls == [(SERIAL_NORMALIZED, True)]
    assert keys[SERIAL_NORMALIZED] is key
    assert observer[SERIAL_NORMALIZED] is key
    assert key.login_blob == b"new-blob"


async def test_refresh_propagates_to_control_and_listeners(
    hass: HomeAssistant,
) -> None:
    """A refreshed key reaches the bound control and the listeners; an
    unsubscribed listener stays quiet (spec 0007 R5)."""
    entry = make_entry(hass)
    keys = {SERIAL_NORMALIZED: make_device_key()}
    client = StubClient({SERIAL_NORMALIZED: make_device_key(blob=b"new-blob")})
    control = StubControl()
    manager = DanalockKeyManager(hass, entry, client, keys)
    manager.bind_controls({SERIAL_NORMALIZED: control})
    events: list[str] = []
    remove = manager.add_listener(
        SERIAL_NORMALIZED, lambda: events.append(SERIAL_NORMALIZED)
    )

    await manager.refresh(SERIAL_NORMALIZED, force=True)
    await hass.async_block_till_done()

    assert control.keys == [keys[SERIAL_NORMALIZED]]
    assert events == [SERIAL_NORMALIZED]

    remove()
    await manager.refresh(SERIAL_NORMALIZED, force=True)
    await hass.async_block_till_done()
    assert events == [SERIAL_NORMALIZED]


async def test_refresh_serializes_concurrent_calls(hass: HomeAssistant) -> None:
    """Concurrent refreshes of one serial run one at a time (single-flight,
    spec 0007 R6)."""
    entry = make_entry(hass)
    keys = {SERIAL_NORMALIZED: make_device_key()}
    client = StubClient(
        {SERIAL_NORMALIZED: make_device_key(blob=b"new-blob")}, delay=0.05
    )
    manager = DanalockKeyManager(hass, entry, client, keys)

    results = await asyncio.gather(
        *[manager.refresh(SERIAL_NORMALIZED, force=True) for _ in range(3)]
    )

    assert client.max_in_flight == 1
    assert len(client.calls) == 3
    assert all(result is results[0] for result in results)


async def test_refresh_all_continues_after_network_error(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """A failing serial is logged and skipped; the remaining serials still
    refresh and no reauth starts (spec 0007 R9)."""
    entry = make_entry(hass)
    keys = {
        SERIAL_NORMALIZED: make_device_key(),
        SECOND_SERIAL: make_device_key(SECOND_SERIAL),
    }
    client = StubClient(
        {SECOND_SERIAL: make_device_key(SECOND_SERIAL, blob=b"second-new-blob")},
        errors={SERIAL_NORMALIZED: ApiError("boom", status=500)},
    )
    manager = DanalockKeyManager(hass, entry, client, keys)

    await manager.refresh_all()

    assert client.calls == [(SERIAL_NORMALIZED, True), (SECOND_SERIAL, True)]
    assert keys[SECOND_SERIAL].login_blob == b"second-new-blob"
    assert keys[SERIAL_NORMALIZED].login_blob == b"synthetic-login-blob"
    assert f"key refresh for {SERIAL_NORMALIZED} failed" in caplog.text
    assert hass.config_entries.flow.async_progress_by_handler(DOMAIN) == []


async def test_refresh_all_starts_reauth_on_auth_error(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """An AuthError starts reauthentication exactly once and the cycle
    continues (spec 0007 R9)."""
    entry = make_entry(hass)
    keys = {
        SERIAL_NORMALIZED: make_device_key(),
        SECOND_SERIAL: make_device_key(SECOND_SERIAL),
    }
    client = StubClient(
        {},
        errors={
            SERIAL_NORMALIZED: AuthError("denied"),
            SECOND_SERIAL: AuthError("denied"),
        },
    )
    manager = DanalockKeyManager(hass, entry, client, keys)

    await manager.refresh_all()
    await hass.async_block_till_done()

    assert client.calls == [(SERIAL_NORMALIZED, True), (SECOND_SERIAL, True)]
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert len(flows) == 1
    assert flows[0]["context"]["source"] == "reauth"
    assert "failed authentication" in caplog.text


async def test_stop_during_in_flight_cycle_does_not_rearm(
    hass: HomeAssistant, managers_to_stop: list[DanalockKeyManager]
) -> None:
    """A cycle that is in flight when the manager stops finishes its serial
    but never re-arms the timer (spec 0007 R10)."""
    entry = make_entry(
        hass, options={"refresh_period_hours": 1, "refresh_jitter_hours": 0}
    )
    key = make_device_key(blob=b"new-blob")
    client = BlockingClient(key)
    keys = {SERIAL_NORMALIZED: make_device_key()}
    manager = DanalockKeyManager(hass, entry, client, keys)
    managers_to_stop.append(manager)
    manager.start()

    async_fire_time_changed(hass, dt_util.now() + timedelta(hours=1))
    for _ in range(3):
        await asyncio.sleep(0)
    assert len(client.calls) == 1  # the cycle is now blocked in flight

    manager.stop()
    client.gate.set()
    await hass.async_block_till_done()
    assert keys[SERIAL_NORMALIZED] is key  # the in-flight refresh completed

    # the stopped manager does not re-arm: far in the future nothing runs
    async_fire_time_changed(hass, dt_util.now() + timedelta(days=10))
    await hass.async_block_till_done()
    assert len(client.calls) == 1


async def test_scheduler_delay_is_period_plus_jitter(
    hass: HomeAssistant, managers_to_stop: list[DanalockKeyManager]
) -> None:
    """The first cycle runs after period + one jitter draw (spec 0007 R7)."""
    entry = make_entry(
        hass,
        options={"refresh_period_hours": 1, "refresh_jitter_hours": 1},
    )
    keys = {SERIAL_NORMALIZED: make_device_key()}
    client = StubClient({SERIAL_NORMALIZED: make_device_key(blob=b"new-blob")})
    manager = DanalockKeyManager(hass, entry, client, keys, rng=lambda _lo, _hi: 420.0)
    managers_to_stop.append(manager)
    manager.start()

    # 60 min period + 7 min jitter (420 s) has not fully elapsed yet
    async_fire_time_changed(
        hass, dt_util.now() + timedelta(minutes=67) - timedelta(seconds=1)
    )
    await hass.async_block_till_done()
    assert client.calls == []

    async_fire_time_changed(hass, dt_util.now() + timedelta(minutes=67))
    await hass.async_block_till_done()
    assert client.calls == [(SERIAL_NORMALIZED, True)]
    assert keys[SERIAL_NORMALIZED].login_blob == b"new-blob"


async def test_scheduler_disabled_when_period_zero(
    hass: HomeAssistant,
) -> None:
    """With period 0 the manager schedules nothing (spec 0007 R7)."""
    entry = make_entry(
        hass,
        options={"refresh_period_hours": 0, "refresh_jitter_hours": 1},
    )
    client = StubClient({SERIAL_NORMALIZED: make_device_key()})
    manager = DanalockKeyManager(
        hass, entry, client, {SERIAL_NORMALIZED: make_device_key()}
    )
    manager.start()

    async_fire_time_changed(hass, dt_util.now() + timedelta(days=10))
    await hass.async_block_till_done()

    assert client.calls == []


async def test_scheduler_rerolls_jitter_each_cycle(
    hass: HomeAssistant, managers_to_stop: list[DanalockKeyManager]
) -> None:
    """Every cycle draws a fresh jitter value (spec 0007 R7)."""
    entry = make_entry(
        hass,
        options={"refresh_period_hours": 1, "refresh_jitter_hours": 1},
    )
    keys = {SERIAL_NORMALIZED: make_device_key()}
    client = StubClient({SERIAL_NORMALIZED: make_device_key(blob=b"new-blob")})
    draws = [3600.0, 1800.0, 0.0]
    seen_ranges: list[tuple[float, float]] = []

    def rng(lo: float, hi: float) -> float:
        seen_ranges.append((lo, hi))
        return draws.pop(0)

    manager = DanalockKeyManager(hass, entry, client, keys, rng=rng)
    managers_to_stop.append(manager)
    manager.start()

    base = dt_util.now()

    # first cycle: 1 hour + 1 hour jitter draw of 3600 s = 2 hours
    async_fire_time_changed(hass, base + timedelta(hours=2))
    await hass.async_block_till_done()
    assert len(client.calls) == 1

    # the second cycle was scheduled at the fired point (2 h) + 1 h
    # + 0.5 h = 3:30; ten minutes earlier nothing happens
    async_fire_time_changed(hass, base + timedelta(hours=3, minutes=20))
    await hass.async_block_till_done()
    assert len(client.calls) == 1

    async_fire_time_changed(hass, base + timedelta(hours=3, minutes=30))
    await hass.async_block_till_done()
    assert len(client.calls) == 2

    assert seen_ranges == [(0.0, 3600.0), (0.0, 3600.0), (0.0, 3600.0)]


@pytest.mark.parametrize("expected_lingering_timers", [True])
async def test_refreshed_key_reaches_the_broadcast_monitor(
    enable_bluetooth: None,
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The in-place dict update lets the monitor decode with the new
    broadcast key without any rewiring (spec 0007 R5)."""
    handler = CloudHandler()
    entry = await setup_entry(hass, monkeypatch, handler)

    refreshed = load_fixture("login_token_single.json")
    refreshed["blob"] = base64.b64encode(bytes(range(48))).decode("ascii")
    new_bkey = bytes(range(16, 32))
    refreshed["broadcast_key"] = base64.b64encode(new_bkey).decode("ascii")
    handler.single_payload = refreshed

    await entry.runtime_data.key_manager.refresh(SERIAL_NORMALIZED, force=True)
    await hass.async_block_till_done()

    # an advertisement encrypted with the NEW broadcast key decodes
    inject_advertisement(
        hass,
        make_service_info(make_broadcast_payload(counter=50, key=new_bkey), rssi=-60),
    )
    await hass.async_block_till_done()
    assert (
        entity_state(hass, entry, f"{SERIAL_NORMALIZED}_update_counter").state == "50"
    )
    assert (
        entry.runtime_data.keys[SERIAL_NORMALIZED].broadcast_key
        == entry.runtime_data.monitor._keys[SERIAL_NORMALIZED].broadcast_key
    )


async def test_scheduler_without_devices_is_harmless(
    hass: HomeAssistant, managers_to_stop: list[DanalockKeyManager]
) -> None:
    """An empty key dict never schedules anything (spec 0007 R7)."""
    entry = make_entry(hass)
    client = StubClient({})
    manager = DanalockKeyManager(hass, entry, client, {})
    managers_to_stop.append(manager)
    manager.start()

    async_fire_time_changed(hass, dt_util.now() + timedelta(days=10))
    await hass.async_block_till_done()

    assert client.calls == []


# --- hours options and legacy-minute fallback (spec 0010 R5/R6) ---------------


@pytest.mark.parametrize(
    ("options", "hours_key", "default_hours", "legacy_minutes_key", "expected"),
    [
        ({}, CONF_REFRESH_PERIOD_HOURS, 12, LEGACY_REFRESH_PERIOD_MIN, 12),
        (
            {"refresh_period_hours": 5, "refresh_period_minutes": 720},
            CONF_REFRESH_PERIOD_HOURS,
            12,
            LEGACY_REFRESH_PERIOD_MIN,
            5,
        ),
        (
            {"refresh_period_minutes": 720},
            CONF_REFRESH_PERIOD_HOURS,
            12,
            LEGACY_REFRESH_PERIOD_MIN,
            12,
        ),
        (
            {"refresh_period_minutes": 125},
            CONF_REFRESH_PERIOD_HOURS,
            12,
            LEGACY_REFRESH_PERIOD_MIN,
            2,
        ),
        (
            {"refresh_period_minutes": 0},
            CONF_REFRESH_PERIOD_HOURS,
            12,
            LEGACY_REFRESH_PERIOD_MIN,
            0,
        ),
        (
            {"refresh_jitter_minutes": 90},
            CONF_REFRESH_JITTER_HOURS,
            6,
            LEGACY_REFRESH_JITTER_MIN,
            1,
        ),
    ],
)
def test_option_refresh_hours_reads_hours_with_legacy_fallback(
    options: dict[str, Any],
    hours_key: str,
    default_hours: int,
    legacy_minutes_key: str,
    expected: int,
) -> None:
    """Hour keys win; absent hour keys fall back to floor-divided minutes;
    neither present yields the default (spec 0010 R5/R6)."""
    assert (
        option_refresh_hours(options, hours_key, default_hours, legacy_minutes_key)
        == expected
    )


async def test_scheduler_reads_legacy_minutes_as_fallback(
    hass: HomeAssistant, managers_to_stop: list[DanalockKeyManager]
) -> None:
    """Options saved in minutes by an older version still drive the cycle:
    720 minutes fires as 12 hours (spec 0010 R6)."""
    entry = make_entry(
        hass, options={"refresh_period_minutes": 720, "refresh_jitter_minutes": 0}
    )
    keys = {SERIAL_NORMALIZED: make_device_key()}
    client = StubClient({SERIAL_NORMALIZED: make_device_key(blob=b"new-blob")})
    manager = DanalockKeyManager(hass, entry, client, keys)
    managers_to_stop.append(manager)
    manager.start()

    now = dt_util.now()
    async_fire_time_changed(hass, now + timedelta(hours=12) - timedelta(seconds=1))
    await hass.async_block_till_done()
    assert client.calls == []

    async_fire_time_changed(hass, now + timedelta(hours=12, seconds=1))
    await hass.async_block_till_done()
    assert client.calls == [(SERIAL_NORMALIZED, True)]
    assert keys[SERIAL_NORMALIZED].login_blob == b"new-blob"


async def test_scheduler_disabled_by_legacy_zero_minutes(
    hass: HomeAssistant, managers_to_stop: list[DanalockKeyManager]
) -> None:
    """Legacy 0 minutes keeps the disabled semantics (spec 0010 R6)."""
    entry = make_entry(hass, options={"refresh_period_minutes": 0})
    client = StubClient({SERIAL_NORMALIZED: make_device_key()})
    manager = DanalockKeyManager(
        hass, entry, client, {SERIAL_NORMALIZED: make_device_key()}
    )
    managers_to_stop.append(manager)
    manager.start()

    async_fire_time_changed(hass, dt_util.now() + timedelta(days=10))
    await hass.async_block_till_done()

    assert client.calls == []


async def test_scheduler_prefers_hour_keys_over_legacy_minutes(
    hass: HomeAssistant, managers_to_stop: list[DanalockKeyManager]
) -> None:
    """When both key generations are present the hour keys win
    (spec 0010 R5)."""
    entry = make_entry(
        hass,
        options={
            "refresh_period_hours": 1,
            "refresh_jitter_hours": 0,
            "refresh_period_minutes": 720,
        },
    )
    keys = {SERIAL_NORMALIZED: make_device_key()}
    client = StubClient({SERIAL_NORMALIZED: make_device_key(blob=b"new-blob")})
    manager = DanalockKeyManager(hass, entry, client, keys)
    managers_to_stop.append(manager)
    manager.start()

    async_fire_time_changed(hass, dt_util.now() + timedelta(hours=1, seconds=1))
    await hass.async_block_till_done()

    assert client.calls == [(SERIAL_NORMALIZED, True)]


def test_default_options_are_the_documented_hours() -> None:
    """The defaults match spec 0010 R5 (12 h period, 6 h jitter)."""
    assert DEFAULT_REFRESH_PERIOD_HOURS == 12
    assert DEFAULT_REFRESH_JITTER_HOURS == 6


async def test_run_cycle_tolerates_refresh_failure(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    managers_to_stop: list[DanalockKeyManager],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A failing background refresh is logged and does not re-arm
    (spec 0007 R10). A manager without an active period is used so the
    cycle cannot schedule itself again after the exception."""
    caplog.set_level(logging.WARNING)
    entry = make_entry(hass)
    client = StubClient({SERIAL_NORMALIZED: make_device_key()})
    manager = DanalockKeyManager(
        hass, entry, client, {SERIAL_NORMALIZED: make_device_key()}
    )
    managers_to_stop.append(manager)

    async def _boom() -> None:
        raise RuntimeError("shutdown race")

    monkeypatch.setattr(manager, "refresh_all", _boom)

    await manager._run_cycle(dt_util.now())

    assert "background key refresh aborted" in caplog.text
    assert manager._cancel_timer is None
    assert client.calls == []
