"""Setup/unload/remove lifecycle tests (specs 0001, 0007, 0010)."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from homeassistant.components.update import DATA_COMPONENT, UpdateEntity
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

import custom_components.danalock_ble
from custom_components.danalock_ble.const import DOMAIN, PENDING_TOKENS
from custom_components.danalock_ble.control import DanalockControl
from pydanalock.cloud import DeviceKey, TokenData
from custom_components.danalock_ble.update import LOGGER as UPDATE_LOGGER
from custom_components.danalock_ble.update import (
    DanalockFirmwareUpdateEntity,
    _extract_takes_hass,
)
from tests.conftest import (
    FIRMWARE_LATEST_PATH,
    SERIAL_NORMALIZED,
    SERIAL_RAW,
    USERNAME,
    CloudHandler,
    load_fixture,
    make_token,
    patch_cloud_transport,
    setup_entry,
    stage_pending_tokens,
)
from tests.test_control import FakeLock, install_fake_lock
from tests.test_sensors import (
    BINARY_SENSOR_SUFFIXES,
    LOCK_SUFFIXES,
    SELECT_SUFFIXES,
    SENSOR_SUFFIXES,
    UPDATE_SUFFIXES,
)
from tests.test_update import (
    ENTITY_ID as UPDATE_ENTITY_ID,
)
from tests.test_update import (
    NEWER_PAYLOAD,
    make_info,
    setup_firmware_entry,
)


async def test_setup_registers_runtime_data_and_devices(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Successful setup carries client+keys in runtime_data and registers
    one device per key."""
    handler = CloudHandler()
    entry = await setup_entry(hass, monkeypatch, handler)

    assert entry.state is ConfigEntryState.LOADED
    assert entry.data == {"username": USERNAME}
    runtime = entry.runtime_data
    assert runtime.client is not None
    key = runtime.keys[SERIAL_NORMALIZED]
    assert isinstance(key, DeviceKey)
    assert key.login_blob
    assert len(key.broadcast_key) == 16
    # one devices() call fetched everything; get_key was a cache hit
    assert handler.requests.count(("GET", "/devices/v1/login_tokens")) == 1

    registry = dr.async_get(hass)
    devices = dr.async_entries_for_config_entry(registry, entry.entry_id)
    assert len(devices) == 1
    device = devices[0]
    assert device.identifiers == {(DOMAIN, SERIAL_NORMALIZED)}
    assert (dr.CONNECTION_BLUETOOTH, SERIAL_RAW) in device.connections
    # the cloud device name wins over the synthetic fallback (spec 0002 R1)
    assert device.name == "Fixture Lock"
    assert device.manufacturer == "Danalock"
    # the model comes from the API device type (spec 0005 R1)
    assert device.model == "Danalock V3"


async def test_setup_refetches_expired_keys(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An expired key from devices() is refetched from the single endpoint."""
    handler = CloudHandler(devices_payload=load_fixture("login_tokens_v1_expired.json"))
    entry = await setup_entry(hass, monkeypatch, handler)

    assert entry.state is ConfigEntryState.LOADED
    key = entry.runtime_data.keys[SERIAL_NORMALIZED]
    assert key.valid_to is not None and key.valid_to.year > 2090
    assert ("GET", "/devices/v1/00:11:22:33:44:55/login_token") in handler.requests


async def test_setup_empty_account_succeeds_without_devices(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An account without devices still loads (warn, zero devices)."""
    handler = CloudHandler(devices_payload=[])
    entry = await setup_entry(hass, monkeypatch, handler)

    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data.keys == {}
    assert dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id) == []
    assert "no devices" in caplog.text.lower()


