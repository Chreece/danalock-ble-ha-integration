# 0005 — Device model and supported-type gating

- **Status:** implemented
- **Scope:** Home Assistant custom integration `danalock_ble` (phase 2)

## Summary

The device registry `model` comes from the API `device.device_type`, and
the broadcast monitor with every entity platform exists only for supported
device types (`danalockv3`); key material is fetched only for supported
devices.

## Motivation

Accounts can hold devices other than the Danalock V3 lock (bridge, ext
boards, future products). The broadcast decoding and the entity set are
V3-specific, so unknown device types must not produce entities; the model
string should reflect the product reported by the cloud instead of a
constant.

## Requirements

- R1 (MUST) Model: the device registry `model` derives from
  `device.device_type` (pydanalock-cloud spec 0002 R2): the mapping
  `danalockv3` → `Danalock V3`; an unknown non-empty type is used
  verbatim; an absent type falls back to `V3` (the previous constant).
  The device `unique_id` stays the normalized serial (spec 0001 R3).
- R2 (MUST) Supported-type gate: the broadcast monitor and every entity
  platform (lock, sensors, binary sensors, selects) exist only for
  devices whose `device_type` is supported (`danalockv3`). A device
  without `device_type` is treated as supported (compatibility with
  responses lacking the field). Unsupported devices still get
  device-registry entries with the R1 model; a warning is logged per
  skipped device (type only, no serial).
- R3 (MUST) Key material (login blob, broadcast key) is fetched only for
  supported devices; unsupported devices have no `DeviceKey`.
- R4 (MUST) Runtime data carries the device type per serial.

## Design

- The cloud client parses every device-list entry into a full key record
  (its contract, pydanalock-cloud 0002); the supported-type gate therefore
  happens in the integration after the listing: `get_key` is called per
  supported serial only.
- The device registry sync covers every device from the cloud list, so
  unsupported products still show up (with their raw type as model) and
  the stale-device cleanup keeps working per config entry.

## Test plan

- Model from `danalockv3`; unknown type used verbatim; absent type → `V3`.
- Mixed account (V3 + unsupported type): both devices registered, entities
  and monitor only for the supported one, warning logged, keys only for
  the supported serial.
- Unsupported-only account: entry loads, device registered, no entities,
  monitor absent.

## Acceptance criteria

- Entity tests green; gate green (`pytest -m "not live"`); skeptic without
  `BLOCKING`.

## Out of scope

- Firmware update entity: implementation pending research — the on-device
  version (major.minor.rev) and the cloud-reported version use diverging
  schemes; an experiment with a live version read is required before the
  comparison semantics can be defined.
- Legacy `/ekey/v3/...` sources; percent battery (done in spec 0004).

## Status

`implemented` (spec approved with recorded decision in the group
conversation, 2026-09-07: implement the device-type model and
supported-type gating per the observed API response; the firmware version
comparison stays pending research. Implemented 2026-09-07: device model
and supported-type gating merged).
