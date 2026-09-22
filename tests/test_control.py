"""Lock control tests: DanalockControl wiring, error mapping, transport
factory, key refresh (specs 0006, 0007)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import ClassVar

import httpx
import pytest
from bleak import BleakClient, BleakError
from homeassistant.core import HomeAssistant

from custom_components.danalock_ble.broadcast import DanalockDeviceState
from custom_components.danalock_ble.control import (
    DanalockControl,
    LockAddressUnknownError,
    LockControlError,
)
from pydanalock.ble import (
    ChannelClosed,
    CommandError,
    DeviceInformation,
    GattTransport,
    LockSettings,
    TlsSessionError,
)
from pydanalock.cloud import ApiError, DeviceKey
from tests.conftest import SERIAL_NORMALIZED, fixture_broadcast_key


def make_info(
    firmware: tuple[int, int, int] = (1, 2, 3),
    identifier: str = "DanalockV3_101-025_D1_1.2.3_20990101120000",
) -> DeviceInformation:
    """A synthetic device information result."""
    return DeviceInformation(
        product=3,
        hardware_version=(4, 1, 7),
        firmware_version=firmware,
        firmware_identifier=identifier,
    )


def make_key(
    serial: str = SERIAL_NORMALIZED,
    *,
    blob: bytes = b"synthetic-login-blob",
    valid_to: datetime | None = None,
) -> DeviceKey:
    """A synthetic device key (opaque blobs, no real material)."""
    return DeviceKey(
        serial=serial,
        login_blob=blob,
        broadcast_key=fixture_broadcast_key(),
        valid_from=None,
        valid_to=valid_to,
        permissions=(),
    )


def expired_key(serial: str = SERIAL_NORMALIZED) -> DeviceKey:
    """A key whose validity window has ended (spec 0007 R1)."""
    return make_key(serial, valid_to=datetime.now(UTC) - timedelta(hours=1))


def make_state(serial: str = SERIAL_NORMALIZED) -> DanalockDeviceState:
    """A device state without any advertisement seen yet."""
    return DanalockDeviceState(serial)


def make_settings(
    *,
    speed: int = 5,
    auto_lock_time: int = 0,
    brake_and_go_back_time: int = 0,
    end_to_end_mode: int = 0,
) -> LockSettings:
    """A synthetic settings result from the lock."""
    return LockSettings(
        speed=speed,
        auto_lock_time=auto_lock_time,
        brake_and_go_back_time=brake_and_go_back_time,
        end_to_end_mode=end_to_end_mode,
    )


class FakeLock:
    """Test double for the DanalockLock facade."""

    instances: ClassVar[list[FakeLock]] = []

    def __init__(
        self,
        address: str,
        *,
        serial: bytes,
        login_token: bytes,
        insecure: bool = False,
        transport_factory=None,
        **kwargs: object,
    ) -> None:
        self.address = address
        self.serial = serial
        self.login_token = login_token
        self.insecure = insecure
        self.transport_factory = transport_factory
        self.kwargs = kwargs
        self.calls: list[str] = []
        self.effects: dict[str, Exception] = {}
        self.information: DeviceInformation | None = make_info()
        self.settings_result: LockSettings = make_settings()
        self.setting_writes: dict[str, int] = {}
        self.calibration_points: list[int] = []
        self.disconnects = 0
        self.on_command = None
        FakeLock.instances.append(self)

    async def unlock(self, **kwargs: object) -> None:
        await self._command("unlock", kwargs)

    async def lock(self, **kwargs: object) -> None:
        await self._command("lock", kwargs)

    async def settings(self, **kwargs: object) -> LockSettings:
        self.calls.append("settings")
        if self.on_command is not None:
            self.on_command()
        if self.transport_factory is not None:
            await self.transport_factory(self.address)
        effect = self.effects.get("settings")
        if effect is not None:
            raise effect
        return self.settings_result

    async def set_auto_lock(self, seconds: int, **kwargs: object) -> LockSettings:
        return await self._settings_command("set_auto_lock", seconds)

    async def set_brake_and_go_back(self, seconds: int, **kwargs: object) -> LockSettings:
        return await self._settings_command("set_brake_and_go_back", seconds)

    async def set_end_to_end(self, mode: int, **kwargs: object) -> LockSettings:
        return await self._settings_command("set_end_to_end", mode)

    async def _settings_command(self, name: str, value: int) -> LockSettings:
        self.calls.append(name)
        self.setting_writes[name] = value
        if self.on_command is not None:
            self.on_command()
        if self.transport_factory is not None:
            await self.transport_factory(self.address)
        effect = self.effects.get(name)
        if effect is not None:
            raise effect
        return self.settings_result

    async def set_calibration_point(self, point: int, **kwargs: object) -> None:
        self.calls.append("set_calibration_point")
        self.calibration_points.append(point)
        if self.on_command is not None:
            self.on_command()
        if self.transport_factory is not None:
            await self.transport_factory(self.address)
        effect = self.effects.get("set_calibration_point")
        if effect is not None:
            raise effect

    async def device_information(self, **kwargs: object) -> DeviceInformation:
        self.calls.append("device_information")
        if self.on_command is not None:
            self.on_command()
        if self.transport_factory is not None:
            await self.transport_factory(self.address)
        effect = self.effects.get("device_information")
        if effect is not None:
            raise effect
        assert self.information is not None
        return self.information

    async def _command(self, name: str, kwargs: object) -> None:
        self.calls.append(name)
        if self.on_command is not None:
            self.on_command()
        if self.transport_factory is not None:
            await self.transport_factory(self.address)
        effect = self.effects.get(name)
        if effect is not None:
            raise effect

    async def disconnect(self) -> None:
        self.disconnects += 1


def install_fake_lock(monkeypatch: pytest.MonkeyPatch) -> type[FakeLock]:
    """Route DanalockControl construction to FakeLock."""
    FakeLock.instances.clear()
    monkeypatch.setattr("custom_components.danalock_ble.control.DanalockLock", FakeLock)
    return FakeLock


class FakeKeyManager:
    """Test double for the key manager (control-level tests, spec 0007)."""

    def __init__(self, key: DeviceKey) -> None:
        self.keys: dict[str, DeviceKey] = {key.serial: key}
        self.refresh_calls: list[tuple[str, bool]] = []
        self.control: DanalockControl | None = None
        self.refresh_effect: Exception | None = None

    def key(self, serial: str) -> DeviceKey:
        return self.keys[serial]

    async def refresh(self, serial: str, *, force: bool = False) -> DeviceKey:
        self.refresh_calls.append((serial, force))
        if self.refresh_effect is not None:
            raise self.refresh_effect
        new = make_key(serial, blob=b"refreshed-login-blob")
        self.keys[serial] = new
        assert self.control is not None
        await self.control.set_key(new)
        return new


def make_control(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    key: DeviceKey | None = None,
) -> tuple[DanalockControl, FakeLock, FakeKeyManager]:
    """A control with a FakeLock and a FakeKeyManager (no BLE transport).

    The factory seam is nulled so command-level tests exercise the operate
    semantics; the factory itself is covered by dedicated tests. The class
    factory is stubbed too, so facades rebuilt by `set_key` also run
    without a BLE transport.
    """
    install_fake_lock(monkeypatch)

    async def _no_transport(_self: DanalockControl, _address: str) -> None:
        return None

    monkeypatch.setattr(DanalockControl, "_transport_factory", _no_transport)
    manager = FakeKeyManager(key or make_key())
    control = DanalockControl(hass, SERIAL_NORMALIZED, manager, make_state())
    manager.control = control
    fake = FakeLock.instances[-1]
    fake.transport_factory = None
    return control, fake, manager


async def test_control_builds_facade_with_key_material(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The facade gets the raw serial, the login blob, and insecure TLS
    (spec 0006 R3/R6)."""
    install_fake_lock(monkeypatch)
    key = make_key()
    manager = FakeKeyManager(key)
    DanalockControl(hass, SERIAL_NORMALIZED, manager, make_state())

    fake = FakeLock.instances[-1]
    assert fake.serial == bytes.fromhex(SERIAL_NORMALIZED)
    assert fake.login_token == key.login_blob
    assert fake.insecure is True
    assert callable(fake.transport_factory)


