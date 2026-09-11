"""The Danalock Bluetooth integration (cloud + broadcast + control, specs
0001–0010).

Setup verifies the cloud account and key availability before any device is
registered: keys are fetched at every setup (they are never persisted) and
kept in `entry.runtime_data` only. A passive broadcast monitor decodes
advertisements with the configured keys and feeds the platform entities; a
per-device control object executes lock commands, reads device information,
and reads/writes the device settings with verified results. The key manager
refreshes keys lazily before commands and periodically in the background
(spec 0007; the period and jitter are configured in hours). The update
platform reports the installed and latest firmware versions (spec 0009) and
exposes a forced check action (spec 0010). Authentication failures trigger
reauth; connectivity failures retry; changing the options reloads the entry.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.typing import ConfigType

from custom_components.danalock_ble.broadcast import DanalockBroadcastMonitor
from custom_components.danalock_ble.const import (
    DOMAIN,
    MANUFACTURER,
    MODEL,
    PENDING_TOKENS,
)
from custom_components.danalock_ble.control import DanalockControl
from pydanalock.cloud import (
    AsyncDanalockCloud,
    AuthError,
    DanalockCloudError,
    DeviceKey,
    TokenData,
    TokenStorage,
)
from pydanalock.cloud.models import serial_with_separators
from custom_components.danalock_ble.keymanager import DanalockKeyManager
from custom_components.danalock_ble.storage import DanalockTokenStorage
from custom_components.danalock_ble.update import async_register_firmware_check_service

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.LOCK,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.UPDATE,
]

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


@dataclass
class DanalockRuntimeData:
    """Per-entry runtime objects (specs 0001–0007)."""

    client: AsyncDanalockCloud
    keys: dict[str, DeviceKey]
    names: dict[str, str | None]
    device_types: dict[str, str | None]
    key_manager: DanalockKeyManager
    controls: dict[str, DanalockControl]
    monitor: DanalockBroadcastMonitor | None = None


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the integration once (spec 0010 R3).

    Runs before the first entry is set up; the integration-level actions
    are registered exactly here.
    """
    async_register_firmware_check_service(hass)
    return True


async def new_cloud_client(hass: HomeAssistant, storage: TokenStorage) -> AsyncDanalockCloud:
    """Build the cloud client off the event loop.

    The httpx client constructor loads the TLS trust store synchronously,
    which HA flags as a blocking call inside the event loop; the client is
    therefore constructed in the executor.
    """
    def _build() -> AsyncDanalockCloud:
        return AsyncDanalockCloud(storage=storage)

    return await hass.async_add_executor_job(_build)


def _pop_pending_tokens(hass: HomeAssistant, entry: ConfigEntry) -> TokenData | None:
    """Take staged flow tokens for this entry, if any (spec 0001)."""
    stage = hass.data.get(DOMAIN, {}).get(PENDING_TOKENS)
    if stage is None or entry.unique_id is None:
        return None
    return stage.pop(entry.unique_id, None)


def _device_name(names: dict[str, str | None], serial: str) -> str:
    """Cloud device name when present, serial fallback (spec 0002 R1)."""
    return names.get(serial) or f"danalock_{serial}"


SUPPORTED_DEVICE_TYPES = frozenset({"danalockv3"})
DEVICE_TYPE_MODELS = {"danalockv3": "Danalock V3"}


def _device_model(device_type: str | None) -> str:
    """Human-readable model for an API device type (spec 0005 R1)."""
    if device_type is None:
        return MODEL
    return DEVICE_TYPE_MODELS.get(device_type, device_type)


def _is_supported_device(device_type: str | None) -> bool:
    """Devices without a reported type are treated as supported
    (spec 0005 R2)."""
    return device_type is None or device_type in SUPPORTED_DEVICE_TYPES


def _register_devices(
    hass: HomeAssistant,
    entry: ConfigEntry,
    names: dict[str, str | None],
    device_types: dict[str, str | None],
) -> None:
    """Sync the device registry with the cloud device list (spec 0001
    R2/R3/R7, 0005 R1)."""
    registry = dr.async_get(hass)
    for serial, device_type in device_types.items():
        registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, serial)},
            connections={(dr.CONNECTION_BLUETOOTH, serial_with_separators(serial))},
            manufacturer=MANUFACTURER,
            model=_device_model(device_type),
            name=_device_name(names, serial),
        )
    for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
        serials = {value for domain, value in device.identifiers if domain == DOMAIN}
        if serials and serials.isdisjoint(device_types):
            _LOGGER.debug("removing stale danalock device from the registry")
            registry.async_remove_device(device.id)


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when its options change (spec 0007 R8)."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a Danalock cloud account from a config entry (spec 0001)."""
    storage = DanalockTokenStorage(hass, entry.entry_id)
    await storage.async_load()
    if storage.load() is None:
        pending = _pop_pending_tokens(hass, entry)
        if pending is not None:
            await storage.async_save(pending)

    client = await new_cloud_client(hass, storage)
    try:
        summaries = await client.devices()
        device_types = {summary.serial: summary.device_type for summary in summaries}
        supported = [
            summary for summary in summaries if _is_supported_device(summary.device_type)
        ]
        for summary in summaries:
            if not _is_supported_device(summary.device_type):
                _LOGGER.warning(
                    "skipping danalock device with unsupported device_type %s",
                    summary.device_type,
                )
        keys = {
            summary.serial: await client.get_key(summary.serial)
            for summary in supported
        }
    except AuthError as err:
        await client.aclose()
        raise ConfigEntryAuthFailed(err) from err
    except (DanalockCloudError, httpx.HTTPError) as err:
        await client.aclose()
        raise ConfigEntryNotReady(err) from err

    names = {summary.serial: summary.name for summary in summaries}
    key_manager = DanalockKeyManager(hass, entry, client, keys)
    entry.runtime_data = DanalockRuntimeData(
        client=client,
        keys=keys,
        names=names,
        device_types=device_types,
        key_manager=key_manager,
        controls={},
    )
    _register_devices(hass, entry, names, device_types)

    if keys:
        monitor = DanalockBroadcastMonitor(hass, keys)
        monitor.start()
        entry.runtime_data.monitor = monitor
        entry.runtime_data.controls = {
            serial: DanalockControl(hass, serial, key_manager, monitor.states[serial])
            for serial in keys
        }
        key_manager.bind_controls(entry.runtime_data.controls)
        key_manager.start()
    if not summaries:
        _LOGGER.warning("The Danalock account has no devices")

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_remove_config_entry_device(
    hass: HomeAssistant, config_entry: ConfigEntry, device_entry: dr.DeviceEntry
) -> bool:
    """Allow removing danalock devices from the entry (spec 0001 R8).

    Devices are recreated from the cloud device list at the next setup, so
    removal is always safe; users can delete a device to regenerate entity
    ids (spec 0002 R5).
    """
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry and release the runtime objects (spec 0001/0006).

    The client is closed and the controls disconnected only when the unload
    succeeded; a failed unload leaves the entry (and its runtime objects) in
    use.
    """
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        runtime = getattr(entry, "runtime_data", None)
        if runtime is not None:
            if runtime.monitor is not None:
                runtime.monitor.stop()
            runtime.key_manager.stop()
            for control in runtime.controls.values():
                await control.disconnect()
            await runtime.client.aclose()
    return unload_ok


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove the entry together with its persisted token store (spec 0001)."""
    await DanalockTokenStorage(hass, entry.entry_id).async_remove()
    stage = hass.data.get(DOMAIN, {}).get(PENDING_TOKENS)
    if stage is not None and entry.unique_id is not None:
        stage.pop(entry.unique_id, None)
