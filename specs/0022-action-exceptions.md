# 0022 — Silver `action-exceptions`: validate targets, raise typed action errors

- **Status:** approved (owner decision recorded in the group conversation,
  2026-09-11: close the silver `action-exceptions` rule)
- **Scope:** the `danalock_ble.check_firmware_updates` action
  (`update.py`), the lock platform action (`lock.py`), and the select
  platform action (`select.py`), plus tests, README, and the manifest
  version.
- **Supersedes:** spec 0010 R3's "empty, unknown, or foreign target is a
  quiet no-op" clause. The empty-target clause is kept; the
  unknown/foreign-target clause is replaced by the typed
  `ServiceValidationError` behavior below. Spec 0010 keeps status
  `implemented` for everything else.

## Summary

The silver quality-scale rule `action-exceptions` requires every action to
validate its input and raise `ServiceValidationError` when the caller made a
mistake, and `HomeAssistantError` when the action itself failed (network,
device, or an integration bug). Before this spec the integration was marked
partial: two platform actions silently returned on a missing key, the custom
firmware action silently skipped unknown or foreign targets, and a forced
firmware cycle swallowed probe/BLE failures.

This spec removes those quiet paths. Invalid targets raise
`ServiceValidationError`; execution failures raise `HomeAssistantError`.
Scheduled and background firmware cycles stay best-effort, and the core
`homeassistant.update_entity` action keeps its user-visible behavior.

## Motivation

- The compliance checklist marked `action-exceptions` as partial.
- `DanalockLockEntity._operate` and
  `DanalockSettingSelectEntity.async_select_option` warned and returned when
  no key was available: the user asked for an existing entity and got a
  silent failure.
- `update._async_handle` skipped unknown, foreign, and absent-component
  targets with only a debug log, so a mistyped target looked like success.
- `select._value_for` raised a bare `ValueError` for an unknown option,
  which the HA raising-exceptions guidance assigns to
  `ServiceValidationError`.
- A forced `danalock_ble.check_firmware_updates` reported success even when
  the cloud probe or the BLE read failed.

## Requirements

- R1 (MUST) `danalock_ble.check_firmware_updates`:
  - an empty target remains a quiet no-op (Home Assistant convention; the
    action is registered manually and core does not short-circuit it);
  - a non-empty target with no update component loaded, an unknown
    `entity_id`, or an entity that is not a
    `DanalockFirmwareUpdateEntity` raises `ServiceValidationError`.
- R2 (MUST) A failed forced firmware cycle (cloud probe or BLE read) raises
  `HomeAssistantError` chained from the underlying error. Scheduled,
  timer-driven, and `async_added_to_hass` cycles stay best-effort: they
  keep the debug log and the retry interval and never raise.
- R3 (MUST) `DanalockFirmwareUpdateEntity.async_update()` is the strict
  delegate for a forced cycle. The core `homeassistant.update_entity`
  action calls `async_update()` through
  `Entity.async_update_ha_state(True)`, which catches and logs the
  exception (`homeassistant/helpers/entity.py`), so its behavior for the
  caller does not change; the extra traceback in the core log is accepted
  and is not part of this integration's contract.
- R4 (MUST) `DanalockLockEntity.async_lock`/`async_unlock` without a control
  (no key available) raise `HomeAssistantError` instead of warning and
  returning. Device failures continue to propagate as
  `LockControlError`/`LockAddressUnknownError` (both `HomeAssistantError`).
- R5 (MUST) `DanalockSettingSelectEntity.async_select_option` without a
  control raises `HomeAssistantError`; `_value_for` raises
  `ServiceValidationError` for an unknown option; device write failures
  continue to propagate as `HomeAssistantError`. The core select platform
  already raises `ServiceValidationError` for an invalid option before the
  entity method runs, so `_value_for` is defense-in-depth.
- R6 (SHOULD) README §Actions documents that execution failures raise
  `HomeAssistantError` and an invalid target raises
  `ServiceValidationError`.
- R7 (MUST) Tests cover every mapping and the regressions (Test plan).
- R8 (MUST) Bump `manifest.json` `version` to `0.8.5` (runtime change). The
  release tag must equal the manifest version (`v0.8.5`).

## Design

### Forced firmware cycle: strict `async_update`, best-effort background

All forced-cycle entry points converge on `async_update()`, so a single
strict point suffices. One cycle now yields an immutable result:

