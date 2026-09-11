# 0001 — Config flow and device registration

- **Status:** implemented
- **Scope:** Home Assistant custom integration `danalock_ble` (phase 1)

## Summary

The integration is configured through the Home Assistant UI: cloud login,
then key retrieval. HA devices are created only after keys are successfully
obtained. Config entries are keyed by the cloud account (username); tokens
persist through Home Assistant storage, per-device keys do not.

## Motivation

Credentials and key availability must be validated before entities exist, so
the integration never shows dead devices.

## Requirements

- R1 (MUST) Config flow steps:
  1. user form: username + password;
  2. the integration logs into the Danalock cloud and fetches devices/keys;
  3. success creates a config entry; failure re-shows the form with a typed
     error (`invalid_auth`, `no_keys`, `cannot_connect`, `unknown`).
- R2 (MUST) Devices (HA `DeviceRegistry`) are created only after keys are
  successfully obtained; one key = one device. Without keys there are no
  devices. Setup with zero devices succeeds (warn, empty device set).
- R3 (MUST) Device identity/naming:
  - `unique_id` = serial, normalized lowercase hex without separators
    (e.g. `1a2b3c4d5e6f`);
  - device name = `danalock_<serial>` (e.g. `danalock_1a2b3c4d5e6f`);
  - manufacturer `Danalock`; model `V3`;
  - connection `bluetooth` carries the wire-format serial
    (`xx:xx:xx:xx:xx:xx`).
- R4 (MUST) Re-authentication: when the stored refresh token stops working,
  the config entry enters reauth and asks for credentials again. A
  reconfigure step revalidates and updates the entry. Reauth/reconfigure
  dedupe against the normalized unique_id regardless of input case or
  surrounding whitespace.
- R5 (MUST) Persistence split:
  - tokens (access/refresh/expiry) are stored via Home Assistant storage
    facilities (`helpers.storage.Store`), never in `entry.data`;
  - `entry.data` holds only the username; the password exists only inside
    the config flow and is never persisted or logged;
  - per-device keys are NOT persisted: the cloud shifts the key validity
    window on every request, so persisted keys would always be stale. Keys
    are fetched at every setup (including reload) and kept in
    `entry.runtime_data` only.
- R6 (MUST) Entry identity: `unique_id` = username normalized with
  `strip().lower()` (email normalization per HA config-flow rules). One
  config entry per cloud account; a second login for the same normalized
  username aborts with `already_configured`.
- R7 (MUST) Stale-device cleanup: on setup/reload, devices in the registry
  that belong to this config entry but are absent from the cloud device list
  are removed.
- R8 (MUST) Device removal support: the entry implements
  `async_remove_config_entry_device` and allows removing any of its
  devices (`supports_remove_device` = true). Devices are recreated from the
  account keys at the next setup, so removal is always safe; the user can
  use it to regenerate entity ids (spec 0002 R5).

## Design

- **Dependencies (phase 1).** `pydanalock.cloud` is consumed from PyPI (pinned
  in `manifest.json`; spec 0014). `pydanalock.ble` was vendored into
  `custom_components/danalock_ble/pydanalock/ble/` by `scripts/vendor.py` from
  the local checkout; `VENDORED.json` recorded provenance and
  `scripts/vendor.json` pinned the version. The `pydanalock/` level stayed a
  PEP 420 namespace portion (no `__init__.py`); `pydanalock/ble/` kept its
  package `__init__.py`. No `bluetooth` manifest matchers yet: without the
  advertisement decoder, `manufacturer_id: 456` would discover foreign locks
  (tracked in spec 0003).
- **Dependencies (current).** Both `pydanalock.cloud` (spec 0014) and
  `pydanalock.ble` (spec 0015) come from PyPI and are pinned in
  `manifest.json`; nothing is vendored and `scripts/vendor.*` is gone.
- **Entry identity and username hygiene.** Entry `unique_id`, `entry.data`
  username, and entry title are the only places the username appears. Device
  names come from the normalized serial, the token store key from
  `entry.entry_id`, so no sanitization beyond the unique_id normalization is
  needed by construction.
- **Setup flow.** `async_setup_entry` pre-loads the entry token store,
  builds an `AsyncDanalockCloud` into `entry.runtime_data`
  (`DanalockRuntimeData(client, keys)`), fetches `devices()` (single
  request; the library seeds its key cache), confirms each key with
  `get_key()` (cache hit), registers devices, and prunes stale ones.
  `AuthError` raises `ConfigEntryAuthFailed` (reauth); connection/API errors
  raise `ConfigEntryNotReady` (retry; bronze `test-before-setup`).
  `async_unload_entry` closes the client (`aclose()`); `async_remove_entry`
  deletes the token store.
