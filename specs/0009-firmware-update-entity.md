# 0009 — Firmware update entity

- **Status:** implemented
- **Scope:** `update` platform: installed/latest firmware reporting

## Summary

Adds one read-only update entity per supported lock. The installed
version is read from the lock over Bluetooth (the device information
command of the vendored BLE library); the latest version comes from the
cloud firmware service (the unauthenticated lookup of vendored
pydanalock-cloud 0.4.0). The entity compares the two versions numerically
and reports "update available"; it never installs anything.

## Motivation

Both versions are observable: the lock reports its own firmware version
and firmware identifier, and the cloud firmware service publishes the
latest version for a serial. An update entity surfaces the comparison in
the Home Assistant UI without any user action. Firmware installation
(device-initiated DFU) is a separate project and is out of scope; the
entity exposes no install feature.

## Requirements

- R1 (MUST) One entity per device with a key (the same gate as the
  sensor platform): `DanalockFirmwareUpdateEntity` with
  `unique_id = "{serial}_firmware"`, `translation_key = "firmware"`,
  `has_entity_name`, device info identifiers `(DOMAIN, serial)`,
  suggested entity id `update.danalock_{serial}_firmware`,
  `entity_category = DIAGNOSTIC`, `device_class = firmware`,
  `supported_features = 0`, `in_progress = False`.
- R2 (MUST) `installed_version` is read over BLE through
  `DanalockControl.device_information()`; the version triple is rendered
  with the library's `version_dot` (e.g. `1.2.3`). The value is cached
  for the entry's lifetime and restored across restarts (RestoreEntity).
- R3 (MUST) `latest_version` comes from
  `AsyncDanalockCloud.latest_firmware(serial)`. Cloud and network
  failures are logged at debug level and keep the previous values; they
  never fail the entity or the config entry.
- R4 (MUST) Comparison: numeric major → minor → revision over exactly
  three dot-separated integer components; a malformed version (wrong
  component count, non-numeric) never claims an update (the
  `version_is_newer` override returns `False`).
- R5 (MUST) Cycle: a full cycle runs when the entity is added and then
  once every 24 hours. Each cycle fetches `/latest` and contacts the
  lock only when the installed version is unknown or differs from the
  latest; equal versions leave the lock alone. A failed `/latest` probe
  is retried after 15 minutes; a failed BLE read waits for the next
  cycle. Values are kept on failures.
- R6 (MUST) After a restart the previous installed/latest versions and
  attributes are restored; the restore itself does not contact the lock,
  and the first cycle contacts it only when the versions diverge.
- R7 (MUST) `available` is always `True` (cloud-derived values,
  independent of advertisement freshness, like the key-valid-until
  sensor).
- R8 (MUST) `extra_state_attributes`: `firmware_identifier` (from the
  lock, falling back to the `/latest` value), `hardware_version` (dot
  string), `maturity`. The short-lived signed download URL is not
  stored.
- R9 (MUST) BLE reads run through `DanalockControl.device_information()`
  with the same error mapping and disconnect discipline as the lock
  commands (including the lazy key pre-check and the single
  refresh-and-retry on a token rejection); control failures reach the
  entity as `LockControlError` and are logged without raising.
- R10 (MUST) The vendored libraries are refreshed from their
  repositories (pydanalock-cloud 0.4.0 latest-firmware lookup,
  pydanalock-ble 0.8.1 device information — bumped from 0.8.0 by the
  live-capture correction recorded in the Status section) via
  `scripts/vendor.py`, and the manifest `version` moves to `0.4.0` with
  a tagged release.
- R11 (MUST) `translations/en.json` gains
  `entity.update.firmware.name = "Firmware"`.
- R12 (MUST) `Platform.UPDATE` joins the forwarded platforms.

## Design

- Polling: HA core owns the update platform's scan interval (15 minutes)
  and a custom component cannot change it. The entity therefore
  schedules its own cycle the way the Z-Wave reference integration does:
  `_attr_should_poll = False`, a first full cycle when added, then
  `async_call_later` with a 24-hour interval (15-minute retry after a
  failed `/latest` probe). This differs from the original plan note
  (`_attr_should_poll = True` with a platform scan interval): the
  platform interval is not configurable from a custom component, so the
  reference implementation's self-scheduling delivers the same 24-hour
  behavior.
- BLE reads are bounded by the facade's per-command timeout (10 s
  default) and keep the event loop responsive (async BLE); a lock that
  is out of range leaves the values in place until the next cycle.
- The vendor refresh is done in the feature branch so the vendored
  snapshot, the new platform, and the release version travel together.

## API

```python
# control.py
class DanalockControl:
    async def device_information(self) -> DeviceInformation: ...


# update.py (new platform)
class DanalockFirmwareUpdateEntity(UpdateEntity):
    # installed_version / latest_version / extra_state_attributes /
    # version_is_newer override / 24-hour self-scheduling
```

## Test plan

The cloud mock handler gains a configurable `/latest` scenario (status
and payload, routed by path; the mock transport ignores the host); BLE
is exercised through a control test double that counts
`device_information` calls (no real Bluetooth in tests):

- Bootstrap: installed unknown → exactly one BLE read →
  installed = latest → state off.
- Equal versions → the next cycle does not contact the lock.
- Latest newer than installed → BLE read, lock still older → state on;
  after the lock reports the new version → state off.
- BLE failure → installed kept, entity available, the cycle raises
  nothing.
- HTTP 422 or malformed `/latest` → latest kept, no exception.
- Restart: restored installed/latest and attributes; the lock is not
  contacted while the restored versions agree with `/latest`.
- `version_is_newer` unit cases: equal → `False`; `0.9.10` vs `0.9.9` →
  `True` (numeric, not lexicographic); two components or garbage →
  `False`.
- Attributes, DIAGNOSTIC category, unique id, zero supported features,
  and the entity id format.
- Control: `device_information` maps command and transport failures like
  `operate` (the facade test double grows a `device_information`
  method).

## Acceptance criteria

- Unit tests green; full gate green (`pytest -m "not live"`); hassfest
  and HACS validation green; skeptic review without `BLOCKING`.

## Out of scope

- Firmware installation (DFU), the install feature, release notes.
- The firmware tracker endpoint as a data source.
- Persisting the signed download URL.

## Status

`implemented` (spec approved with a recorded decision in the group
planning conversation 2026-09-09: installed version from the lock over
BLE, latest version from the unauthenticated firmware lookup, numeric
comparison, no installation support, 24-hour cycle, DIAGNOSTIC category;
the signed download URL is never persisted. Implemented 2026-09-09:
update entity merged, manifest 0.4.0; the self-scheduling deviation from
the plan's should_poll note is recorded in the Design section.
Corrected 2026-09-09 during the live verification: the BLE library's
device information parser moved to 0.8.1 — the firmware identifier is a
NUL-terminated string without a length prefix, and the previous parse
failed on every real response — and `device_information` now maps a
malformed response to `LockControlError` instead of leaking `ValueError`.)
