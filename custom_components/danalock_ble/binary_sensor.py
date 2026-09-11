"""Lock-state and status binary sensors from decoded advertisements
(spec 0002 R2, 0004 R2/R3/R4, 0010 R2)."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from custom_components.danalock_ble.broadcast import DanalockBroadcastMonitor, DanalockDeviceState

from . import DanalockRuntimeData
from .entity import DanalockEntity

# Read-only state pushed by the shared broadcast monitor; no outbound action.
PARALLEL_UPDATES = 0


class DanalockLockStateBinarySensor(DanalockEntity, BinarySensorEntity):
    """Lock state from the advertisement lock flags (spec 0002 R2).

    Device class `lock`: `on` = unlocked, `off` = locked.
    """

    _attr_device_class = BinarySensorDeviceClass.LOCK
    _entity_id_domain = Platform.BINARY_SENSOR

    def __init__(
        self, entry: ConfigEntry, monitor: DanalockBroadcastMonitor, state: DanalockDeviceState
    ) -> None:
        super().__init__(entry, monitor, state, "lock_state", "lock_state")

    @property
    def is_on(self) -> bool | None:
        report = self._device_state.report
        if report is None:
            return None
        return not report.locked


class DanalockStatusBinarySensor(DanalockEntity, BinarySensorEntity):
    """Diagnostic status from one lock flag (spec 0004 R3/R4)."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _entity_id_domain = Platform.BINARY_SENSOR

    def __init__(
        self,
        entry: ConfigEntry,
        monitor: DanalockBroadcastMonitor,
        state: DanalockDeviceState,
        suffix: str,
        flag_name: str,
    ) -> None:
        super().__init__(entry, monitor, state, suffix, suffix)
        self._flag_name = flag_name

    @property
    def is_on(self) -> bool | None:
        report = self._device_state.report
        if report is None:
            return None
        return self._flag_name in report.lock_flags


class DanalockTwistAssistBinarySensor(DanalockStatusBinarySensor):
    """Transient twist-assist runtime flag (spec 0010 R2).

    The flag reports the lock's last physical operation, not a stored
    setting, so it is diagnostic and disabled by the integration by
    default.
    """

    _attr_entity_registry_enabled_default = False


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create the binary sensor entities of a config entry (spec 0002/0004/0010)."""
    runtime: DanalockRuntimeData = entry.runtime_data
    if runtime.monitor is None:
        return
    async_add_entities(
        [
            DanalockLockStateBinarySensor(entry, runtime.monitor, state)
            for state in runtime.monitor.states.values()
        ]
        + [
            DanalockStatusBinarySensor(entry, runtime.monitor, state, suffix, flag_name)
            for state in runtime.monitor.states.values()
            for suffix, flag_name in (
                ("jammed", "blocked"),
                ("auto_calibrated", "auto_calibrated"),
                ("point_calibrated", "point_calibrated"),
            )
        ]
        + [
            DanalockTwistAssistBinarySensor(
                entry, runtime.monitor, state, "twist_assist", "twist_assist"
            )
            for state in runtime.monitor.states.values()
        ]
    )
