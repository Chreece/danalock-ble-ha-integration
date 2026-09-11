# 0007 — Key refresh: lazy pre-check, retry on token rejection, background cycle

- **Status:** implemented
- **Scope:** `custom_components/danalock_ble` (control, sensors, setup, config
  flow) plus the `pydanalock.cloud` client (PyPI)
- **Depends on:** `pydanalock.cloud` ≥ 0.3.0 from PyPI
  (`get_key(..., force=)`; spec 0014)

## Summary

Login keys expire. After `valid_to` passes, every lock/unlock command is
rejected by the lock with the AFI `TOKEN_VALIDITY` status and today the only
remedy is a manual reload of the integration. This feature keeps keys fresh
without user action:

- **Lazy pre-check** — before each command, an expired key is refetched from
  the cloud (forced, ignoring the cache);
- **Retry once on rejection** — a `TOKEN_VALIDITY` rejection triggers one
  forced refetch and one retry of the command;
- **Background cycle** — keys are force-refreshed periodically with a
  configurable interval and random jitter; both parameters live in a new
  options flow.

Refreshed keys propagate to the broadcast monitor (same dict), the control
facade (rebuilt with the new login blob), and the `key_valid_until` sensor
(without a reload).

## Motivation

Keys are fetched once at setup (`spec 0001`) and never refreshed: the
`login_token` blob is signed by the cloud and validated by the lock itself,
so expired keys can only be replaced by a new cloud fetch. When the token
window ends, the lock refuses login after a completed TLS handshake with the
`TOKEN_VALIDITY` status family, and the current error message instructs the
user to reload the integration — which re-runs setup and refetches keys as a
side effect. A self-refreshing integration must (a) refresh proactively
before the window ends and (b) recover from a rejection with a fresh key
instead of asking the user to reload.

## Requirements

- R1 (MUST) Before each lock/unlock command, the control checks whether the
  current key has expired (`key_expired`); when it has, the control requests
  a forced refetch (`force=True`) from the key manager before running the
  command.
- R2 (MUST) A cloud failure during the R1 pre-check does not block the
  command: it is logged at debug level and the command runs with the
  existing key.
- R3 (MUST) When a command is rejected with `TOKEN_VALIDITY`, the control
  requests one forced refetch and retries the command exactly once. The
  facade is rebuilt with the refreshed login blob before the retry.
- R4 (MUST) If the retried command is rejected again with `TOKEN_VALIDITY`,
  or the R3 refetch fails, the control raises `LockControlError` with a
  message that no longer tells the user to reload the integration; the
  message names the key refresh and, on a refetch failure, includes the
  failure reason.
- R5 (MUST) A refreshed key propagates to every consumer without a reload:
  the shared key dict is updated in place (the broadcast monitor keeps
  decoding with the new broadcast key), the control facade is rebuilt with
  the new login blob, and registered listeners are notified.
- R6 (MUST) A key manager orchestrates refreshes: single-flight per serial
  (concurrent refresh requests for one serial are serialized), forced
  client fetch, in-place cache update, control update, listener
  notification.
- R7 (MUST) When enabled, the manager runs a background cycle that
  force-refreshes the keys of all configured devices. The interval is
  `refresh_period_minutes` (default 720; `0` disables the cycle; range
  0–43200) plus a per-cycle random jitter drawn uniformly from
  `0..refresh_jitter_minutes` (default 360; range 0–1440). The jitter draw
  is repeated for every cycle. The next run is scheduled with
  `async_track_point_in_time`.
- R8 (MUST) The options flow exposes exactly the two options R7 names, in
  minutes, with integer validation and the R7 ranges, defaulting to the
  stored options or the R7 defaults. Saving options reloads the config
  entry (update listener).
- R9 (MUST) Background refresh errors: `AuthError` is logged as an error
  and starts reauthentication for the entry; other cloud/network errors are
  logged as warnings per serial; the cycle continues with the remaining
  serials either way.
- R10 (MUST) Unloading the entry stops the background cycle before the
  cloud client is closed. Setup failure paths never leave a running cycle.
- R11 (MUST) The `key_valid_until` sensor renders the current key's
  `valid_to` live: it follows the key manager and updates when the key is
  refreshed, without an entity or entry reload.
- R12 (MUST) The `pydanalock.cloud` dependency is updated to 0.3.0 in the
  same branch (source of truth: the `pydanalock-cloud` repository, now on
  PyPI; spec 0014).
- R13 (MUST) Additive feature: the manifest version moves to `0.3.0` and
  ships as a tagged GitHub release (HACS, AGENTS §4/§8).

## Design

- **Key manager** (`keymanager.py`, new): owns the shared key dict
  reference, the control bindings, per-serial `asyncio.Lock` single-flight,
  and the listener registry (mirroring the broadcast monitor's listener
  pattern, loop-safe notification via `hass.loop.call_soon_threadsafe`).
  `refresh(serial, *, force)` fetches through
  `client.get_key(serial, force=force)`, writes the result into the shared
  dict in place, awaits `control.set_key(new_key)`, and notifies listeners.
  `refresh_all()` walks all serials sequentially (R9 error policy). The
  scheduler is armed in `start()` when the period is greater than zero and
  rescheduled after every completed cycle with a fresh jitter draw; `stop()`
  marks the manager final and cancels the pending timer — a cycle that is
  in flight at unload completes its current serial without re-arming
  (R10). The random source is injectable
  (`rng: Callable[[float, float], float]`, default `random.uniform`) for
  deterministic tests.
