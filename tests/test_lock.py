"""Lock, setting-select, and status binary sensor tests (spec 0004,
0010)."""

from __future__ import annotations

import logging

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_registry import RegistryEntryDisabler

from custom_components.danalock_ble.const import DOMAIN
from custom_components.danalock_ble.control import LockControlError
from custom_components.danalock_ble.lock import DanalockLockEntity
from custom_components.danalock_ble.select import DanalockSettingSelectEntity
from tests.conftest import (
    SERIAL_NORMALIZED,
    CloudHandler,
    inject_advertisement,
    make_broadcast_payload,
    make_entry,
    make_service_info,
    patch_cloud_transport,
    stage_pending_tokens,
)
from tests.test_control import FakeLock, install_fake_lock, make_settings
from tests.test_sensors import SELECT_SUFFIXES, entity_state, setup_with_bluetooth

STATUS_BINARY_SUFFIXES = ("jammed", "auto_calibrated", "point_calibrated")
LOCK_ID = f"lock.danalock_ble_{SERIAL_NORMALIZED}_lock"


class StubSettingsControl:
    """Control test double for the settings select tests (spec 0010)."""

    def __init__(
        self,
        serial: str,
        settings: LockSettings | None = None,
        effects: dict[str, Exception] | None = None,
    ) -> None:
        self.serial = serial
        self.settings_calls = 0
        self.writes: list[tuple[str, int]] = []
        self.settings_result = settings if settings is not None else make_settings()
        self.effects = effects or {}

    async def settings(self) -> LockSettings:
        self.settings_calls += 1
        if (effect := self.effects.get("settings")) is not None:
            raise effect
        return self.settings_result

    async def set_auto_lock(self, value: int) -> LockSettings:
        return await self._write("set_auto_lock", value)

    async def set_brake_and_go_back(self, value: int) -> LockSettings:
        return await self._write("set_brake_and_go_back", value)

    async def set_end_to_end(self, value: int) -> LockSettings:
        return await self._write("set_end_to_end", value)

    async def _write(self, name: str, value: int) -> LockSettings:
        self.writes.append((name, value))
        if (effect := self.effects.get(name)) is not None:
            raise effect
        return self.settings_result

    async def set_key(self, key: object) -> None:
        """Accepted for key manager compatibility."""

    async def disconnect(self) -> None:
        """Nothing to disconnect."""


def install_stub_settings_controls(
    monkeypatch: pytest.MonkeyPatch,
    settings: LockSettings | None = None,
    effects: dict[str, Exception] | None = None,
) -> dict[str, StubSettingsControl]:
    """Route control construction to settings stubs; stubs by serial."""
    stubs: dict[str, StubSettingsControl] = {}

    def factory(_hass: HomeAssistant, serial: str, *_args: object) -> StubSettingsControl:
        stub = StubSettingsControl(serial, settings=settings, effects=effects)
        stubs[serial] = stub
        return stub

    monkeypatch.setattr("custom_components.danalock_ble.DanalockControl", factory)
    return stubs