async def test_operate_unlock_calls_facade_and_disconnects(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A successful command reaches the facade and disconnects afterwards
    (spec 0006 R1/R6)."""
    control, fake, _ = make_control(hass, monkeypatch)

    await control.operate("unlock")

    assert fake.calls == ["unlock"]
    assert fake.disconnects == 1


async def test_operate_lock_is_symmetric(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The lock command reaches the facade the same way (spec 0006 R1)."""
    control, fake, _ = make_control(hass, monkeypatch)

    await control.operate("lock")

    assert fake.calls == ["lock"]
    assert fake.disconnects == 1


async def test_disconnect_is_idempotent_passthrough(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Control.disconnect forwards to the facade (spec 0006 R6)."""
    control, fake, _ = make_control(hass, monkeypatch)

    await control.disconnect()
    await control.disconnect()

    assert fake.disconnects == 2


@pytest.mark.parametrize(
    ("status", "fragment"),
    [
        (0x05, "busy"),
        (0x0B, "battery"),
        (0x15, "state"),
        (0x17, "blocked"),
        (0x18, "hardware"),
        (0x19, "respond"),
        (0x41, "logged in"),
    ],
)
async def test_command_error_status_messages(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    status: int,
    fragment: str,
) -> None:
    """Known AFI statuses map to human-readable messages (spec 0006 R5).

    The TOKEN_VALIDITY statuses (0x5E-0x73) take the refresh-and-retry path
    of spec 0007 and are covered by dedicated tests below.
    """
    control, fake, _ = make_control(hass, monkeypatch)
    fake.effects["unlock"] = CommandError(status, 4, 2)

    with pytest.raises(LockControlError, match=fragment):
        await control.operate("unlock")
    assert fake.disconnects == 1


async def test_unknown_status_keeps_raw_code(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unknown AFI status surfaces the raw code (spec 0006 R5)."""
    control, fake, _ = make_control(hass, monkeypatch)
    fake.effects["lock"] = CommandError(0x80, 4, 2)

    with pytest.raises(LockControlError, match="status 0x80"):
        await control.operate("lock")
    assert fake.disconnects == 1


async def test_nothing_changed_is_success(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AFI 0x07 (nothing changed) completes without an error (spec 0006 R5)."""
    control, fake, _ = make_control(hass, monkeypatch)
    fake.effects["unlock"] = CommandError(0x07, 4, 2)

    await control.operate("unlock")

    assert fake.disconnects == 1


@pytest.mark.parametrize(
    "effect",
    [
        TimeoutError("no result within 10s"),
        ChannelClosed("peer closed the session (FIN)"),
        BleakError("device disconnected"),
    ],
)
async def test_transport_errors_are_wrapped(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    effect: Exception,
) -> None:
    """Timeout, channel, and bleak failures become HomeAssistantError
    (spec 0006 R5)."""
    control, fake, _ = make_control(hass, monkeypatch)
    fake.effects["unlock"] = effect

    with pytest.raises(LockControlError, match="Bluetooth"):
        await control.operate("unlock")
    assert fake.disconnects == 1


async def test_tls_errors_are_wrapped(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TLS session failures become HomeAssistantError (spec 0006 R5)."""
    control, fake, _ = make_control(hass, monkeypatch)
    fake.effects["unlock"] = TlsSessionError("certificate verification failed")

    with pytest.raises(LockControlError, match="secure session"):
        await control.operate("unlock")
    assert fake.disconnects == 1


async def test_disconnect_runs_on_facade_error_and_missing_address(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Even a factory failure disconnects the facade attempt (spec 0006 R6)."""
    control, fake, _ = make_control(hass, monkeypatch)

    async def failing_factory(_address: str):
        raise LockAddressUnknownError("no address")

    fake.transport_factory = failing_factory

    with pytest.raises(LockAddressUnknownError):
        await control.operate("unlock")
    assert fake.disconnects == 1


def install_fake_bluetooth(
    monkeypatch: pytest.MonkeyPatch,
    device: object | None,
) -> tuple[
    list[tuple[str, bool]],
    list[tuple[type[BleakClient], object, str]],
    object,
]:
    """Stub HA Bluetooth lookup and retry-aware connection setup (spec 0026)."""
    lookup_calls: list[tuple[str, bool]] = []
    connection_calls: list[tuple[type[BleakClient], object, str]] = []
    connected_client = object()

    def lookup(
        _hass: HomeAssistant, address: str, connectable: bool = True
    ) -> object | None:
        lookup_calls.append((address, connectable))
        return device if connectable else None

    async def connect(
        client_class: type[BleakClient],
        resolved_device: object,
        name: str,
        **_kwargs: object,
    ) -> object:
        connection_calls.append((client_class, resolved_device, name))
        return connected_client

    monkeypatch.setattr(
        "custom_components.danalock_ble.control.async_ble_device_from_address", lookup
    )
    monkeypatch.setattr(
        "custom_components.danalock_ble.control.establish_connection", connect
    )
    return lookup_calls, connection_calls, connected_client


async def test_transport_factory_uses_connectable_retry_aware_connection(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Active GATT uses a connectable HA device and establish_connection
    (spec 0026 R1/R3/R6)."""
    install_fake_lock(monkeypatch)
    device = object()
    lookup_calls, connection_calls, connected_client = install_fake_bluetooth(
        monkeypatch, device
    )
    state = make_state()
    control = DanalockControl(hass, SERIAL_NORMALIZED, FakeKeyManager(make_key()), state)
    state.address = "AA:BB:CC:DD:EE:FF"

    transport = await control._transport_factory("unused")

    assert lookup_calls == [("AA:BB:CC:DD:EE:FF", True)]
    assert connection_calls == [
        (BleakClient, device, "AA:BB:CC:DD:EE:FF")
    ]
    assert transport._client is connected_client
    assert isinstance(transport, GattTransport)


async def test_transport_factory_does_not_use_non_connectable_history(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Advertisement-only visibility is insufficient for active GATT
    (spec 0026 R2)."""
    install_fake_lock(monkeypatch)
    lookup_calls, connection_calls, _connected_client = install_fake_bluetooth(
        monkeypatch, None
    )
    state = make_state()
    control = DanalockControl(hass, SERIAL_NORMALIZED, FakeKeyManager(make_key()), state)
    state.address = "AA:BB:CC:DD:EE:FF"

    with pytest.raises(LockAddressUnknownError, match="connectable"):
        await control._transport_factory("unused")

    assert lookup_calls == [("AA:BB:CC:DD:EE:FF", True)]
    assert connection_calls == []


async def test_transport_factory_without_address_fails_typed(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No advertisement seen yet: typed error, no lookup (spec 0026 R1)."""
    install_fake_lock(monkeypatch)
    lookup_calls, connection_calls, _connected_client = install_fake_bluetooth(
        monkeypatch, object()
    )
    control = DanalockControl(
        hass, SERIAL_NORMALIZED, FakeKeyManager(make_key()), make_state()
    )

    with pytest.raises(LockAddressUnknownError):
        await control._transport_factory("unused")

    assert lookup_calls == []
    assert connection_calls == []


# --- key refresh (spec 0007) -------------------------------------------------


class _PersistentRejectionManager(FakeKeyManager):
    """Refresh double whose rebuilt facade rejects with TOKEN_VALIDITY."""

    def __init__(self, key: DeviceKey, *, command: str = "unlock") -> None:
        super().__init__(key)
        self._command = command

    async def refresh(self, serial: str, *, force: bool = False) -> DeviceKey:
        new = await super().refresh(serial, force=force)
        FakeLock.instances[-1].effects[self._command] = CommandError(0x5E, 4, 2)
        return new


async def test_operate_refreshes_expired_key_before_command(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An expired key is force-refreshed before the command runs (R1); the
    command then runs on the facade rebuilt with the fresh blob."""
    control, fake, manager = make_control(hass, monkeypatch, key=expired_key())

    await control.operate("unlock")

    assert manager.refresh_calls == [(SERIAL_NORMALIZED, True)]
    rebuilt = FakeLock.instances[-1]
    assert rebuilt is not fake
    assert rebuilt.calls == ["unlock"]
    assert rebuilt.disconnects == 1


async def test_valid_key_skips_the_precheck_refresh(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A key inside its validity window is not refreshed before commands."""
    control, fake, manager = make_control(hass, monkeypatch)

    await control.operate("lock")

    assert manager.refresh_calls == []
    assert fake.calls == ["lock"]


@pytest.mark.parametrize(
    "effect",
    [
        httpx.ConnectError("connection refused"),
        httpx.ReadTimeout("timed out"),
        ApiError("boom", status=500),
    ],
)
async def test_precheck_refresh_failure_still_runs_the_command(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    effect: Exception,
) -> None:
    """A failed pre-check refresh is logged and does not block the command
    (R2); the command runs with the stored key."""
    control, fake, manager = make_control(hass, monkeypatch, key=expired_key())
    manager.refresh_effect = effect

    await control.operate("unlock")

    assert manager.refresh_calls == [(SERIAL_NORMALIZED, True)]
    assert fake.calls == ["unlock"]
    assert fake.disconnects == 1


async def test_token_validity_rejection_refreshes_and_retries(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A TOKEN_VALIDITY rejection triggers one refresh and one retry (R3)."""
    control, fake, manager = make_control(hass, monkeypatch)
    fake.effects["unlock"] = CommandError(0x5E, 4, 2)

    await control.operate("unlock")

    assert manager.refresh_calls == [(SERIAL_NORMALIZED, True)]
    assert fake.calls == ["unlock"]
    # one disconnect for the failed attempt, one from set_key's rebuild
    assert fake.disconnects == 2
    retried = FakeLock.instances[-1]
    assert retried is not fake
    assert retried.login_token == b"refreshed-login-blob"
    assert retried.calls == ["unlock"]
    assert retried.disconnects == 1


async def test_token_validity_second_rejection_raises_without_reload_hint(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A second TOKEN_VALIDITY rejection maps to the new message (R4)."""
    install_fake_lock(monkeypatch)

    async def _no_transport(_self: DanalockControl, _address: str) -> None:
        return None

    monkeypatch.setattr(DanalockControl, "_transport_factory", _no_transport)
    manager = _PersistentRejectionManager(make_key())
    control = DanalockControl(hass, SERIAL_NORMALIZED, manager, make_state())
    manager.control = control
    FakeLock.instances[-1].effects["unlock"] = CommandError(0x5E, 4, 2)

    with pytest.raises(LockControlError, match="even after a key refresh") as exc_info:
        await control.operate("unlock")

    assert "reload" not in str(exc_info.value)
    assert len(manager.refresh_calls) == 1
    assert len(FakeLock.instances) == 2


async def test_token_validity_refresh_failure_raises_with_cause(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed refresh on the rejection path raises with the cause (R4)."""
    control, fake, manager = make_control(hass, monkeypatch)
    fake.effects["unlock"] = CommandError(0x73, 4, 2)
    manager.refresh_effect = httpx.ConnectError("connection refused")

    with pytest.raises(
        LockControlError, match="refreshing the key from the cloud failed"
    ) as exc_info:
        await control.operate("unlock")

    assert "connection refused" in str(exc_info.value)
    assert "reload" not in str(exc_info.value)
    assert len(manager.refresh_calls) == 1
    assert len(FakeLock.instances) == 1


async def test_set_key_rebuilds_facade_with_new_blob(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """set_key disconnects the old facade and rebuilds with the new blob
    (R3/R5); transport factory and serial are kept."""
    install_fake_lock(monkeypatch)
    manager = FakeKeyManager(make_key())
    control = DanalockControl(hass, SERIAL_NORMALIZED, manager, make_state())
    fake = FakeLock.instances[-1]
    new_key = make_key(blob=b"replaced-login-blob")

    await control.set_key(new_key)

    assert fake.disconnects == 1
    rebuilt = FakeLock.instances[-1]
    assert rebuilt is not fake
    assert rebuilt.login_token == b"replaced-login-blob"
    assert rebuilt.serial == fake.serial
    assert rebuilt.transport_factory == control._transport_factory


# --- device information read (spec 0009) --------------------------------------


async def test_device_information_returns_lock_data(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A successful read returns the facade result and disconnects (R2)."""
    control, fake, _ = make_control(hass, monkeypatch)
    fake.information = make_info()

    info = await control.device_information()

    assert info == make_info()
    assert fake.calls == ["device_information"]
    assert fake.disconnects == 1


@pytest.mark.parametrize(
    ("status", "fragment"),
    [
        (0x05, "busy"),
        (0x15, "state"),
        (0x18, "hardware"),
        (0x19, "respond"),
    ],
)
async def test_device_information_maps_status_messages(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    status: int,
    fragment: str,
) -> None:
    """Known AFI statuses map to human-readable messages (R9)."""
    control, fake, _ = make_control(hass, monkeypatch)
    fake.effects["device_information"] = CommandError(status, 2, 2)

    with pytest.raises(LockControlError, match=fragment):
        await control.device_information()
    assert fake.disconnects == 1


async def test_device_information_unknown_status_keeps_raw_code(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unknown AFI status surfaces the raw code with a read-specific
    action (R9)."""
    control, fake, _ = make_control(hass, monkeypatch)
    fake.effects["device_information"] = CommandError(0x80, 2, 2)

    with pytest.raises(LockControlError, match="device information request.*0x80"):
        await control.device_information()


@pytest.mark.parametrize(
    "effect",
    [
        TimeoutError("no result within 10s"),
        ChannelClosed("peer closed the session (FIN)"),
        BleakError("device disconnected"),
    ],
)
async def test_device_information_wraps_transport_errors(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    effect: Exception,
) -> None:
    """Timeout, channel, and bleak failures become HomeAssistantError (R9)."""
    control, fake, _ = make_control(hass, monkeypatch)
    fake.effects["device_information"] = effect

    with pytest.raises(LockControlError, match="Bluetooth"):
        await control.device_information()
    assert fake.disconnects == 1


async def test_device_information_wraps_tls_errors(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TLS session failures become HomeAssistantError (R9)."""
    control, fake, _ = make_control(hass, monkeypatch)
    fake.effects["device_information"] = TlsSessionError("certificate verification failed")

    with pytest.raises(LockControlError, match="secure session"):
        await control.device_information()


async def test_device_information_refreshes_expired_key_first(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An expired key is force-refreshed before the read; the read runs on
    the facade rebuilt with the fresh blob (R9)."""
    control, fake, manager = make_control(hass, monkeypatch, key=expired_key())
    fake.information = make_info()

    info = await control.device_information()

    assert manager.refresh_calls == [(SERIAL_NORMALIZED, True)]
    rebuilt = FakeLock.instances[-1]
    assert rebuilt is not fake
    assert rebuilt.calls == ["device_information"]
    assert info == make_info()


async def test_device_information_token_rejection_refreshes_and_retries(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A TOKEN_VALIDITY rejection triggers one refresh and one retry (R9)."""
    control, fake, manager = make_control(hass, monkeypatch)
    fake.effects["device_information"] = CommandError(0x5E, 4, 2)

    info = await control.device_information()

    assert manager.refresh_calls == [(SERIAL_NORMALIZED, True)]
    retried = FakeLock.instances[-1]
    assert retried is not fake
    assert retried.calls == ["device_information"]
    assert info == make_info()


async def test_device_information_second_rejection_raises(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A second TOKEN_VALIDITY rejection maps to the refresh-failure
    message (R9)."""
    install_fake_lock(monkeypatch)

    async def _no_transport(_self: DanalockControl, _address: str) -> None:
        return None

    monkeypatch.setattr(DanalockControl, "_transport_factory", _no_transport)
    manager = _PersistentRejectionManager(make_key(), command="device_information")
    control = DanalockControl(hass, SERIAL_NORMALIZED, manager, make_state())
    manager.control = control
    FakeLock.instances[-1].effects["device_information"] = CommandError(0x5E, 4, 2)

    with pytest.raises(LockControlError, match="even after a key refresh"):
        await control.device_information()

    assert len(manager.refresh_calls) == 1


async def test_device_information_maps_malformed_response(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A malformed information payload becomes LockControlError (R9)."""
    control, fake, _ = make_control(hass, monkeypatch)
    fake.effects["device_information"] = ValueError(
        "device information identifier has no NUL terminator"
    )

    with pytest.raises(LockControlError, match="malformed device information"):
        await control.device_information()
    assert fake.disconnects == 1


# --- settings read and write (spec 0010) --------------------------------------


async def test_settings_read_calls_facade_and_disconnects(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A settings read reaches the facade and disconnects (spec 0010 R7)."""
    control, fake, _ = make_control(hass, monkeypatch)
    fake.settings_result = make_settings(auto_lock_time=60)

    settings = await control.settings()

    assert settings == make_settings(auto_lock_time=60)
    assert fake.calls == ["settings"]
    assert fake.disconnects == 1


@pytest.mark.parametrize(
    ("method", "value"),
    [
        ("set_auto_lock", 300),
        ("set_brake_and_go_back", 15),
        ("set_end_to_end", 1),
    ],
)
async def test_settings_write_round_trips_verified_value(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    value: int,
) -> None:
    """A write reaches the library setter and returns the verified
    settings as-is; one disconnect per write (spec 0010 R3/R7)."""
    control, fake, _ = make_control(hass, monkeypatch)
    fake.settings_result = make_settings(auto_lock_time=300)

    settings = await getattr(control, method)(value)

    assert settings == make_settings(auto_lock_time=300)
    assert fake.calls == [method]
    assert fake.setting_writes == {method: value}
    assert fake.disconnects == 1


async def test_settings_write_rejects_not_permitted(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A NOT_PERMITTED rejection maps to a permission message (spec 0010 R3)."""
    control, fake, _ = make_control(hass, monkeypatch)
    fake.effects["set_auto_lock"] = CommandError(0x40, 4, 7)

    with pytest.raises(LockControlError, match="permission"):
        await control.set_auto_lock(300)
    assert fake.disconnects == 1


async def test_settings_write_rejects_unsupported_argument(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An UNSUPPORTED_ARGUMENT rejection maps to its message (spec 0010 R3)."""
    control, fake, _ = make_control(hass, monkeypatch)
    fake.effects["set_end_to_end"] = CommandError(0x09, 4, 7)

    with pytest.raises(LockControlError, match="does not support"):
        await control.set_end_to_end(1)
    assert fake.disconnects == 1


@pytest.mark.parametrize(
    "effect",
    [
        TimeoutError("no result within 10s"),
        ChannelClosed("peer closed the session (FIN)"),
        BleakError("device disconnected"),
    ],
)
async def test_settings_transport_errors_are_wrapped(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    effect: Exception,
) -> None:
    """Timeout, channel, and bleak failures become HomeAssistantError
    (spec 0010 R7)."""
    control, fake, _ = make_control(hass, monkeypatch)
    fake.effects["settings"] = effect

    with pytest.raises(LockControlError, match="Bluetooth"):
        await control.settings()
    assert fake.disconnects == 1


async def test_settings_token_rejection_refreshes_and_retries(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A TOKEN_VALIDITY rejection on a write triggers one refresh and one
    retry on the rebuilt facade (spec 0010 R7)."""
    control, fake, manager = make_control(hass, monkeypatch)
    fake.effects["set_auto_lock"] = CommandError(0x5E, 4, 2)

    settings = await control.set_auto_lock(60)

    assert manager.refresh_calls == [(SERIAL_NORMALIZED, True)]
    retried = FakeLock.instances[-1]
    assert retried is not fake
    assert retried.calls == ["set_auto_lock"]
    assert retried.setting_writes == {"set_auto_lock": 60}
    assert retried.disconnects == 1
    assert settings == make_settings()


async def test_settings_write_refreshes_expired_key_first(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An expired key is force-refreshed before the write (spec 0010 R7)."""
    control, fake, manager = make_control(hass, monkeypatch, key=expired_key())

    await control.set_auto_lock(60)

    assert manager.refresh_calls == [(SERIAL_NORMALIZED, True)]
    rebuilt = FakeLock.instances[-1]
    assert rebuilt is not fake
    assert rebuilt.calls == ["set_auto_lock"]
    assert rebuilt.disconnects == 1


class _RetryEffectManager(FakeKeyManager):
    """Refresh double that arms an effect on the rebuilt retry facade."""

    def __init__(self, key: DeviceKey, command: str, retry_effect: Exception) -> None:
        super().__init__(key)
        self._command = command
        self._retry_effect = retry_effect

    async def refresh(self, serial: str, *, force: bool = False) -> DeviceKey:
        new = await super().refresh(serial, force=force)
        FakeLock.instances[-1].effects[self._command] = self._retry_effect
        return new


def make_retry_control(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    *,
    command: str,
    retry_effect: Exception,
) -> tuple[DanalockControl, FakeLock, _RetryEffectManager]:
    """A control whose first attempt rejects the token and whose retry is
    armed with `retry_effect` (retry-branch coverage, spec 0006 R3/R5)."""
    install_fake_lock(monkeypatch)

    async def _no_transport(_self: DanalockControl, _address: str) -> None:
        return None

    monkeypatch.setattr(DanalockControl, "_transport_factory", _no_transport)
    manager = _RetryEffectManager(make_key(), command, retry_effect)
    control = DanalockControl(hass, SERIAL_NORMALIZED, manager, make_state())
    manager.control = control
    fake = FakeLock.instances[-1]
    fake.transport_factory = None
    fake.effects[command] = CommandError(0x5E, 4, 2)
    return control, fake, manager


async def test_retry_nothing_changed_is_success(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A NOTHING_CHANGED status on the retry is a success, not an error
    (spec 0006 R3)."""
    control, fake, manager = make_retry_control(
        hass, monkeypatch, command="unlock", retry_effect=CommandError(0x07, 4, 2)
    )

    await control.operate("unlock")

    assert manager.refresh_calls == [(SERIAL_NORMALIZED, True)]
    assert fake.disconnects == 2
    assert FakeLock.instances[-1].calls == ["unlock"]


async def test_retry_transport_error_is_wrapped(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A transport error on the retry maps to a LockControlError
    (spec 0006 R3/R5)."""
    control, fake, _ = make_retry_control(
        hass,
        monkeypatch,
        command="unlock",
        retry_effect=TlsSessionError("certificate verification failed"),
    )

    with pytest.raises(LockControlError, match="secure session"):
        await control.operate("unlock")

    assert fake.disconnects == 2
    assert FakeLock.instances[-1].disconnects == 1


async def test_device_information_retry_transport_error_is_wrapped(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A transport error on the device-information retry is wrapped
    (specs 0006 R3/R5, 0009)."""
    control, fake, _ = make_retry_control(
        hass,
        monkeypatch,
        command="device_information",
        retry_effect=TimeoutError("no result within 10s"),
    )

    with pytest.raises(LockControlError, match="Bluetooth"):
        await control.device_information()

    assert fake.disconnects == 2
    assert FakeLock.instances[-1].disconnects == 1


# --- calibration (spec 0008) --------------------------------------------------


async def test_calibrate_reaches_facade_with_point_and_disconnects(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A calibration command passes the point to the facade and disconnects
    (spec 0008 R2)."""
    control, fake, _ = make_control(hass, monkeypatch)

    await control.calibrate(0)

    assert fake.calls == ["set_calibration_point"]
    assert fake.calibration_points == [0]
    assert fake.disconnects == 1


async def test_calibrate_rejects_not_permitted(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A NOT_PERMITTED rejection maps to a permission message (R2)."""
    control, fake, _ = make_control(hass, monkeypatch)
    fake.effects["set_calibration_point"] = CommandError(0x40, 4, 2)

    with pytest.raises(LockControlError, match="permission"):
        await control.calibrate(1)
    assert fake.disconnects == 1


@pytest.mark.parametrize(
    "effect",
    [
        TimeoutError("no result within 30s"),
        ChannelClosed("peer closed the session (FIN)"),
        BleakError("device disconnected"),
    ],
)
async def test_calibrate_wraps_transport_errors(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    effect: Exception,
) -> None:
    """Timeout, channel, and bleak failures become HomeAssistantError (R2)."""
    control, fake, _ = make_control(hass, monkeypatch)
    fake.effects["set_calibration_point"] = effect

    with pytest.raises(LockControlError, match="Bluetooth"):
        await control.calibrate(0)
    assert fake.disconnects == 1


async def test_calibrate_token_rejection_refreshes_and_retries(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A TOKEN_VALIDITY rejection triggers one refresh and one retry (R2)."""
    control, fake, manager = make_control(hass, monkeypatch)
    fake.effects["set_calibration_point"] = CommandError(0x5E, 4, 2)

    await control.calibrate(1)

    assert manager.refresh_calls == [(SERIAL_NORMALIZED, True)]
    retried = FakeLock.instances[-1]
    assert retried is not fake
    assert retried.calibration_points == [1]
    assert retried.disconnects == 1


async def test_calibrate_refreshes_expired_key_first(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An expired key is force-refreshed before the command (R2)."""
    control, fake, manager = make_control(hass, monkeypatch, key=expired_key())

    await control.calibrate(0)

    assert manager.refresh_calls == [(SERIAL_NORMALIZED, True)]
    rebuilt = FakeLock.instances[-1]
    assert rebuilt is not fake
    assert rebuilt.calibration_points == [0]
    assert rebuilt.disconnects == 1


async def test_calibrate_second_rejection_raises(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A second TOKEN_VALIDITY rejection maps to the refresh-failure
    message (R2)."""
    install_fake_lock(monkeypatch)

    async def _no_transport(_self: DanalockControl, _address: str) -> None:
        return None

    monkeypatch.setattr(DanalockControl, "_transport_factory", _no_transport)
    manager = _PersistentRejectionManager(make_key(), command="set_calibration_point")
    control = DanalockControl(hass, SERIAL_NORMALIZED, manager, make_state())
    manager.control = control
    FakeLock.instances[-1].effects["set_calibration_point"] = CommandError(0x5E, 4, 2)

    with pytest.raises(LockControlError, match="even after a key refresh"):
        await control.calibrate(0)

    assert len(manager.refresh_calls) == 1


class SerializingFakeLock(FakeLock):
    """FakeLock that serializes calibration commands like the real facade.

    The real facade holds one command lock per lock, so a second command
    waits for the first. This double mirrors that behaviour to exercise the
    HA control's concurrent presses deterministically.
    """

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self._calibration_lock = asyncio.Lock()
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.active = 0
        self.max_active = 0

    async def set_calibration_point(self, point: int, **kwargs: object) -> None:
        async with self._calibration_lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            self.entered.set()
            await self.release.wait()
            self.calls.append("set_calibration_point")
            self.calibration_points.append(point)
            self.active -= 1


async def test_concurrent_calibration_commands_serialize(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A second calibration command waits for the in-flight one (R8)."""
    SerializingFakeLock.instances.clear()
    monkeypatch.setattr(
        "custom_components.danalock_ble.control.DanalockLock", SerializingFakeLock
    )

    async def _no_transport(_self: DanalockControl, _address: str) -> None:
        return None

    monkeypatch.setattr(DanalockControl, "_transport_factory", _no_transport)
    manager = FakeKeyManager(make_key())
    control = DanalockControl(hass, SERIAL_NORMALIZED, manager, make_state())
    fake = SerializingFakeLock.instances[-1]
    fake.transport_factory = None

    first = asyncio.create_task(control.calibrate(0))
    await asyncio.wait_for(fake.entered.wait(), timeout=1.0)
    second = asyncio.create_task(control.calibrate(1))
    await asyncio.sleep(0.01)
    assert fake.max_active == 1  # the second waits for the command lock

    fake.release.set()
    await asyncio.gather(first, second)
    assert fake.calibration_points == [0, 1]
    assert fake.disconnects == 2