- **Control** (`control.py`): the constructor receives the key manager
  instead of a frozen key and takes its token via the manager. `set_key`
  disconnects the current facade session (idempotent; a running command
  finishes first through the facade's command lock) and rebuilds
  `DanalockLock` with the new login blob, keeping the same transport
  factory and device state. `operate` gains the R1/R2 pre-check and the
  R3/R4 rejection path; the `TOKEN_VALIDITY` status message loses the
  "reload the integration" instruction.
- **Sensor** (`sensor.py`): `DanalockKeyValidUntilSensor` holds the manager
  and serial instead of a frozen key, resolves the value through the
  manager, and subscribes to manager notifications alongside the monitor
  subscription.
- **Setup** (`__init__.py`): build order keys → manager (over the shared
  dict) → monitor (same dict) → controls → `manager.bind_controls` →
  `manager.start()` → platforms. The manager is created before the monitor
  and always present in `DanalockRuntimeData`; with no supported devices
  there is nothing to schedule (`keys` empty → no controls, cycle skipped).
  Unload stops monitor and manager before closing the client (R10). An
  update listener reloads the entry when options change (R8).
- **Options flow** (`config_flow.py`): `async_get_options_flow` returns a
  one-step `DanalockOptionsFlow` with the two integer fields (R8); runtime
  translations come from `translations/en.json` (new `options` section).
- **Dependency sync** (R12): `pydanalock.cloud` 0.3.0 came from the
  `pydanalock-cloud` repository; the library is now consumed from PyPI
  (spec 0014) and no vendored copy remains.

## API

```python
class DanalockKeyManager:
    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: AsyncDanalockCloud,
        keys: dict[str, DeviceKey],
        rng: Callable[[float, float], float] = random.uniform,
    ) -> None: ...

    def bind_controls(self, controls: dict[str, DanalockControl]) -> None: ...
    def key(self, serial: str) -> DeviceKey: ...
    def add_listener(
        self, serial: str, listener: Callable[[], None]
    ) -> Callable[[], None]: ...
    async def refresh(self, serial: str, *, force: bool = False) -> DeviceKey: ...
    async def refresh_all(self) -> None: ...
    def start(self) -> None: ...
    def stop(self) -> None: ...


class DanalockControl:
    def __init__(
        self,
        hass: HomeAssistant,
        serial: str,
        key_manager: DanalockKeyManager,
        state: DanalockDeviceState,
    ) -> None: ...

    async def set_key(self, key: DeviceKey) -> None: ...
    async def operate(self, command: Command) -> None: ...
```

## Test plan

- Key manager (new `tests/test_keymanager.py`): forced flag reaches the
  client; shared dict updated in place (the monitor observes the new
  broadcast key and decodes a fresh advertisement); `set_key` invoked on
  the bound control; listeners notified; concurrent refreshes serialized
  (single-flight); `AuthError` in `refresh_all` starts reauth and continues;
  a network error is logged and the remaining serials still refresh;
  scheduler delay equals `period + rng` stub, `period=0` schedules nothing,
  the jitter draw repeats every cycle.
- Control (`tests/test_control.py`): pre-check refreshes an expired key
  before the command; pre-check failure runs the command with the old key;
  `TOKEN_VALIDITY` triggers one refresh and one successful retry; a second
  rejection raises the new message (no reload instruction); a refresh
  failure on the rejection path raises with the cause; `set_key` rebuilds
  the facade with the new blob after disconnecting the old one.
- Sensors (`tests/test_sensors.py`): `key_valid_until` follows a refreshed
  key without a reload.
- Config flow (`tests/test_config_flow.py`): options flow defaults, saving
  values, range rejection, and the resulting reload.
- Setup (`tests/test_init.py`): manager created, started per options
  (disabled with `period=0`), stopped on unload, update listener reloads.
- Full gate: `.venv/bin/pytest -m "not live"` green.

## Acceptance criteria

- All R1–R13 implemented and covered by tests; gate green; skeptic review
  without `BLOCKING`; release `v0.3.0` with the manifest version match.

## Out of scope

- Per-key timers scheduled at `valid_to` (the coarse periodic cycle covers
  freshness); reacting to account device-list changes in the background
  (still setup/reload only); diagnostics; refreshing device names in the
  background; RU translations (public repo stays EN).

## Status

`implemented` (spec approved with a recorded decision in the planning
2026-09-09 conversation: strategy lazy + periodic background with
configurable period and jitter, configurable through the options flow,
options change reloads the entry. Implemented 2026-09-09: lazy pre-check,
retry-once on token rejection, background cycle with options flow merged;
manifest version 0.3.0).
