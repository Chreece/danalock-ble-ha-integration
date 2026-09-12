"""Calibration point buttons (spec 0008).

Two config-category buttons per device: one records the unlocked endpoint,
one the locked endpoint. A press runs one calibration command on the shared
per-device control (connect -> command -> disconnect); the entity does not
change its own state, and the resulting calibration state arrives through the
broadcast monitor's point-calibrated flag.
"""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from custom_components.danalock_ble.broadcast import (
    DanalockBroadcastMonitor,
    DanalockDeviceState,
)
from custom_components.danalock_ble.control import DanalockControl
from pydanalock.ble import CALIBRATION_POINT_LOCKED, CALIBRATION_POINT_UNLOCKED

from . import DanalockRuntimeData
from .entity import DanalockEntity

# Suffix -> calibration point (spec 0008 R3): the open endpoint first.
CALIBRATION_POINTS: dict[str, int] = {
    "set_point_open": CALIBRATION_POINT_UNLOCKED,
    "set_point_closed": CALIBRATION_POINT_LOCKED,
}

# Outbound BLE calibration command against one device; serialize one session.
PARALLEL_UPDATES = 1


class DanalockCalibrationButton(DanalockEntity, ButtonEntity):
    """One manual calibration endpoint as a button (spec 0008)."""

    _attr_entity_category = EntityCategory.CONFIG
    _entity_id_domain = Platform.BUTTON

    def __init__(
        self,
        entry: ConfigEntry,
        monitor: DanalockBroadcastMonitor,
        state: DanalockDeviceState,
        control: DanalockControl | None,
        suffix: str,
    ) -> None:
        super().__init__(entry, monitor, state, suffix, suffix)
        self._control = control
        self._point = CALIBRATION_POINTS[suffix]

    @property
    def available(self) -> bool:
        """A calibration command needs a control and a known address, not a
        fresh advertisement (spec 0008 R4)."""
        return self._control is not None and self._device_state.address is not None

    async def async_press(self) -> None:
        """Send the mapped calibration point; the entity state does not
        change (spec 0008 R5)."""
        if self._control is None:
            raise HomeAssistantError(
                f"Cannot calibrate {self.entity_id}: "
                "no key is available for this device"
            )
        await self._control.calibrate(self._point)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create the calibration buttons of a config entry (spec 0008 R3)."""
    runtime: DanalockRuntimeData = entry.runtime_data
    if runtime.monitor is None:
        return
    entities: list[ButtonEntity] = []
    for state in runtime.monitor.states.values():
        control = runtime.controls.get(state.serial)
        entities.extend(
            DanalockCalibrationButton(entry, runtime.monitor, state, control, suffix)
            for suffix in CALIBRATION_POINTS
        )
    async_add_entities(entities)
