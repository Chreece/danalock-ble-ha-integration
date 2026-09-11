"""Broadcast monitor tests (spec 0003)."""

from __future__ import annotations

import logging
import time
from datetime import timedelta

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.danalock_ble.const import (
    BROADCAST_TIMEOUT_SECONDS,
    RSSI_REFRESH_INTERVAL,
)
from pydanalock.ble import LockSettings
from tests.conftest import (
    FOREIGN_BROADCAST_KEY,
    FOREIGN_SERIAL,
    SERIAL_NORMALIZED,
    CloudHandler,
    inject_advertisement,
    make_broadcast_payload,
    make_service_info,
    setup_entry,
)
from custom_components.danalock_ble.broadcast import DanalockDeviceState


@pytest.fixture(autouse=True)
def expected_lingering_timers() -> bool:
    """Advertisement injections leave a manager unavailable-check timer and
    loaded entries keep the monitor's staleness timer — both harmless."""
    return True


async def setup_with_bluetooth(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> MockConfigEntry:
    """An entry set up with the bluetooth machinery enabled."""
    entry = await setup_entry(hass, monkeypatch, CloudHandler())
    await hass.async_block_till_done()
    return entry


async def test_matching_advertisement_updates_state(
    enable_bluetooth: None,
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An advertisement encrypted with the device key updates its state."""
    entry = await setup_with_bluetooth(hass, monkeypatch)
    state = entry.runtime_data.monitor.states[SERIAL_NORMALIZED]

    inject_advertisement(
        hass,
        make_service_info(make_broadcast_payload(counter=4660), rssi=-70),
    )
    await hass.async_block_till_done()

    assert state.report is not None
    assert state.report.counter == 4660
    assert state.rssi == -70
    assert state.last_seen is not None


async def test_equal_counter_updates_rssi_but_keeps_report(
    enable_bluetooth: None,
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A changed payload with an unchanged counter refreshes RSSI/freshness
    but keeps the stored report (spec 0003 R2)."""
    entry = await setup_with_bluetooth(hass, monkeypatch)
    state = entry.runtime_data.monitor.states[SERIAL_NORMALIZED]

    inject_advertisement(
        hass, make_service_info(make_broadcast_payload(counter=1, battery_raw=137), rssi=-70)
    )
    await hass.async_block_till_done()
    assert state.report is not None and state.report.counter == 1
    assert state.rssi == -70
    last_seen = state.last_seen

    # different payload (battery field) but the same counter: the manager
    # dispatches it, the counter gate keeps the old report
    inject_advertisement(
        hass, make_service_info(make_broadcast_payload(counter=1, battery_raw=99), rssi=-80)
    )
    await hass.async_block_till_done()

    assert state.report is not None and state.report.counter == 1
    assert state.report.battery_raw == 137
    assert state.rssi == -80
    assert state.last_seen is not None and state.last_seen > last_seen


async def test_rssi_refreshed_by_poll_between_payload_changes(
    enable_bluetooth: None,
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Payload-identical advertisements are deduplicated by the manager and
    never reach the callback; the history poll still refreshes RSSI
    (spec 0003 R2a)."""
    entry = await setup_with_bluetooth(hass, monkeypatch)
    state = entry.runtime_data.monitor.states[SERIAL_NORMALIZED]

    inject_advertisement(hass, make_service_info(make_broadcast_payload(counter=1), rssi=-70))
    await hass.async_block_till_done()
    assert state.rssi == -70

    # same payload: the manager drops it before our callback (dedup)
    inject_advertisement(hass, make_service_info(make_broadcast_payload(counter=1), rssi=-80))
    await hass.async_block_till_done()
    assert state.rssi == -70

    entry.runtime_data.monitor._refresh_states(None)
    await hass.async_block_till_done()

    assert state.rssi == -80


async def test_counter_wrap_is_accepted(
    enable_bluetooth: None,
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A counter wrap (65535 → 0) counts as an increase."""
    entry = await setup_with_bluetooth(hass, monkeypatch)
    state = entry.runtime_data.monitor.states[SERIAL_NORMALIZED]

    inject_advertisement(hass, make_service_info(make_broadcast_payload(counter=65535)))
    await hass.async_block_till_done()
    assert state.report is not None and state.report.counter == 65535

    inject_advertisement(hass, make_service_info(make_broadcast_payload(counter=0)))
    await hass.async_block_till_done()
    assert state.report is not None and state.report.counter == 0


async def test_backwards_counter_is_rejected(
    enable_bluetooth: None,
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A counter that did not increase never overwrites fresher data."""
    entry = await setup_with_bluetooth(hass, monkeypatch)
    state = entry.runtime_data.monitor.states[SERIAL_NORMALIZED]

    inject_advertisement(hass, make_service_info(make_broadcast_payload(counter=1000)))
    await hass.async_block_till_done()
    assert state.report is not None and state.report.counter == 1000

    for stale in (999, 1000 + 32768):
        inject_advertisement(hass, make_service_info(make_broadcast_payload(counter=stale)))
        await hass.async_block_till_done()
        assert state.report is not None and state.report.counter == 1000


async def test_foreign_lock_is_ignored(
    enable_bluetooth: None,
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An advertisement of a foreign lock (own key) leaves state untouched."""
    entry = await setup_with_bluetooth(hass, monkeypatch)
    state = entry.runtime_data.monitor.states[SERIAL_NORMALIZED]

    payload = make_broadcast_payload(
        serial=FOREIGN_SERIAL, key=FOREIGN_BROADCAST_KEY, counter=99
    )
    inject_advertisement(hass, make_service_info(payload, address="11:22:33:44:55:66"))
    await hass.async_block_till_done()

    assert state.report is None


async def test_cleartext_serial_mismatch_is_ignored(
    enable_bluetooth: None,
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A payload decrypting with the key but claiming another serial is
    ignored (defensive cross-check, spec 0003 R1)."""
    entry = await setup_with_bluetooth(hass, monkeypatch)
    state = entry.runtime_data.monitor.states[SERIAL_NORMALIZED]

    payload = make_broadcast_payload(serial=FOREIGN_SERIAL, counter=5)
    inject_advertisement(hass, make_service_info(payload))
    await hass.async_block_till_done()

    assert state.report is None


async def test_non_danalock_advertisement_is_ignored(
    enable_bluetooth: None,
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Advertisements without the danalock manufacturer id are skipped."""
    entry = await setup_with_bluetooth(hass, monkeypatch)
    state = entry.runtime_data.monitor.states[SERIAL_NORMALIZED]

    service_info = make_service_info(make_broadcast_payload(counter=5))
    service_info.manufacturer_data.clear()
    service_info.manufacturer_data[999] = b"\x00"
    inject_advertisement(hass, service_info)
    await hass.async_block_till_done()

    assert state.report is None

    # the manager filters these out; the guard is exercised directly
    from homeassistant.components.bluetooth import BluetoothChange

    entry.runtime_data.monitor._handle_advertisement(service_info, BluetoothChange.ADVERTISEMENT)
    assert state.report is None


async def test_poll_skips_devices_without_address_or_fresh_history(
    enable_bluetooth: None,
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The history poll is a no-op before the first match and for devices
    whose history did not change."""
    entry = await setup_with_bluetooth(hass, monkeypatch)
    monitor = entry.runtime_data.monitor
    state = monitor.states[SERIAL_NORMALIZED]

    monitor._refresh_states(None)  # no address known yet
    assert state.rssi is None

    inject_advertisement(hass, make_service_info(make_broadcast_payload(counter=1), rssi=-70))
    await hass.async_block_till_done()
    assert state.rssi == -70
    monitor._refresh_states(None)  # history unchanged since the dispatch
    assert state.rssi == -70

    # a fresh history entry with the same rssi refreshes without a notify
    notifies: list[int] = []
    unsub = monitor.add_listener(SERIAL_NORMALIZED, lambda: notifies.append(1))
    inject_advertisement(hass, make_service_info(make_broadcast_payload(counter=1), rssi=-70))
    monitor._refresh_states(None)
    await hass.async_block_till_done()
    assert state.rssi == -70
    assert notifies == []
    unsub()


async def test_wrong_version_is_ignored(
    enable_bluetooth: None,
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unknown format versions are ignored (spec 0005 R2)."""
    entry = await setup_with_bluetooth(hass, monkeypatch)
    state = entry.runtime_data.monitor.states[SERIAL_NORMALIZED]

    payload = make_broadcast_payload(version=0x01)
    inject_advertisement(hass, make_service_info(payload))
    await hass.async_block_till_done()

    assert state.report is None


async def test_refresh_timer_fires_and_unload_cancels_it(
    enable_bluetooth: None,
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The 30 s history poll runs on its timer and stops with the entry
    (spec 0003 R2a).

    `async_track_time_interval` dispatches the non-callback `_refresh_states`
    through the executor as a *background* job, which a plain
    `async_block_till_done()` does not await. Waiting for background tasks
    after firing time is therefore required for the fired poll to have run
    before the state is asserted (the plain wait was the source of the CI
    flake).
    """
    entry = await setup_with_bluetooth(hass, monkeypatch)
    state = entry.runtime_data.monitor.states[SERIAL_NORMALIZED]

    inject_advertisement(hass, make_service_info(make_broadcast_payload(counter=1), rssi=-70))
    await hass.async_block_till_done()
    assert state.rssi == -70

    # deduped advertisement updates only the manager history
    inject_advertisement(hass, make_service_info(make_broadcast_payload(counter=1), rssi=-80))
    await hass.async_block_till_done()
    assert state.rssi == -70

    async_fire_time_changed(
        hass, dt_util.utcnow() + RSSI_REFRESH_INTERVAL + timedelta(seconds=1)
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    assert state.rssi == -80

    # after unload the timer is cancelled: the history no longer reaches us
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    inject_advertisement(hass, make_service_info(make_broadcast_payload(counter=1), rssi=-90))
    async_fire_time_changed(
        hass, dt_util.utcnow() + RSSI_REFRESH_INTERVAL + timedelta(seconds=1)
    )
    await hass.async_block_till_done(wait_background_tasks=True)

    assert state.rssi == -80


async def test_setup_without_bluetooth_succeeds(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without the bluetooth machinery setup still loads; monitoring is off."""
    import custom_components.danalock_ble.broadcast as broadcast_module

    def _raise(*args: object, **kwargs: object) -> None:
        raise RuntimeError("BluetoothManager has not been set")

    monkeypatch.setattr(broadcast_module, "async_register_callback", _raise)
    entry = await setup_entry(hass, monkeypatch, CloudHandler())

    assert entry.state.value == "loaded"
    assert entry.runtime_data.monitor is not None
    assert not entry.runtime_data.monitor.active


async def test_unload_stops_the_monitor(
    enable_bluetooth: None,
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After unload, advertisements no longer update the state."""
    entry = await setup_with_bluetooth(hass, monkeypatch)
    state = entry.runtime_data.monitor.states[SERIAL_NORMALIZED]
    inject_advertisement(hass, make_service_info(make_broadcast_payload(counter=5)))
    await hass.async_block_till_done()
    assert state.report is not None

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    inject_advertisement(hass, make_service_info(make_broadcast_payload(counter=6)))
    await hass.async_block_till_done()
    assert state.report is not None and state.report.counter == 5


async def test_pending_locked_clears_on_counter_advancing_broadcast(
    enable_bluetooth: None,
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An optimistic pending state is cleared by the next real report
    (spec 0006 R4)."""
    entry = await setup_with_bluetooth(hass, monkeypatch)
    state = entry.runtime_data.monitor.states[SERIAL_NORMALIZED]

    inject_advertisement(
        hass, make_service_info(make_broadcast_payload(counter=1, lock_flags=0b00001))
    )
    await hass.async_block_till_done()
    state.pending_locked = False

    inject_advertisement(
        hass, make_service_info(make_broadcast_payload(counter=2, lock_flags=0b00001))
    )
    await hass.async_block_till_done()

    assert state.report is not None and state.report.counter == 2
    assert state.pending_locked is None


async def test_pending_locked_survives_stale_broadcast(
    enable_bluetooth: None,
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An equal-counter advertisement refreshes freshness but keeps the
    pending state (spec 0006 R4)."""
    entry = await setup_with_bluetooth(hass, monkeypatch)
    state = entry.runtime_data.monitor.states[SERIAL_NORMALIZED]

    inject_advertisement(
        hass, make_service_info(make_broadcast_payload(counter=10, lock_flags=0b00001))
    )
    await hass.async_block_till_done()
    state.pending_locked = False

    inject_advertisement(
        hass,
        make_service_info(make_broadcast_payload(counter=10, battery_raw=99), rssi=-80),
    )
    await hass.async_block_till_done()

    assert state.pending_locked is False

    # a backwards counter does not clear it either
    inject_advertisement(hass, make_service_info(make_broadcast_payload(counter=9)))
    await hass.async_block_till_done()
    assert state.pending_locked is False


# --- setting value cache (spec 0010) ------------------------------------------


def known_state(entry: MockConfigEntry) -> DanalockDeviceState:
    """The device state of the fixture device."""
    return entry.runtime_data.monitor.states[SERIAL_NORMALIZED]


async def test_setting_values_start_unknown(
    enable_bluetooth: None,
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Before any GATT read every setting value is unknown (spec 0010 R4)."""
    entry = await setup_with_bluetooth(hass, monkeypatch)
    state = known_state(entry)

    assert state.setting_values == {
        "auto_lock": None,
        "brake_and_go_back": None,
        "blocked_to_blocked": None,
    }
    assert state.settings_known() is False


async def test_flag_off_transition_stores_zero(
    enable_bluetooth: None,
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A broadcast flag turning off stores the value 0 (spec 0010 R4)."""
    entry = await setup_with_bluetooth(hass, monkeypatch)
    state = known_state(entry)
    # bit5 auto_lock, bit0 locked: a prior GATT read had stored 60
    inject_advertisement(
        hass, make_service_info(make_broadcast_payload(counter=1, lock_flags=0b100001))
    )
    await hass.async_block_till_done()
    state.setting_values["auto_lock"] = 60

    inject_advertisement(
        hass, make_service_info(make_broadcast_payload(counter=2, lock_flags=0b00001))
    )
    await hass.async_block_till_done()

    assert state.setting_values["auto_lock"] == 0
    assert state.settings_known() is False  # the other two stay unknown


async def test_flag_on_transition_stores_unknown(
    enable_bluetooth: None,
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A broadcast flag turning on makes the value unknown again
    (spec 0010 R4)."""
    entry = await setup_with_bluetooth(hass, monkeypatch)
    state = known_state(entry)

    inject_advertisement(
        hass, make_service_info(make_broadcast_payload(counter=1, lock_flags=0b00001))
    )
    await hass.async_block_till_done()
    state.setting_values["brake_and_go_back"] = 0

    # bit4 brake_and_go_back turns on
    inject_advertisement(
        hass, make_service_info(make_broadcast_payload(counter=2, lock_flags=0b10001))
    )
    await hass.async_block_till_done()

    assert state.setting_values["brake_and_go_back"] is None


async def test_flag_still_on_keeps_known_value(
    enable_bluetooth: None,
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A flag that stays on keeps the cached value: the broadcast cannot
    carry the configured seconds (spec 0010 R4)."""
    entry = await setup_with_bluetooth(hass, monkeypatch)
    state = known_state(entry)

    inject_advertisement(
        hass, make_service_info(make_broadcast_payload(counter=1, lock_flags=0b100001))
    )
    await hass.async_block_till_done()
    state.setting_values["auto_lock"] = 60

    inject_advertisement(
        hass, make_service_info(make_broadcast_payload(counter=2, lock_flags=0b100001))
    )
    await hass.async_block_till_done()

    assert state.setting_values["auto_lock"] == 60


async def test_stuck_flag_does_not_zero_known_value(
    enable_bluetooth: None,
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A flag that stays off never zeroes a known value: the payload is a
    snapshot from the last regeneration and a written setting does not
    regenerate it (spec 0010 R4)."""
    entry = await setup_with_bluetooth(hass, monkeypatch)
    state = known_state(entry)

    inject_advertisement(
        hass, make_service_info(make_broadcast_payload(counter=1, lock_flags=0b00001))
    )
    await hass.async_block_till_done()
    state.setting_values["auto_lock"] = 60

    inject_advertisement(
        hass, make_service_info(make_broadcast_payload(counter=2, lock_flags=0b00001))
    )
    await hass.async_block_till_done()

    assert state.setting_values["auto_lock"] == 60


async def test_stale_broadcasts_never_touch_the_cache(
    enable_bluetooth: None,
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Equal-counter and backwards-counter broadcasts keep the cache
    (spec 0010 R4, the counter gate)."""
    entry = await setup_with_bluetooth(hass, monkeypatch)
    state = known_state(entry)

    inject_advertisement(
        hass, make_service_info(make_broadcast_payload(counter=10, lock_flags=0b100001))
    )
    await hass.async_block_till_done()
    state.setting_values["auto_lock"] = 60

    inject_advertisement(
        hass, make_service_info(make_broadcast_payload(counter=10, lock_flags=0b00001))
    )
    await hass.async_block_till_done()
    assert state.setting_values["auto_lock"] == 60

    inject_advertisement(
        hass, make_service_info(make_broadcast_payload(counter=9, lock_flags=0b00001))
    )
    await hass.async_block_till_done()
    assert state.setting_values["auto_lock"] == 60


async def test_apply_settings_fills_the_cache(
    enable_bluetooth: None,
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A verified settings result fills all three cache entries
    (spec 0010 R3)."""
    entry = await setup_with_bluetooth(hass, monkeypatch)
    state = known_state(entry)

    state.apply_settings(
        LockSettings(
            speed=5,
            auto_lock_time=60,
            brake_and_go_back_time=15,
            end_to_end_mode=1,
        )
    )

    assert state.setting_values == {
        "auto_lock": 60,
        "brake_and_go_back": 15,
        "blocked_to_blocked": 1,
    }
    assert state.settings_known() is True


# --- availability transition logs (spec 0023) ---------------------------------


@pytest.fixture(autouse=True)
def capture_broadcast_info(caplog: pytest.LogCaptureFixture) -> None:
    """Capture info records of the broadcast logger in every test."""
    caplog.set_level(logging.INFO, logger="custom_components.danalock_ble.broadcast")


def set_fake_time(monkeypatch: pytest.MonkeyPatch, offset: float) -> None:
    """Shift the broadcast module's monotonic clock by ``offset`` seconds."""
    real_monotonic = time.monotonic
    monkeypatch.setattr(
        "custom_components.danalock_ble.broadcast.time",
        type(
            "FakeTime",
            (),
            {"monotonic": staticmethod(lambda: real_monotonic() + offset)},
        ),
    )


def broadcast_info(caplog: pytest.LogCaptureFixture, needle: str) -> list[str]:
    """INFO messages of the broadcast logger containing ``needle``."""
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == "custom_components.danalock_ble.broadcast"
        and record.levelno == logging.INFO
        and needle in record.getMessage()
    ]


async def test_unavailable_transition_logged_once(
    enable_bluetooth: None,
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A fresh→stale transition logs exactly one info message (R2/R3a/R5)."""
    entry = await setup_with_bluetooth(hass, monkeypatch)

    inject_advertisement(hass, make_service_info(make_broadcast_payload(counter=1)))
    await hass.async_block_till_done()
    assert broadcast_info(caplog, "is unavailable") == []

    set_fake_time(monkeypatch, BROADCAST_TIMEOUT_SECONDS + 100)
    entry.runtime_data.monitor._check_stale(None)
    await hass.async_block_till_done()

    messages = broadcast_info(caplog, "is unavailable")
    assert len(messages) == 1
    assert SERIAL_NORMALIZED in messages[0]

    entry.runtime_data.monitor._check_stale(None)
    await hass.async_block_till_done()
    assert len(broadcast_info(caplog, "is unavailable")) == 1


async def test_recovery_transition_logged_once(
    enable_bluetooth: None,
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A stale→fresh transition logs exactly one back-online message."""
    entry = await setup_with_bluetooth(hass, monkeypatch)

    inject_advertisement(hass, make_service_info(make_broadcast_payload(counter=1)))
    await hass.async_block_till_done()

    set_fake_time(monkeypatch, BROADCAST_TIMEOUT_SECONDS + 100)
    entry.runtime_data.monitor._check_stale(None)
    await hass.async_block_till_done()
    assert len(broadcast_info(caplog, "is unavailable")) == 1

    set_fake_time(monkeypatch, 0)
    inject_advertisement(hass, make_service_info(make_broadcast_payload(counter=2)))
    await hass.async_block_till_done()

    online = broadcast_info(caplog, "back online")
    assert len(online) == 1
    assert SERIAL_NORMALIZED in online[0]

    inject_advertisement(hass, make_service_info(make_broadcast_payload(counter=3)))
    await hass.async_block_till_done()
    assert len(broadcast_info(caplog, "back online")) == 1


async def test_no_unavailable_log_before_first_advertisement_within_timeout(
    enable_bluetooth: None,
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A never-seen device is only logged after the timeout (R3b)."""
    entry = await setup_with_bluetooth(hass, monkeypatch)

    set_fake_time(monkeypatch, 60)
    entry.runtime_data.monitor._check_stale(None)
    await hass.async_block_till_done()
    assert broadcast_info(caplog, "is unavailable") == []

    set_fake_time(monkeypatch, BROADCAST_TIMEOUT_SECONDS + 100)
    entry.runtime_data.monitor._check_stale(None)
    await hass.async_block_till_done()
    assert len(broadcast_info(caplog, "is unavailable")) == 1


async def test_no_unavailable_log_when_bluetooth_disabled(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Without the Bluetooth callback there is no start time and no log (R7)."""
    import custom_components.danalock_ble.broadcast as broadcast_module

    def _raise(*args: object, **kwargs: object) -> None:
        raise RuntimeError("BluetoothManager has not been set")

    monkeypatch.setattr(broadcast_module, "async_register_callback", _raise)
    entry = await setup_entry(hass, monkeypatch, CloudHandler())
    await hass.async_block_till_done()

    set_fake_time(monkeypatch, BROADCAST_TIMEOUT_SECONDS + 100)
    entry.runtime_data.monitor._check_stale(None)
    await hass.async_block_till_done()

    assert entry.runtime_data.monitor.active is False
    assert broadcast_info(caplog, "is unavailable") == []


async def test_unavailable_log_once_per_device_with_many_entities(
    enable_bluetooth: None,
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """All entities share one device state: one transition, one log (R2)."""
    entry = await setup_with_bluetooth(hass, monkeypatch)

    inject_advertisement(hass, make_service_info(make_broadcast_payload(counter=1)))
    await hass.async_block_till_done()

    set_fake_time(monkeypatch, BROADCAST_TIMEOUT_SECONDS + 100)
    entry.runtime_data.monitor._check_stale(None)
    await hass.async_block_till_done()

    assert len(broadcast_info(caplog, "is unavailable")) == 1


async def test_recovery_via_history_poll_logged(
    enable_bluetooth: None,
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Recovery through the history poll logs one back-online message (R4)."""
    entry = await setup_with_bluetooth(hass, monkeypatch)
    monitor = entry.runtime_data.monitor
    state = monitor.states[SERIAL_NORMALIZED]

    inject_advertisement(
        hass, make_service_info(make_broadcast_payload(counter=1), rssi=-70)
    )
    await hass.async_block_till_done()

    set_fake_time(monkeypatch, BROADCAST_TIMEOUT_SECONDS + 100)
    monitor._check_stale(None)
    await hass.async_block_till_done()
    assert len(broadcast_info(caplog, "is unavailable")) == 1

    set_fake_time(monkeypatch, 0)
    # same payload: the manager deduplicates the callback, history updates
    inject_advertisement(
        hass, make_service_info(make_broadcast_payload(counter=1), rssi=-80)
    )
    await hass.async_block_till_done()
    monitor._refresh_states(None)
    await hass.async_block_till_done()

    online = broadcast_info(caplog, "back online")
    assert len(online) == 1
    assert state.rssi == -80
