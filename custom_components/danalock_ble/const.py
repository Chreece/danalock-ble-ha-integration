"""Constants for the Danalock Bluetooth integration."""

from collections.abc import Mapping
from datetime import timedelta
from typing import Any

DOMAIN = "danalock_ble"
CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_REFRESH_PERIOD_HOURS = "refresh_period_hours"
CONF_REFRESH_JITTER_HOURS = "refresh_jitter_hours"
DEFAULT_REFRESH_PERIOD_HOURS = 12
DEFAULT_REFRESH_JITTER_HOURS = 6
MAX_REFRESH_PERIOD_HOURS = 720
MAX_REFRESH_JITTER_HOURS = 24
# Minute-based option keys used before 0.5.0 (specs 0007); read only as a
# fallback until the options are saved again (spec 0010 R6).
LEGACY_REFRESH_PERIOD_MIN = "refresh_period_minutes"
LEGACY_REFRESH_JITTER_MIN = "refresh_jitter_minutes"

SERVICE_CHECK_FIRMWARE_UPDATES = "check_firmware_updates"

MANUFACTURER = "Danalock"
MODEL = "V3"
TOKENS_STORE_VERSION = 1
PENDING_TOKENS = "danalock_ble_pending_tokens"

MANUFACTURER_ID = 456
BROADCAST_TIMEOUT_SECONDS = 300.0
STALE_CHECK_INTERVAL = timedelta(minutes=1)
RSSI_REFRESH_INTERVAL = timedelta(seconds=30)
COUNTER_MODULUS = 1 << 16
BLE_CONNECT_TIMEOUT = 20.0


def option_refresh_hours(
    options: Mapping[str, Any],
    hours_key: str,
    default_hours: int,
    legacy_minutes_key: str,
) -> int:
    """One refresh setting in integer hours (spec 0010 R5/R6).

    An hour key wins when present; without one, the legacy minute value is
    converted with floor division (0 keeps the disabled semantics); with
    neither key present the default applies.
    """
    if hours_key in options:
        return int(options[hours_key])
    if legacy_minutes_key in options:
        return int(options[legacy_minutes_key]) // 60
    return default_hours
