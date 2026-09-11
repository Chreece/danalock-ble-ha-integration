# 0002 — Entities

- **Status:** implemented
- **Scope:** Home Assistant custom integration `danalock_ble` (phase 2)

## Summary

Per device the integration creates a lock-state binary sensor and a set of
advertisement/key sensors; the device takes its friendly name from the
cloud when the API provides one. Lock/unlock control is deferred until
`pydanalock-ble` 0004 (AFI) exists.

## Motivation

Dashboards need the live lock state, signal quality, battery, and update
health without a GATT connection; the cloud key metadata exposes when the
broadcast key stops working.

## Requirements

- R1 (MUST) Device naming: the device registry entry is named after the
  cloud device name when the API returns one (e.g. a user-chosen name);
  otherwise it falls back to `danalock_<serial>`. The device `unique_id`
  stays the normalized serial (spec 0001 R3); names are re-applied at
  every setup/reload.
- R2 (MUST) Entities per device (unique_id = `<serial>_<suffix>`,
  `has_entity_name` = true, all except the lock state sensor are
  `entity_category: diagnostic`):
  - lock state binary sensor (`lock_state`, device class `lock`,
    `on` = unlocked, `off` = locked); attributes: one boolean per lock
    flag (same shape as the device-flags sensor);
  - `signal_strength` sensor: integer percent 0..100 from advertisement
    RSSI, `percent = clamp((rssi + 100) * 100 / 70, 0, 100)`
    (`-100 dBm → 0%`, `-65 dBm → 50%`, `-30 dBm → 100%`),
    unit `%` (`PERCENTAGE`), no `device_class`; attribute `rssi`
    (raw dBm);
  - `rssi` sensor: raw dBm (`SIGNAL_STRENGTH` device class, unit `dBm`);
  - `update_counter` sensor: raw update counter from the advertisement;
  - `battery_raw` sensor: raw battery byte 0..255 (scaling is
    device-defined and not confirmed; no percent conversion);
  - `device_flags` sensor: state = active device flag names joined by
    `", "` (`none` when empty); attributes: one boolean per device flag
    plus `broadcast_version` (2);
  - `key_valid_until` sensor: key validity end from the cloud key
    metadata (`device_class: timestamp`), independent of advertisement
    availability.
- R3 (MUST) Entity availability follows spec 0003 R3 (unavailable after 5
  minutes without a valid advertisement), except `key_valid_until`, which
  stays available while the key metadata exists.
- R4 (MUST) State updates on every accepted advertisement (roughly every
  2 seconds): the signal/RSSI sensors refresh on every decoded
  advertisement and via the spec 0003 R2a history poll between manager
  dispatches, the state entities (lock, battery, counter, flags) on
  counter changes; no active device polling (GATT) in this phase.
- R5 (MUST) Entity ids: every entity suggests the object id
  `danalock_<serial>_<suffix>` (via the entity `entity_id` suggestion
  path), so registry ids are `binary_sensor.danalock_<serial>_<suffix>`
  and `sensor.danalock_<serial>_<suffix>` and never depend on the
  user-changeable cloud device name. The cloud device name stays in the
  friendly names (device name + entity name). Already-registered entities
  keep their ids (the registry entry wins over the suggestion).

## Design

- Entities read the shared per-device state holder of the broadcast
  monitor (spec 0003) and write state on monitor updates; the RSSI→percent
  mapping is linear with clamping, boundaries reflect consumer adapter
  range.
- Lock control (lock/unlock entity) is intentionally deferred: it needs
  the AFI/TLS stack (`pydanalock-ble` 0003/0004) and its own spec update.
- The percent battery conversion waits for a confirmed scaling; the raw
  byte is exposed until then.

## Test plan

- RSSI→percent mapping table tests (boundaries -100/-65/-30, clamping on
  both sides).
- Synthetic advertisements drive lock state, signal, counter, battery,
  flags; cloud name propagates to the device registry; availability flips
  after the staleness timeout (frozen clock).

## Acceptance criteria

- Entity tests green; gate green; skeptic without `BLOCKING`.

## Out of scope

- Config flow (0001), broadcast scanner internals (0003), lock control
  entity (deferred to `pydanalock-ble` 0004), percent battery (unconfirmed
  scaling).

## Status

`implemented` (spec approved with recorded decision, 2026-09-06: sensor
set per the user's list — rssi, signal level, counter, device flags, lock
state, battery raw, key validity — plus cloud device naming; lock control
deferred. Amended 2026-09-07: entity ids must be serial-prefixed
(`danalock_<serial>_<suffix>`) regardless of the cloud device name (R5);
the cloud name stays in the friendly names. Implemented 2026-09-06/07:
sensors and naming merged; the serial-prefixed entity ids (R5) and the
RSSI freshness rework (spec 0003 R2/R2a) followed. Spec 0004 R6
supersedes the raw battery caveat: `battery_raw` became `battery_level`,
a percent value with `device_class: battery`).
