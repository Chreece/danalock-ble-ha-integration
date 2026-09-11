"""Shared danalock entity plumbing (specs 0002/0003)."""

from __future__ import annotations

from collections.abc import Callable

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity

from custom_components.danalock_ble.broadcast import DanalockBroadcastMonitor, DanalockDeviceState
from custom_components.danalock_ble.const import DOMAIN


class DanalockEntity(Entity):
    """Base entity: per-device identity, monitor updates, availability.

    `_entity_id_domain` (set by each platform base class) supplies the
    domain prefix of the suggested entity_id (spec 0002 R5).
    """

    _attr_should_poll = False
    _attr_has_entity_name = True
    _entity_id_domain: Platform

    def __init__(
        self,
        entry: ConfigEntry,
        monitor: DanalockBroadcastMonitor,
        state: DanalockDeviceState,
        suffix: str,
        translation_key: str,
    ) -> None:
        self._monitor = monitor
        self._device_state = state
        self._attr_unique_id = f"{state.serial}_{suffix}"
        self._attr_translation_key = translation_key
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, state.serial)})
        # Suggest a stable object id independent of the user-changeable cloud
        # device name (spec 0002 R5): the entity platform takes the object id
        # from entity_id when it registers the entity for the first time, so
        # the entity_id becomes danalock_ble_<serial>_<suffix> while the cloud
        # device name stays in the friendly names.
        self.entity_id = f"{self._entity_id_domain}.{DOMAIN}_{state.serial}_{suffix}"
        self._unsub_listener: Callable[[], None] | None = None

    @property
    def available(self) -> bool:
        """Freshness of the last decoded advertisement (spec 0002 R3)."""
        return self._device_state.is_fresh

    async def async_added_to_hass(self) -> None:
        """Start listening for monitor updates (spec 0003)."""
        self._unsub_listener = self._monitor.add_listener(
            self._device_state.serial, self.async_write_ha_state
        )

    async def async_will_remove_from_hass(self) -> None:
        """Stop listening for monitor updates."""
        if self._unsub_listener is not None:
            self._unsub_listener()
            self._unsub_listener = None