async def test_setup_cloud_unreachable_retries(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A transport failure puts the entry into SETUP_RETRY."""
    handler = CloudHandler(devices_error=httpx.ConnectError("connection refused"))
    entry = await setup_entry(hass, monkeypatch, handler)

    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_setup_api_error_retries(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An API failure puts the entry into SETUP_RETRY."""
    handler = CloudHandler(devices_status=500)
    entry = await setup_entry(hass, monkeypatch, handler)

    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_setup_expired_tokens_start_reauth(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed refresh raises ConfigEntryAuthFailed and starts reauth."""
    handler = CloudHandler(token_status=400)
    entry = await setup_entry(
        hass, monkeypatch, handler, token=make_token(valid_for=-3600.0)
    )

    assert entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert len(flows) == 1
    assert flows[0]["context"]["source"] == "reauth"


async def test_setup_without_tokens_start_reauth(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An entry without any tokens goes to reauth (never a hard failure)."""
    handler = CloudHandler()
    patch_cloud_transport(monkeypatch, handler)
    entry = MockConfigEntry(domain=DOMAIN, unique_id=USERNAME, data={"username": USERNAME})
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert len(flows) == 1
    assert flows[0]["context"]["source"] == "reauth"


async def test_device_model_falls_back_without_device_type(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without a cloud device_type the model keeps the V3 fallback
    (spec 0005 R1)."""
    fixture = load_fixture("login_tokens_v1.json")
    del fixture[0]["device"]["device_type"]
    entry = await setup_entry(hass, monkeypatch, CloudHandler(devices_payload=fixture))
    device = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)[0]
    assert device.model == "V3"


async def test_unsupported_device_type_registered_but_skipped(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An unsupported device type gets a registry entry but no entities,
    monitor, or key material (spec 0005 R2/R3)."""
    pad = load_fixture("login_tokens_v1.json")[0]
    pad["device"] = {
        "serial_number": "0a:0b:0c:0d:0e:0f",
        "name": "Fixture Pad",
        "device_type": "danapad",
        "timezone": "UTC",
    }
    entry = await setup_entry(
        hass,
        monkeypatch,
        CloudHandler(devices_payload=[load_fixture("login_tokens_v1.json")[0], pad]),
    )

    registry = dr.async_get(hass)
    devices = {d.name: d for d in dr.async_entries_for_config_entry(registry, entry.entry_id)}
    assert set(devices) == {"Fixture Lock", "Fixture Pad"}
    assert devices["Fixture Pad"].model == "danapad"

    runtime = entry.runtime_data
    assert set(runtime.keys) == {SERIAL_NORMALIZED}
    assert set(runtime.controls) == {SERIAL_NORMALIZED}
    assert runtime.device_types == {
        SERIAL_NORMALIZED: "danalockv3",
        "0a0b0c0d0e0f": "danapad",
    }
    assert set(runtime.monitor.states) == {SERIAL_NORMALIZED}

    entity_registry = er.async_get(hass)
    unique_ids = {
        registry_entry.unique_id
        for registry_entry in er.async_entries_for_config_entry(entity_registry, entry.entry_id)
    }
    assert all(uid.startswith(SERIAL_NORMALIZED) for uid in unique_ids)
    assert len(unique_ids) == (
        len(BINARY_SENSOR_SUFFIXES)
        + len(SENSOR_SUFFIXES)
        + len(LOCK_SUFFIXES)
        + len(SELECT_SUFFIXES)
        + len(UPDATE_SUFFIXES)
    )
    assert "unsupported device_type" in caplog.text


async def test_unsupported_only_account_loads_without_entities(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An account with only unsupported devices loads with zero entities
    (spec 0005 R2)."""
    fixture = load_fixture("login_tokens_v1.json")
    fixture[0]["device"]["device_type"] = "danapad"
    entry = await setup_entry(hass, monkeypatch, CloudHandler(devices_payload=fixture))

    assert entry.state is ConfigEntryState.LOADED
    registry = dr.async_get(hass)
    devices = dr.async_entries_for_config_entry(registry, entry.entry_id)
    assert len(devices) == 1 and devices[0].model == "danapad"
    assert entry.runtime_data.monitor is None
    assert er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id) == []
    assert "unsupported device_type" in caplog.text


async def test_reload_refetches_keys_and_prunes_devices(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A reload re-reads the device list and removes stale devices."""
    handler = CloudHandler()
    entry = await setup_entry(hass, monkeypatch, handler)
    registry = dr.async_get(hass)
    assert len(dr.async_entries_for_config_entry(registry, entry.entry_id)) == 1

    # a device of another entry must survive our stale-device cleanup
    other = MockConfigEntry(domain=DOMAIN, unique_id="other@example.com")
    other.add_to_hass(hass)
    foreign = registry.async_get_or_create(
        config_entry_id=other.entry_id,
        identifiers={(DOMAIN, "otherdevice001")},
        manufacturer="Danalock",
        model="V3",
    )

    # the device vanished from the account between setup and reload
    handler.devices_payload = []
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data.keys == {}
    assert dr.async_entries_for_config_entry(registry, entry.entry_id) == []
    assert foreign.id in {
        d.id for d in dr.async_entries_for_config_entry(registry, other.entry_id)
    }
    assert handler.requests.count(("GET", "/devices/v1/login_tokens")) == 2


async def test_entry_allows_device_removal(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The entry supports removing its devices, so a delete + reload
    regenerates devices and entity ids (spec 0001 R8)."""
    handler = CloudHandler()
    entry = await setup_entry(hass, monkeypatch, handler)
    registry = dr.async_get(hass)
    device = dr.async_entries_for_config_entry(registry, entry.entry_id)[0]

    assert entry.supports_remove_device is True
    assert await custom_components.danalock_ble.async_remove_config_entry_device(
        hass, entry, device
    )

    # the delete + reload path recreates the device and its entities with
    # the serial-prefixed ids (spec 0002 R5)
    registry.async_remove_device(device.id)
    await hass.async_block_till_done()
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    devices = dr.async_entries_for_config_entry(registry, entry.entry_id)
    assert devices and devices[0].id == device.id
    registry = er.async_get(hass)
    entity_ids = {
        entry.entity_id for entry in er.async_entries_for_config_entry(registry, entry.entry_id)
    }
    assert entity_ids == {
        *(
            f"binary_sensor.danalock_ble_{SERIAL_NORMALIZED}_{suffix}"
            for suffix in BINARY_SENSOR_SUFFIXES
        ),
        *(f"sensor.danalock_ble_{SERIAL_NORMALIZED}_{suffix}" for suffix in SENSOR_SUFFIXES),
        *(f"lock.danalock_ble_{SERIAL_NORMALIZED}_{suffix}" for suffix in LOCK_SUFFIXES),
        *(f"select.danalock_ble_{SERIAL_NORMALIZED}_{suffix}" for suffix in SELECT_SUFFIXES),
        *(f"update.danalock_ble_{SERIAL_NORMALIZED}_{suffix}" for suffix in UPDATE_SUFFIXES),
    }


async def test_unload_closes_the_client(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unloading the entry releases the underlying httpx client."""
    handler = CloudHandler()
    entry = await setup_entry(hass, monkeypatch, handler)
    client = entry.runtime_data.client

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED
    with pytest.raises(RuntimeError):
        await client.devices()


async def test_setup_builds_controls_for_keyed_devices(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every keyed supported device gets a control (spec 0006 R6)."""
    install_fake_lock(monkeypatch)
    entry = await setup_entry(hass, monkeypatch, CloudHandler())
    runtime = entry.runtime_data

    assert set(runtime.controls) == {SERIAL_NORMALIZED}
    control = runtime.controls[SERIAL_NORMALIZED]
    assert isinstance(control, DanalockControl)


async def test_unload_disconnects_controls(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unloading disconnects every control (spec 0006 R6)."""
    install_fake_lock(monkeypatch)
    entry = await setup_entry(hass, monkeypatch, CloudHandler())
    fake = FakeLock.instances[-1]
    assert fake.disconnects == 0

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert fake.disconnects == 1


async def test_empty_account_has_no_controls(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An account without devices has neither monitor nor controls
    (spec 0006 R7)."""
    entry = await setup_entry(hass, monkeypatch, CloudHandler(devices_payload=[]))
    assert entry.runtime_data.controls == {}


async def test_remove_deletes_the_token_store(
    hass: HomeAssistant,
    hass_storage: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Removing the entry deletes its persisted token store."""
    handler = CloudHandler()
    entry = await setup_entry(hass, monkeypatch, handler)
    store_key = f"danalock_ble_tokens_{entry.entry_id}"
    assert store_key in hass_storage

    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert store_key not in hass_storage


async def test_remove_drops_unconsumed_staged_tokens(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tokens staged for an entry that never reached setup do not linger."""
    handler = CloudHandler()
    patch_cloud_transport(monkeypatch, handler)
    entry = MockConfigEntry(domain=DOMAIN, unique_id=USERNAME, data={"username": USERNAME})
    entry.add_to_hass(hass)
    stage_pending_tokens(hass, USERNAME)

    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert USERNAME not in hass.data[DOMAIN][PENDING_TOKENS]


# --- key refresh lifecycle (spec 0007) ---------------------------------------


async def test_setup_arms_the_background_refresh(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Setup arms the background cycle with the default period (R7): firing
    past the maximum default delay (12 h + 6 h jitter) refreshes."""
    handler = CloudHandler()
    entry = await setup_entry(hass, monkeypatch, handler)

    manager = entry.runtime_data.key_manager
    assert manager.key(SERIAL_NORMALIZED) is entry.runtime_data.keys[SERIAL_NORMALIZED]
    assert handler.requests.count(
        ("GET", "/devices/v1/00:11:22:33:44:55/login_token")
    ) == 0

    async_fire_time_changed(hass, dt_util.now() + timedelta(hours=18, seconds=1))
    await hass.async_block_till_done()

    assert handler.requests.count(
        ("GET", "/devices/v1/00:11:22:33:44:55/login_token")
    ) == 1


async def test_unload_stops_the_background_refresh(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unloading cancels the pending cycle before the client closes (R10)."""
    entry = await setup_entry(hass, monkeypatch, CloudHandler())
    manager = entry.runtime_data.key_manager
    assert manager._cancel_timer is not None

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert manager._cancel_timer is None


async def test_background_refresh_disabled_by_options(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """period=0 in the options schedules nothing (R7)."""
    handler = CloudHandler()
    entry = await setup_entry(
        hass,
        monkeypatch,
        handler,
        options={"refresh_period_hours": 0, "refresh_jitter_hours": 1},
    )
    assert entry.runtime_data.key_manager._cancel_timer is None

    async_fire_time_changed(hass, dt_util.now() + timedelta(days=5))
    await hass.async_block_till_done()

    assert ("GET", "/devices/v1/00:11:22:33:44:55/login_token") not in handler.requests


async def test_background_refresh_runs_periodically(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A due cycle force-refreshes the keys of all devices (R7).

    The fire lands one second past the deadline: firing exactly at the
    deadline is environment-sensitive in the harness (same idiom as the
    timer tests in test_broadcast).
    """
    handler = CloudHandler()
    entry = await setup_entry(
        hass,
        monkeypatch,
        handler,
        options={"refresh_period_hours": 1, "refresh_jitter_hours": 0},
    )
    assert handler.requests.count(
        ("GET", "/devices/v1/00:11:22:33:44:55/login_token")
    ) == 0

    async_fire_time_changed(hass, dt_util.now() + timedelta(hours=1, seconds=1))
    await hass.async_block_till_done()

    assert handler.requests.count(
        ("GET", "/devices/v1/00:11:22:33:44:55/login_token")
    ) == 1
    assert (
        entry.runtime_data.keys[SERIAL_NORMALIZED].valid_to
        == datetime(2099, 1, 1, tzinfo=UTC)
    )


async def test_background_refresh_auth_error_starts_reauth(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An AuthError in the background starts reauth; the entry stays loaded
    (R9)."""
    handler = CloudHandler(single_status=401)
    entry = await setup_entry(
        hass,
        monkeypatch,
        handler,
        options={"refresh_period_hours": 1, "refresh_jitter_hours": 0},
    )
    assert entry.state is ConfigEntryState.LOADED

    async_fire_time_changed(hass, dt_util.now() + timedelta(hours=1, seconds=1))
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert len(flows) == 1
    assert flows[0]["context"]["source"] == "reauth"


async def test_background_refresh_network_error_is_tolerated(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A cloud error in the background logs a warning and keeps the entry
    loaded without reauth (R9)."""
    handler = CloudHandler(single_status=500)
    entry = await setup_entry(
        hass,
        monkeypatch,
        handler,
        options={"refresh_period_hours": 1, "refresh_jitter_hours": 0},
    )

    async_fire_time_changed(hass, dt_util.now() + timedelta(hours=1, seconds=1))
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert "key refresh" in caplog.text
    assert hass.config_entries.flow.async_progress_by_handler(DOMAIN) == []


async def test_options_change_reloads_the_entry(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Changing options reloads the entry; the rebuilt manager follows the
    new options (R8): with period=0 nothing runs even far in the future."""
    handler = CloudHandler()
    entry = await setup_entry(hass, monkeypatch, handler)
    assert handler.requests.count(("GET", "/devices/v1/login_tokens")) == 1

    hass.config_entries.async_update_entry(
        entry, options={"refresh_period_hours": 0}
    )
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert handler.requests.count(("GET", "/devices/v1/login_tokens")) == 2

    async_fire_time_changed(hass, dt_util.now() + timedelta(days=5))
    await hass.async_block_till_done()

    assert handler.requests.count(
        ("GET", "/devices/v1/00:11:22:33:44:55/login_token")
    ) == 0


# --- firmware update check action (spec 0010 R3) -------------------------------


async def test_check_firmware_updates_action_forces_an_immediate_cycle(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The action runs a full firmware cycle for the targeted entity at
    once (spec 0010 R3)."""
    handler = CloudHandler()
    stubs = await setup_firmware_entry(hass, monkeypatch, handler, make_info())
    stub = stubs[SERIAL_NORMALIZED]
    assert stub.calls == 1
    assert hass.services.has_service(DOMAIN, "check_firmware_updates") is True

    handler.firmware_payload = NEWER_PAYLOAD
    await hass.services.async_call(
        DOMAIN,
        "check_firmware_updates",
        {"entity_id": UPDATE_ENTITY_ID},
        blocking=True,
    )
    await hass.async_block_till_done()

    assert stub.calls == 2
    state = hass.states.get(UPDATE_ENTITY_ID)
    assert state.state == "on"
    assert state.attributes["latest_version"] == "1.2.4"
    # the target resolution must not use the form HA deprecates
    assert "deprecated argument" not in caplog.text


def test_extract_takes_hass_detects_the_signature_generation() -> None:
    """Both generations of the target helper are recognized (spec 0010
    R3)."""

    async def old_helper(
        hass: Any, service_call: Any, expand_group: bool = True
    ) -> set[str]:
        return {service_call}

    async def new_helper(service_call: Any, expand_group: bool = True) -> set[str]:
        return {service_call}

    assert _extract_takes_hass(old_helper) is True
    assert _extract_takes_hass(new_helper) is False


async def test_check_firmware_updates_action_uses_the_modern_helper_form(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With a helper that no longer takes the legacy argument the handler
    calls the modern form (spec 0010 R3)."""
    handler = CloudHandler()
    stubs = await setup_firmware_entry(hass, monkeypatch, handler, make_info())
    seen: list[Any] = []

    async def modern_extract(
        service_call: Any, expand_group: bool = True
    ) -> set[str]:
        seen.append(service_call)
        return {UPDATE_ENTITY_ID}

    monkeypatch.setattr(
        "custom_components.danalock_ble.update.async_extract_entity_ids",
        modern_extract,
    )
    monkeypatch.setattr(
        "custom_components.danalock_ble.update._EXTRACT_TAKES_HASS", False
    )

    handler.firmware_payload = NEWER_PAYLOAD
    await hass.services.async_call(
        DOMAIN,
        "check_firmware_updates",
        {"entity_id": UPDATE_ENTITY_ID},
        blocking=True,
    )
    await hass.async_block_till_done()

    assert seen  # the modern call form received the service call
    assert stubs[SERIAL_NORMALIZED].calls == 2
    assert hass.states.get(UPDATE_ENTITY_ID).state == "on"


class _ForeignUpdateEntity(UpdateEntity):
    """A non-danalock update entity used to exercise target validation."""

    _attr_should_poll = False
    _attr_unique_id = "foreign_firmware"
    _attr_name = "Foreign firmware"

    async def async_update(self) -> None:
        """The action must never call this entity."""


async def test_check_firmware_updates_action_empty_target_is_a_noop(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An empty target stays a quiet no-op (spec 0022 R1)."""
    handler = CloudHandler()
    stubs = await setup_firmware_entry(hass, monkeypatch, handler, make_info())
    stub = stubs[SERIAL_NORMALIZED]
    assert stub.calls == 1

    await hass.services.async_call(DOMAIN, "check_firmware_updates", blocking=True)
    await hass.async_block_till_done()

    assert stub.calls == 1
    update_records = [
        record for record in caplog.records if record.name == UPDATE_LOGGER.name
    ]
    assert not [
        record for record in update_records if record.levelno >= logging.WARNING
    ]


async def test_check_firmware_updates_action_rejects_unknown_target(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unknown entity_id raises ServiceValidationError (spec 0022 R1)."""
    handler = CloudHandler()
    stubs = await setup_firmware_entry(hass, monkeypatch, handler, make_info())
    stub = stubs[SERIAL_NORMALIZED]
    assert stub.calls == 1

    with pytest.raises(ServiceValidationError, match="Unknown entity"):
        await hass.services.async_call(
            DOMAIN,
            "check_firmware_updates",
            {"entity_id": "update.some_other_firmware"},
            blocking=True,
        )
    await hass.async_block_till_done()
    assert stub.calls == 1


async def test_check_firmware_updates_action_rejects_foreign_target(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A registered non-danalock update entity raises ServiceValidationError
    (spec 0022 R1)."""
    handler = CloudHandler()
    stubs = await setup_firmware_entry(hass, monkeypatch, handler, make_info())
    stub = stubs[SERIAL_NORMALIZED]
    component = hass.data[DATA_COMPONENT]
    await component.async_add_entities([_ForeignUpdateEntity()])
    await hass.async_block_till_done()
    assert stub.calls == 1

    with pytest.raises(ServiceValidationError, match="not a danalock firmware entity"):
        await hass.services.async_call(
            DOMAIN,
            "check_firmware_updates",
            {"entity_id": "update.foreign_firmware"},
            blocking=True,
        )
    await hass.async_block_till_done()
    assert stub.calls == 1


async def test_check_firmware_updates_action_without_update_platform_raises(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-empty target without the update component raises
    ServiceValidationError (spec 0022 R1)."""
    handler = CloudHandler()
    stubs = await setup_firmware_entry(hass, monkeypatch, handler, make_info())
    component = hass.data.pop(DATA_COMPONENT)
    try:
        with pytest.raises(
            ServiceValidationError, match="No update entities are loaded"
        ):
            await hass.services.async_call(
                DOMAIN,
                "check_firmware_updates",
                {"entity_id": UPDATE_ENTITY_ID},
                blocking=True,
            )
    finally:
        hass.data[DATA_COMPONENT] = component
    await hass.async_block_till_done()
    assert stubs[SERIAL_NORMALIZED].calls == 1


async def test_check_firmware_updates_action_forced_probe_failure_raises(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed forced cloud probe raises HomeAssistantError (spec 0022 R2)."""
    handler = CloudHandler()
    stubs = await setup_firmware_entry(hass, monkeypatch, handler, make_info())
    handler.firmware_status = 500

    with pytest.raises(HomeAssistantError, match="Firmware check failed"):
        await hass.services.async_call(
            DOMAIN,
            "check_firmware_updates",
            {"entity_id": UPDATE_ENTITY_ID},
            blocking=True,
        )
    await hass.async_block_till_done()
    assert stubs[SERIAL_NORMALIZED].calls == 1


async def test_check_firmware_updates_action_forced_ble_failure_raises(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed forced BLE read raises HomeAssistantError (spec 0022 R2)."""
    handler = CloudHandler()
    stubs = await setup_firmware_entry(
        hass,
        monkeypatch,
        handler,
        make_info(),
        effect=HomeAssistantError("no address"),
    )
    stub = stubs[SERIAL_NORMALIZED]
    assert stub.calls == 1  # the bootstrap read failed silently

    with pytest.raises(HomeAssistantError, match="Firmware check failed"):
        await hass.services.async_call(
            DOMAIN,
            "check_firmware_updates",
            {"entity_id": UPDATE_ENTITY_ID},
            blocking=True,
        )
    await hass.async_block_till_done()
    assert stub.calls == 2


async def test_concurrent_forced_cycles_run_one_chain(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Overlapping forced checks share one cycle: the second call returns
    while the first is blocked in the BLE read, and the daily timer is
    re-armed once (spec 0010 R2)."""
    handler = CloudHandler()
    stubs = await setup_firmware_entry(hass, monkeypatch, handler, make_info())
    stub = stubs[SERIAL_NORMALIZED]
    assert stub.calls == 1

    handler.firmware_payload = NEWER_PAYLOAD
    entity = hass.data[DATA_COMPONENT].get_entity(UPDATE_ENTITY_ID)
    assert isinstance(entity, DanalockFirmwareUpdateEntity)
    stub.gate = asyncio.Event()
    first = asyncio.create_task(entity.async_update())
    second = asyncio.create_task(entity.async_update())

    for _ in range(20):
        await asyncio.sleep(0)
    assert stub.calls == 2  # the first forced cycle is blocked in the read
    assert not first.done()
    assert second.done()  # the in-flight guard returned without a cycle
    assert second.exception() is None

    stub.gate.set()
    await first
    await hass.async_block_till_done()

    assert stub.calls == 2  # exactly one forced BLE read happened
    latest_probes = [m for m, p in handler.requests if p == FIRMWARE_LATEST_PATH]
    assert len(latest_probes) == 2  # bootstrap plus exactly one forced cycle
    assert hass.states.get(UPDATE_ENTITY_ID).state == "on"

    # the single chain re-armed: one daily cycle later, nothing extra ran
    handler.firmware_payload = load_fixture("firmware_latest.json")
    async_fire_time_changed(hass, dt_util.now() + timedelta(hours=25))
    await hass.async_block_till_done()
    assert stub.calls == 2
    latest_probes = [m for m, p in handler.requests if p == FIRMWARE_LATEST_PATH]
    assert len(latest_probes) == 3
    assert hass.states.get(UPDATE_ENTITY_ID).state == "off"