```python
@dataclass(frozen=True)
class _CycleOutcome:
    next_delay: timedelta
    failed: bool = False
    error: Exception | None = None
```

`_async_read_lock()` no longer discards its exception: it returns the
caught exception (or `None` on success) after the debug log. `_async_refresh()`
returns a `_CycleOutcome`: a cloud probe failure (`DanalockCloudError` /
`httpx.HTTPError`) or a BLE read failure sets `failed=True` and carries the
error, while a `RuntimeError` shutdown race is not a user failure
(`failed=False`). `_async_cycle()` propagates the outcome; the timer and
`async_added_to_hass` ignore `failed`, so scheduling and cadence are
unchanged. `async_update()` reads the outcome and raises
`HomeAssistantError(...) from outcome.error` when `failed`.

### Action target validation

```python
entity_ids = await _extract_targets(hass, call)
if not entity_ids:
    return
component = hass.data.get(DATA_COMPONENT)
if component is None:
    raise ServiceValidationError("No update entities are loaded")
for entity_id in entity_ids:
    entity = component.get_entity(entity_id)
    if entity is None:
        raise ServiceValidationError(f"Unknown entity {entity_id}")
    if not isinstance(entity, DanalockFirmwareUpdateEntity):
        raise ServiceValidationError(
            f"{entity_id} is not a danalock firmware entity"
        )
    await entity.async_update()
```

Area/device targets that expand to non-danalock update entities are
validated the same way (strict, no distinction from explicit entity ids).

## API

- `update.py`: `_CycleOutcome`; `_async_read_lock() -> Exception | None`;
  `_async_refresh() -> _CycleOutcome`; `_async_cycle() -> _CycleOutcome`;
  `async_update()` raises `HomeAssistantError` on a failed forced cycle;
  `_async_handle` raises `ServiceValidationError`.
- `lock.py`: `_operate` raises `HomeAssistantError` when `_control is None`.
- `select.py`: `async_select_option` raises `HomeAssistantError` when
  `_control is None`; `_value_for` raises `ServiceValidationError`.
- No new services, fields, or translation keys.

## Test plan

Synthetic fixtures only (cloud mock handler, control doubles, synthetic
broadcasts):

- `tests/test_init.py`:
  - empty target is a no-op (no forced cycle, no warning-level log);
  - unknown `entity_id` raises `ServiceValidationError`;
  - a registered non-danalock update entity raises
    `ServiceValidationError`;
  - a non-empty target with the update component removed raises
    `ServiceValidationError`;
  - a forced cloud-probe failure raises `HomeAssistantError`;
  - a forced BLE-read failure raises `HomeAssistantError`.
- `tests/test_update.py`: the existing scheduled-failure tests
  (`test_ble_failure_keeps_values_and_stays_available`,
  `test_latest_probe_422_keeps_previous_values`,
  `test_failed_probe_is_retried_after_fifteen_minutes`) stay green —
  background cycles remain best-effort.
- `tests/test_lock.py`:
  - lock without a control raises `HomeAssistantError` (`no key`);
  - select without a control raises `HomeAssistantError` (`no key`);
  - an invalid option raises `ServiceValidationError` (direct
    `_value_for` and `async_select_option` calls).
- Regressions stay green: the forced-cycle action, the
  `homeassistant.update_entity` forced cycle, and a valid select write.

## Acceptance criteria

- Every action mapping above is implemented and covered by a test.
- `grep -rn "ServiceValidationError" custom_components/` yields at least
  one match; `_operate`, `async_select_option`, and `_async_handle` have no
  silent `return` for an invalid target.
- `pytest -m "not live"` is green; `hassfest` and `hacs/action` stay green.
- `manifest.json` version is `0.8.5`; the README §Actions section documents
  the error semantics.

## Out of scope

- `exception-translations` (gold): `translation_key` /
  `translation_domain`; English messages only here.
- Changing the select option validation in Home Assistant core, or entity
  availability semantics.
- New services or `services.yaml` fields, `PARALLEL_UPDATES`,
  `log-when-unavailable`, pytest-in-CI, or coverage gates.
- Changing the BLE failure cadence (a BLE failure keeps the 24-hour
  schedule; only the `failed` flag is added).

## Status

`approved` (2026-09-11). Implementation on
`feat/0022-action-exceptions`: spec, red tests, code, README, version bump,
local gate, then the group review gate. The release `v0.8.5` is tagged
after the squash-merge into `master`.
