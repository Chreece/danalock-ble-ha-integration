"""Shared fixtures for danalock integration tests.

All fixture data is synthetic, using the same depersonalization scheme as
the pydanalock-cloud test suite: serial `00:11:22:33:44:55`, username
`user@example.com`, constant key material. The pydanalock.cloud library runs
against an `httpx.MockTransport` — the real library code, no HTTP mocking
above the library boundary.
"""

from __future__ import annotations

import base64
import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl

import httpx
import pytest
from bleak.backends.device import BLEDevice
from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_get_advertisement_callback,
)
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.danalock_ble.const import DOMAIN, MANUFACTURER_ID, PENDING_TOKENS
from pydanalock.ble import crc16_ccitt_false
from pydanalock.cloud import (
    AsyncDanalockCloud,
    TokenData,
    TokenStorage,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"

USERNAME = "user@example.com"
PASSWORD = "correct-horse-battery-staple"
SERIAL_RAW = "00:11:22:33:44:55"
SERIAL_NORMALIZED = "001122334455"

TOKEN_PATH = "/oauth2/token"
DEVICES_LIST_PATH = "/devices/v1/login_tokens"
FIRMWARE_LATEST_PATH = f"/Firmware/v1/by-serial-number/{SERIAL_RAW}/latest"

FIRMWARE_422_PAYLOAD: dict[str, Any] = {
    "errors": [
        {
            "field": "serial_number",
            "message": "SerialNumber is not a valid serial number.",
        }
    ],
    "message": "Validation Failed",
}


def load_fixture(name: str) -> Any:
    """Load a synthetic API fixture by file name."""
    return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))


def make_token(
    access: str = "fx-access-token",
    refresh: str = "fx-refresh-token",
    valid_for: float = 3600.0,
) -> TokenData:
    """A synthetic token pair; valid_for <= 0 produces an expired token."""
    return TokenData(
        access_token=access,
        refresh_token=refresh,
        expires_at=time.time() + valid_for,
    )


class CloudHandler:
    """httpx.MockTransport handler emulating the Danalock cloud API.

    Scenarios are configured through attributes; tests may mutate them
    between requests (for example to empty the device list before a
    reload).
    """

    def __init__(
        self,
        *,
        token_status: int = 200,
        token_payload: dict[str, Any] | None = None,
        devices_status: int = 200,
        devices_payload: list[dict[str, Any]] | None = None,
        devices_error: Exception | None = None,
        single_status: int = 200,
        single_payload: dict[str, Any] | None = None,
        firmware_status: int = 200,
        firmware_payload: dict[str, Any] | None = None,
        unexpected_error: Exception | None = None,
    ) -> None:
        self.token_status = token_status
        self.token_payload = (
            token_payload
            if token_payload is not None
            else load_fixture("oauth2_token_password.json")
        )
        self.devices_status = devices_status
        self.devices_payload = (
            devices_payload
            if devices_payload is not None
            else load_fixture("login_tokens_v1.json")
        )
        self.devices_error = devices_error
        self.single_status = single_status
        self.single_payload = (
            single_payload
            if single_payload is not None
            else load_fixture("login_token_single.json")
        )
        self.firmware_status = firmware_status
        # Default: the firmware service is not mocked unless a test opts in
        # (unknown paths answer 404, like before the update platform).
        self.firmware_payload = firmware_payload
        self.unexpected_error = unexpected_error
        self.requests: list[tuple[str, str]] = []
        self.firmware_requests: list[httpx.Request] = []
        self.password_grants: list[dict[str, str]] = []
        self.refresh_grants = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.requests.append((request.method, path))
        if request.method == "POST" and path == TOKEN_PATH:
            fields = dict(parse_qsl(request.content.decode("utf-8")))
            if fields.get("grant_type") == "refresh_token":
                self.refresh_grants += 1
            else:
                self.password_grants.append(fields)
            if self.unexpected_error is not None:
                raise self.unexpected_error
            return httpx.Response(self.token_status, json=self.token_payload)
        if request.method == "GET" and path == DEVICES_LIST_PATH:
            if self.unexpected_error is not None:
                raise self.unexpected_error
            if self.devices_error is not None:
                raise self.devices_error
            return httpx.Response(self.devices_status, json=self.devices_payload)
        if request.method == "GET" and path == FIRMWARE_LATEST_PATH:
            # The firmware service answers on its own host without
            # credentials; the mock transport routes by path only. Unless
            # the test opted in, the endpoint is not mocked and 404s.
            self.firmware_requests.append(request)
            if self.unexpected_error is not None:
                raise self.unexpected_error
            if self.firmware_payload is None:
                return httpx.Response(404, json={"message": "not found"})
            return httpx.Response(self.firmware_status, json=self.firmware_payload)
        if (
            request.method == "GET"
            and path.startswith("/devices/v1/")
            and path.endswith("/login_token")
        ):
            if self.unexpected_error is not None:
                raise self.unexpected_error
            if self.devices_error is not None:
                raise self.devices_error
            return httpx.Response(self.single_status, json=self.single_payload)
        return httpx.Response(404, json={"message": "not found"})


