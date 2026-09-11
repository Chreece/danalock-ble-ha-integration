"""Entity platform tests for sensors and the lock-state binary sensor (spec 0002)."""

from __future__ import annotations

import time
from typing import Any

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.danalock_ble.const import DOMAIN
from tests.conftest import (
    SERIAL_NORMALIZED,
    CloudHandler,
    inject_advertisement,
    load_fixture,
    make_broadcast_payload,
    make_service_info,
    setup_entry,
)
from custom_components.danalock_ble.binary_sensor import (
    DanalockLockStateBinarySensor,
    DanalockStatusBinarySensor,
)
from custom_components.danalock_ble.sensor import (
    DanalockBatteryLevelSensor,
    DanalockSignalStrengthSensor,
    DanalockUpdateCounterSensor,
)

SENSOR_SUFFIXES = (
    "battery_level",
    "key_valid_until",
    "rssi",
    "signal_strength",
    "update_counter",
)
BINARY_SENSOR_SUFFIXES = (
    "auto_calibrated",
    "jammed",
    "lock_state",
    "point_calibrated",
    "twist_assist",
)
LOCK_SUFFIXES = ("lock",)
SELECT_SUFFIXES = (
    "auto_lock",
    "brake_and_go_back",
    "blocked_to_blocked",
)
UPDATE_SUFFIXES = ("firmware",)


@pytest.fixture(autouse=True)
def expected_lingering_timers() -> bool:
    """Bluetooth manager timers survive single-shot advertisement injections."""
    return True


def registry_entry_for_unique_id(
    hass: HomeAssistant, entry: MockConfigEntry, unique_id: str
) -> er.RegistryEntry:
    """The entity registry entry for a unique_id of this config entry."""
    registry = er.async_get(hass)
    matches = [
        registry_entry
        for registry_entry in er.async_entries_for_config_entry(registry, entry.entry_id)
        if registry_entry.unique_id == unique_id
    ]
    assert len(matches) == 1, f"missing entity {unique_id}"
    return matches[0]


def entity_state(hass: HomeAssistant, entry: MockConfigEntry, unique_id: str) -> Any:
    """The current state object of an entity by unique_id."""
    return hass.states.get(registry_entry_for_unique_id(hass, entry, unique_id).entity_id)


async def setup_with_bluetooth(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    handler: CloudHandler | None = None,
) -> MockConfigEntry:
    """An entry set up with the bluetooth machinery enabled."""
    entry = await setup_entry(hass, monkeypatch, handler or CloudHandler())
    await hass.async_block_till_done()
    return entry


async def test_platform_entities_created(
    enable_bluetooth: None,
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One entity per spec 0002/0004 suffix, linked to the device."""
    entry = await setup_with_bluetooth(hass, monkeypatch)
    registry = er.async_get(hass)
    unique_ids = {
        registry_entry.unique_id
        for registry_entry in er.async_entries_for_config_entry(registry, entry.entry_id)
    }
    assert unique_ids == {
        f"{SERIAL_NORMALIZED}_{suffix}"
        for suffix in SENSOR_SUFFIXES
        + BINARY_SENSOR_SUFFIXES
        + LOCK_SUFFIXES
        + SELECT_SUFFIXES
        + UPDATE_SUFFIXES
    }

    device = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)[0]
    for suffix in (
        SENSOR_SUFFIXES + BINARY_SENSOR_SUFFIXES + LOCK_SUFFIXES + SELECT_SUFFIXES + UPDATE_SUFFIXES
    ):
        assert (
            registry_entry_for_unique_id(hass, entry, f"{SERIAL_NORMALIZED}_{suffix}").device_id
            == device.id
        )


async def test_device_name_comes_from_the_cloud(
    enable_bluetooth: None,
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The friendly name is the cloud device name (spec 0002 R1)."""
    entry = await setup_with_bluetooth(hass, monkeypatch)
    device = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)[0]
    assert device.name == "Fixture Lock"


