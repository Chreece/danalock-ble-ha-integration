"""Per-device lock control over the pydanalock.ble stack (specs 0006, 0007,
0008, 0009, 0010).

One `DanalockControl` per device serial owns the `DanalockLock` facade
(RPD+TLS+AFI session on demand) and resolves the BLE transport through Home
Assistant's shared Bluetooth machinery: the address comes from the broadcast
monitor's device state and a connectable device from Home Assistant's
bluetooth history. Each command connects through the shared retry-aware connector,
runs, and disconnects; failures map to `HomeAssistantError` subclasses with
human-readable messages. The login token is taken from the key manager at
construction and refreshed through it: expired keys are refetched before a
command, and a token rejection triggers one forced refresh and one retry.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Literal, TypeVar

import httpx
from bleak import BleakClient, BleakError
from bleak_retry_connector import establish_connection
from homeassistant.components.bluetooth import async_ble_device_from_address
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from custom_components.danalock_ble.broadcast import DanalockDeviceState
from pydanalock.ble import (
    ChannelClosed,
    CommandError,
    DanalockLock,
    DeviceInformation,
    GattTransport,
    LockSettings,
    TlsSessionError,
)
from pydanalock.cloud import DanalockCloudError, DeviceKey
from pydanalock.cloud.models import key_expired

if TYPE_CHECKING:
    from custom_components.danalock_ble.keymanager import DanalockKeyManager

LOGGER = logging.getLogger(__name__)

Command = Literal["lock", "unlock"]

_T = TypeVar("_T")

NOTHING_CHANGED_STATUS = "NOTHING_CHANGED"
TOKEN_VALIDITY_STATUS = "TOKEN_VALIDITY"

_STATUS_MESSAGES: dict[str, str] = {
    "BUSY": "The lock is busy; try again shortly.",
    "BLOCKED": "The lock is blocked (jammed).",
    "CRITICAL_BATTERY": "The lock battery is critically low.",
    "BAD_STATE": "The lock is in a state that does not allow this command.",
    "HARDWARE_FAILURE": "The lock reported a hardware failure.",
    "TIMEOUT": "The lock did not respond in time.",
    "NOT_LOGGED_IN": "The lock session is not logged in.",
    "UNSUPPORTED_ARGUMENT": "The lock does not support this setting or value.",
    "NOT_PERMITTED": (
        "The lock rejected the change: the account does not have the "
        "permission to change this setting."
    ),
    "TOKEN_VALIDITY": (
        "The lock rejected the login token even after a key refresh; "
        "check the cloud account and the device enrollment of the lock."
    ),
}


class LockControlError(HomeAssistantError):
    """A lock command failed (transport or device rejection)."""


class LockAddressUnknownError(LockControlError):
    """The Bluetooth address of the lock is not (yet) known."""


class DanalockControl:
    """Lock command executor for one device (specs 0006 R1/R2/R5/R6, 0007).

    The facade address argument is unused: the transport factory resolves
    the current BLE address from the broadcast state at connect time. The
    login token comes from the key manager and can be swapped at any time
    (`set_key`) without touching the transport factory or device state.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        serial: str,
        key_manager: DanalockKeyManager,
        state: DanalockDeviceState,
    ) -> None:
        self._hass = hass
        self._serial = serial
        self._state = state
        self._key_manager = key_manager
        self._lock = DanalockLock(
            "",
            serial=bytes.fromhex(serial),
            login_token=key_manager.key(serial).login_blob,
            insecure=True,
            transport_factory=self._transport_factory,
        )

    async def operate(self, command: Command) -> None:
        """Run one lock/unlock command with lazy key refresh (spec 0007).

        An expired key is force-refreshed before the command (a refresh
        failure does not block the command); a TOKEN_VALIDITY rejection
        triggers one forced refresh and one retry.

        Raises:
            LockControlError: the device rejected the command or the
                transport failed.
            LockAddressUnknownError: the lock address or device is unknown.
        """
        await self._refresh_if_expired()
        try:
            await self._attempt(command)
        except CommandError as err:
            if err.name == NOTHING_CHANGED_STATUS:
                return
            if err.name != TOKEN_VALIDITY_STATUS:
                raise self._command_error(err, f"{command} command") from err
            await self._refresh_after_rejection(err)
            try:
                await self._attempt(command)
            except CommandError as retry_err:
                if retry_err.name == NOTHING_CHANGED_STATUS:
                    return
                raise self._command_error(retry_err, f"{command} command") from retry_err
            except (
                TimeoutError,
                ChannelClosed,
                BleakError,
                TlsSessionError,
            ) as retry_err:
                raise self._transport_error(retry_err) from retry_err
        except (TimeoutError, ChannelClosed, BleakError, TlsSessionError) as err:
            raise self._transport_error(err) from err

    async def device_information(self) -> DeviceInformation:
        """Read the device information with lazy key refresh (spec 0009).

        An expired key is force-refreshed before the read (a refresh
        failure does not block it); a TOKEN_VALIDITY rejection triggers
        one forced refresh and one retry.

        Raises:
            LockControlError: the device rejected the read or the
                transport failed.
            LockAddressUnknownError: the lock address or device is unknown.
        """
        return await self._run_command(
            "device information request",
            lambda: self._attempt_call(self._read_information),
        )

    async def settings(self) -> LockSettings:
        """Read the device settings with lazy key refresh (spec 0010 R7).

        Raises:
            LockControlError: the device rejected the read or the
                transport failed.
            LockAddressUnknownError: the lock address or device is unknown.
        """
        return await self._run_command(
            "settings read", lambda: self._attempt_call(self._lock.settings)
        )

    async def set_auto_lock(self, seconds: int) -> LockSettings:
        """Set the auto lock time and verify in one BLE session
        (spec 0010 R7).

        The library setter writes the setting and reads the settings back
        inside the session; the returned values are the device's actual
        state.

        Raises:
            LockControlError: the device rejected the write or the
                transport failed.
            LockAddressUnknownError: the lock address or device is unknown.
        """
        return await self._run_command(
            "auto lock setting change",
            lambda: self._attempt_call(lambda: self._lock.set_auto_lock(seconds)),
        )

    async def set_brake_and_go_back(self, seconds: int) -> LockSettings:
        """Set the brake and go back time and verify in one BLE session
        (spec 0010 R7)."""
        return await self._run_command(
            "brake and go back setting change",
            lambda: self._attempt_call(
                lambda: self._lock.set_brake_and_go_back(seconds)
            ),
        )

    async def set_end_to_end(self, mode: int) -> LockSettings:
        """Set the end-to-end mode and verify in one BLE session
        (spec 0010 R7)."""
        return await self._run_command(
            "end-to-end mode change",
            lambda: self._attempt_call(lambda: self._lock.set_end_to_end(mode)),
        )

    async def calibrate(self, point: int) -> None:
        """Store one manual calibration point (spec 0008 R2).

        Runs the library command through the shared attempt path: lazy
        expired-key refresh before the attempt, one forced refresh and one
        retry on a TOKEN_VALIDITY rejection, and connect -> command ->
        disconnect in `finally`.

        Raises:
            LockControlError: the device rejected the command or the
                transport failed.
            LockAddressUnknownError: the lock address or device is unknown.
        """
        await self._run_command(
            "calibration point change",
            lambda: self._attempt_call(lambda: self._lock.set_calibration_point(point)),
        )

    async def set_key(self, key: DeviceKey) -> None:
        """Swap in a freshly fetched key (spec 0007 R3/R5).

        The current facade session is closed first (a running command
        finishes through the facade's command lock), then the facade is
        rebuilt with the new login blob; transport factory and device
        state are kept.
        """
        await self._lock.disconnect()
        self._lock = DanalockLock(
            "",
            serial=bytes.fromhex(self._serial),
            login_token=key.login_blob,
            insecure=True,
            transport_factory=self._transport_factory,
        )

    async def disconnect(self) -> None:
        """Disconnect the facade session if one is open (idempotent)."""
        await self._lock.disconnect()

    async def _attempt(self, command: Command) -> None:
        """One connect/run/disconnect attempt on the current facade."""
        try:
            if command == "lock":
                await self._lock.lock()
            else:
                await self._lock.unlock()
        finally:
            await self._lock.disconnect()

    async def _attempt_call(self, call: Callable[[], Awaitable[_T]]) -> _T:
        """One connect/run/disconnect attempt on the current facade."""
        try:
            return await call()
        finally:
            await self._lock.disconnect()

    async def _read_information(self) -> DeviceInformation:
        """One device information read on the current facade."""
        try:
            return await self._lock.device_information()
        except ValueError as err:
            raise LockControlError(
                f"The lock returned a malformed device information response: {err}"
            ) from err

    async def _run_command(
        self, action: str, attempt: Callable[[], Awaitable[_T]]
    ) -> _T:
        """Run one attempt with lazy key refresh (specs 0007, 0009, 0010).

        An expired key is force-refreshed before the attempt (a refresh
        failure does not block it); a TOKEN_VALIDITY rejection triggers
        one forced refresh and one retry.

        Raises:
            LockControlError: the device rejected the attempt or the
                transport failed.
            LockAddressUnknownError: the lock address or device is unknown.
        """
        await self._refresh_if_expired()
        try:
            return await attempt()
        except CommandError as err:
            if err.name != TOKEN_VALIDITY_STATUS:
                raise self._command_error(err, action) from err
            await self._refresh_after_rejection(err)
            try:
                return await attempt()
            except CommandError as retry_err:
                raise self._command_error(retry_err, action) from retry_err
            except (
                TimeoutError,
                ChannelClosed,
                BleakError,
                TlsSessionError,
            ) as retry_err:
                raise self._transport_error(retry_err) from retry_err
        except (TimeoutError, ChannelClosed, BleakError, TlsSessionError) as err:
            raise self._transport_error(err) from err

    def _command_error(self, err: CommandError, action: str) -> LockControlError:
        """Map a device rejection to a HomeAssistantError (spec 0006 R5).

        `action` is the human-readable phrase for what was rejected (e.g.
        "unlock command", "device information request").
        """
        message = _STATUS_MESSAGES.get(err.name)
        if message is None:
            message = f"The lock rejected the {action} (status {err.status:#04x})."
        return LockControlError(message)

    def _transport_error(self, err: Exception) -> LockControlError:
        """Map a transport/TLS failure to a HomeAssistantError (0006 R5)."""
        if isinstance(err, TlsSessionError):
            return LockControlError(
                f"Failed to establish a secure session with the lock: {err}"
            )
        return LockControlError(f"Failed to reach the lock over Bluetooth: {err}")

    async def _refresh_if_expired(self) -> None:
        """Force-refetch an expired key before a command (spec 0007 R1/R2).

        A cloud failure is logged and non-blocking: the command runs with
        the stored key.
        """
        if not key_expired(self._key_manager.key(self._serial)):
            return
        try:
            await self._key_manager.refresh(self._serial, force=True)
        except (DanalockCloudError, httpx.HTTPError) as err:
            LOGGER.debug(
                "key refresh before the command failed; using the stored key: %s", err
            )

    async def _refresh_after_rejection(self, err: CommandError) -> None:
        """One forced refetch after a token rejection (spec 0007 R3/R4)."""
        try:
            await self._key_manager.refresh(self._serial, force=True)
        except (DanalockCloudError, httpx.HTTPError) as refresh_err:
            raise LockControlError(
                "The lock rejected the login token; refreshing the key from the "
                f"cloud failed: {refresh_err}. Check the cloud connection and "
                "the device enrollment."
            ) from err

    async def _transport_factory(self, _address: str) -> GattTransport:
        """Build a retry-aware transport from HA's connectable BLE history."""
        address = self._state.address
        if address is None:
            raise LockAddressUnknownError(
                "The lock has not been seen advertising yet; its Bluetooth address is unknown."
            )
        device = async_ble_device_from_address(self._hass, address, connectable=True)
        if device is None:
            raise LockAddressUnknownError(
                "The lock is visible only through a non-connectable Bluetooth path; "
                "wait for a connectable adapter or proxy to see it."
            )
        client = await establish_connection(BleakClient, device, address)
        return GattTransport(client)
