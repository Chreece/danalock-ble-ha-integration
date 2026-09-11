# 0006 — Lock control over BLE

- **Status:** implemented
- **Scope:** Home Assistant custom integration `danalock_ble` (phase 3)

## Summary

Make the lock entity operational: `lock`/`unlock` service calls drive the
real lock through the vendored `pydanalock.ble` 0.6.0 control stack (GATT →
RPD → TLS → AFI). One control object per device owns the `DanalockLock`
facade; commands connect to the lock on demand through Home Assistant's
shared Bluetooth machinery and disconnect afterwards. The lock entity shows
an in-progress state while a command runs and an optimistic state until the
next counter-advancing broadcast confirms it.

## Motivation

Until now the lock entity was display-only (spec 0004 R1 kept `lock`/`unlock`
as no-ops because the control stack did not exist). `pydanalock-ble` 0.6.0
(specs 0001–0005) provides a session-capable facade with an explicit
`transport_factory` seam made for this integration. This spec supersedes the
no-op clauses of spec 0004 R1 (lock entity); spec 0004 stays `implemented`
for the rest of its scope. The selects (spec 0004 R5) remain no-ops.

## Requirements

- R1 (MUST) `lock`/`unlock` on the lock entity run the corresponding
  `DanalockLock` operation. Nothing invokes commands implicitly: no
  background commands, no state() polling, no keep-alive.
- R2 (MUST) The BLE transport is obtained through Home Assistant's
  `bluetooth` integration: resolve the current advertisement address of the
  device (`DanalockDeviceState.address`), then
  `async_ble_device_from_address(hass, address, connectable=True)` with a
  fallback to `connectable=False` (the monitor matcher is
  `connectable: false`, so the address may only exist in the non-connectable
  history), wrap the device in `BleakClient` and hand an already-constructed
  `GattTransport` to the facade via `transport_factory`. `GattTransport.connect`
  (raw address string) is never used. When no address is known (no broadcast
  seen yet) or no device is in the bluetooth history, the command fails with
  a typed error and the lock state is untouched.
- R3 (MUST) The TLS session runs in insecure mode
  (`trust_anchor=None, insecure=True`): the CA that issued the lock's
  certificate is not available for bundling, so certificate chain
  verification is skipped. Subject/serial verification of the presented
  certificate is still enforced by the library. The MITM risk this creates
  (an attacker who can present a certificate for the correct serial and
  position themselves between Home Assistant and the lock may steal the
  login token) is documented in this spec and the README.
- R4 (MUST) Optimistic state: while a command runs the entity reports
  `is_locking`/`is_unlocking` (state `locking`/`unlocking`); on success the
  entity sets a pending state (`pending_locked`) on the shared device state
  and `is_locked` returns it until a counter-advancing broadcast replaces
  the stored report (the counter gate of spec 0003 R2). Stale broadcasts
  (equal or lower counter) and RSSI refreshes never clear the pending state.
  After the staleness timeout entities go unavailable as before. The
  `binary_sensor.*_lock_state` and the flag attributes always reflect the
  last real broadcast, never the pending state.
- R5 (MUST) Command errors are surfaced as `HomeAssistantError` subclasses
  with human-readable messages: AFI `CommandError` maps by known status —
  `0x07` (nothing changed) is treated as success (the lock is already in the
  target state); `0x05` busy, `0x17` blocked, `0x0b` critical battery,
  `0x15` bad state, `0x18` hardware failure, `0x19` timeout, `0x41` not
  logged in, and the login-token validity range `0x5E`–`0x73` map to
  dedicated messages; unknown statuses carry the raw status code.
  `TimeoutError`, `ChannelClosed`, and `BleakError` map to a reachability
  error. On any error the pending state is not set and the exception
  propagates to Home Assistant.
- R6 (MUST) One `DanalockControl` per device serial, built at setup from the
  device key (`serial` as raw bytes, `login_blob` as the login token,
  `insecure=True`, `transport_factory`). Each command connects on demand and
  disconnects after the command (success or failure); the facade's internal
  command lock serializes concurrent commands. Entry unload disconnects all
  controls.
- R7 (MUST) Control objects are not built for devices without keys or
  unsupported device types; the lock entity of a device without a control
  keeps the no-op behavior with a warning.
- R8 (MUST) Live validation against the real lock (connect, unlock, lock)
  runs only behind the `live` marker, manually, and only after explicit user
  confirmation in the conversation.

