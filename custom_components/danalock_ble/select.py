"""Setting select entities with verified GATT writes (spec 0010).

One select per configured lock setting: the options are the observed
presets, the current option comes from the per-device value cache (the
GATT read is authoritative — broadcasts cannot carry the configured
values), and a change runs a verified write through the per-device
control (one BLE session: write plus a settings read-back; the device's
verified value is stored and shown as-is). The `blocked_to_blocked`
select keeps its stable historical name and drives the device's
end-to-end mode (0 = off, 1/2 = the device's variants).
"""

from __future__ import annotations

import asyncio
import logging

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from custom_components.danalock_ble.broadcast import (
    DanalockBroadcastMonitor,
    DanalockDeviceState,
    SETTING_FLAG_NAMES,
)
from custom_components.danalock_ble.control import DanalockControl

from . import DanalockRuntimeData
from .entity import DanalockEntity

SETTING_SUFFIXES = SETTING_FLAG_NAMES

# Option labels are neutral slug keys (off, 5_s, … one, two). The user-facing
# text is provided by the translation state cards in translations/*.json
# (English "5 s", Russian "5 с"); the label is what automations pass to
# select.select_option, so renaming to slugs is a breaking change (spec 0011).
# The preset values are protocol constants observed on the wire (spec 0010).
AUTO_LOCK_OPTIONS: tuple[tuple[int, str], ...] = (
    (0, "off"),
    (5, "5_s"),
    (10, "10_s"),
    (15, "15_s"),
    (30, "30_s"),
    (45, "45_s"),
    (60, "60_s"),
    (180, "180_s"),
    (300, "300_s"),
    (600, "600_s"),
    (900, "900_s"),
)
BRAKE_AND_GO_BACK_OPTIONS: tuple[tuple[int, str], ...] = (
    (0, "off"),
    (3, "3_s"),
    (5, "5_s"),
    (10, "10_s"),
    (15, "15_s"),
    (20, "20_s"),
    (25, "25_s"),
    (30, "30_s"),
    (45, "45_s"),
    (50, "50_s"),
    (60, "60_s"),
)
BLOCKED_TO_BLOCKED_OPTIONS: tuple[tuple[int, str], ...] = (
    (0, "off"),
    (1, "one"),
    (2, "two"),
)

SETTING_OPTIONS: dict[str, tuple[tuple[int, str], ...]] = {
    "auto_lock": AUTO_LOCK_OPTIONS,
    "brake_and_go_back": BRAKE_AND_GO_BACK_OPTIONS,
    "blocked_to_blocked": BLOCKED_TO_BLOCKED_OPTIONS,
}

SETTING_WRITES: dict[str, str] = {
    "auto_lock": "set_auto_lock",
    "brake_and_go_back": "set_brake_and_go_back",
    "blocked_to_blocked": "set_end_to_end",
}

# One in-flight settings read per device serial (spec 0010 R5): the first
# select added schedules the read, the others await the same task.
_PENDING_READS: dict[str, asyncio.Task[None]] = {}

LOGGER = logging.getLogger(__name__)


async def _async_read_settings(
    control: DanalockControl, state: DanalockDeviceState
) -> None:
    """One GATT settings read for a device; failures leave the values
    unknown and are not retried (spec 0010 R5)."""
    try:
        settings = await control.settings()
    except Exception as err:  # noqa: BLE001 - the entity add must not fail
        LOGGER.debug("settings read failed for %s: %s", state.serial, err)
        return
    state.apply_settings(settings)


class DanalockSettingSelectEntity(DanalockEntity, SelectEntity):
    """One lock setting as a select with a verified write (spec 0010)."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_entity_registry_enabled_default = False
    _entity_id_domain = Platform.SELECT

    def __init__(
        self,
        entry: ConfigEntry,
        monitor: DanalockBroadcastMonitor,
        state: DanalockDeviceState,
        control: DanalockControl | None,
        suffix: str,
    ) -> None:
        super().__init__(entry, monitor, state, suffix, suffix)
        self._setting = suffix
        self._options = SETTING_OPTIONS[suffix]
        self._attr_options = [option for _, option in self._options]
        self._control = control

    @property
    def current_option(self) -> str | None:
        """The option for the cached value; unknown values stay unknown."""
        value = self._device_state.setting_values.get(self._setting)
        if value is None:
            return None
        return self._option_for(value)

    def _option_for(self, value: int) -> str | None:
        for preset, option in self._options:
            if preset == value:
                return option
        return None

    def _value_for(self, option: str) -> int:
        for value, candidate in self._options:
            if candidate == option:
                return value
        raise ValueError(f"{option} is not a valid option for {self.entity_id}")

    def _log_unmapped_value(self) -> None:
        """Debug-log a device-reported value with no matching option."""
        value = self._device_state.setting_values.get(self._setting)
        if value is not None and self._option_for(value) is None:
            LOGGER.debug(
                "%s reports the non-preset value %s; showing unknown",
                self.entity_id,
                value,
            )

    async def async_added_to_hass(self) -> None:
        """Listen for updates and fill the cache from one shared GATT
        settings read (spec 0010 R5)."""
        await super().async_added_to_hass()
        if self._control is None or self._device_state.settings_known():
            return
        await self._async_ensure_settings_read()
        self._log_unmapped_value()
        self.async_write_ha_state()

    async def _async_ensure_settings_read(self) -> None:
        """Join or start the one shared settings read of this device."""
        serial = self._device_state.serial
        task = _PENDING_READS.get(serial)
        if task is None:
            task = self.hass.async_create_task(
                _async_read_settings(self._control, self._device_state)
            )
            _PENDING_READS[serial] = task
            task.add_done_callback(lambda _task: _PENDING_READS.pop(serial, None))
        await task

    async def async_select_option(self, option: str) -> None:
        """Write the option and show the verified device value
        (spec 0010 R3)."""
        if self._control is None:
            LOGGER.warning(
                "Ignoring the %s option change for %s: no key is available for this device",
                option,
                self.entity_id,
            )
            self.async_write_ha_state()
            return
        value = self._value_for(option)
        write = getattr(self._control, SETTING_WRITES[self._setting])
        settings = await write(value)
        self._device_state.apply_settings(settings)
        self._log_unmapped_value()
        self.async_write_ha_state()


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create the setting select entities of a config entry (spec 0010)."""
    runtime: DanalockRuntimeData = entry.runtime_data
    if runtime.monitor is None:
        return
    entities: list[SelectEntity] = []
    for state in runtime.monitor.states.values():
        control = runtime.controls.get(state.serial)
        entities.extend(
            DanalockSettingSelectEntity(entry, runtime.monitor, state, control, suffix)
            for suffix in SETTING_SUFFIXES
        )
    async_add_entities(entities)
