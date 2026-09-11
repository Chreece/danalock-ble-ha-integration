"""Key refresh orchestration (spec 0007).

One manager per config entry owns the shared key cache and refreshes it:
lazily before commands (through the controls) and periodically in the
background with a configurable period and random jitter. A refreshed key is
written into the shared dict in place — the broadcast monitor, which holds
the same dict, decodes advertisements with the new broadcast key without
further wiring — is pushed to the bound control (facade rebuild), and is
announced to registered listeners (the entities).
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

import httpx
from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.helpers.event import async_track_point_in_time
from homeassistant.util import dt as dt_util

from custom_components.danalock_ble.const import (
    CONF_REFRESH_JITTER_HOURS,
    CONF_REFRESH_PERIOD_HOURS,
    DEFAULT_REFRESH_JITTER_HOURS,
    DEFAULT_REFRESH_PERIOD_HOURS,
    LEGACY_REFRESH_JITTER_MIN,
    LEGACY_REFRESH_PERIOD_MIN,
    option_refresh_hours,
)
from pydanalock.cloud import (
    AsyncDanalockCloud,
    AuthError,
    DanalockCloudError,
    DeviceKey,
)
from pydanalock.cloud.models import normalize_serial

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

    from custom_components.danalock_ble.control import DanalockControl

LOGGER = logging.getLogger(__name__)


class DanalockKeyManager:
    """Owns the shared key cache and refreshes keys (spec 0007).

    Concurrent refreshes of one serial are serialized (single-flight).
    Background cycle parameters come from the entry options:
    `refresh_period_hours` (0 disables the cycle) and
    `refresh_jitter_hours`, both re-read on `start()` after a reload.
    Entries saved by versions before 0.5.0 store minutes; those are
    converted by floor division until the options are saved again
    (spec 0010 R6).
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: AsyncDanalockCloud,
        keys: dict[str, DeviceKey],
        rng: Callable[[float, float], float] = random.uniform,
    ) -> None:
        self._hass = hass
        self._entry = entry
        self._client = client
        self._keys = keys
        self._rng = rng
        self._controls: dict[str, DanalockControl] = {}
        self._listeners: dict[str, list[Callable[[], None]]] = {
            serial: [] for serial in keys
        }
        self._refresh_locks: dict[str, asyncio.Lock] = {}
        self._cancel_timer: CALLBACK_TYPE | None = None
        self._stopped = False
        self._period = timedelta(0)
        self._jitter = timedelta(0)

    # --- consumers ---

    def bind_controls(self, controls: dict[str, DanalockControl]) -> None:
        """Attach the per-device controls (spec 0007 R6)."""
        self._controls = controls

    def key(self, serial: str) -> DeviceKey:
        """The current key of one serial from the shared cache."""
        return self._keys[serial]

    def add_listener(
        self, serial: str, listener: Callable[[], None]
    ) -> Callable[[], None]:
        """Register an entity update listener for one device."""

        self._listeners.setdefault(serial, []).append(listener)

        def _remove() -> None:
            self._listeners[serial].remove(listener)

        return _remove

    # --- refresh ---

    async def refresh(self, serial: str, *, force: bool = False) -> DeviceKey:
        """Refetch one key and propagate it (spec 0007 R5/R6).

        The fetched key replaces the shared cache entry in place, is pushed
        to the bound control, and listeners are notified. Identity is the
        normalized serial.
        """
        serial = normalize_serial(serial)
        async with self._refresh_locks.setdefault(serial, asyncio.Lock()):
            key = await self._client.get_key(serial, force=force)
            self._keys[serial] = key
            control = self._controls.get(serial)
            if control is not None:
                await control.set_key(key)
            self._notify(serial)
            return key

    async def refresh_all(self) -> None:
        """Force-refresh the keys of all configured devices (spec 0007 R9).

        An `AuthError` starts reauthentication for the entry; other cloud
        and network errors are logged per serial. The remaining serials are
        always processed.
        """
        for serial in list(self._keys):
            try:
                await self.refresh(serial, force=True)
            except AuthError as err:
                LOGGER.error(
                    "danalock key refresh for %s failed authentication: %s", serial, err
                )
                self._entry.async_start_reauth(self._hass)
            except (DanalockCloudError, httpx.HTTPError) as err:
                LOGGER.warning("danalock key refresh for %s failed: %s", serial, err)

    # --- background cycle ---

    def start(self) -> None:
        """Arm the background cycle from the entry options (specs 0007 R7,
        0010 R6).

        The period and jitter are integer hours; legacy minute keys are
        read as a fallback (no writes to the stored options).
        """
        period_hours = option_refresh_hours(
            self._entry.options,
            CONF_REFRESH_PERIOD_HOURS,
            DEFAULT_REFRESH_PERIOD_HOURS,
            LEGACY_REFRESH_PERIOD_MIN,
        )
        jitter_hours = option_refresh_hours(
            self._entry.options,
            CONF_REFRESH_JITTER_HOURS,
            DEFAULT_REFRESH_JITTER_HOURS,
            LEGACY_REFRESH_JITTER_MIN,
        )
        self._period = timedelta(hours=period_hours)
        self._jitter = timedelta(hours=jitter_hours)
        if self._period <= timedelta(0):
            return
        self._schedule_next()

    def stop(self) -> None:
        """Cancel the pending background refresh (spec 0007 R10).

        Final for this manager instance: a cycle that is in flight when the
        entry unloads finishes its current serial but does not re-arm the
        timer.
        """
        self._stopped = True
        if self._cancel_timer is not None:
            self._cancel_timer()
            self._cancel_timer = None

    def _schedule_next(self, base: datetime | None = None) -> None:
        """Schedule one cycle after the period plus a fresh jitter draw.

        The base defaults to the current time; a running cycle passes the
        fired point in time, so consecutive delays do not drift.
        """
        jitter_seconds = (
            self._rng(0.0, self._jitter.total_seconds())
            if self._jitter > timedelta(0)
            else 0.0
        )
        delay = self._period + timedelta(seconds=jitter_seconds)
        point = (base if base is not None else dt_util.now()) + delay
        self._cancel_timer = async_track_point_in_time(self._hass, self._run_cycle, point)

    async def _run_cycle(self, now: datetime) -> None:
        """One background cycle; the next one draws a fresh jitter (R7)."""
        self._cancel_timer = None
        try:
            await self.refresh_all()
        except Exception as err:  # noqa: BLE001 - shutdown races abort the cycle
            LOGGER.warning("danalock background key refresh aborted: %s", err)
        finally:
            if self._period > timedelta(0) and not self._stopped:
                self._schedule_next(now)

    def _notify(self, serial: str) -> None:
        """Write the entities of one device (loop-safe scheduling).

        Timer callbacks normally run on the event loop, but test harnesses
        may fire handles inline from other threads; scheduling the writes
        keeps entity updates on the loop in every case.
        """
        for listener in self._listeners.get(serial, ()):
            self._hass.loop.call_soon_threadsafe(listener)
