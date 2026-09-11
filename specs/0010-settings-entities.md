# 0010 — Setting selects with verified writes

- **Status:** implemented
- **Scope:** `select` platform settings control, one diagnostic binary
  sensor, `DanalockControl` settings read/write

## Summary

Turn the three no-op setting selects (`auto_lock`,
`brake_and_go_back`, `blocked_to_blocked`) into real controls: options
follow the device's actual settings read over BLE, and a change writes
the setting through the vendored BLE stack and verifies it with a
follow-up read in the same session. Because the broadcasts cannot carry
the configured seconds (the flags are a boolean snapshot from the last
broadcast regeneration), the entity values live in a small in-memory
cache on the per-device state; the GATT read is authoritative. The
transient `twist_assist` select is removed and replaced by a diagnostic
binary sensor driven by its broadcast flag.

## Motivation

The selects exist since spec 0004 R5 as `Disabled`/`Enabled` no-ops
mirroring the boolean broadcast flags. The BLE library now reads and
writes the device settings (settings read/write of the vendored
`pydanalock.ble`), so the selects can expose the actual configuration and
apply changes, superseding the no-op clause of spec 0004 R5. The
broadcast flags cannot represent the configured values (seconds,
mode numbers) — only whether a feature is on — so the previous
`Disabled`/`Enabled` shape was also factually misleading. The
`twist_assist` flag is a transient runtime flag of the last operation,
not a stored setting; it belongs in a binary sensor, not in a select.

## Requirements

- R1 (MUST) The `select` platform exposes exactly three setting selects
  with unchanged unique ids (`<serial>_auto_lock`,
  `<serial>_brake_and_go_back`, `<serial>_blocked_to_blocked`) and
  suggested entity ids `select.danalock_<serial>_<suffix>` (spec 0002
  R5), `has_entity_name`, entity category `config`, disabled by the
  integration by default. Option sets:
  - `auto_lock`: `Off`, `5 s`, `10 s`, `15 s`, `30 s`, `45 s`, `60 s`,
    `180 s`, `300 s`, `600 s`, `900 s` (seconds; 0 = off).
  - `brake_and_go_back`: `Off`, `3 s`, `5 s`, `10 s`, `15 s`, `20 s`,
    `25 s`, `30 s`, `45 s`, `50 s`, `60 s` (seconds; 0 = off).
  - `blocked_to_blocked`: `Off`, `One`, `Two` (values 0, 1, 2). The
    stable historical suffix is kept (unique-id stability, spec 0004
    R5); the underlying device setting is the end-to-end mode carried
    in member 5 of the settings object (0 = off, 1 and 2 = the device's
    end-to-end variants). This mapping is documented in this spec and
    the README.
- R2 (MUST) The `twist_assist` select is removed. A diagnostic
  binary sensor `binary_sensor.danalock_<serial>_twist_assist`
  (unique id `<serial>_twist_assist`, `on` = the `twist_assist` lock
  flag set) replaces it, disabled by the integration by default
  (transient flag). The `twist_assist` translation moves from the
  `select` section to the `binary_sensor` section of
  `translations/en.json`. The registry entry of the removed select
  lingers until Home Assistant's orphaned-entity cleanup; the
  integration performs no registry surgery.
- R3 (MUST) A select change is a verified write: map the option to its
  value, run the matching control method (one BLE session: connect,
  write the setting, read the settings back, disconnect), store the
  returned settings in the value cache, and write the entity state. The
  device's verified value is shown as-is: it is authoritative even when
  it differs from the requested value. AFI status `0x07`
  (nothing changed) is success (the requested value is already set).
  On failure a `HomeAssistantError` with a human-readable message is
  raised (including `0x40` `NOT_PERMITTED`: the lock rejected the
  change because the account lacks the permission to change settings;
  `0x09` `UNSUPPORTED_ARGUMENT`: the lock does not support the setting
  or value); the value cache is not changed.
