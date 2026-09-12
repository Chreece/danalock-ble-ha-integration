"""Calibration button tests (spec 0008)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er

from pydanalock.ble import CommandError
from tests.conftest import (
    SERIAL_NORMALIZED,
    inject_advertisement,
    make_broadcast_payload,
    make_service_info,
)
from tests.test_lock import setup_with_fake_lock
from tests.test_sensors import setup_with_bluetooth

OPEN_ID = f"button.danalock_ble_{SERIAL_NORMALIZED}_set_point_open"
CLOSED_ID = f"button.danalock_ble_{SERIAL_NORMALIZED}_set_point_closed"
LOCK_ID = f"lock.danalock_ble_{SERIAL_NORMALIZED}_lock"


@pytest.fixture(autouse=True)
def expected_lingering_timers() -> bool:
    """Bluetooth manager timers survive single-shot advertisement injections."""
    return True


async def advertise_locked(hass: HomeAssistant) -> None:
    """Feed one locked advertisement so the address becomes known."""
    inject_advertisement(
        hass, make_service_info(make_broadcast_payload(counter=1, lock_flags=0b00001))
    )
    await hass.async_block_till_done()


async def test_registry_shape(
    enable_bluetooth: None,
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two config-category buttons per device, enabled by default (spec 0008 R3)."""
    entry, _ = await setup_with_fake_lock(hass, monkeypatch)
    registry = er.async_get(hass)
    entries = {
        registry_entry.unique_id: registry_entry
        for registry_entry in er.async_entries_for_config_entry(registry, entry.entry_id)
    }

    open_entry = entries[f"{SERIAL_NORMALIZED}_set_point_open"]
    closed_entry = entries[f"{SERIAL_NORMALIZED}_set_point_closed"]
    assert open_entry.entity_id == OPEN_ID
    assert closed_entry.entity_id == CLOSED_ID
    assert open_entry.entity_category == "config"
    assert closed_entry.entity_category == "config"
    assert open_entry.disabled_by is None
    assert closed_entry.disabled_by is None


