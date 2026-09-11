# 0023 — Silver `log-when-unavailable`: advertisement availability transitions

- **Status:** approved (owner decision recorded in the group conversation,
  2026-09-11: close the silver `log-when-unavailable` rule)
- **Scope:** `custom_components/danalock_ble/broadcast.py` (transition
  logging) and its tests, plus the `manifest.json` version. No change to the
  availability model, entity state, or cloud-derived paths.

## Summary

The Home Assistant integration quality scale silver rule
`log-when-unavailable` requires an integration to log **once** when a device
becomes unavailable and **once** when it comes back, at `info` level, so a
user can understand from the log why entities turned unavailable.

Today the availability of every advertisement-driven entity is derived from
`DanalockDeviceState.is_fresh` (`broadcast.py:77-82`) via
`DanalockEntity.available`, but the monitor never logs the fresh→stale or
stale→fresh transitions. This spec adds exactly one `info` log per device per
transition, at the monitor level (`DanalockBroadcastMonitor`), so the message
count is per device, not per entity (many entities share one device state).

## Motivation

- The group compliance checklist marks `log-when-unavailable` as not satisfied:
  availability transitions are only visible on `debug`/error paths, not as
  `info` transitions.
- A user whose lock stops advertising currently sees entities go
  `unavailable` with no explanatory `info` line and no recovery line.
- Logging at the entity level would emit one line per entity (~15 per device)
  for a single transition, violating the rule's "log only once in total". The
  monitor is the single place where transitions are detected and has a natural
  per-device key (the serial).

## Requirements

- R1 (MUST) Availability stays `DanalockDeviceState.is_fresh`; this spec
  changes logging only. Entity `available` values must not change.
- R2 (MUST) The monitor logs exactly one `info` message when a device goes
  from fresh to stale, and exactly one `info` message when it returns from
  stale to fresh. The unit is the **device**, not the entity.
- R3 (MUST) "Unavailable" is logged when either (a) the device had been seen
  (`last_seen is not None`) and a stale check found `not is_fresh`, or (b) the
  device has never been seen and at least `BROADCAST_TIMEOUT_SECONDS` have
  elapsed since the monitor started.
- R4 (MUST) "Back online" is logged when a previously logged unavailability is
  cleared by a decoded advertisement (`_accept`) or by the history poll
  (`_refresh_states`).
- R5 (MUST) Repeated stale checks while the device stays stale, and repeated
  advertisements while it stays fresh, must not produce additional records.
- R6 (MUST) Messages are at `info` level and contain the device serial only;
  the BLE address is not included (it is not authoritative and is `None`
  before the first advertisement).
- R7 (MUST) When Bluetooth monitoring is disabled
  (`async_register_callback` raised `RuntimeError`), no per-device
  availability logs are produced; the existing `warning` remains.
- R8 (SHOULD) Cloud-derived entities (`update.py`,
  `DanalockKeyValidUntilSensor`, key manager) keep `available=True` and
  receive no transition logs; their failures keep their current levels.
- R9 (MUST) Bump `manifest.json` `version` to `0.8.6`; `pytest -m "not live"`
  is green and `hassfest`/`hacs/action` stay green.

## Design

Per-device state is tracked on the monitor:

- `self._unavailable: dict[str, bool]` — whether the current unavailability of
  the device has already been logged (initialized `False` for every serial).
- `self._monitor_started_at: float | None` — monotonic timestamp recorded in
  `start()` **only** when the Bluetooth callback registration succeeds; stays
  `None` when monitoring is disabled.

A single helper implements the transition rules and is called from all three
places where freshness changes or is observed:

```python
def _sync_availability(self, serial: str) -> None:
    """Log exactly one info message per availability transition."""
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
```

Call sites:

- `start()` — in the success branch, record `self._monitor_started_at =
  time.monotonic()` (the `RuntimeError` branch leaves it `None`).
- `_accept` — after `state.last_seen = time.monotonic()`: recovery on
  dispatch.
- `_refresh_states` — after `state.last_seen = time.monotonic()`: recovery via
  the history poll.
- `_check_stale` — at the top of the per-device loop, before the `_notified`
  block: stale detection.

Deduplication follows directly: while a device is stale the `_check_stale`
timer fires every minute but `_unavailable[serial]` is already `True`; while a
device is fresh every `_accept` runs the helper but the flag is `False`, so no
log is produced. A "back online" message is only ever produced after an
"unavailable" message.

The never-seen case is deliberately deferred: a freshly started monitor has
never seen the device, so logging immediately would produce a false transition
on every Home Assistant restart. After `BROADCAST_TIMEOUT_SECONDS` without any
advertisement the device is logged once as unavailable, which also covers a
lock that is offline from the moment the monitor starts. With monitoring
disabled there is no start time and no per-device log.

## API

No public API change. `DanalockDeviceState` and the monitor's listener
interface are unchanged; only log records are added.

## Test plan

Tests are written first (TDD) in `tests/test_broadcast.py`, using `caplog` on
the `custom_components.danalock_ble.broadcast` logger and the existing
synthetic-broadcast fixtures (`tests/conftest.py`):

1. `test_unavailable_transition_logged_once` — fresh advertisement, fake
   time past the timeout, `_check_stale`: exactly one `info` "is unavailable";
   a second `_check_stale` adds none.
2. `test_recovery_transition_logged_once` — after the stale log, a new
   counter-advancing advertisement logs exactly one "back online"; another
   advertisement adds none.
3. `test_no_unavailable_log_before_first_advertisement_within_timeout` — no
   advertisement, fake +60 s: no log; fake +400 s: exactly one "is
   unavailable".
4. `test_no_unavailable_log_when_bluetooth_disabled` — registration raises
   `RuntimeError`, fake +400 s: no per-device availability log.
5. `test_unavailable_log_once_per_device_with_many_entities` — a fully loaded
   entry (all platforms) with a fresh advertisement going stale logs exactly
   one line (per device, not per entity).
6. `test_recovery_via_history_poll_logged` — after the stale log, a
   deduplicated advertisement plus `_refresh_states` logs exactly one "back
   online".

## Acceptance criteria

- The six tests fail before the implementation and pass after it.
- The full `pytest -m "not live"` suite stays green.
- `manifest.json` version is `0.8.6`; `hassfest` and `hacs/action` stay green
  with no ignores.
- The group compliance checklist marks `log-when-unavailable` as satisfied.

## Out of scope

- Availability and logging of cloud-derived entities (`update.py`,
  `DanalockKeyValidUntilSensor`, key manager).
- Any change to `BROADCAST_TIMEOUT_SECONDS` / `STALE_CHECK_INTERVAL` or to the
  `is_fresh` model.
- Other silver rules (parallel-updates, action-exceptions, test-coverage),
  repair issues, translated exceptions, `icons.json`, diagnostics.
- Translations of log messages.

## Status

`approved` (2026-09-11). Implementation on
`feat/0023-log-when-unavailable`: monitor transition tracking, call-site
integration, caplog tests, version bump, local gate, then the group review
gate. Release `v0.8.6` is tagged after the squash-merge into `master`.