- R4 (MUST) The per-device value cache lives on the shared device
  state (three entries keyed by the setting suffixes, each an int or
  unknown): the GATT read is authoritative; the broadcast only refines
  off/on transitions. On a counter-advancing broadcast (the counter
  gate of spec 0003 R2): a flag turning off (1→0) stores the value 0;
  a flag turning on (0→1) stores unknown; a flag that stays on or
  stays off keeps the cached value. Equal-counter and
  backwards-counter broadcasts never touch the cache (like
  `pending_locked`). Observed device behavior this design accounts
  for: writing a setting over a local BLE session does not regenerate
  the broadcast payload, so a flag can stay off while the setting is
  on — the cache must not be zeroed by a stale flag; flags are a
  snapshot from the last broadcast regeneration and cannot carry the
  configured seconds. Documented consequence: while a flag is on, the
  concrete value (300 s vs 900 s, One vs Two) is known only from the
  GATT — a change made by another authority while the flag stays on is
  displayed as the last known value until the next GATT read, and a
  flag going 0→1 makes the value unknown again.
- R5 (MUST) When a select entity is added to hass
  (`async_added_to_hass`), the device settings are read over BLE
  (one read fills all three cache entries; the three selects share one
  read per device) unless all three values are already known. A failed
  read (lock out of range, transport failure) is logged at debug level
  and not retried; the values stay unknown. Reads happen when a user
  enables a select and at every setup/reload of the entry for already
  enabled selects; there is no read at start for disabled selects, no
  periodic polling, and the cache is in-memory only (not persisted
  across restarts).
- R6 (MUST) `current_option` maps the cached value to its option
  string; an unknown value reports `unknown`; a value with no matching
  option (a non-preset value returned by the device) reports `unknown`
  and is logged once at debug level when it is stored. Availability
  stays tied to broadcast freshness (spec 0002 R3). A select of a
  device without a control keeps the no-op behavior with a warning
  (spec 0006 R7).
- R7 (MUST) `DanalockControl` gains a settings read and one write
  method per setting with the same discipline as the other commands
  (lazy key pre-check, one refresh-and-retry on a token rejection,
  connect-per-command, disconnect in `finally`, error mapping by AFI
  status). A write is one BLE session: the library setter writes the
  setting and reads the settings back inside the session; the control
  connects before and disconnects after. The vendored BLE snapshot
  already carries the settings API (verified importability); the
  manifest `version` moves to `0.5.0` with the release that ships this
  feature.
