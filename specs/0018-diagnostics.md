# 0018 — Home Assistant diagnostics support

- **Status:** draft (implementation pending; see Status)
- **Scope:** add the Home Assistant diagnostics platform to the integration
  (config-entry diagnostics with redaction). No runtime change is made by this
  spec yet.

## Summary

Implement the Home Assistant diagnostics platform for the integration: a
`diagnostics.py` module with `async_get_config_entry_diagnostics(hass, entry)`
that returns a redacted snapshot of the config entry, its options, and the
per-device decoded state. This gives users a one-click way to attach useful
data to a bug report and satisfies the quality-scale rule
[`diagnostics`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/diagnostics/),
which the README currently lists under Roadmap.

## Motivation

When a lock misbehaves (wrong state, stale advertisements, a rejected login
token, a failed settings write), the useful context is spread across the
config entry options, the runtime `DanalockRuntimeData`, and the decoded
advertisement state. Today a user has to reconstruct that by hand from logs.
HA's diagnostics platform provides a standard, discoverable dump
(*Settings → Devices & Services → Danalock Bluetooth → Download diagnostics*)
and is the expected support tool for a gold-tier integration. The integration
has no `diagnostics.py` today.

## Requirements

- R1 (MUST) `custom_components/danalock_ble/diagnostics.py` implements
  `async_get_config_entry_diagnostics(hass, entry)`. Home Assistant discovers
  the module automatically; no `manifest.json` key is needed.
- R2 (MUST) The handler returns a plain, JSON-serializable `dict`; it performs
  no cloud or BLE I/O and no blocking calls. It must not mutate runtime state.
- R3 (MUST) The handler builds the snapshot from an explicit field whitelist;
  it never returns `entry.as_dict()` and never includes `entry.title` or
  `entry.unique_id`. Both carry the normalized account username (email) —
  `config_flow.py` sets `unique_id = normalized_username(username)` and
  `title = username` — and Home Assistant does not redact diagnostics
  automatically, so the platform is responsible. `entry.data` is passed
  through `homeassistant.components.diagnostics.async_redact_data` to redact
  the username, and all key material (`login_blob`, `broadcast_key`) is kept
  out of the snapshot. Raw `DeviceKey` objects are never returned. Password
  and tokens are already not persisted in `entry.data`, but the handler must
  not assume that.
- R4 (MUST) The snapshot includes, without secrets:
  - the effective options (`refresh_period_hours`, `refresh_jitter_hours`),
  - the integration version, read from the manifest through
    `homeassistant.loader.async_get_integration(hass, DOMAIN)` (it is not part
    of `DanalockRuntimeData`),
  - per device: decoded lock state (`locked`, `battery_raw`, `counter`,
    `lock_flags`), signal (`rssi`), setting-value cache, availability
    (advertisement freshness), and whether an optimistic lock state is
    pending,
  - per device: the key validity timestamp and permissions metadata (not the
    key bytes).
- R5 (MUST) The handler tolerates a missing or `None` `entry.runtime_data`
  (entry not loaded, setup failed, unloaded during download) and still returns
  the entry-level diagnostics instead of raising.
- R6 (MUST) Tests in `tests/test_diagnostics.py` use the existing
  `pytest-homeassistant-custom-component` fixtures and the mocked cloud
  handler, and assert: the expected top-level keys exist and, as a deep check
  over `json.dumps(result)`, neither the account username string nor any
  `login_blob`/`broadcast_key` substring occurs anywhere in the output (not
  merely at a guessed key). An unloaded entry returns entry-level diagnostics
  without raising.
- R7 (MUST) The full gate is green: `pytest -m "not live"`, plus `hassfest`
  and `hacs/action` (integration) in CI.
- R8 (MUST) On implementation, the README Roadmap entry for diagnostics is
  removed (or marked as implemented); the README already labels the rule as a
  gold step. The manifest version is bumped per AGENTS §12 (runtime change).
- R9 (MUST) No new runtime dependency: the diagnostics helpers ship with Home
  Assistant core.

## Design

- HA calls the module's `async_get_config_entry_diagnostics` when the user
  downloads diagnostics for the entry. The implementation reads
  `entry.runtime_data` (`DanalockRuntimeData`), whose shape is defined in
  `__init__.py`: `client`, `keys`, `names`, `device_types`, `key_manager`,
  `controls`, `monitor`.
- The snapshot is built from explicit fields: `entry.options`, a redacted copy
  of `entry.data`, the public integration version, and the per-device state.
  `entry.title`/`entry.unique_id` are deliberately excluded because they carry
  the account email, and HA does not redact diagnostics automatically.
- Redaction list: the config-entry username plus the key-material attribute
  names. The diagnostics builder maps `DeviceKey` fields explicitly
  (`valid_to`, `permissions`) instead of dumping the dataclass, so a future
  field cannot leak by accident. Each `Permission` (a frozen dataclass) is
  reduced to a plain `{id, name, description}` mapping so the result stays
  JSON-serializable (R2).
- `monitor.states` already holds the decoded per-device state
  (`DanalockDeviceState`); the builder copies scalar values into a fresh dict
  rather than returning the live objects, satisfying R2.
- The handler is config-entry scoped. Device-level diagnostics
  (`async_get_device_diagnostics`) is not required by the rule and is out of
  scope here.
- Serials and advertiser addresses are device identifiers used for matching;
  they stay in the (user-downloaded) snapshot and are never committed. The
  group publication rules are unaffected because diagnostics output is not
  repository content.

## API

No integration API change. A new, HA-standard diagnostics endpoint becomes
available for the config entry; no service, entity, or option is added.

## Test plan

- Load an entry with the mocked cloud handler and download diagnostics; assert
  the structure contains the expected entry/options/device keys.
- Assert, over `json.dumps(result)`, that the account username and any
  `login_blob`/`broadcast_key` substring are absent.
- Call the handler with an entry that has no `runtime_data`; assert it returns
  entry-level diagnostics.
- Full gate `pytest -m "not live"` green; `hassfest` and `hacs/action` green.

## Acceptance criteria

- R1–R9 hold.
- Downloading diagnostics produces valid JSON with no credentials, tokens, or
  key bytes.
- The entry loads and unloads normally; diagnostics never raise for a loaded
  or unloaded entry.
- The quality-scale `diagnostics` rule is satisfied.

## Out of scope

- Device-level diagnostics (`async_get_device_diagnostics`).
- Diagnostics for config-flow steps (HA does not have such a mechanism).
- Redacting entity ids or the serial numbers used for device matching.
- Any change to the library or its public API.
- Implementation before the spec moves to `approved`.

## Status

`draft` — implementation pending. Per the spec-driven process, a `draft` spec
must not be implemented until it moves to `approved`. The intended target is a
release after the current publication window; the manifest version bump, the
tests, and the README Roadmap update happen on the implementing branch
(R6–R8).

The number `0018` is used because `0016` was left unused to avoid colliding
with a possible renumbering of the duplicated `0010` pair (the tree keeps both
`0010` specs as-is), and `0017` covers the key-refresh period cap; `0018` is
the next free number.
