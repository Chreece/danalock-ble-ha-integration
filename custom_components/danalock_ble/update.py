"""Update entity reporting danalock firmware versions (specs 0009, 0010).

One read-only entity per device with a key. The installed version is
read from the lock over BLE (device information command through the
per-device control); the latest version comes from the cloud firmware
service (unauthenticated lookup on its own host). The comparison is
numeric (major, minor, revision); nothing is ever installed.

HA core owns the update platform's scan interval, so the entity
schedules its own daily cycle (first cycle when added, then a 24-hour
interval; a failed /latest probe is retried after 15 minutes) — the same
self-scheduling the Z-Wave reference integration uses. A forced check
(`homeassistant.update_entity` or the `danalock_ble.check_firmware_updates`
action) runs one immediate cycle and re-arms the daily schedule from it.
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

import httpx
from homeassistant.components.update import (
    ATTR_INSTALLED_VERSION,
    ATTR_LATEST_VERSION,
    DATA_COMPONENT,
    UpdateDeviceClass,
    UpdateEntity,
    UpdateEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, ServiceCall, State, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.service import async_extract_entity_ids

from custom_components.danalock_ble.control import DanalockControl
from pydanalock.ble import version_dot
from pydanalock.cloud import (
    AsyncDanalockCloud,
    DanalockCloudError,
)

from .const import DOMAIN, SERVICE_CHECK_FIRMWARE_UPDATES

if TYPE_CHECKING:
    from homeassistant.helpers.entity_component import EntityComponent

    from . import DanalockRuntimeData

LOGGER = logging.getLogger(__name__)


def _extract_takes_hass(func: Callable[..., Any]) -> bool:
    """Whether a target helper still takes the legacy leading `hass`."""
    return "hass" in inspect.signature(func).parameters


# `async_extract_entity_ids` took `hass` as its first argument up to and
# including HA 2025.9; HA 2025.10 dropped the argument and deprecated the
# legacy form, which is removed in HA 2026.10 (spec 0010 R3). Pick the
# call form from the installed helper's signature.
_EXTRACT_TAKES_HASS = _extract_takes_hass(async_extract_entity_ids)


async def _extract_targets(hass: HomeAssistant, call: ServiceCall) -> set[str]:
    """Resolve the targets of a service call on either helper generation."""
    if _EXTRACT_TAKES_HASS:
        return await async_extract_entity_ids(hass, call)
    return await async_extract_entity_ids(call)


FIRMWARE_POLL_INTERVAL = timedelta(hours=24)
FIRMWARE_RETRY_INTERVAL = timedelta(minutes=15)

ATTR_FIRMWARE_IDENTIFIER = "firmware_identifier"
ATTR_HARDWARE_VERSION = "hardware_version"
ATTR_MATURITY = "maturity"

ENTITY_ID_FORMAT = f"update.{DOMAIN}_{{}}_firmware"


def _version_tuple(version: str) -> tuple[int, int, int] | None:
    """Exactly three dot-separated integer components, else ``None``."""
    parts = version.split(".")
    if len(parts) != 3:
        return None
    try:
        major, minor, revision = (int(part) for part in parts)
    except ValueError:
        return None
    return (major, minor, revision)


def version_is_newer(latest_version: str, installed_version: str) -> bool:
    """Numeric major → minor → revision comparison (spec 0009 R4).

    A malformed version never claims an update.
    """
    latest = _version_tuple(latest_version)
    installed = _version_tuple(installed_version)
    if latest is None or installed is None:
        return False
    return latest > installed


def _versions_equal(a: str | None, b: str | None) -> bool:
    """Numeric equality; unknown or malformed versions are not equal."""
    if a is None or b is None:
        return False
    version_a = _version_tuple(a)
    version_b = _version_tuple(b)
    return version_a is not None and version_b is not None and version_a == version_b


class DanalockFirmwareUpdateEntity(UpdateEntity, RestoreEntity):
    """Firmware version reporting for one lock (spec 0009).

    The entity is read-only: no install feature, no progress. Values
    survive a restart through the state machine; the lock is contacted
    only when the installed and latest versions diverge.
    """

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = UpdateDeviceClass.FIRMWARE
    _attr_supported_features = UpdateEntityFeature(0)
    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_translation_key = "firmware"
    _attr_in_progress = False

    def __init__(
        self,
        client: AsyncDanalockCloud,
        control: DanalockControl,
        serial: str,
    ) -> None:
        self._client = client
        self._control = control
        self._serial = serial
        self._attr_unique_id = f"{serial}_firmware"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, serial)})
        self._attr_installed_version: str | None = None
        self._attr_latest_version: str | None = None
        self._firmware_identifier: str | None = None
        self._hardware_version: str | None = None
        self._maturity: str | None = None
        self._unsub_cycle: Callable[[], None] | None = None
        self._cycle_in_flight = False
        self.entity_id = ENTITY_ID_FORMAT.format(serial)

    @property
    def available(self) -> bool:
        """Cloud-derived values do not depend on advertisement freshness."""
        return True

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        """Diagnostics: identifier, hardware version, maturity."""
        attributes: dict[str, str] = {}
        if self._firmware_identifier is not None:
            attributes[ATTR_FIRMWARE_IDENTIFIER] = self._firmware_identifier
        if self._hardware_version is not None:
            attributes[ATTR_HARDWARE_VERSION] = self._hardware_version
        if self._maturity is not None:
            attributes[ATTR_MATURITY] = self._maturity
        return attributes

    def version_is_newer(self, latest_version: str, installed_version: str) -> bool:
        """Numeric comparison; malformed input never claims an update."""
        return version_is_newer(latest_version, installed_version)

    async def async_added_to_hass(self) -> None:
        """Restore the previous versions, then run the first cycle."""
        self._async_restore(await self.async_get_last_state())
        self.hass.async_create_task(self._async_cycle())

    async def async_update(self) -> None:
        """Run one immediate cycle (spec 0010 R2).

        The pending cycle timer is cancelled and re-armed after the cycle,
        so the 24-hour schedule restarts from this check. This is the
        delegate for a core force refresh (`homeassistant.update_entity`)
        and for the `danalock_ble.check_firmware_updates` action.
        """
        if self._unsub_cycle is not None:
            self._unsub_cycle()
            self._unsub_cycle = None
        await self._async_cycle()

    async def async_will_remove_from_hass(self) -> None:
        """Cancel the pending cycle."""
        if self._unsub_cycle is not None:
            self._unsub_cycle()
            self._unsub_cycle = None

    @callback
    def _async_restore(self, state: State | None) -> None:
        """Restore the versions and attributes from the last state."""
        if state is None:
            return
        attributes = state.attributes
        self._attr_installed_version = attributes.get(ATTR_INSTALLED_VERSION) or None
        self._attr_latest_version = attributes.get(ATTR_LATEST_VERSION) or None
        self._firmware_identifier = attributes.get(ATTR_FIRMWARE_IDENTIFIER) or None
        self._hardware_version = attributes.get(ATTR_HARDWARE_VERSION) or None
        self._maturity = attributes.get(ATTR_MATURITY) or None

    async def _async_cycle(self, _now: datetime | None = None) -> None:
        """One cycle: /latest probe, BLE read on divergence, reschedule."""
        self._unsub_cycle = None
        if self.hass is None:
            return  # the entity was removed while the timer was pending
        if self._cycle_in_flight:
            # a forced or scheduled cycle is already running; it re-arms
            # the timer itself (spec 0010 R2: one chain, never stacked)
            return
        self._cycle_in_flight = True
        try:
            delay = await self._async_refresh()
            if self.hass is None:
                return  # removed while the cycle was in flight
            self.async_write_ha_state()
            self._unsub_cycle = async_call_later(self.hass, delay, self._async_cycle)
        finally:
            self._cycle_in_flight = False

    async def _async_refresh(self) -> timedelta:
        """Refresh latest and (when needed) installed; return the next delay."""
        try:
            latest = await self._client.latest_firmware(self._serial)
        except (DanalockCloudError, httpx.HTTPError) as err:
            LOGGER.debug("latest firmware lookup failed for %s: %s", self._serial, err)
            return FIRMWARE_RETRY_INTERVAL
        except RuntimeError as err:
            # the entry was unloaded while this cycle was in flight and the
            # cloud client is closed already (shutdown race)
            LOGGER.debug("latest firmware lookup skipped for %s: %s", self._serial, err)
            return FIRMWARE_RETRY_INTERVAL
        self._attr_latest_version = latest.version
        self._maturity = latest.maturity
        if self._firmware_identifier is None:
            # the lock's own identifier wins once it has been read
            self._firmware_identifier = latest.firmware_identifier
        if not _versions_equal(self._attr_installed_version, latest.version):
            await self._async_read_lock()
        return FIRMWARE_POLL_INTERVAL

    async def _async_read_lock(self) -> None:
        """Read the installed version over BLE; failures keep the values."""
        try:
            info = await self._control.device_information()
        except Exception as err:  # noqa: BLE001 - the cycle must never fail
            LOGGER.debug(
                "device information read failed for %s: %s", self._serial, err
            )
            return
        self._attr_installed_version = version_dot(info.firmware_version)
        self._firmware_identifier = info.firmware_identifier
        self._hardware_version = version_dot(info.hardware_version)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create the firmware update entities of a config entry (spec 0009)."""
    runtime: DanalockRuntimeData = entry.runtime_data
    if runtime.monitor is None:
        return
    async_add_entities(
        DanalockFirmwareUpdateEntity(runtime.client, runtime.controls[serial], serial)
        for serial in runtime.keys
    )


@callback
def async_register_firmware_check_service(hass: HomeAssistant) -> None:
    """Register the `danalock_ble.check_firmware_updates` action (spec 0010 R3).

    Called once from the component-level `async_setup`. The update
    platform's entity component is resolved at call time: it only exists
    after the update domain has loaded.
    """

    async def _async_handle(call: ServiceCall) -> None:
        entity_ids = await _extract_targets(hass, call)
        component: EntityComponent[UpdateEntity] | None = hass.data.get(DATA_COMPONENT)
        if component is None:
            LOGGER.debug(
                "check_firmware_updates skipped: no update entities are loaded"
            )
            return
        for entity_id in entity_ids:
            entity = component.get_entity(entity_id)
            if not isinstance(entity, DanalockFirmwareUpdateEntity):
                LOGGER.debug(
                    "check_firmware_updates skipped: %s is not a danalock "
                    "firmware entity",
                    entity_id,
                )
                continue
            await entity.async_update()

    hass.services.async_register(DOMAIN, SERVICE_CHECK_FIRMWARE_UPDATES, _async_handle)
