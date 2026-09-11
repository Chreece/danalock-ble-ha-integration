# 0010 — Firmware update check action and hourly key refresh options

- **Status:** implemented
- **Scope:** `update` platform action (forced firmware check) and the
  background key-refresh options (minutes → hours)

## Summary

Three review findings on the firmware update entity are resolved:

1. "Remove the state attributes that never appear" — **rejected**. The
   standard update attributes are emitted by Home Assistant core itself;
   overriding them would violate the core entity contract. No code change;
   the reasoning is recorded below.
2. A forced firmware check is missing — **implemented** as the
   `danalock_ble.check_firmware_updates` action (discoverable in the UI,
   target-restricted to the integration's update entities) plus an
   `async_update()` delegate on the entity, which also enables the core
   `homeassistant.update_entity` action for it.
3. The key-refresh options are confusing in minutes — **converted to
   hours**: `refresh_period_hours` (default 12, max 720) and
   `refresh_jitter_hours` (default 6, max 24), with a read-only fallback
   for entries saved with the old minute keys.

The firmware check interval itself stays fixed at 24 hours (confirmed in
the review scope).

## Motivation

The update entity checks the cloud for a newer firmware once a day on a
self-scheduled cycle. There is no way to run that check on demand: after
installing a firmware on the lock, a user wants an immediate refresh
instead of waiting for the next cycle. The core `homeassistant.update_entity`
action does not work for the entity today, because a force refresh calls
`async_update()` and the entity does not define it. A dedicated action
with a target selector makes the check discoverable and scriptable from
the UI.

The background key refresh options (`refresh_period_minutes`,
`refresh_jitter_minutes`) ask for values in minutes with huge defaults
(720/360); hours are the natural unit users reason about.

## Requirements

- R1 (MUST, recorded decision) The update entity does not override the
  standard state attributes. `UpdateEntity.state_attributes` is marked
  `@final` by HA core (verified on the core shipped with the test harness
  and on production HA 2026.9.1, `homeassistant/components/update/__init__.py`)
  and unconditionally emits all ten standard keys — `auto_update`,
  `display_precision`, `installed_version`, `in_progress`, `latest_version`,
  `release_summary`, `release_url`, `skipped_version`, `title`,
  `update_percentage` — with `null` where a value does not apply. That is
  identical for every update entity in Home Assistant (zwave_js, Hue, ...).
  Overriding a `@final` core API breaks on core upgrades and duplicates
  core bookkeeping (`skipped_version` state). The entity keeps only its own
  real additions: `firmware_identifier`, `hardware_version`, `maturity`.
- R2 (MUST) `DanalockFirmwareUpdateEntity.async_update()` runs one
  immediate cycle: cancel the pending cycle timer, run a full cycle
  (latest-version probe, BLE read when the versions diverge, state write),
  then re-arm the 24-hour schedule from that cycle. The forced cycle
  ignores the 24-hour window; it does not stack an additional timer. This
  also makes the core `homeassistant.update_entity` action work for the
  entity (a force refresh invokes `async_update()` when the entity defines
  it; `should_poll` stays `False`).
- R3 (MUST) The `danalock_ble.check_firmware_updates` action:
  - `services.yaml` declares it with a `target` of entities filtered to
    `domain: update` + `integration: danalock_ble`.
  - It is registered once in the component-level `async_setup` of the
    integration (not per entry, not on reload).
  - The handler resolves the targeted entity ids (entity, device, and
    area targets, groups expanded), looks each entity up in the update
    platform's entity component (`homeassistant.components.update.DATA_COMPONENT`,
    resolved at call time), and calls the `async_update()` path for every
    `DanalockFirmwareUpdateEntity` (`isinstance` filter).
  - An empty target, an unknown entity id, or a non-danalock update
    entity is a quiet no-op with a debug log (no exceptions, no warnings).
  - This closes the bronze `action-setup` quality-scale rule for the
    update platform.
- R4 (MUST) The action name and description come from
  `translations/en.json` (`services` section); `services.yaml` carries no
  `name`/`description` keys.
- R5 (MUST) New option keys in integer hours: `refresh_period_hours`
  (default 12, max 720) and `refresh_jitter_hours` (default 6, max 24).
  Period 0 disables the background cycle; jitter 0 adds no delay. The
  minute-based keys leave `const.py` and the options flow.
- R6 (MUST) Options migration without writes: `keymanager.start()` reads
  the hour keys; when a key is absent, it falls back to the legacy minute
  key of the saved options (`minutes // 60`, floor division; 0 minutes
  keeps the disabled semantics). Setup never writes to `entry.options`.
  The options flow prefills the form with the effective hours (the same
  fallback) and, when saving, writes only the new hour keys.
- R7 (MUST) `translations/en.json`: the options labels and descriptions
  say "(hours)"; a `services` section describes the action.
- R8 (MUST) The manifest version moves to `0.5.0`; the README capability
  list mentions the action and the hourly refresh settings.

## Design

- Null attributes (R1): the "missing" attributes are not missing — HA core
  renders every update entity's attribute list from one `@final`
  implementation. Trimming the list would require overriding that API,
  which is a contract violation against core updates.