- R8 (MUST) Method-neutral wording: describe behavior without stating how it
  was determined; cite only this repo's specs. The freshness limits are
  described as protocol behavior ("flags are a snapshot from the last
  broadcast regeneration; they cannot carry the configured seconds"). Live
  validation (reading the settings, changing a setting through the
  Home Assistant UI while watching the lock, restoring the previous
  value) runs only after explicit user confirmation in the
  conversation.

## Design

- The value cache rides on `DanalockDeviceState` (broadcast.py) next to
  `pending_locked`: `setting_values` maps the three setting suffixes to
  `int | None`, with `apply_settings()` (store a `LockSettings` result)
  and the transition update inside the counter-gated `_accept` path.
  The broadcast flag names equal the setting suffixes, so one tuple of
  suffixes drives both the cache and the selects.
- The shared per-device read: the first select that is added schedules
  the read task keyed by serial in a module-level registry; the other
  selects of the same device await the same task. With the synthetic
  test doubles this is deterministic; with real BLE it saves two
  connect/login round trips.
- Options are module constants mapping value → label; the select maps
  both ways and rejects unmapped values.
- Control: the four new methods (read, three writes) share one
  refresh/retry/error-mapping helper to avoid growing the
  operate/device_information duplication further; `operate` keeps its
  own shape (its nothing-changed handling differs). `device_information`
  is refactored onto the same helper (behavior-identical; covered by
  existing tests).
- Error messages for the new statuses join the shared status-message
  table; `0x40` and `0x09` thereby also map for lock commands.
- Vendoring: no re-vendor needed; the current snapshot
  (pydanalock-ble 0.8.1, pydanalock-cloud 0.4.0) already carries the
  settings API. `requirements` stays `[]` (bleak and cryptography ship
  with Home Assistant core).

## API

```python
# control.py
class DanalockControl:
    async def settings(self) -> LockSettings
    async def set_auto_lock(self, seconds: int) -> LockSettings
    async def set_brake_and_go_back(self, seconds: int) -> LockSettings
    async def set_end_to_end(self, mode: int) -> LockSettings

# broadcast.py
SETTING_FLAG_NAMES: tuple[str, ...]  # ("auto_lock", "brake_and_go_back", "blocked_to_blocked")
class DanalockDeviceState:
    setting_values: dict[str, int | None]
    def settings_known(self) -> bool
    def apply_settings(self, settings: LockSettings) -> None

# select.py
SETTING_SUFFIXES  # the three setting suffixes, no twist_assist
class DanalockSettingSelectEntity(entry, monitor, state, control, suffix)

# binary_sensor.py
class DanalockTwistAssistBinarySensor  # diagnostic, disabled by default
```

## Test plan

- Registry: three config selects (disabled by the integration), no
  `twist_assist` select, the `twist_assist` diagnostic binary sensor
  (disabled by the integration); platform suffix sets updated.
- Read at add: enabling the selects performs exactly one settings read
  for the device; all three `current_option` values render from the
  returned settings; a failed read leaves them unknown and logs at
  debug level.
- Write: selecting an option calls the matching control method with the
  mapped value, stores the verified result, and renders it; `Off`
  writes 0; the blocked-to-blocked select drives the end-to-end mode
  (0/1/2).
- Errors: `0x40` and `0x09` map to dedicated `HomeAssistantError`
  messages; the option stays unchanged.
- A non-preset verified value renders unknown and is debug-logged.
- Cache transitions (broadcast tests): 1→0 → 0; 0→1 → unknown; 1→1 and
  0→0 keep the cached value (the stuck-flag case must not zero a known
  value); stale counters never touch the cache.
- Control: settings read/write call the facade and disconnect on
  success and failure; status mapping (0x07 is handled inside the
  library setter, 9/0x40 at the control); transport failures wrap;
  token rejection refreshes and retries.
- Package: `LockSettings`, `lock_set_setting`,
  `parse_settings_payload` import from the vendored snapshot.
- The twist-assist binary sensor follows the broadcast flag.
- Gate: `pytest -m "not live"` green; hassfest + HACS validation green
  in CI.

## Acceptance criteria

- All requirements implemented and covered by the tests above; gate
  green; skeptic review without `BLOCKING`; live validation (R8)
  recorded in the Status section with user confirmation before the
  release; the blocked-to-blocked end-to-end mode Two is verified live
  with user confirmation or the option set is downgraded to `Off`/`One`
  before the release that ships it.

## Out of scope

- Arbitrary seconds (a number entity), the speed member as an entity,
  permission pre-checks in the integration (the lock itself rejects
  with 0x40), a cloud/bridge path for settings, periodic background
  reads, persisting the value cache, diagnostics support.

## Status

`implemented` (spec approved with the recorded decisions of the settings
planning conversation, confirmed at launch 2026-09-09: preset option
sets over a free-form number entity, the blocked-to-blocked suffix kept
for unique-id stability while driving the end-to-end mode member,
twist assist as a diagnostic binary sensor, no permission pre-check,
GATT-authoritative value cache with off/on broadcast transitions only,
one settings read when the selects are added, no write-time ether
confirmation, supersession of the spec 0004 R5 no-op clauses.
Implemented 2026-09-09: value cache with the off/on transition rules,
verified selects, twist assist diagnostic binary sensor, control
settings read/write on a shared refresh/retry helper, manifest 0.5.0.
Live validation 2026-09-09 with user confirmation: the startup read
reported auto lock 60 s (the value left by an earlier direct BLE
session), brake and go back Off, end-to-end Off; a write cycle through
Home Assistant (60 → 300 → off) completed with verified read-backs each
step and left the lock at auto lock 0 as agreed. The removed twist
assist select registry entry was cleaned up by Home Assistant's orphan
cleanup at the restart, and the diagnostic twist assist binary sensor
registered disabled by the integration. The visual end-to-end cycle
(0 → 1 → 0, optionally 1 → 2 → 1) is scheduled separately by the user
and has not run yet; until it runs, mode Two is unverified against the
firmware.)