async def test_press_open_sends_the_unlocked_point(
    enable_bluetooth: None,
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pressing the open button sends point 0 and writes no lock state
    (spec 0008 R5)."""
    entry, fake = await setup_with_fake_lock(hass, monkeypatch)
    await advertise_locked(hass)

    await hass.services.async_call(
        "button", "press", {"entity_id": OPEN_ID}, blocking=True
    )
    await hass.async_block_till_done()

    assert fake.calls == ["set_calibration_point"]
    assert fake.calibration_points == [0]
    assert fake.disconnects == 1
    assert hass.states.get(LOCK_ID).state == "locked"
    assert entry.runtime_data.monitor.states[SERIAL_NORMALIZED].pending_locked is None


async def test_press_closed_sends_the_locked_point(
    enable_bluetooth: None,
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pressing the closed button sends point 1 (spec 0008 R3/R5)."""
    _, fake = await setup_with_fake_lock(hass, monkeypatch)
    await advertise_locked(hass)

    await hass.services.async_call(
        "button", "press", {"entity_id": CLOSED_ID}, blocking=True
    )
    await hass.async_block_till_done()

    assert fake.calls == ["set_calibration_point"]
    assert fake.calibration_points == [1]


async def test_press_error_surfaces_status_message(
    enable_bluetooth: None,
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rejected calibration raises HomeAssistantError with the message
    (spec 0008 R5)."""
    _, fake = await setup_with_fake_lock(hass, monkeypatch)
    await advertise_locked(hass)
    # a raw device status must map to the human-readable message end to end
    fake.effects["set_calibration_point"] = CommandError(0x40, 4, 2)

    with pytest.raises(HomeAssistantError, match="permission"):
        await hass.services.async_call(
            "button", "press", {"entity_id": OPEN_ID}, blocking=True
        )
    await hass.async_block_till_done()


async def test_buttons_unavailable_without_address(
    enable_bluetooth: None,
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without an advertisement the buttons are unavailable; a broadcast
    makes them available (spec 0008 R4)."""
    await setup_with_fake_lock(hass, monkeypatch)

    assert hass.states.get(OPEN_ID).state == "unavailable"
    assert hass.states.get(CLOSED_ID).state == "unavailable"

    await advertise_locked(hass)
    assert hass.states.get(OPEN_ID).state != "unavailable"
    assert hass.states.get(CLOSED_ID).state != "unavailable"


async def test_press_without_control_raises(
    enable_bluetooth: None,
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A device without a control raises HomeAssistantError (spec 0008 R5)."""
    import custom_components.danalock_ble

    monkeypatch.setattr(
        custom_components.danalock_ble, "DanalockControl", lambda *args, **kwargs: None
    )
    await setup_with_bluetooth(hass, monkeypatch)
    await advertise_locked(hass)

    entity = hass.data["button"].get_entity(OPEN_ID)
    with pytest.raises(HomeAssistantError, match="no key"):
        await entity.async_press()


async def test_concurrent_presses_do_not_interleave(
    enable_bluetooth: None,
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two concurrent presses are serialized by the facade command lock
    (spec 0008 R8)."""
    from tests.test_control import SerializingFakeLock

    SerializingFakeLock.instances.clear()
    monkeypatch.setattr(
        "custom_components.danalock_ble.control.DanalockLock", SerializingFakeLock
    )
    await setup_with_bluetooth(hass, monkeypatch)
    await advertise_locked(hass)
    fake = SerializingFakeLock.instances[-1]
    fake.transport_factory = None

    first = asyncio.create_task(
        hass.services.async_call("button", "press", {"entity_id": OPEN_ID}, blocking=True)
    )
    await asyncio.wait_for(fake.entered.wait(), timeout=1.0)
    second = asyncio.create_task(
        hass.services.async_call("button", "press", {"entity_id": CLOSED_ID}, blocking=True)
    )
    await asyncio.sleep(0.05)
    max_active = fake.max_active

    fake.release.set()
    await asyncio.gather(first, second)
    assert max_active == 1  # the second waits for the in-flight command
    assert fake.calibration_points == [0, 1]


class BlockingCalibrationControl:
    """Control double whose calibration wait for release (spec 0008 R8)."""

    def __init__(self, serial: str) -> None:
        self.serial = serial
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.points: list[int] = []

    async def calibrate(self, point: int) -> None:
        self.points.append(point)
        self.entered.set()
        await self.release.wait()

    async def set_key(self, key: object) -> None:
        """Accepted for key manager compatibility."""

    async def disconnect(self) -> None:
        """Nothing to disconnect."""


def install_blocking_control(
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, BlockingCalibrationControl]:
    """Route control construction to blocking stubs; stubs by serial."""
    stubs: dict[str, BlockingCalibrationControl] = {}

    def factory(
        _hass: HomeAssistant, serial: str, *_args: object
    ) -> BlockingCalibrationControl:
        stub = BlockingCalibrationControl(serial)
        stubs[serial] = stub
        return stub

    monkeypatch.setattr("custom_components.danalock_ble.DanalockControl", factory)
    return stubs


async def test_unload_during_calibration_completes(
    enable_bluetooth: None,
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unloading the entry while a calibration is in flight completes
    (spec 0008 R8)."""
    controls = install_blocking_control(monkeypatch)
    entry = await setup_with_bluetooth(hass, monkeypatch)
    await advertise_locked(hass)
    stub = controls[SERIAL_NORMALIZED]

    press = asyncio.create_task(
        hass.services.async_call("button", "press", {"entity_id": OPEN_ID}, blocking=True)
    )
    await asyncio.wait_for(stub.entered.wait(), timeout=1.0)

    unload = asyncio.create_task(hass.config_entries.async_unload(entry.entry_id))
    await asyncio.sleep(0)
    stub.release.set()
    await press

    assert stub.points == [0]
    assert await unload is True


def test_button_translation_keys_exist() -> None:
    """Both language files define both button names (spec 0008 R7)."""
    translations = (
        Path(__file__).resolve().parent.parent
        / "custom_components"
        / "danalock_ble"
        / "translations"
    )
    for name in ("en.json", "ru.json"):
        tree = json.loads((translations / name).read_text(encoding="utf-8"))
        button = tree["entity"]["button"]
        assert set(button) == {"set_point_open", "set_point_closed"}
        for suffix in button:
            assert button[suffix]["name"].strip()