async def setup_with_fake_lock(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> tuple[MockConfigEntry, FakeLock]:
    """An entry set up with the bluetooth machinery and a fake facade.

    The transport factory seam is nulled so the entity tests exercise the
    control semantics without a BLE stack; the factory is covered in
    tests/test_control.py.
    """
    install_fake_lock(monkeypatch)
    entry = await setup_with_bluetooth(hass, monkeypatch)
    fake = FakeLock.instances[-1]
    fake.transport_factory = None
    return entry, fake


async def setup_with_enabled_selects(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    settings: LockSettings | None = None,
    effects: dict[str, Exception] | None = None,
) -> tuple[MockConfigEntry, StubSettingsControl]:
    """An entry with the bluetooth machinery, a settings stub control, and
    all three setting selects enabled (spec 0010)."""
    stubs = install_stub_settings_controls(monkeypatch, settings, effects)
    entry = await setup_with_bluetooth(hass, monkeypatch)
    registry = er.async_get(hass)
    for suffix in SELECT_SUFFIXES:
        registry.async_update_entity(
            f"select.danalock_ble_{SERIAL_NORMALIZED}_{suffix}", disabled_by=None
        )
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    return entry, stubs[SERIAL_NORMALIZED]


@pytest.fixture(autouse=True)
def expected_lingering_timers() -> bool:
    """Bluetooth manager timers survive single-shot advertisement injections."""
    return True


async def test_registry_flags(
    enable_bluetooth: None,
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The lock entity is the primary feature; selects are config and
    disabled by the integration; status binary sensors are diagnostic;
    twist assist is a diagnostic binary sensor, not a select
    (spec 0004 R1/R3/R4/R5, 0010 R1/R2)."""
    entry = await setup_with_bluetooth(hass, monkeypatch)
    registry = er.async_get(hass)
    registry_entries = {
        registry_entry.unique_id: registry_entry
        for registry_entry in er.async_entries_for_config_entry(registry, entry.entry_id)
    }

    lock_entry = registry_entries[f"{SERIAL_NORMALIZED}_lock"]
    assert lock_entry.entity_id == LOCK_ID
    assert lock_entry.entity_category is None

    for suffix in SELECT_SUFFIXES:
        select_entry = registry_entries[f"{SERIAL_NORMALIZED}_{suffix}"]
        assert select_entry.entity_id == f"select.danalock_ble_{SERIAL_NORMALIZED}_{suffix}"
        assert select_entry.entity_category == "config"
        assert select_entry.disabled_by is RegistryEntryDisabler.INTEGRATION

    for suffix in STATUS_BINARY_SUFFIXES:
        status_entry = registry_entries[f"{SERIAL_NORMALIZED}_{suffix}"]
        assert status_entry.entity_id == f"binary_sensor.danalock_ble_{SERIAL_NORMALIZED}_{suffix}"
        assert status_entry.entity_category == "diagnostic"

    twist_assist = registry_entries[f"{SERIAL_NORMALIZED}_twist_assist"]
    assert twist_assist.entity_id == f"binary_sensor.danalock_ble_{SERIAL_NORMALIZED}_twist_assist"
    assert twist_assist.entity_category == "diagnostic"
    assert twist_assist.disabled_by is RegistryEntryDisabler.INTEGRATION

    entity_ids = {
        registry_entry.entity_id
        for registry_entry in er.async_entries_for_config_entry(registry, entry.entry_id)
    }
    assert f"select.danalock_ble_{SERIAL_NORMALIZED}_twist_assist" not in entity_ids


async def test_lock_state_follows_broadcast(
    enable_bluetooth: None,
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The lock state derives from the locked flag (spec 0004 R1)."""
    await setup_with_bluetooth(hass, monkeypatch)

    assert hass.states.get(LOCK_ID).state == "unavailable"

    inject_advertisement(
        hass, make_service_info(make_broadcast_payload(counter=1, lock_flags=0b00001))
    )
    await hass.async_block_till_done()
    assert hass.states.get(LOCK_ID).state == "locked"
    assert hass.states.get(LOCK_ID).attributes["supported_features"] == 0

    inject_advertisement(
        hass, make_service_info(make_broadcast_payload(counter=2, lock_flags=0b00000))
    )
    await hass.async_block_till_done()
    assert hass.states.get(LOCK_ID).state == "unlocked"


async def test_lock_carries_device_flags_attributes(
    enable_bluetooth: None,
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Device flags and the broadcast version ride on the lock entity
    (spec 0004 R1)."""
    await setup_with_bluetooth(hass, monkeypatch)

    inject_advertisement(
        hass,
        make_service_info(
            make_broadcast_payload(counter=1, device_flags=0b00111, lock_flags=0b1)
        ),
    )
    await hass.async_block_till_done()

    attributes = hass.states.get(LOCK_ID).attributes
    assert attributes["utc_reliable"] is True
    assert attributes["local_time_reliable"] is True
    assert attributes["battery_powered"] is True
    assert attributes["ext_board_present"] is False
    assert attributes["ext_board_online"] is False
    assert attributes["broadcast_version"] == 2

    inject_advertisement(
        hass, make_service_info(make_broadcast_payload(counter=2, device_flags=0b00000))
    )
    await hass.async_block_till_done()
    attributes = hass.states.get(LOCK_ID).attributes
    assert attributes["utc_reliable"] is False
    assert attributes["battery_powered"] is False


async def test_unlock_command_reports_progress_and_optimistic_state(
    enable_bluetooth: None,
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unlock shows unlocking during the command, the optimistic unlocked
    state after success, and the real state once a fresh broadcast arrives
    (spec 0006 R4)."""
    entry, fake = await setup_with_fake_lock(hass, monkeypatch)
    assert fake.serial == bytes.fromhex(SERIAL_NORMALIZED)
    assert fake.login_token == entry.runtime_data.keys[SERIAL_NORMALIZED].login_blob
    assert fake.insecure is True

    inject_advertisement(
        hass, make_service_info(make_broadcast_payload(counter=1, lock_flags=0b00001))
    )
    await hass.async_block_till_done()
    assert hass.states.get(LOCK_ID).state == "locked"

    snapshots: list[str] = []
    fake.on_command = lambda: snapshots.append(hass.states.get(LOCK_ID).state)

    await hass.services.async_call("lock", "unlock", {"entity_id": LOCK_ID}, blocking=True)
    await hass.async_block_till_done()

    assert fake.calls == ["unlock"]
    assert snapshots == ["unlocking"]
    assert hass.states.get(LOCK_ID).state == "unlocked"
    assert entry.runtime_data.monitor.states[SERIAL_NORMALIZED].pending_locked is False

    # a counter-advancing broadcast of the real (still locked) state wins
    inject_advertisement(
        hass, make_service_info(make_broadcast_payload(counter=2, lock_flags=0b00001))
    )
    await hass.async_block_till_done()
    assert hass.states.get(LOCK_ID).state == "locked"
    assert entry.runtime_data.monitor.states[SERIAL_NORMALIZED].pending_locked is None


async def test_lock_command_reports_progress_and_optimistic_state(
    enable_bluetooth: None,
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Lock is symmetric to unlock (spec 0006 R4)."""
    _, fake = await setup_with_fake_lock(hass, monkeypatch)

    inject_advertisement(
        hass, make_service_info(make_broadcast_payload(counter=1, lock_flags=0b00000))
    )
    await hass.async_block_till_done()
    assert hass.states.get(LOCK_ID).state == "unlocked"

    snapshots: list[str] = []
    fake.on_command = lambda: snapshots.append(hass.states.get(LOCK_ID).state)

    await hass.services.async_call("lock", "lock", {"entity_id": LOCK_ID}, blocking=True)
    await hass.async_block_till_done()

    assert fake.calls == ["lock"]
    assert snapshots == ["locking"]
    assert hass.states.get(LOCK_ID).state == "locked"

    inject_advertisement(
        hass, make_service_info(make_broadcast_payload(counter=2, lock_flags=0b00000))
    )
    await hass.async_block_till_done()
    assert hass.states.get(LOCK_ID).state == "unlocked"


async def test_lock_command_error_keeps_reported_state(
    enable_bluetooth: None,
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed command propagates, keeps the reported state, and sets no
    optimistic pending state (spec 0006 R4/R5)."""
    entry, fake = await setup_with_fake_lock(hass, monkeypatch)

    inject_advertisement(
        hass, make_service_info(make_broadcast_payload(counter=1, lock_flags=0b00001))
    )
    await hass.async_block_till_done()
    assert hass.states.get(LOCK_ID).state == "locked"

    fake.effects["unlock"] = HomeAssistantError("boom")
    with pytest.raises(HomeAssistantError, match="boom"):
        await hass.services.async_call("lock", "unlock", {"entity_id": LOCK_ID}, blocking=True)
    await hass.async_block_till_done()

    assert hass.states.get(LOCK_ID).state == "locked"
    assert entry.runtime_data.monitor.states[SERIAL_NORMALIZED].pending_locked is None


async def test_lock_state_binary_sensor_ignores_pending(
    enable_bluetooth: None,
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The lock-state binary sensor keeps showing the broadcast value while
    the lock entity shows the optimistic state (spec 0006 R4)."""
    entry, fake = await setup_with_fake_lock(hass, monkeypatch)

    inject_advertisement(
        hass, make_service_info(make_broadcast_payload(counter=1, lock_flags=0b00001))
    )
    await hass.async_block_till_done()

    await hass.services.async_call("lock", "unlock", {"entity_id": LOCK_ID}, blocking=True)
    await hass.async_block_till_done()

    assert hass.states.get(LOCK_ID).state == "unlocked"
    assert entity_state(hass, entry, f"{SERIAL_NORMALIZED}_lock_state").state == "off"


async def test_lock_entity_without_control_raises(
    enable_bluetooth: None,
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A device without a control raises HomeAssistantError (spec 0022 R4)."""
    import custom_components.danalock_ble

    monkeypatch.setattr(
        custom_components.danalock_ble, "DanalockControl", lambda *args, **kwargs: None
    )
    await setup_with_bluetooth(hass, monkeypatch)
    inject_advertisement(
        hass, make_service_info(make_broadcast_payload(counter=1, lock_flags=0b00001))
    )
    await hass.async_block_till_done()

    with pytest.raises(HomeAssistantError, match="no key"):
        await hass.services.async_call(
            "lock", "unlock", {"entity_id": LOCK_ID}, blocking=True
        )
    await hass.async_block_till_done()

    assert hass.states.get(LOCK_ID).state == "locked"


async def test_status_binary_sensors_follow_flags(
    enable_bluetooth: None,
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Jammed and calibration sensors follow their lock flags
    (spec 0004 R3/R4)."""
    entry = await setup_with_bluetooth(hass, monkeypatch)
    # bit0 locked, bit1 auto_calibrated, bit3 blocked
    inject_advertisement(
        hass, make_service_info(make_broadcast_payload(counter=1, lock_flags=0b01011))
    )
    await hass.async_block_till_done()

    assert entity_state(hass, entry, f"{SERIAL_NORMALIZED}_jammed").state == "on"
    assert (
        entity_state(hass, entry, f"{SERIAL_NORMALIZED}_auto_calibrated").state == "on"
    )
    assert (
        entity_state(hass, entry, f"{SERIAL_NORMALIZED}_point_calibrated").state == "off"
    )

    inject_advertisement(
        hass, make_service_info(make_broadcast_payload(counter=2, lock_flags=0b00001))
    )
    await hass.async_block_till_done()
    assert entity_state(hass, entry, f"{SERIAL_NORMALIZED}_jammed").state == "off"
    assert (
        entity_state(hass, entry, f"{SERIAL_NORMALIZED}_auto_calibrated").state == "off"
    )


async def test_selects_read_settings_once_when_added(
    enable_bluetooth: None,
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Enabling the selects performs exactly one shared settings read and
    all three options render from the verified values (spec 0010 R1/R5)."""
    entry, stub = await setup_with_enabled_selects(
        hass,
        monkeypatch,
        settings=make_settings(
            auto_lock_time=60, brake_and_go_back_time=15, end_to_end_mode=1
        ),
    )

    inject_advertisement(
        hass, make_service_info(make_broadcast_payload(counter=1, lock_flags=0b00001))
    )
    await hass.async_block_till_done()

    assert stub.settings_calls == 1
    assert stub.writes == []
    auto_lock = entity_state(hass, entry, f"{SERIAL_NORMALIZED}_auto_lock")
    assert auto_lock.state == "60_s"
    assert auto_lock.attributes["options"] == [
        "off", "5_s", "10_s", "15_s", "30_s", "45_s", "60_s",
        "180_s", "300_s", "600_s", "900_s",
    ]
    assert (
        entity_state(hass, entry, f"{SERIAL_NORMALIZED}_brake_and_go_back").state
        == "15_s"
    )
    assert (
        entity_state(hass, entry, f"{SERIAL_NORMALIZED}_blocked_to_blocked").state
        == "one"
    )


async def test_settings_read_failure_leaves_selects_unknown(
    enable_bluetooth: None,
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A failed read at add is logged and not retried; the options stay
    unknown (spec 0010 R5)."""
    caplog.set_level(logging.DEBUG)
    entry, stub = await setup_with_enabled_selects(
        hass,
        monkeypatch,
        effects={"settings": LockControlError("Failed to reach the lock over Bluetooth: no result within 10s")},
    )

    inject_advertisement(
        hass, make_service_info(make_broadcast_payload(counter=1, lock_flags=0b00001))
    )
    await hass.async_block_till_done()

    assert stub.settings_calls == 1
    for suffix in SELECT_SUFFIXES:
        assert entity_state(hass, entry, f"{SERIAL_NORMALIZED}_{suffix}").state == "unknown"
    assert "settings read failed" in caplog.text


async def test_select_option_writes_and_shows_verified_value(
    enable_bluetooth: None,
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Selecting an option writes the mapped value and shows the verified
    device value (spec 0010 R3)."""
    entry, stub = await setup_with_enabled_selects(
        hass, monkeypatch, settings=make_settings(auto_lock_time=60)
    )

    inject_advertisement(
        hass, make_service_info(make_broadcast_payload(counter=1, lock_flags=0b00001))
    )
    await hass.async_block_till_done()
    assert entity_state(hass, entry, f"{SERIAL_NORMALIZED}_auto_lock").state == "60_s"

    stub.settings_result = make_settings(auto_lock_time=300)
    await hass.services.async_call(
        "select",
        "select_option",
        {
            "entity_id": f"select.danalock_ble_{SERIAL_NORMALIZED}_auto_lock",
            "option": "300_s",
        },
        blocking=True,
    )
    await hass.async_block_till_done()

    assert stub.writes == [("set_auto_lock", 300)]
    assert entity_state(hass, entry, f"{SERIAL_NORMALIZED}_auto_lock").state == "300_s"


async def test_blocked_to_blocked_select_drives_end_to_end_mode(
    enable_bluetooth: None,
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The blocked-to-blocked select maps Off/One/Two to the end-to-end
    mode values 0/1/2 (spec 0010 R1)."""
    entry, stub = await setup_with_enabled_selects(hass, monkeypatch)

    inject_advertisement(
        hass, make_service_info(make_broadcast_payload(counter=1, lock_flags=0b00001))
    )
    await hass.async_block_till_done()

    stub.settings_result = make_settings(end_to_end_mode=2)
    await hass.services.async_call(
        "select",
        "select_option",
        {
            "entity_id": f"select.danalock_ble_{SERIAL_NORMALIZED}_blocked_to_blocked",
            "option": "two",
        },
        blocking=True,
    )
    await hass.async_block_till_done()

    assert stub.writes == [("set_end_to_end", 2)]
    assert (
        entity_state(hass, entry, f"{SERIAL_NORMALIZED}_blocked_to_blocked").state
        == "two"
    )

    stub.settings_result = make_settings(end_to_end_mode=0)
    await hass.services.async_call(
        "select",
        "select_option",
        {
            "entity_id": f"select.danalock_ble_{SERIAL_NORMALIZED}_blocked_to_blocked",
            "option": "off",
        },
        blocking=True,
    )
    await hass.async_block_till_done()

    assert stub.writes == [("set_end_to_end", 2), ("set_end_to_end", 0)]
    assert (
        entity_state(hass, entry, f"{SERIAL_NORMALIZED}_blocked_to_blocked").state
        == "off"
    )


async def test_select_option_error_keeps_option(
    enable_bluetooth: None,
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rejected write raises HomeAssistantError with the status message
    and keeps the previous option (spec 0010 R3)."""
    entry, stub = await setup_with_enabled_selects(
        hass, monkeypatch, settings=make_settings(auto_lock_time=60)
    )

    inject_advertisement(
        hass, make_service_info(make_broadcast_payload(counter=1, lock_flags=0b00001))
    )
    await hass.async_block_till_done()
    assert entity_state(hass, entry, f"{SERIAL_NORMALIZED}_auto_lock").state == "60_s"

    stub.effects["set_auto_lock"] = LockControlError(
        "The lock rejected the change: the account does not have the permission "
        "to change this setting."
    )
    with pytest.raises(HomeAssistantError, match="permission"):
        await hass.services.async_call(
            "select",
            "select_option",
            {
                "entity_id": f"select.danalock_ble_{SERIAL_NORMALIZED}_auto_lock",
                "option": "300_s",
            },
            blocking=True,
        )
    await hass.async_block_till_done()

    assert entity_state(hass, entry, f"{SERIAL_NORMALIZED}_auto_lock").state == "60_s"


async def test_select_option_unsupported_argument_maps_message(
    enable_bluetooth: None,
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An UNSUPPORTED_ARGUMENT rejection surfaces its message
    (spec 0010 R3)."""
    entry, stub = await setup_with_enabled_selects(
        hass,
        monkeypatch,
        effects={"set_end_to_end": LockControlError("The lock does not support this setting or value.")},
    )

    inject_advertisement(
        hass, make_service_info(make_broadcast_payload(counter=1, lock_flags=0b00001))
    )
    await hass.async_block_till_done()

    with pytest.raises(HomeAssistantError, match="does not support"):
        await hass.services.async_call(
            "select",
            "select_option",
            {
                "entity_id": f"select.danalock_ble_{SERIAL_NORMALIZED}_blocked_to_blocked",
                "option": "two",
            },
            blocking=True,
        )


async def test_select_invalid_option_is_service_validation_error(
    enable_bluetooth: None,
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unknown option maps to ServiceValidationError, not a bare
    ValueError (spec 0022 R5)."""
    _, stub = await setup_with_enabled_selects(hass, monkeypatch)

    inject_advertisement(
        hass, make_service_info(make_broadcast_payload(counter=1, lock_flags=0b00001))
    )
    await hass.async_block_till_done()

    entity = hass.data["select"].get_entity(
        f"select.danalock_ble_{SERIAL_NORMALIZED}_auto_lock"
    )
    assert isinstance(entity, DanalockSettingSelectEntity)
    with pytest.raises(ServiceValidationError, match="not a valid option"):
        entity._value_for("not_an_option")
    with pytest.raises(ServiceValidationError, match="not a valid option"):
        await entity.async_select_option("not_an_option")
    assert stub.writes == []


async def test_nonpreset_value_reports_unknown_and_logs(
    enable_bluetooth: None,
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A verified value without a matching option renders unknown and is
    debug-logged once (spec 0010 R6)."""
    caplog.set_level(logging.DEBUG)
    entry, stub = await setup_with_enabled_selects(
        hass, monkeypatch, settings=make_settings(auto_lock_time=77)
    )

    inject_advertisement(
        hass, make_service_info(make_broadcast_payload(counter=1, lock_flags=0b00001))
    )
    await hass.async_block_till_done()

    assert entity_state(hass, entry, f"{SERIAL_NORMALIZED}_auto_lock").state == "unknown"
    assert entity_state(
        hass, entry, f"{SERIAL_NORMALIZED}_brake_and_go_back"
    ).state == "off"
    assert "non-preset value" in caplog.text


async def test_select_without_control_raises(
    enable_bluetooth: None,
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A device without a control raises HomeAssistantError (spec 0022 R5).

    The enabled registry entries are pre-created so the first setup adds
    the selects without a reload (a None control must not survive an
    unload).
    """
    import custom_components.danalock_ble

    monkeypatch.setattr(
        custom_components.danalock_ble, "DanalockControl", lambda *args, **kwargs: None
    )
    patch_cloud_transport(monkeypatch, CloudHandler())
    entry = make_entry(hass)
    registry = er.async_get(hass)
    for suffix in SELECT_SUFFIXES:
        registry.async_get_or_create(
            "select", DOMAIN, f"{SERIAL_NORMALIZED}_{suffix}", config_entry=entry
        )
    stage_pending_tokens(hass, entry.data["username"])
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    inject_advertisement(
        hass, make_service_info(make_broadcast_payload(counter=1, lock_flags=0b00001))
    )
    await hass.async_block_till_done()
    assert entity_state(hass, entry, f"{SERIAL_NORMALIZED}_auto_lock").state == "unknown"

    with pytest.raises(HomeAssistantError, match="no key"):
        await hass.services.async_call(
            "select",
            "select_option",
            {
                "entity_id": f"select.danalock_ble_{SERIAL_NORMALIZED}_auto_lock",
                "option": "300_s",
            },
            blocking=True,
        )
    await hass.async_block_till_done()

    assert entity_state(hass, entry, f"{SERIAL_NORMALIZED}_auto_lock").state == "unknown"


async def test_twist_assist_binary_sensor_follows_flag(
    enable_bluetooth: None,
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The twist assist diagnostic binary sensor follows its transient
    broadcast flag (spec 0010 R2)."""
    entry = await setup_with_bluetooth(hass, monkeypatch)
    registry = er.async_get(hass)
    registry.async_update_entity(
        f"binary_sensor.danalock_ble_{SERIAL_NORMALIZED}_twist_assist", disabled_by=None
    )
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    # bit7 twist_assist, bit0 locked
    inject_advertisement(
        hass, make_service_info(make_broadcast_payload(counter=1, lock_flags=0b10000001))
    )
    await hass.async_block_till_done()
    assert entity_state(hass, entry, f"{SERIAL_NORMALIZED}_twist_assist").state == "on"

    inject_advertisement(
        hass, make_service_info(make_broadcast_payload(counter=2, lock_flags=0b00000001))
    )
    await hass.async_block_till_done()
    assert entity_state(hass, entry, f"{SERIAL_NORMALIZED}_twist_assist").state == "off"


async def test_lock_entity_without_report_reports_unknown(
    enable_bluetooth: None,
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without a decoded report the lock reports unknown (spec 0004 R1)."""
    entry = await setup_with_bluetooth(hass, monkeypatch)
    state = entry.runtime_data.monitor.states[SERIAL_NORMALIZED]
    state.report = None
    state.pending_locked = None

    entity = DanalockLockEntity(entry, entry.runtime_data.monitor, state, None)

    assert entity.is_locked is None
    assert entity.extra_state_attributes is None


async def test_select_value_for_rejects_unknown_option(
    enable_bluetooth: None,
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unknown option has no preset value and is rejected (spec 0010 R3).

    Spec 0022 changed `_value_for` to raise `ServiceValidationError` instead
    of a bare `ValueError`; after the spec-0025 rebase the expectation is
    tightened to the service-validation exception alone.
    """
    entry = await setup_with_bluetooth(hass, monkeypatch)
    state = entry.runtime_data.monitor.states[SERIAL_NORMALIZED]
    entity = DanalockSettingSelectEntity(
        entry, entry.runtime_data.monitor, state, None, "auto_lock"
    )

    with pytest.raises(ServiceValidationError):
        entity._value_for("bogus")