- Forced cycle (R2): `async_update()` reuses the existing cycle method so
  the forced check and the scheduled daily check share one code path. The
  pending timer is cancelled before the cycle and re-armed after it, so
  the daily schedule restarts from the forced check instead of running
  double-fire.
- Action handler (R3): the component `async_setup` runs once per Home
  Assistant instance before any entry loads, while the update platform's
  entity component only exists after the update domain loads; the handler
  therefore resolves `DATA_COMPONENT` at call time and degrades to a
  quiet no-op when it is absent. Target resolution uses the standard
  `async_extract_entity_ids` helper (entity ids, device ids, area ids,
  group expansion).
- Hours migration (R6): entries saved by released versions do not exist
  yet (no releases so far), so no one-time option rewrite is needed; the
  fallback read covers locally modified installations. The fallback is a
  pure read of legacy keys; the first options-flow save replaces the
  option set with hour keys only.

## API

```python
# update.py
class DanalockFirmwareUpdateEntity(UpdateEntity, RestoreEntity):
    async def async_update(self) -> None: ...  # one immediate cycle, re-arms 24 h

def async_register_firmware_check_service(hass: HomeAssistant) -> None: ...

# const.py
CONF_REFRESH_PERIOD_HOURS = "refresh_period_hours"
CONF_REFRESH_JITTER_HOURS = "refresh_jitter_hours"
DEFAULT_REFRESH_PERIOD_HOURS = 12
DEFAULT_REFRESH_JITTER_HOURS = 6
MAX_REFRESH_PERIOD_HOURS = 720
MAX_REFRESH_JITTER_HOURS = 24
LEGACY_REFRESH_PERIOD_MIN = "refresh_period_minutes"
LEGACY_REFRESH_JITTER_MIN = "refresh_jitter_minutes"

def option_refresh_hours(
    options: Mapping[str, Any], hours_key: str,
    default_hours: int, legacy_minutes_key: str,
) -> int: ...

# keymanager.py
class DanalockKeyManager:
    def start(self) -> None:  # reads hours, timedelta(hours=...)
```

## Test plan

Synthetic fixtures only (the existing cloud mock handler and control test
double; no real Bluetooth):

- `test_update.py`: `homeassistant.update_entity` on the entity runs the
  cycle immediately (the BLE read counter grows, the entity state flips on
  with a diverging `/latest` payload); the timer was re-armed — the next
  daily cycle 24 hours later runs a fresh probe and, with equal versions,
  leaves the lock alone.
- `test_init.py`: `danalock_ble.check_firmware_updates` with the entity as
  target runs an immediate cycle; an unknown/foreign entity target and an
  empty target are quiet no-ops (no exception, no BLE read, no error-level
  log).
- `test_config_flow.py`: the options flow saves hours (untouched form →
  12/6 defaults); bounds — 0 and the maxima are valid, 721/25 raise
  `InvalidData`; an entry with legacy minute options prefills the form
  with the converted hours and saves hour keys only.
- `test_keymanager.py`: the scheduler uses the hour keys (period plus
  jitter); period 0 schedules nothing; the legacy minute keys are
  converted by floor division (720 → 12 h, 0 → disabled); hour keys win
  over legacy keys when both are present.
- Full gate `pytest -m "not live"` green.

## Acceptance criteria

- Full green `pytest -m "not live"`; skeptic review without `BLOCKING`.
- Live (Sweet Home, after a full Home Assistant restart — a reload does
  not re-read the modules): the options flow shows hours; calling
  `danalock_ble.check_firmware_updates` on the entity triggers an immediate
  cycle (the state stays `off`, BLE reads happen at once), and the daily
  timer is re-armed.

## Out of scope

- A configurable firmware check interval (stays fixed at 24 hours;
  confirmed in the review scope).
- Removing or redefining the standard core update attributes (R1).
- Changes in the `pydanalock.ble` / `pydanalock.cloud` libraries (both from
  PyPI; specs 0014, 0015).
- Tagging/releasing: `v0.5.0` remains a user step (the repository has no
  remote configured).

## Status

`implemented` (spec approved with a recorded decision in the group planning
conversation 2026-09-09: the null-attribute finding is rejected against the
HA core `@final` contract, the forced-check action and hourly options are
approved; the firmware check interval stays fixed at 24 hours).
Implemented 2026-09-09: merged with the manifest at `0.5.0`; the skeptic
review returned no blocking findings and its three nits (cycle
serialization, component-absent no-op coverage, warning-level log
assertions) were fixed on the branch.
Live-verified 2026-09-10 on Sweet Home (HA 2026.9.1, full restart): the
config entry loads, `danalock_ble.check_firmware_updates` is registered, the
options flow shows `refresh_period_hours` = 12 and `refresh_jitter_hours`
= 6 (the entry had no stored options, so the legacy fallback stays
test-only for this instance), and a forced check leaves the state at
`off` with a clean log. The live run surfaced one correction, fixed
here: HA 2025.10 dropped the `hass` argument from
`async_extract_entity_ids` and deprecated the legacy form (removed in
HA 2026.10), so the handler now picks the call form by inspecting the
helper's signature.
