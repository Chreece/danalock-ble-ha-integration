# 0003 — Broadcast monitor

- **Status:** implemented
- **Scope:** Home Assistant custom integration `danalock_ble` (phase 2)

## Summary

State comes from passive BLE advertisement scanning through Home
Assistant's shared Bluetooth machinery; per-device state is decoded with
the configured broadcast keys and fanned out to the entities. No private
scanning loop, no GATT connection in this phase.

## Motivation

Passive scanning keeps the integration cheap and parallel to any other BLE
user of the lock; advertisements arrive roughly every 2 seconds and carry
lock flags, battery, and a fresh update counter.

## Requirements

- R1 (MUST) The manifest declares `bluetooth` matchers
  (`manufacturer_id: 456`, `connectable: false`) and setup registers a
  callback with Home Assistant's Bluetooth API. Each received manufacturer
  payload is tried against every configured device's `broadcast_key` with
  `pydanalock.ble.parse_broadcast` (spec 0005); the first checksum-valid
  decode identifies the device by the payload serial. The advertiser
  address must not be used as the device identity: the lock advertises
  from an unrelated/rotating address, only the payload serial is
  authoritative. Advertisements that do not decode to a configured serial
  are ignored (debug log, no errors).
- R2 (MUST) Updates: per device the latest decoded `BroadcastReport` and
  RSSI are kept in memory. The report (lock state, battery, counter, flags)
  is replaced only when the update counter increased modulo 65536
  (wrap-safe; equal counters keep the stored report). RSSI and freshness
  (`last_seen`) are refreshed on every decoded advertisement — the counter
  gate must not freeze signal data between state changes. Equal-counter
  advertisements with unchanged RSSI do not trigger entity writes.
- R2a (MUST) Manager dedup interplay: the Home Assistant Bluetooth manager
  suppresses dispatch of advertisements whose payload is identical to the
  previous one (RSSI is not part of that comparison), while its history is
  updated on every received advertisement. The monitor therefore also
  stores the advertiser address of the last decoded advertisement and polls
  `bluetooth.async_last_service_info` for that address every 30 seconds:
  a new history entry refreshes RSSI and freshness even when the payload
  is unchanged. A later payload change refreshes the stored address. If
  the lock silently rotates its advertiser address without a payload
  change, the poll freezes on the old address (RSSI freshness pauses)
  until the next payload change recaptures it.
- R3 (MUST) Availability: entities are `unavailable` when no valid
  advertisement has been decoded for the device within 5 minutes. A
  staleness check runs every minute and writes affected entities. GATT
  fallback polling and an options flow for the timeout are deferred until
  `pydanalock-ble` 0003/0004 (TLS/AFI) exist.
- R4 (MUST) Scanner coexistence: only Home Assistant's shared Bluetooth
  machinery is used (callback registration, passive mode); no private
  adapter session.
- R5 (SHOULD) Unparsable advertisements (wrong key/version) are ignored
  with a debug log, not errors.

## Design

- A per-entry `DanalockBroadcastMonitor` is started when the entry loads
  and stopped at unload. It holds a `DanalockDeviceState` per configured
  serial (`report`, `rssi`, `last_seen` from `time.monotonic()`) and a
  listener registry keyed by serial; platform entities register listeners
  and write state on updates.
- Freshness refreshes on every decoded advertisement (via dispatch or the
  R2a history poll): a checksum-valid but rollback-rejected report keeps
  the stored report but still counts as a received advertisement.
- Decoding runs in the callback thread of Home Assistant's Bluetooth
  manager (single cheap AES-CBC per configured device; 24-byte payload).
- The monitor and its timer handle are created in `async_setup_entry` and
  torn down in `async_unload_entry`.

## Test plan

- Synthetic advertisements (`BluetoothServiceInfoBleak` injected through
  the manager) drive sensor updates, availability, and staleness.
- Stale-counter handling (wrap-safe comparison, equal counter no-op);
  foreign-lock advertisement ignored; wrong key ignored.

## Acceptance criteria

- Tests green; gate green; skeptic without `BLOCKING`.

## Out of scope

- Advertisement format (`pydanalock-ble` spec 0005), entities (0002),
  GATT/TLS fallback polling (pending `pydanalock-ble` 0003/0004), options
  flow (deferred).

## Status

`implemented` (spec approved with recorded decision, 2026-09-06: sensors
from advertisements only, availability timeout as a constant, GATT
fallback and options flow deferred. 2026-09-07: R2 split into report-gate
vs per-ad RSSI freshness and R2a added for the manager dedup interplay.
Implemented 2026-09-06/07: broadcast monitor and key/flag sensors merged;
the R2/R2a freshness fix followed).