- **Token storage.** `DanalockTokenStorage(hass, entry)` wraps a
  `Store(hass, version=1, key=f"danalock_ble_tokens_{entry.entry_id}")`. The
  library's sync `TokenStorage` protocol maps to in-memory state plus
  fire-and-forget persistence (`hass.async_create_task` on
  `store.async_save`/`async_remove`); setup awaits one `async_load()`.
  Serialized shape: `{access_token, refresh_token, expires_at}`.
- **Flow-to-entry token handoff.** The user step has no entry (and thus no
  `entry_id`-keyed store) yet: its validation client uses an in-memory
  storage. Validated tokens are staged in `hass.data` keyed by the
  normalized username; the first `async_setup_entry` pops the stage
  synchronously after its own store load came up empty and seeds the entry
  store with an awaited save (race-free). Reauth/reconfigure validation
  likewise runs against an in-memory storage; on success the fresh tokens
  are persisted with an awaited save to the entry store before the reload
  is scheduled, so setup always sees them.
- **Live API facts** (non-public observations, described as protocol behavior): the
  key validity window shifts on every request, so `get_key()` re-checks
  expiry against the cached copy; `devices()` returns all keys in one
  request; the single-device endpoint requires the colon-separated wire
  serial, which the library reconstructs from the normalized serial.

## API

- Config flow: steps `user`, `reauth` (+ `reauth_confirm`), `reconfigure`;
  error mapping: `AuthError → invalid_auth`;
  `httpx.HTTPError`/`ApiError`/`DanalockCloudError → cannot_connect`;
  empty device list → `no_keys`; anything else → `unknown`.
- Runtime data: `DanalockRuntimeData(client: AsyncDanalockCloud,
  keys: dict[str, DeviceKey])`.
- Token store: `{"version": 1, "data": {access_token, refresh_token,
  expires_at}}` under key `danalock_ble_tokens_<entry_id>`.

## Test plan

- `pytest-homeassistant-custom-component` fixtures with
  `httpx.MockTransport` handlers and synthetic fixtures (same
  depersonalization scheme as the pydanalock-cloud test suite: serial
  `00:11:22:33:44:55`, `user@example.com`, constant key material); the real
  library runs against the mock transport — no HA-level HTTP mocks.
- Config flow: happy path; `invalid_auth`; `cannot_connect`; `no_keys`;
  `unknown`; `already_configured` on repeat login (including case/whitespace
  variants); reauth (same and changed username); reconfigure; username with
  special characters (`User+Tag@Example.COM ` with trailing space) →
  normalized unique_id, untouched device names.
- Setup: runtime data carries client+keys; devices registered; cloud
  unreachable → `ConfigEntryNotReady`; `AuthError` → `ConfigEntryAuthFailed`;
  reload re-fetches keys and prunes a removed device; unload closes the
  client; entry removal deletes the token store.
- Storage: save/load/clear roundtrip through `hass.storage`; tokens never in
  `entry.data`; password never persisted.
- Package smoke: the vendored namespace import worked (phase 1); the current
  smoke test imports the installed `pydanalock.ble`/`pydanalock.cloud`.

## Acceptance criteria

- Config flow tests green with full `config_flow.py` coverage (bronze);
  gate green (`pytest -m "not live"`); skeptic without `BLOCKING`.

## Out of scope

- Entity behaviour (spec 0002), broadcast monitoring and Bluetooth matchers
  (spec 0003), options flow (phase 2), diagnostics.
- `pydanalock.ble` vendoring landed with phase 2; it is now consumed from
  PyPI instead (spec 0015).

## Status

`implemented` (spec approved with recorded decisions in the phase-1
planning conversation, 2026-09-06: vendoring mechanics, entry unique_id =
username, deferred Bluetooth matchers, non-persistent keys, HA-Store
tokens, library 0004 close methods as precondition. Amended 2026-09-07:
device removal support (R8, `async_remove_config_entry_device`) so users
can regenerate entity ids by deleting a device. Implemented 2026-09-06/07:
config flow, cloud registration and key fetch merged; the event-loop
build fix and the serial-prefixed entity ids (R8) followed).
