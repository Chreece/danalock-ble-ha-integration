"""Firmware update entity tests: bootstrap, daily cycle, forced check,
restore, version comparison (specs 0009, 0010).

BLE is exercised through a control test double (no real Bluetooth); the
cloud side runs the real installed client against the mock transport.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

import pytest
from homeassistant.components.update import (
    DATA_COMPONENT,
    UpdateDeviceClass,
    UpdateEntityFeature,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, State
from homeassistant.exceptions import HomeAssistantError
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    mock_restore_cache,
)

from pydanalock.ble import DeviceInformation
from pydanalock.cloud import FirmwareVersion
from pydanalock.cloud import __version__ as cloud_version
from custom_components.danalock_ble.update import (
    DanalockFirmwareUpdateEntity,
    version_is_newer,
)
from tests.conftest import (
    FIRMWARE_422_PAYLOAD,
    FIRMWARE_LATEST_PATH,
    SERIAL_NORMALIZED,
    CloudHandler,
    load_fixture,
    setup_entry,
)

ENTITY_ID = f"update.danalock_ble_{SERIAL_NORMALIZED}_firmware"
LOCK_IDENTIFIER = "DanalockV3_101-025_D1_1.2.3_20990101120000"
CYCLE = timedelta(hours=25)


def make_info(
    firmware: tuple[int, int, int] = (1, 2, 3),
    identifier: str = LOCK_IDENTIFIER,
) -> DeviceInformation:
    """A synthetic device information result from the lock."""
    return DeviceInformation(
        product=3,
        hardware_version=(4, 1, 7),
        firmware_version=firmware,
        firmware_identifier=identifier,
    )


class StubControl:
    """Control test double counting device information reads."""

    def __init__(
        self,
        info: DeviceInformation,
        effect: Exception | None = None,
        gate: asyncio.Event | None = None,
    ) -> None:
        self.calls = 0
        self.info = info
        self.effect = effect
        self.gate = gate

    async def device_information(self) -> DeviceInformation:
        self.calls += 1
        if self.effect is not None:
            raise self.effect
        if self.gate is not None:
            await self.gate.wait()
        return self.info

    async def set_key(self, key: Any) -> None:
        """Accepted for key manager compatibility; nothing to rebuild."""

    async def disconnect(self) -> None:
        """Nothing to disconnect."""


def install_stub_controls(
    monkeypatch: pytest.MonkeyPatch,
    info: DeviceInformation,
    effect: Exception | None = None,
) -> dict[str, StubControl]:
    """Route control construction to stubs; returns the stubs by serial."""
    stubs: dict[str, StubControl] = {}

    def factory(_hass: HomeAssistant, serial: str, *_args: Any) -> StubControl:
        stub = StubControl(info=info, effect=effect)
        stubs[serial] = stub
        return stub

    monkeypatch.setattr("custom_components.danalock_ble.DanalockControl", factory)
    return stubs


def get_entity(hass: HomeAssistant) -> DanalockFirmwareUpdateEntity:
    """The registered update entity of the entry."""
    entity = hass.data[DATA_COMPONENT].get_entity(ENTITY_ID)
    assert entity is not None
    assert isinstance(entity, DanalockFirmwareUpdateEntity)
    return entity


async def setup_firmware_entry(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    handler: CloudHandler,
    info: DeviceInformation,
    effect: Exception | None = None,
) -> dict[str, StubControl]:
    """A danalock entry set up with stub controls and a mocked /latest."""
    handler.firmware_payload = load_fixture("firmware_latest.json")
    stubs = install_stub_controls(monkeypatch, info, effect)
    await setup_entry(hass, monkeypatch, handler)
    return stubs


async def test_bootstrap_reads_lock_once_and_reports_no_update(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Installed unknown → exactly one BLE read → installed = latest →
    state off, attributes filled (spec 0009 R2/R5)."""
    handler = CloudHandler()
    stubs = await setup_firmware_entry(hass, monkeypatch, handler, make_info())

    assert stubs[SERIAL_NORMALIZED].calls == 1
    state = hass.states.get(ENTITY_ID)
    assert state is not None
    assert state.state == "off"
    assert state.attributes["installed_version"] == "1.2.3"
    assert state.attributes["latest_version"] == "1.2.3"
    assert state.attributes["firmware_identifier"] == LOCK_IDENTIFIER
    assert state.attributes["hardware_version"] == "4.1.7"
    assert state.attributes["maturity"] == "production"


