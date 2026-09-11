# 0004 — Entity shape alignment

- **Status:** implemented
- **Scope:** Home Assistant custom integration `danalock_ble` (phase 2)

## Summary

Align the entity shape with the Z-Wave integration of the same lock: one
primary lock entity, secondary status binary sensors, config-category
setting selects, and numeric state classes for counter-like sensors. Lock
control stays a no-op until `pydanalock-ble` 0004 (AFI) exists.

## Motivation

The same physical lock is exposed twice (Z-Wave and Bluetooth); dashboards
and automations should see a comparable shape. The `device_flags` sensor as
a string-state entity is not useful, counter/battery graphs render as
discrete sections because the states lack `state_class`, and the lock-state
binary sensor mixes state with device settings.

## Requirements

- R1 (MUST) Lock entity: unique_id `<serial>_lock`, entity id
  `lock.danalock_<serial>_lock` (spec 0002 R5), `has_entity_name`, no
  entity category, `supported_features` = 0. State derives from the
  broadcast `locked` flag (`locked` / `unlocked`). `lock`/`unlock` service
  calls are no-ops until `pydanalock-ble` 0004: log a warning, never change
  state optimistically; the state always re-derives from the latest
  broadcast. Attributes: one boolean per device flag (`utc_reliable`,
  `local_time_reliable`, `battery_powered`, `ext_board_present`,
  `ext_board_online`) plus `broadcast_version` (2).
- R2 (MUST) The lock-state binary sensor stays (spec 0002 R2, device class
  `lock`, `on` = unlocked) but carries no settings/calibration attributes —
  the state alone.
- R3 (MUST) Jammed status: `binary_sensor.danalock_<serial>_jammed`,
  entity category diagnostic, `on` = `blocked` flag set.
- R4 (MUST) Calibration status: `binary_sensor.danalock_<serial>_auto_calibrated`
  and `binary_sensor.danalock_<serial>_point_calibrated`, entity category
  diagnostic, `on` = the respective flag set.
- R5 (MUST) Setting selects: `select.danalock_<serial>_auto_lock`,
  `_brake_and_go_back`, `_blocked_to_blocked`, `_twist_assist`; options
  `Disabled` / `Enabled`; the current option follows the broadcast flag.
  `async_select_option` is a no-op until `pydanalock-ble` 0004: log a
  warning, never change state optimistically. Entity category config,
  disabled by the integration by default (Z-Wave parity).
- R6 (MUST) Battery sensor: the `battery_raw` sensor (spec 0002 R2) is
  renamed to `battery_level` — unique_id suffix `battery_level`, entity id
  `sensor.danalock_<serial>_battery_level`, display name "Battery level" —
  and re-interpreted as a percent value: `device_class: battery`, unit `%`
  (PERCENTAGE), `state_class: measurement`, an integer value (no
  fractional part, unlike the Z-Wave `battery_level` which reports a
  decimal). The broadcast
  byte is percent, not a raw 0..255 byte: it matches the Z-Wave
  integration's battery percent of the same lock (74 vs 76.0, readings
  taken hours apart) and the user's recent battery replacement
  (70–80% expected). This supersedes the spec 0002 "unconfirmed scaling,
  no percent conversion" caveat. The `battery_level` sensor and the
  `update_counter` sensor draw line graphs from numeric
  `state_class: measurement` samples.
- R7 (MUST) The `device_flags` sensor (spec 0002 R2) is removed; its data
  lives in the lock entity attributes (R1).
- R8 (MUST) Entity ids follow spec 0002 R5
  (`danalock_<serial>_<suffix>`); new suffixes: `lock`, `jammed`,
  `auto_calibrated`, `point_calibrated`, `auto_lock`, `brake_and_go_back`,
  `blocked_to_blocked`, `twist_assist`; the `battery_raw` suffix is
  renamed to `battery_level` (the old registry entry lingers until
  Home Assistant's orphaned-entity cleanup or user deletion).

## Design

- Lock and selects read the shared per-device state of the broadcast
  monitor (spec 0003). The no-op control pattern: service handlers log and
  return without touching state; because every state write derives from
  `DanalockDeviceState`, toggles never move the value and any refresh
  returns the actual state.
- Selects are disabled by the integration by default, mirroring the Z-Wave
  integration of this lock (its configuration entities ship disabled).
- Broadcast flags are booleans; the Z-Wave configuration parameters carry
  richer values (e.g. the auto-lock delay). When `pydanalock-ble` 0004 lands,
  the selects may gain real option sets — a spec update at that point.
- Registry entries of removed entities linger until Home Assistant's
  orphaned-entity cleanup; no registry surgery in the integration.

## Test plan

- Registry: entity ids per R8, select entities `disabled_by` integration.
- Synthetic advertisements drive lock state, jammed, calibration sensors;
  the lock entity carries the device-flag attributes; the `device_flags`
  sensor is absent.
- No-op behavior: `lock`/`unlock` calls and `select_option` leave the
  states unchanged and log a warning; a subsequent advertisement keeps the
  actual state.
- `battery_level` (renamed, percent, `device_class: battery`) and
  `update_counter` expose `state_class: measurement`; the `battery_raw`
  entity id no longer exists.
- The lock-state binary sensor has no extra attributes.

## Acceptance criteria

- Entity tests green; gate green (`pytest -m "not live"`); skeptic without
  `BLOCKING`.

## Out of scope

- Real lock control and settings changes (deferred to `pydanalock-ble` 0004,
  AFI/TLS stack), firmware update entity.

## Status

`implemented` (spec approved with recorded decisions in the planning
conversation, 2026-09-07: keep the lock-state binary sensor alongside the
lock entity, settings as selects not switches, jammed as a separate
diagnostic binary sensor, calibration bits as separate diagnostic binary
sensors, selects disabled by default, numeric state classes for counter
and battery, battery_raw renamed to battery_level; the broadcast battery
byte is percent — cross-checked against the Z-Wave integration of the
same lock; battery reported as an integer without a fractional part.
Implemented 2026-09-07: battery_level percent, numeric state classes and
the diagnostic entity category merged).
