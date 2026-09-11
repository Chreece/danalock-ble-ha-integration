"""Sensor entities for danalock devices (specs 0002, 0004 R6, 0007 R11)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, SIGNAL_STRENGTH_DECIBELS_MILLIWATT, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from custom_components.danalock_ble.broadcast import DanalockBroadcastMonitor, DanalockDeviceState

from . import DanalockRuntimeData
from .entity import DanalockEntity
from .keymanager import DanalockKeyManager

# Read-only state pushed by the shared broadcast monitor; no outbound action.
PARALLEL_UPDATES = 0


def _rssi_to_percent(rssi: int) -> int:
    """RSSI→percent mapping with clamping (spec 0002 R2)."""
    return round(max(0.0, min(100.0, (rssi + 100) * 100 / 70)))


class DanalockSensor(DanalockEntity, SensorEntity):
    """Base for the danalock sensors."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _entity_id_domain = Platform.SENSOR

    def __init__(
        self,
        entry: ConfigEntry,
        monitor: DanalockBroadcastMonitor,
        state: DanalockDeviceState,
        suffix: str,
    ) -> None:
        super().__init__(entry, monitor, state, suffix, suffix)


class DanalockSignalStrengthSensor(DanalockSensor):
    """Signal quality as a 0-100 percent (spec 0002 R2)."""

    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self, entry: ConfigEntry, monitor: DanalockBroadcastMonitor, state: DanalockDeviceState
    ) -> None:
        super().__init__(entry, monitor, state, "signal_strength")

    @property
    def native_value(self) -> int | None:
        if self._device_state.rssi is None:
            return None
        return _rssi_to_percent(self._device_state.rssi)

    @property
    def extra_state_attributes(self) -> dict[str, int] | None:
        if self._device_state.rssi is None:
            return None
        return {"rssi": self._device_state.rssi}


class DanalockRssiSensor(DanalockSensor):
    """Raw advertisement RSSI (spec 0002 R2)."""

    _attr_native_unit_of_measurement = SIGNAL_STRENGTH_DECIBELS_MILLIWATT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_device_class = SensorDeviceClass.SIGNAL_STRENGTH

    def __init__(
        self, entry: ConfigEntry, monitor: DanalockBroadcastMonitor, state: DanalockDeviceState
    ) -> None:
        super().__init__(entry, monitor, state, "rssi")

    @property
    def native_value(self) -> int | None:
        return self._device_state.rssi


class DanalockUpdateCounterSensor(DanalockSensor):
    """The advertisement update counter (spec 0002 R2, 0004 R6)."""

    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self, entry: ConfigEntry, monitor: DanalockBroadcastMonitor, state: DanalockDeviceState
    ) -> None:
        super().__init__(entry, monitor, state, "update_counter")

    @property
    def native_value(self) -> int | None:
        if self._device_state.report is None:
            return None
        return self._device_state.report.counter


class DanalockBatteryLevelSensor(DanalockSensor):
    """Battery level in percent from the advertisement (spec 0002 R2, 0004 R6).

    The broadcast byte is percent, cross-checked against the Z-Wave
    integration of the same lock; reported as an integer.
    """

    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self, entry: ConfigEntry, monitor: DanalockBroadcastMonitor, state: DanalockDeviceState
    ) -> None:
        super().__init__(entry, monitor, state, "battery_level")

    @property
    def native_value(self) -> int | None:
        if self._device_state.report is None:
            return None
        return self._device_state.report.battery_raw


class DanalockKeyValidUntilSensor(DanalockSensor):
    """Key validity end from the cloud key metadata (spec 0002 R2, 0007 R11).

    The value is resolved through the key manager, so it follows a
    refreshed key without an entity or entry reload.
    """

    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(
        self,
        entry: ConfigEntry,
        monitor: DanalockBroadcastMonitor,
        state: DanalockDeviceState,
        key_manager: DanalockKeyManager,
    ) -> None:
        super().__init__(entry, monitor, state, "key_valid_until")
        self._key_manager = key_manager
        self._unsub_key_listener: Callable[[], None] | None = None

    @property
    def available(self) -> bool:
        """Cloud-derived value: independent of advertisement freshness."""
        return True

    @property
    def native_value(self) -> datetime | None:
        return self._key_manager.key(self._device_state.serial).valid_to

    async def async_added_to_hass(self) -> None:
        """Listen for monitor updates and key manager updates."""
        await super().async_added_to_hass()
        self._unsub_key_listener = self._key_manager.add_listener(
            self._device_state.serial, self.async_write_ha_state
        )

    async def async_will_remove_from_hass(self) -> None:
        """Stop listening for monitor and key manager updates."""
        await super().async_will_remove_from_hass()
        if self._unsub_key_listener is not None:
            self._unsub_key_listener()
            self._unsub_key_listener = None


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create the sensor entities of a config entry (spec 0002)."""
    runtime: DanalockRuntimeData = entry.runtime_data
    if runtime.monitor is None:
        return
    entities: list[SensorEntity] = []
    for serial in runtime.keys:
        state = runtime.monitor.states[serial]
        entities.extend(
            [
                DanalockSignalStrengthSensor(entry, runtime.monitor, state),
                DanalockRssiSensor(entry, runtime.monitor, state),
                DanalockUpdateCounterSensor(entry, runtime.monitor, state),
                DanalockBatteryLevelSensor(entry, runtime.monitor, state),
                DanalockKeyValidUntilSensor(entry, runtime.monitor, state, runtime.key_manager),
            ]
        )
    async_add_entities(entities)
