"""Passive broadcast monitor (spec 0003).

One monitor per config entry: it registers with Home Assistant's shared
Bluetooth machinery, tries each configured device's broadcast key against
every danalock advertisement (the advertiser address is not the serial —
only the payload serial is authoritative), and fans accepted updates out to
the platform entities. Entities go unavailable when no valid advertisement
is decoded within the availability timeout.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from homeassistant.components.bluetooth import (
    BluetoothChange,
    BluetoothScanningMode,
    BluetoothServiceInfoBleak,
    async_last_service_info,
    async_register_callback,
)
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_interval

from custom_components.danalock_ble.const import (
    BROADCAST_TIMEOUT_SECONDS,
    COUNTER_MODULUS,
    MANUFACTURER_ID,
    RSSI_REFRESH_INTERVAL,
    STALE_CHECK_INTERVAL,
)
from pydanalock.ble import (
    BroadcastError,
    BroadcastReport,
    LockSettings,
    parse_broadcast,
)
from pydanalock.cloud import DeviceKey

LOGGER = logging.getLogger(__name__)

# The names are this library's own labels for the device settings; the
# preset values behind them are observed on the wire (spec 0010).
SETTING_FLAG_NAMES = ("auto_lock", "brake_and_go_back", "blocked_to_blocked")


@dataclass
class DanalockDeviceState:
    """Latest decoded state of one device (specs 0003, 0006, 0010).

    The report follows the counter gate; RSSI and freshness refresh on
    every decoded advertisement (dispatch or the R2a history poll), so
    signal data never freezes between state changes. `pending_locked`
    carries the optimistic lock state between a successful command and the
    next counter-advancing broadcast (spec 0006 R4). `setting_values`
    caches the configured settings (spec 0010): the GATT read is
    authoritative, the broadcasts cannot carry the configured values
    (the flags are a snapshot from the last broadcast regeneration), so
    only an off transition of a flag (1→0) stores a value (0); a flag
    turning on (0→1) makes the value unknown again.
    """

    serial: str
    report: BroadcastReport | None = None
    rssi: int | None = None
    last_seen: float | None = None
    address: str | None = None
    last_info: BluetoothServiceInfoBleak | None = None
    pending_locked: bool | None = None
    setting_values: dict[str, int | None] = field(
        default_factory=lambda: {name: None for name in SETTING_FLAG_NAMES}
    )

    @property
    def is_fresh(self) -> bool:
        """True while advertisements arrived within the timeout (R3)."""
        return self.last_seen is not None and (
            time.monotonic() - self.last_seen < BROADCAST_TIMEOUT_SECONDS
        )

    def settings_known(self) -> bool:
        """True when every setting value is known (spec 0010 R5)."""
        return all(value is not None for value in self.setting_values.values())

    def apply_settings(self, settings: LockSettings) -> None:
        """Store a verified settings result (spec 0010 R3/R4)."""
        self.setting_values["auto_lock"] = settings.auto_lock_time
        self.setting_values["brake_and_go_back"] = settings.brake_and_go_back_time
        self.setting_values["blocked_to_blocked"] = settings.end_to_end_mode


def _counter_increased(old: int, new: int) -> bool:
    """Wrap-safe increase check (spec 0003 R2)."""
    delta = (new - old) % COUNTER_MODULUS
    return 0 < delta < COUNTER_MODULUS // 2


class DanalockBroadcastMonitor:
    """Per-entry advertisement monitor (spec 0003)."""

    def __init__(self, hass: HomeAssistant, keys: dict[str, DeviceKey]) -> None:
        self._hass = hass
        self._keys = keys
        self.states = {serial: DanalockDeviceState(serial) for serial in keys}
        self._listeners: dict[str, list[Callable[[], None]]] = {
            serial: [] for serial in keys
        }
        self._notified: dict[str, bool] = {serial: False for serial in keys}
        self._unavailable: dict[str, bool] = {serial: False for serial in keys}
        self._monitor_started_at: float | None = None
        self._cancel_callback: CALLBACK_TYPE | None = None
        self._cancel_stale_timer: CALLBACK_TYPE | None = None
        self._cancel_refresh_timer: CALLBACK_TYPE | None = None
        self._logged_decode_failure = False

    def start(self) -> None:
        """Register with the shared Bluetooth machinery (spec 0003 R1/R4)."""
        try:
            self._cancel_callback = async_register_callback(
                self._hass,
                self._handle_advertisement,
                {"manufacturer_id": MANUFACTURER_ID, "connectable": False},
                BluetoothScanningMode.PASSIVE,
            )
        except RuntimeError:
            LOGGER.warning(
                "bluetooth is not available; danalock advertisement monitoring is disabled"
            )
        else:
            self._monitor_started_at = time.monotonic()
        self._cancel_stale_timer = async_track_time_interval(
            self._hass, self._check_stale, STALE_CHECK_INTERVAL
        )
        self._cancel_refresh_timer = async_track_time_interval(
            self._hass, self._refresh_states, RSSI_REFRESH_INTERVAL
        )

    def stop(self) -> None:
        """Cancel the callback and the timers."""
        for cancel in (self._cancel_callback, self._cancel_stale_timer, self._cancel_refresh_timer):
            if cancel is not None:
                cancel()
        self._cancel_callback = None
        self._cancel_stale_timer = None
        self._cancel_refresh_timer = None

    @property
    def active(self) -> bool:
        """True while the Bluetooth callback is registered."""
        return self._cancel_callback is not None

    def add_listener(self, serial: str, listener: Callable[[], None]) -> Callable[[], None]:
        """Register an entity update listener for one device."""
        self._listeners[serial].append(listener)

        def _remove() -> None:
            self._listeners[serial].remove(listener)

        return _remove

    @callback
    def _handle_advertisement(
        self, service_info: BluetoothServiceInfoBleak, change: BluetoothChange
    ) -> None:
        """Try the configured keys against one advertisement (spec 0003 R1)."""
        payload = service_info.manufacturer_data.get(MANUFACTURER_ID)
        if payload is None:
            return
        for serial, key in self._keys.items():
            try:
                report = parse_broadcast(payload, key.broadcast_key)
            except BroadcastError:
                # wrong key or a foreign lock (spec 0003 R5); log once per monitor
                if not self._logged_decode_failure:
                    self._logged_decode_failure = True
                    LOGGER.debug(
                        "ignoring undecodable danalock advertisement (wrong key or version)"
                    )
                continue
            if report.serial != serial:
                LOGGER.debug("ignoring advertisement of an unconfigured danalock device")
                continue
            self._accept(serial, report, service_info)
            return

    def _accept(
        self,
        serial: str,
        report: BroadcastReport,
        service_info: BluetoothServiceInfoBleak,
    ) -> None:
        """Refresh the device state from one decoded advertisement (R2).

        The report follows the counter gate; RSSI and freshness refresh on
        every advertisement so signal data never freezes between state
        changes. Only a counter-advancing report clears the optimistic
        `pending_locked` flag (spec 0006 R4) and updates the setting
        value transitions (spec 0010 R4).
        """
        state = self.states[serial]
        report_changed = state.report is None or _counter_increased(
            state.report.counter, report.counter
        )
        rssi_changed = state.rssi != service_info.rssi
        was_stale = not state.is_fresh
        if report_changed:
            self._update_setting_values(state, report)
            state.report = report
            state.pending_locked = None
        state.rssi = service_info.rssi
        state.last_seen = time.monotonic()
        state.address = service_info.address
        state.last_info = service_info
        self._sync_availability(serial)
        if report_changed or rssi_changed or was_stale:
            self._notified[serial] = True
            self._notify(serial)

    @staticmethod
    def _update_setting_values(
        state: DanalockDeviceState, report: BroadcastReport
    ) -> None:
        """Apply the off/on transitions of the setting flags (spec 0010 R4).

        A flag turning off stores the value 0; a flag turning on makes the
        value unknown; a flag that stays on or stays off keeps the cached
        value — a stale flag snapshot must not zero a value written over
        the GATT (the payload is not regenerated by a settings write).
        """
        previous = state.report
        for name in SETTING_FLAG_NAMES:
            was_on = previous is not None and name in previous.lock_flags
            is_on = name in report.lock_flags
            if was_on and not is_on:
                state.setting_values[name] = 0
            elif is_on and not was_on:
                state.setting_values[name] = None

    def _refresh_states(self, now: object = None) -> None:
        """Refresh RSSI/freshness from the manager history (spec 0003 R2a).

        The manager suppresses dispatch of payload-identical advertisements,
        but its history carries every received one; polling the last entry
        for the matched address keeps signal data moving between payload
        changes.
        """
        for state in self.states.values():
            if state.address is None:
                continue
            info = async_last_service_info(self._hass, state.address, connectable=False)
            if info is None or info is state.last_info:
                continue
            rssi_changed = state.rssi != info.rssi
            was_stale = not state.is_fresh
            state.last_info = info
            state.rssi = info.rssi
            state.last_seen = time.monotonic()
            self._sync_availability(state.serial)
            if rssi_changed or was_stale:
                self._notify(state.serial)

    def _check_stale(self, now: object = None) -> None:
        """Write entities that just went stale (spec 0003 R3)."""
        for serial, state in self.states.items():
            self._sync_availability(serial)
            if self._notified[serial] and not state.is_fresh:
                self._notified[serial] = False
                self._notify(serial)

    def _sync_availability(self, serial: str) -> None:
        """Log exactly one info message per availability transition (spec 0023).

        A device that had been seen is logged unavailable once the stale
        check observes it is no longer fresh; a device never seen is logged
        once after the broadcast timeout since the monitor started (a fresh
        monitor start must not produce a false transition). Recovery is
        logged once when freshness returns and a prior unavailability was
        logged. The per-device flag keeps repeated stale checks and
        repeated advertisements from spamming the log.
        """
        state = self.states[serial]
        if state.is_fresh:
            if self._unavailable[serial]:
                self._unavailable[serial] = False
                LOGGER.info("danalock device %s is back online", serial)
            return
        if self._unavailable[serial]:
            return
        if state.last_seen is None and (
            self._monitor_started_at is None
            or time.monotonic() - self._monitor_started_at < BROADCAST_TIMEOUT_SECONDS
        ):
            return
        self._unavailable[serial] = True
        LOGGER.info(
            "danalock device %s is unavailable: no advertisement within %.0f seconds",
            serial,
            BROADCAST_TIMEOUT_SECONDS,
        )

    def _notify(self, serial: str) -> None:
        """Write the entities of one device (loop-safe scheduling).

        Timer callbacks normally run on the event loop, but test harnesses
        may fire handles inline from other threads; scheduling the writes
        keeps entity updates on the loop in every case.
        """
        for listener in self._listeners[serial]:
            self._hass.loop.call_soon_threadsafe(listener)
