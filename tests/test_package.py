"""Package smoke tests: both libraries come from PyPI (spec 0015)."""

from __future__ import annotations

import importlib.util

import custom_components.danalock_ble as integration
import pydanalock.ble
from pydanalock.ble import (
    FORMAT_VERSION_STATE,
    BatteryInfo,
    ChannelClosed,
    CommandError,
    DanalockLock,
    DeviceInformation,
    GattTransport,
    LockSettings,
    LockState,
    lock_set_setting,
    parse_settings_payload,
    version_dot,
)
from pydanalock.cloud import AsyncDanalockCloud
from pydanalock.cloud import __version__ as cloud_version


def test_component_imports() -> None:
    """The integration package is importable and exposes its domain."""
    assert integration.DOMAIN == "danalock_ble"


def test_pypi_cloud_imports() -> None:
    """The installed pydanalock.cloud distribution keeps its public re-exports."""
    from pydanalock.cloud import FirmwareVersion

    assert cloud_version == "0.5.1"
    assert AsyncDanalockCloud is not None
    assert FirmwareVersion is not None


def test_pypi_ble_imports() -> None:
    """The installed pydanalock.ble distribution exposes the 0.9.0 public API
    (spec 0008) and the settings API (spec 0010) from the package root."""
    assert pydanalock.ble.__version__ == "0.9.0"
    assert FORMAT_VERSION_STATE == 0x02
    assert DanalockLock is not None
    assert LockState is not None
    assert BatteryInfo is not None
    assert CommandError is not None
    assert ChannelClosed is not None
    assert DeviceInformation is not None
    assert GattTransport is not None
    assert LockSettings is not None
    assert lock_set_setting is not None
    assert parse_settings_payload is not None
    assert version_dot((0, 32, 0)) == "0.32.0"


def test_vendored_ble_is_gone() -> None:
    """The integration no longer vendors pydanalock.ble (spec 0015 R3)."""
    assert importlib.util.find_spec("custom_components.danalock_ble.pydanalock") is None
