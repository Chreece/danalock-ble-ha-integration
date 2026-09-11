"""Lock entity: broadcast state plus BLE control (specs 0004 R1, 0006)."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.lock import LockEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from custom_components.danalock_ble.broadcast import DanalockBroadcastMonitor, DanalockDeviceState
from pydanalock.ble import DEVICE_FLAG_BITS, FORMAT_VERSION_STATE

from . import DanalockRuntimeData
from .control import DanalockControl
from .entity import DanalockEntity

DEVICE_FLAG_NAMES = tuple(name for _, name in DEVICE_FLAG_BITS)
BROADCAST_VERSION = FORMAT_VERSION_STATE

LOGGER = logging.getLogger(__name__)


class DanalockLockEntity(DanalockEntity, LockEntity):
    """The lock of a danalock device (spec 0004 R1, control per spec 0006).

    `is_locked` prefers the optimistic `pending_locked` state over the
    stored broadcast report; the pending state exists between a successful
    command and the next counter-advancing broadcast. Device flags and the
    broadcast version ride as attributes.
    """

    _entity_id_domain = Platform.LOCK

    def __init__(
        self,
        entry: ConfigEntry,
        monitor: DanalockBroadcastMonitor,
        state: DanalockDeviceState,
        control: DanalockControl | None,
    ) -> None:
        super().__init__(entry, monitor, state, "lock", "lock")
        self._control = control

    @property
    def is_locked(self) -> bool | None:
        pending = self._device_state.pending_locked
        if pending is not None:
            return pending
        report = self._device_state.report
        if report is None:
            return None
        return report.locked

    @property
    def extra_state_attributes(self) -> dict[str, object] | None:
        report = self._device_state.report
        if report is None:
            return None
        attributes: dict[str, object] = {
            name: name in report.device_flags for name in DEVICE_FLAG_NAMES
        }
        attributes["broadcast_version"] = BROADCAST_VERSION
        return attributes

    async def async_lock(self, **kwargs: Any) -> None:
        """Lock the lock (spec 0006 R1)."""
        await self._operate(True)

    async def async_unlock(self, **kwargs: Any) -> None:
        """Unlock the lock (spec 0006 R1)."""
        await self._operate(False)

    async def _operate(self, target_locked: bool) -> None:
        command = "lock" if target_locked else "unlock"
        if self._control is None:
            LOGGER.warning(
                "Ignoring the %s command for %s: no key is available for this device",
                command,
                self.entity_id,
            )
            self.async_write_ha_state()
            return
        self._attr_is_locking = target_locked
        self._attr_is_unlocking = not target_locked
        self.async_write_ha_state()
        try:
            await self._control.operate(command)
        except Exception:
            self._clear_in_progress()
            self.async_write_ha_state()
            raise
        self._clear_in_progress()
        self._device_state.pending_locked = target_locked
        self.async_write_ha_state()

    def _clear_in_progress(self) -> None:
        self._attr_is_locking = False
        self._attr_is_unlocking = False


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create the lock entities of a config entry (spec 0004/0006)."""
    runtime: DanalockRuntimeData = entry.runtime_data
    if runtime.monitor is None:
        return
    async_add_entities(
        [
            DanalockLockEntity(
                entry, runtime.monitor, state, runtime.controls.get(state.serial)
            )
            for state in runtime.monitor.states.values()
        ]
    )