def patch_cloud_transport(
    monkeypatch: pytest.MonkeyPatch,
    handler: Callable[[httpx.Request], httpx.Response],
) -> None:
    """Route the cloud clients built by the integration through the mock
    transport. The library itself stays real; only the constructor is
    wrapped to inject its transport. `new_cloud_client` (the single
    construction site) resolves the class from the package module at call
    time, so patching the package attribute covers setup and config flow.
    """
    def factory(*args: Any, **kwargs: Any) -> AsyncDanalockCloud:
        kwargs.setdefault("transport", httpx.MockTransport(handler))
        return AsyncDanalockCloud(*args, **kwargs)

    monkeypatch.setattr("custom_components.danalock_ble.AsyncDanalockCloud", factory)


def stage_pending_tokens(
    hass: HomeAssistant,
    username: str,
    token: TokenData | None = None,
) -> None:
    """Stage validated tokens the way the config flow hands them to setup."""
    stage = hass.data.setdefault(DOMAIN, {}).setdefault(PENDING_TOKENS, {})
    stage[username.strip().lower()] = token if token is not None else make_token()


def make_entry(
    hass: HomeAssistant,
    *,
    username: str = USERNAME,
    unique_id: str | None = None,
) -> MockConfigEntry:
    """Create and register a danalock config entry for the account."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=unique_id if unique_id is not None else username.strip().lower(),
        data={"username": username},
        title=username,
    )
    entry.add_to_hass(hass)
    return entry


async def setup_entry(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    handler: CloudHandler,
    *,
    username: str = USERNAME,
    token: TokenData | None = None,
    options: dict[str, Any] | None = None,
) -> MockConfigEntry:
    """A danalock entry set up against the mock handler."""
    patch_cloud_transport(monkeypatch, handler)
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=username.strip().lower(),
        data={"username": username},
        title=username,
        options=options or {},
    )
    entry.add_to_hass(hass)
    stage_pending_tokens(hass, username, token if token is not None else make_token())
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


# --- broadcast test helpers (spec 0003) --------------------------------------

FOREIGN_SERIAL = "aabbccddeeff"
FOREIGN_BROADCAST_KEY = bytes.fromhex("f0e1d2c3b4a5968778695a4b3c2d1e0f")


def fixture_broadcast_key() -> bytes:
    """The broadcast key of the synthetic device fixture (base64)."""
    entry = load_fixture("login_tokens_v1.json")[0]
    return base64.b64decode(entry["broadcast_key"])


def make_broadcast_payload(
    *,
    serial: str = SERIAL_NORMALIZED,
    key: bytes | None = None,
    counter: int = 1,
    device_flags: int = 0b00100,
    battery_raw: int = 137,
    lock_flags: int = 0b00001,
    version: int = 0x02,
) -> bytes:
    """Build a synthetic v2 state broadcast (spec 0005 layout)."""
    if key is None:
        key = fixture_broadcast_key()
    plain = bytearray(16)
    plain[2:4] = counter.to_bytes(2, "little")
    plain[4:6] = device_flags.to_bytes(2, "little")
    plain[6] = battery_raw
    plain[10:12] = lock_flags.to_bytes(2, "little")
    crc = crc16_ccitt_false(bytes(plain[2:16]))
    plain[0:2] = crc.to_bytes(2, "little")
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    encryptor = Cipher(algorithms.AES(key), modes.CBC(b"\x00" * 16)).encryptor()
    cipher = encryptor.update(bytes(plain)) + encryptor.finalize()
    return bytes([version]) + bytes.fromhex(serial) + b"\x00" + cipher


def make_service_info(
    payload: bytes,
    *,
    rssi: int = -65,
    address: str = "AA:BB:CC:DD:EE:FF",
) -> BluetoothServiceInfoBleak:
    """A BluetoothServiceInfoBleak for one advertisement."""
    device = BLEDevice(address, "Danalock", None)
    return BluetoothServiceInfoBleak(
        "Danalock",
        address,
        rssi,
        {MANUFACTURER_ID: payload},
        {},
        [],
        "test",
        device,
        None,
        False,
        0.0,
        None,
    )


def inject_advertisement(hass: HomeAssistant, service_info: BluetoothServiceInfoBleak) -> None:
    """Feed one advertisement through the HA bluetooth manager."""
    async_get_advertisement_callback(hass)(service_info)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Enable loading the custom integration in every test."""
    yield