async def test_device_name_falls_back_to_serial(
    enable_bluetooth: None,
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without a cloud name the device is named danalock_<serial>."""
    fixture = load_fixture("login_tokens_v1.json")[0]
    del fixture["device"]["name"]
    entry = await setup_with_bluetooth(
        hass, monkeypatch, CloudHandler(devices_payload=[fixture])
    )
    device = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)[0]
    assert device.name == f"danalock_{SERIAL_NORMALIZED}"
    registry = er.async_get(hass)
    entry_ids = {
        registry_entry.entity_id
        for registry_entry in er.async_entries_for_config_entry(registry, entry.entry_id)
    }
    assert f"binary_sensor.danalock_ble_{SERIAL_NORMALIZED}_lock_state" in entry_ids


async def test_entity_ids_are_serial_prefixed(
    enable_bluetooth: None,
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Entity ids are danalock_ble_<serial>_<suffix>, independent of the cloud
    device name (spec 0002 R5)."""
    entry = await setup_with_bluetooth(hass, monkeypatch)
    registry = er.async_get(hass)
    entity_ids = {
        registry_entry.entity_id
        for registry_entry in er.async_entries_for_config_entry(registry, entry.entry_id)
    }
    expected = {
        f"binary_sensor.danalock_ble_{SERIAL_NORMALIZED}_{suffix}"
        for suffix in BINARY_SENSOR_SUFFIXES
    }
    expected |= {
        f"sensor.danalock_ble_{SERIAL_NORMALIZED}_{suffix}" for suffix in SENSOR_SUFFIXES
    }
    expected |= {f"lock.danalock_ble_{SERIAL_NORMALIZED}_{suffix}" for suffix in LOCK_SUFFIXES}
    expected |= {
        f"select.danalock_ble_{SERIAL_NORMALIZED}_{suffix}" for suffix in SELECT_SUFFIXES
    }
    expected |= {
        f"update.danalock_ble_{SERIAL_NORMALIZED}_{suffix}" for suffix in UPDATE_SUFFIXES
    }
    assert entity_ids == expected


async def test_friendly_names_use_the_cloud_name(
    enable_bluetooth: None,
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The cloud device name prefixes the friendly names (spec 0002 R1/R5)."""
    entry = await setup_with_bluetooth(hass, monkeypatch)

    lock_state = entity_state(hass, entry, f"{SERIAL_NORMALIZED}_lock_state")
    assert lock_state.attributes["friendly_name"] == "Fixture Lock Lock state"
    rssi = entity_state(hass, entry, f"{SERIAL_NORMALIZED}_rssi")
    assert rssi.attributes["friendly_name"] == "Fixture Lock RSSI"


async def test_entity_ids_survive_reload(
    enable_bluetooth: None,
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Registered entities keep their ids across reloads; the registry
    entry wins over the suggestion (spec 0002 R5)."""
    entry = await setup_with_bluetooth(hass, monkeypatch)
    registry = er.async_get(hass)
    before = {
        registry_entry.unique_id: registry_entry.entity_id
        for registry_entry in er.async_entries_for_config_entry(registry, entry.entry_id)
    }

    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    after = {
        registry_entry.unique_id: registry_entry.entity_id
        for registry_entry in er.async_entries_for_config_entry(registry, entry.entry_id)
    }
    assert after == before
    assert f"binary_sensor.danalock_ble_{SERIAL_NORMALIZED}_lock_state" in after.values()


async def test_advertisement_updates_entities(
    enable_bluetooth: None,
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A decoded advertisement drives every entity value (spec 0002 R2/R4)."""
    entry = await setup_with_bluetooth(hass, monkeypatch)

    inject_advertisement(
        hass,
        make_service_info(
            make_broadcast_payload(counter=7, device_flags=0b00100, battery_raw=137, lock_flags=0b1),
            rssi=-65,
        ),
    )
    await hass.async_block_till_done()

    assert entity_state(hass, entry, f"{SERIAL_NORMALIZED}_lock_state").state == "off"
    lock_state_attributes = entity_state(
        hass, entry, f"{SERIAL_NORMALIZED}_lock_state"
    ).attributes
    assert "locked" not in lock_state_attributes
    assert "utc_reliable" not in lock_state_attributes
    assert entity_state(hass, entry, f"{SERIAL_NORMALIZED}_signal_strength").state == "50"
    assert (
        entity_state(hass, entry, f"{SERIAL_NORMALIZED}_signal_strength").attributes["rssi"]
        == -65
    )
    assert entity_state(hass, entry, f"{SERIAL_NORMALIZED}_rssi").state == "-65"
    assert entity_state(hass, entry, f"{SERIAL_NORMALIZED}_update_counter").state == "7"
    assert entity_state(hass, entry, f"{SERIAL_NORMALIZED}_battery_level").state == "137"
    assert (
        entity_state(hass, entry, f"{SERIAL_NORMALIZED}_key_valid_until").state
        == "2099-01-01T00:00:00+00:00"
    )


async def test_numeric_sensors_carry_state_class(
    enable_bluetooth: None,
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The counter and battery sensors are numeric for the recorder
    (spec 0004 R6); the battery is an integer percent."""
    entry = await setup_with_bluetooth(hass, monkeypatch)

    counter = entity_state(hass, entry, f"{SERIAL_NORMALIZED}_update_counter")
    assert counter.attributes["state_class"] == "measurement"
    battery = entity_state(hass, entry, f"{SERIAL_NORMALIZED}_battery_level")
    assert battery.attributes["state_class"] == "measurement"
    assert battery.attributes["unit_of_measurement"] == "%"
    assert battery.attributes["device_class"] == "battery"


async def test_unlocked_advertisement_flips_binary_sensor(
    enable_bluetooth: None,
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unlocked lock flags show as on (spec 0002 R2)."""
    entry = await setup_with_bluetooth(hass, monkeypatch)

    inject_advertisement(hass, make_service_info(make_broadcast_payload(counter=1, lock_flags=0)))
    await hass.async_block_till_done()

    assert entity_state(hass, entry, f"{SERIAL_NORMALIZED}_lock_state").state == "on"


async def test_entities_without_data_report_unknown(
    enable_bluetooth: None,
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Before any advertisement the value properties report unknown."""
    entry = await setup_with_bluetooth(hass, monkeypatch)
    runtime = entry.runtime_data
    state = runtime.monitor.states[SERIAL_NORMALIZED]

    signal = DanalockSignalStrengthSensor(entry, runtime.monitor, state)
    assert signal.native_value is None
    assert signal.extra_state_attributes is None
    assert DanalockUpdateCounterSensor(entry, runtime.monitor, state).native_value is None
    battery = DanalockBatteryLevelSensor(entry, runtime.monitor, state)
    assert battery.native_value is None
    lock = DanalockLockStateBinarySensor(entry, runtime.monitor, state)
    assert lock.is_on is None
    assert lock.extra_state_attributes is None


async def test_rssi_refreshes_on_every_advertisement(
    enable_bluetooth: None,
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A dispatched advertisement with an unchanged counter refreshes the
    signal entities while the state entities keep the stored report
    (spec 0002 R4)."""
    entry = await setup_with_bluetooth(hass, monkeypatch)

    inject_advertisement(
        hass, make_service_info(make_broadcast_payload(counter=1, battery_raw=137), rssi=-70)
    )
    await hass.async_block_till_done()
    assert entity_state(hass, entry, f"{SERIAL_NORMALIZED}_rssi").state == "-70"

    inject_advertisement(
        hass, make_service_info(make_broadcast_payload(counter=1, battery_raw=99), rssi=-80)
    )
    await hass.async_block_till_done()

    assert entity_state(hass, entry, f"{SERIAL_NORMALIZED}_rssi").state == "-80"
    assert entity_state(hass, entry, f"{SERIAL_NORMALIZED}_signal_strength").state == "29"
    assert (
        entity_state(hass, entry, f"{SERIAL_NORMALIZED}_signal_strength").attributes["rssi"]
        == -80
    )
    assert entity_state(hass, entry, f"{SERIAL_NORMALIZED}_update_counter").state == "1"
    assert entity_state(hass, entry, f"{SERIAL_NORMALIZED}_battery_level").state == "137"


async def test_rssi_entity_refreshes_via_poll(
    enable_bluetooth: None,
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Signal/RSSI entities also follow the history poll between payload
    changes (spec 0002 R4 + 0003 R2a)."""
    entry = await setup_with_bluetooth(hass, monkeypatch)

    inject_advertisement(hass, make_service_info(make_broadcast_payload(counter=1), rssi=-70))
    await hass.async_block_till_done()
    inject_advertisement(hass, make_service_info(make_broadcast_payload(counter=1), rssi=-80))
    await hass.async_block_till_done()
    assert entity_state(hass, entry, f"{SERIAL_NORMALIZED}_rssi").state == "-70"

    entry.runtime_data.monitor._refresh_states(None)
    await hass.async_block_till_done()

    assert entity_state(hass, entry, f"{SERIAL_NORMALIZED}_rssi").state == "-80"
    assert entity_state(hass, entry, f"{SERIAL_NORMALIZED}_signal_strength").state == "29"


@pytest.mark.parametrize(
    ("rssi", "expected"),
    [(-110, "0"), (-100, "0"), (-80, "29"), (-65, "50"), (-30, "100"), (-20, "100")],
)
async def test_signal_strength_mapping(
    enable_bluetooth: None,
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    rssi: int,
    expected: str,
) -> None:
    """The RSSI→percent mapping matches spec 0002 R2 (boundaries, clamping)."""
    entry = await setup_with_bluetooth(hass, monkeypatch)

    inject_advertisement(hass, make_service_info(make_broadcast_payload(counter=1), rssi=rssi))
    await hass.async_block_till_done()

    assert entity_state(hass, entry, f"{SERIAL_NORMALIZED}_signal_strength").state == expected


async def test_entities_go_unavailable_after_stale_timeout(
    enable_bluetooth: None,
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After 5 minutes without advertisements entities are unavailable
    (spec 0002 R3); the cloud-derived key sensor stays available."""
    entry = await setup_with_bluetooth(hass, monkeypatch)

    inject_advertisement(hass, make_service_info(make_broadcast_payload(counter=1)))
    await hass.async_block_till_done()
    assert entity_state(hass, entry, f"{SERIAL_NORMALIZED}_rssi").state == "-65"

    real_monotonic = time.monotonic
    monkeypatch.setattr(
        "custom_components.danalock_ble.broadcast.time",
        type("FakeTime", (), {"monotonic": staticmethod(lambda: real_monotonic() + 400)}),
    )
    entry.runtime_data.monitor._check_stale(None)
    await hass.async_block_till_done()

    assert entity_state(hass, entry, f"{SERIAL_NORMALIZED}_rssi").state == "unavailable"
    assert (
        entity_state(hass, entry, f"{SERIAL_NORMALIZED}_lock_state").state == "unavailable"
    )
    assert (
        entity_state(hass, entry, f"{SERIAL_NORMALIZED}_key_valid_until").state
        == "2099-01-01T00:00:00+00:00"
    )


async def test_entities_recover_after_fresh_advertisement(
    enable_bluetooth: None,
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A new advertisement after a stale period makes entities available."""
    entry = await setup_with_bluetooth(hass, monkeypatch)

    inject_advertisement(hass, make_service_info(make_broadcast_payload(counter=1)))
    await hass.async_block_till_done()

    real_monotonic = time.monotonic
    monkeypatch.setattr(
        "custom_components.danalock_ble.broadcast.time",
        type("FakeTime", (), {"monotonic": staticmethod(lambda: real_monotonic() + 400)}),
    )
    entry.runtime_data.monitor._check_stale(None)
    await hass.async_block_till_done()
    assert entity_state(hass, entry, f"{SERIAL_NORMALIZED}_rssi").state == "unavailable"

    monkeypatch.setattr("custom_components.danalock_ble.broadcast.time", time)
    inject_advertisement(
        hass, make_service_info(make_broadcast_payload(counter=2, battery_raw=90), rssi=-72)
    )
    await hass.async_block_till_done()

    assert entity_state(hass, entry, f"{SERIAL_NORMALIZED}_rssi").state == "-72"
    assert entity_state(hass, entry, f"{SERIAL_NORMALIZED}_battery_level").state == "90"


async def test_key_valid_until_follows_refreshed_key(
    enable_bluetooth: None,
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The key sensor re-renders when the manager refreshes the key, without
    an entity or entry reload (spec 0007 R11)."""
    handler = CloudHandler()
    entry = await setup_with_bluetooth(hass, monkeypatch, handler)

    refreshed = load_fixture("login_token_single.json")
    refreshed["metadata"]["valid_to"] = "2100-06-01T00:00:00+00:00"
    handler.single_payload = refreshed

    await entry.runtime_data.key_manager.refresh(SERIAL_NORMALIZED, force=True)
    await hass.async_block_till_done()

    assert (
        entity_state(hass, entry, f"{SERIAL_NORMALIZED}_key_valid_until").state
        == "2100-06-01T00:00:00+00:00"
    )


async def test_status_binary_sensor_without_report_is_unknown(
    enable_bluetooth: None,
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A status binary sensor without a report reports unknown (spec 0004 R3)."""
    entry = await setup_with_bluetooth(hass, monkeypatch)
    runtime = entry.runtime_data
    state = runtime.monitor.states[SERIAL_NORMALIZED]

    sensor = DanalockStatusBinarySensor(
        entry, runtime.monitor, state, "jammed", "blocked"
    )

    assert sensor.is_on is None