async def test_latest_probe_carries_no_authorization_header(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The firmware lookup is unauthenticated (spec 0009 R3)."""
    handler = CloudHandler()
    await setup_firmware_entry(hass, monkeypatch, handler, make_info())

    assert handler.firmware_requests
    assert all("Authorization" not in request.headers for request in handler.firmware_requests)


async def test_equal_versions_leave_the_lock_alone(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the versions agree, the next cycle skips the BLE read
    (spec 0009 R5)."""
    handler = CloudHandler()
    stubs = await setup_firmware_entry(hass, monkeypatch, handler, make_info())

    async_fire_time_changed(hass, dt_util.now() + CYCLE)
    await hass.async_block_till_done()

    assert stubs[SERIAL_NORMALIZED].calls == 1  # bootstrap only
    latest_probes = [m for m, p in handler.requests if p == FIRMWARE_LATEST_PATH]
    assert len(latest_probes) == 2  # the daily /latest probe still runs


NEWER_PAYLOAD: dict[str, Any] = {
    "firmware_identifier": "DanalockV3_101-025_D1_1.2.4_20990101120000",
    "maturity": "production",
    "url": "https://s3.example.com/danalock-v3/app-1.2.4.bin?synthetic",
    "version": "1.2.4",
}


async def test_homeassistant_update_entity_forces_an_immediate_cycle(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The core action runs a full cycle at once and re-arms the daily
    timer from it (spec 0010 R2)."""
    assert await async_setup_component(hass, "homeassistant", {})
    handler = CloudHandler()
    stubs = await setup_firmware_entry(hass, monkeypatch, handler, make_info())
    stub = stubs[SERIAL_NORMALIZED]
    assert stub.calls == 1
    assert hass.states.get(ENTITY_ID).state == "off"

    handler.firmware_payload = NEWER_PAYLOAD
    await hass.services.async_call(
        "homeassistant", "update_entity", {"entity_id": ENTITY_ID}, blocking=True
    )
    await hass.async_block_till_done()

    assert stub.calls == 2  # the forced cycle read the lock immediately
    state = hass.states.get(ENTITY_ID)
    assert state.state == "on"
    assert state.attributes["latest_version"] == "1.2.4"

    # the daily schedule restarted from the forced cycle: with equal
    # versions the next cycle probes the cloud but leaves the lock alone
    handler.firmware_payload = load_fixture("firmware_latest.json")
    async_fire_time_changed(hass, dt_util.now() + CYCLE)
    await hass.async_block_till_done()

    assert stub.calls == 2
    latest_probes = [m for m, p in handler.requests if p == FIRMWARE_LATEST_PATH]
    assert len(latest_probes) == 3  # bootstrap, forced cycle, daily cycle
    assert hass.states.get(ENTITY_ID).state == "off"


async def test_newer_latest_reports_update_then_resolves(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Latest newer than the lock → state on; after the lock updates →
    state off (spec 0009 R4/R5)."""
    handler = CloudHandler()
    stubs = await setup_firmware_entry(hass, monkeypatch, handler, make_info())
    assert hass.states.get(ENTITY_ID).state == "off"

    handler.firmware_payload = NEWER_PAYLOAD
    async_fire_time_changed(hass, dt_util.now() + CYCLE)
    await hass.async_block_till_done()

    stub = stubs[SERIAL_NORMALIZED]
    assert stub.calls == 2  # the diverging cycle contacts the lock
    assert hass.states.get(ENTITY_ID).state == "on"
    assert hass.states.get(ENTITY_ID).attributes["installed_version"] == "1.2.3"
    assert hass.states.get(ENTITY_ID).attributes["latest_version"] == "1.2.4"

    stub.info = make_info(firmware=(1, 2, 4), identifier="DanalockV3_101-025_D1_1.2.4_20990101120000")
    async_fire_time_changed(hass, dt_util.now() + CYCLE)
    await hass.async_block_till_done()

    assert stub.calls == 3
    state = hass.states.get(ENTITY_ID)
    assert state.state == "off"
    assert state.attributes["installed_version"] == "1.2.4"
    assert state.attributes["firmware_identifier"] == "DanalockV3_101-025_D1_1.2.4_20990101120000"


async def test_ble_failure_keeps_values_and_stays_available(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed BLE read keeps the values, raises nothing, and the entity
    stays available (spec 0009 R5/R7/R9)."""
    handler = CloudHandler()
    stubs = await setup_firmware_entry(
        hass, monkeypatch, handler, make_info(), effect=HomeAssistantError("no address")
    )

    stub = stubs[SERIAL_NORMALIZED]
    assert stub.calls == 1
    state = hass.states.get(ENTITY_ID)
    assert state.state == "unknown"  # installed still unknown
    assert state.attributes["latest_version"] == "1.2.3"
    assert state.attributes["maturity"] == "production"

    stub.effect = None
    async_fire_time_changed(hass, dt_util.now() + CYCLE)
    await hass.async_block_till_done()

    assert stub.calls == 2
    state = hass.states.get(ENTITY_ID)
    assert state.state == "off"
    assert state.attributes["installed_version"] == "1.2.3"


async def test_latest_probe_422_keeps_previous_values(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 422 from /latest keeps the previous values without raising
    (spec 0009 R3)."""
    handler = CloudHandler()
    stubs = await setup_firmware_entry(hass, monkeypatch, handler, make_info())
    assert hass.states.get(ENTITY_ID).state == "off"

    handler.firmware_status = 422
    handler.firmware_payload = FIRMWARE_422_PAYLOAD
    async_fire_time_changed(hass, dt_util.now() + CYCLE)
    await hass.async_block_till_done()

    assert stubs[SERIAL_NORMALIZED].calls == 1  # probe failed: no comparison, no BLE
    state = hass.states.get(ENTITY_ID)
    assert state.state == "off"
    assert state.attributes["latest_version"] == "1.2.3"


async def test_failed_probe_is_retried_after_fifteen_minutes(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed /latest probe is retried after 15 minutes instead of a day;
    the succeeding retry then contacts the lock (spec 0009 R5)."""
    handler = CloudHandler(firmware_status=500)
    stubs = await setup_firmware_entry(hass, monkeypatch, handler, make_info())

    stub = stubs[SERIAL_NORMALIZED]
    assert stub.calls == 0  # the bootstrap probe failed: no comparison
    assert hass.states.get(ENTITY_ID).state == "unknown"

    handler.firmware_status = 200
    async_fire_time_changed(hass, dt_util.now() + timedelta(minutes=15, seconds=1))
    await hass.async_block_till_done()

    assert stub.calls == 1  # the retry succeeded and read the lock
    state = hass.states.get(ENTITY_ID)
    assert state.state == "off"
    assert state.attributes["latest_version"] == "1.2.3"


async def test_latest_probe_malformed_body_keeps_previous_values(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A malformed /latest body keeps the previous values (spec 0009 R3)."""
    handler = CloudHandler()
    stubs = await setup_firmware_entry(hass, monkeypatch, handler, make_info())

    handler.firmware_payload = {"unexpected": "shape"}
    async_fire_time_changed(hass, dt_util.now() + CYCLE)
    await hass.async_block_till_done()

    assert stubs[SERIAL_NORMALIZED].calls == 1
    state = hass.states.get(ENTITY_ID)
    assert state.state == "off"
    assert state.attributes["latest_version"] == "1.2.3"


async def test_restart_restores_state_and_skips_ble(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After a restart the versions and attributes are restored; the lock
    is not contacted while they agree with /latest (spec 0009 R6)."""
    mock_restore_cache(
        hass,
        [
            State(
                ENTITY_ID,
                "off",
                {
                    "installed_version": "1.2.3",
                    "latest_version": "1.2.3",
                    "firmware_identifier": LOCK_IDENTIFIER,
                    "hardware_version": "4.1.7",
                    "maturity": "production",
                },
            )
        ],
    )
    handler = CloudHandler()
    stubs = await setup_firmware_entry(hass, monkeypatch, handler, make_info())

    stub = stubs[SERIAL_NORMALIZED]
    assert stub.calls == 0  # restored agreement: the lock is untouched
    state = hass.states.get(ENTITY_ID)
    assert state.state == "off"
    assert state.attributes["installed_version"] == "1.2.3"
    assert state.attributes["firmware_identifier"] == LOCK_IDENTIFIER

    # the restored agreement only holds until /latest diverges
    handler.firmware_payload = NEWER_PAYLOAD
    async_fire_time_changed(hass, dt_util.now() + CYCLE)
    await hass.async_block_till_done()

    assert stub.calls == 1
    assert hass.states.get(ENTITY_ID).state == "on"


async def test_restored_state_recovers_from_unknown_installed(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A state without an installed version (BLE never succeeded) contacts
    the lock on the first cycle (spec 0009 R5)."""
    mock_restore_cache(
        hass,
        [State(ENTITY_ID, "unknown", {"latest_version": "1.2.3"})],
    )
    handler = CloudHandler()
    stubs = await setup_firmware_entry(hass, monkeypatch, handler, make_info())

    assert stubs[SERIAL_NORMALIZED].calls == 1
    assert hass.states.get(ENTITY_ID).state == "off"


@pytest.mark.parametrize(
    ("latest", "installed", "expected"),
    [
        ("1.2.3", "1.2.3", False),
        ("0.9.10", "0.9.9", True),  # numeric, not lexicographic
        ("0.9.9", "0.9.10", False),
        ("2.0.0", "1.9.9", True),
        ("1.2.3", "1.2.4", False),
        ("1.2", "1.1.9", False),  # malformed latest: never claims
        ("1.2.3", "x.y.z", False),  # malformed installed: never claims
        ("1.2.3", "1.2", False),  # malformed installed: never claims
    ],
)
def test_version_comparison_is_numeric(
    latest: str, installed: str, expected: bool
) -> None:
    """major→minor→revision over exactly three components; malformed
    input never claims an update (spec 0009 R4)."""
    assert version_is_newer(latest, installed) is expected


async def test_entity_shape_and_identity(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unique id, entity id, category, device class, and the read-only
    feature set (spec 0009 R1)."""
    handler = CloudHandler()
    await setup_firmware_entry(hass, monkeypatch, handler, make_info())
    entity = get_entity(hass)

    assert entity.unique_id == f"{SERIAL_NORMALIZED}_firmware"
    assert entity.entity_id == ENTITY_ID
    assert entity.has_entity_name is True
    assert entity.translation_key == "firmware"
    assert entity.entity_category is EntityCategory.DIAGNOSTIC
    assert entity.device_class is UpdateDeviceClass.FIRMWARE
    assert entity.supported_features == UpdateEntityFeature(0)
    assert entity.in_progress is False
    assert entity.available is True


async def test_unload_removes_the_update_entity(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unloading the entry removes the entity like the other platforms
    (spec 0009 R1)."""
    handler = CloudHandler()
    stubs = await setup_firmware_entry(hass, monkeypatch, handler, make_info())
    assert stubs  # the fixture device has a key and an entity
    entry = hass.config_entries.async_entries("danalock_ble")[0]
    assert isinstance(entry, MockConfigEntry)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.data[DATA_COMPONENT].get_entity(ENTITY_ID) is None
    assert hass.states.is_state(ENTITY_ID, "unavailable")


async def test_pypi_cloud_has_firmware_lookup() -> None:
    """The installed pydanalock-cloud distribution carries the firmware API
    (spec 0009 R10)."""
    assert cloud_version == "0.5.1"
    assert FirmwareVersion(
        firmware_identifier="x", maturity="production", url="u", version="1.2.3"
    ).version == "1.2.3"
