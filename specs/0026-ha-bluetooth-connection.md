# 0026 — Retry-aware Home Assistant Bluetooth connections

- **Status:** approved
- **Scope:** `custom_components/danalock_ble/control.py` active GATT connection setup

## Summary

Use Home Assistant's connectable Bluetooth device resolution together with
`bleak-retry-connector.establish_connection` for active Danalock GATT
connections. Advertisement monitoring remains passive and unchanged.

## Motivation

The integration already discovers Danalock advertisements through Home
Assistant's shared Bluetooth manager, but the active command path constructs a
`BleakClient` directly and falls back to a `connectable=False` history
entry when no connectable device is available.

A non-connectable history entry only proves that an adapter or remote scanner
can hear the advertisement; it does not prove that the path can establish a
GATT connection. Home Assistant integrations that actively connect should use
a connectable `BLEDevice` and the shared retry-aware connector so local and
remote Bluetooth adapters get consistent connection handling.

## Requirements

- R1 (MUST) Active Danalock commands MUST resolve the current address with
  `async_ble_device_from_address(..., connectable=True)`.
- R2 (MUST) The active connection path MUST NOT fall back to
  `connectable=False`. If no connectable device is available, it MUST raise
  `LockAddressUnknownError`.
- R3 (MUST) The resolved `BLEDevice` MUST be connected through
  `bleak_retry_connector.establish_connection` using Home Assistant's
  installed connector package and `BleakClient`.
- R4 (MUST) The integration MUST NOT add `bleak-retry-connector` to
  `manifest.json` requirements because Home Assistant already provides and
  pins it.
- R5 (MUST) Passive advertisement monitoring MUST remain unchanged:
  Danalock advertisements continue to be accepted from non-connectable
  scanners/proxies.
- R6 (MUST) The resulting connected client MUST continue to be wrapped in
  `pydanalock.ble.GattTransport`; protocol, TLS, AFI, key handling, entity
  behavior, and command semantics are out of scope.
- R7 (SHOULD) Connection failures SHOULD retain the existing
  `HomeAssistantError` mapping through the current control error path.

## Design

`DanalockControl._transport_factory` keeps the address from the latest
decoded advertisement. It asks Home Assistant for a connectable device for
that address. If one exists, it calls:

```python
client = await establish_connection(
    BleakClient,
    device,
    address,
)
```

The returned connected client is passed to `GattTransport`. No private scan
loop or protocol changes are introduced.

## API

No public API or entity changes.

Internal behavior changes from:

```text
HA history (connectable, then non-connectable) -> BleakClient.connect()
```

to:

```text
HA history (connectable only) -> establish_connection() -> GattTransport
```

## Test plan

- A connectable history entry is requested exactly once.
- `establish_connection` receives `BleakClient`, the resolved device, and
  the current advertiser address.
- The connected client returned by `establish_connection` is wrapped in
  `GattTransport`.
- If no connectable device exists, the factory raises
  `LockAddressUnknownError` and never queries non-connectable history.
- Existing missing-address behavior remains unchanged.
- Existing command/error/key-refresh tests remain green.

## Acceptance criteria

- `pytest -m "not live"` passes.
- Coverage remains above the repository thresholds.
- hassfest and HACS validation remain green.
- No new runtime dependency is added to `manifest.json`.
- Skeptic review has no BLOCKING findings.

## Out of scope

- Advertisement monitoring changes.
- Danalock protocol/library changes.
- Certificate trust-anchor work.
- Cloud key persistence or authentication changes.
- Firmware, calibration, settings, or entity changes.

## Status

`approved` (2026-09-22): approved for implementation as a focused upstream
connection-path fix.