## Design

- `DanalockControl` (new `control.py`) is the only owner of the
  `DanalockLock` facade. It resolves the BLE address from the broadcast
  monitor's device state at connect time (addresses can change between
  advertisements), builds the transport, and lets the facade establish
  RPD+TLS+login on demand. The facade address argument is unused because the
  transport factory resolves it; it is set to an empty string.
- Connect-per-command costs roughly 2–4 s (connect + RPD + TLS + login)
  before the command, which is acceptable to spare the lock battery and a
  connection slot; the lock tears idle sessions down after ~10 s itself.
- Errors: `LockAddressUnknownError` covers "no address yet" and "device not
  in bluetooth history"; `LockControlError` (both subclasses of
  `HomeAssistantError`) covers AFI status rejections and transport
  failures. Exception messages are English text; runtime translation of
  exception messages is not required (custom component, bronze scope).
- The vendored `pydanalock.ble` grows from the 0.3.0 advertising subset to the
  full 0.6.0 package (frames, RPD, TLS, AFI, facade). `bleak` and
  `cryptography` ship with Home Assistant core, so `manifest.json`
  `requirements` stays `[]`. Provenance is recorded in `VENDORED.json`
  (ble 0.6.0, commit `9f8e52060007046e581cc5634f5105cf875498f3`).
- Version: the manifest version becomes 0.2.0 with the release that ships
  this feature (`v0.2.0` tag + GitHub Release).

## API

```python
class DanalockControl:
    def __init__(self, hass, serial: str, key: DeviceKey, state: DanalockDeviceState) -> None
    async def operate(self, command: Literal["lock", "unlock"]) -> None
    async def disconnect(self) -> None

class LockControlError(HomeAssistantError): ...
class LockAddressUnknownError(LockControlError): ...
```

Lock entity (`lock.py`): `is_locked` prefers `pending_locked` over the
stored report; `async_lock`/`async_unlock` set the in-progress attributes,
call `control.operate`, set `pending_locked` on success, and re-raise errors
without a pending state.

`DanalockRuntimeData` gains `controls: dict[str, DanalockControl]`;
`async_unload_entry` disconnects every control when the unload succeeded.

## Test plan

- Control: facade built with the key material and `insecure=True`;
  `operate` calls the facade and disconnects on success and on error; AFI
  status mapping (`0x07` success, dedicated messages, raw code fallback);
  timeout/channel/bleak wrapping; transport factory resolves the address
  from the device state, uses `connectable=True` with the `connectable=False`
  fallback, and raises `LockAddressUnknownError` without an address or
  device.
- Lock entity: unlock shows `unlocking` during the command, optimistic
  `unlocked` after success, reset by a counter-advancing broadcast; lock is
  symmetric; errors leave the reported state and set no pending state; the
  lock-state binary sensor keeps showing the broadcast value.
- Broadcast: `pending_locked` resets only on a counter-advancing
  advertisement, never on equal-counter refreshes.
- Setup/unload: `runtime_data.controls` filled for keyed devices only; the
  entry unload disconnects all controls.
- Package: the vendored 0.6.0 symbols import (`DanalockLock`, `LockState`,
  `BatteryInfo`, `CommandError`, `ChannelClosed`, `GattTransport`).
- Live (manual only): connect + unlock + lock against the real lock.
- Gate: `pytest -m "not live"` green.

## Acceptance criteria

- All requirements implemented and covered by the tests above; gate green;
  skeptic review without `BLOCKING`; live run (R8) recorded as successful
  with user confirmation before the release.

## Out of scope

- Real select control (settings), AFI `battery()` queries, periodic
  `state()` queries, keep-alive sessions, `LockEntityFeature.OPEN`,
  config flow/reauth changes, a secure-TLS option (requires an out-of-band
  trust anchor), translation of exception messages.

## Status

`implemented` (spec approved with recorded decisions in the planning
conversation, 2026-09-07: TLS always insecure with the risk documented,
connect-per-command with disconnect after each command, optimistic pending
state on the lock entity only while the lock-state binary sensor stays
broadcast-derived, scope limited to unlock/lock, spec 0004 keeps status
`implemented` with its no-op clauses superseded by this spec. Live
validation 2026-09-07: unlock/lock against the real lock through the Home
Assistant instance; the lock entity and lock-state binary sensor statuses
confirmed as expected by the user).
