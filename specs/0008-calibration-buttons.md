# 0008 — Calibration point buttons

- **Status:** approved
- **Scope:** the `button` platform with two per-device calibration buttons, the
  `DanalockControl.calibrate` command, translations, the library pin, and the
  manifest version

## Summary

The lock exposes a manual calibration command that records the current
mechanical position as an endpoint. This spec adds two config-category
buttons per device — one for the unlocked endpoint and one for the locked
endpoint — so a user can re-run the manual calibration from Home Assistant.
The command is issued over BLE through the shared per-device control; the
resulting calibration state arrives through the broadcast monitor, not from
the button.

## Motivation

- The lock's motor travel can drift after a mechanical change and needs the
  same manual endpoint calibration the vendor application offers.
- The integration already owns a per-device BLE control with lazy key
  refresh and one-shot token retry (spec 0007); calibration is one more
  command on that existing path, not a new manager.
- The broadcast monitor already decodes the calibration state; the button
  only sends the command, so no state is duplicated on the entity.

## Requirements

- R1 (MUST) Dependency: `manifest.json` requires `pydanalock-ble==0.10.0`
  (the version that adds the calibration command) and `requirements_dev.txt`
  pins the same version. No library is vendored.
- R2 (MUST) `DanalockControl.calibrate(point)` runs
  `self._lock.set_calibration_point(point)` through
  `_run_command`/`_attempt_call`, keeping the existing discipline: lazy
  expired-key refresh before the command, one forced refresh and one retry on
  `TOKEN_VALIDITY`, connect → command → disconnect in `finally`, and a
  `LockControlError` (message from the status mapping) for a device
  rejection or transport failure. An unknown address still raises
  `LockAddressUnknownError`.
- R3 (MUST) New platform module `button.py` with two entities per device:
  `set_point_open` (unlocked endpoint) and `set_point_closed` (locked
  endpoint). `unique_id` = `<serial>_<suffix>`; the suggested entity id is
  `button.danalock_ble_<serial>_<suffix>`; `entity_category` = config;
  enabled by default (parity with the application); `PARALLEL_UPDATES = 1`
  with the standard one-line rationale.
- R4 (MUST) `button.py` defines `available` as
  `control is not None and state.address is not None` — the command does not
  depend on advertisement freshness, so the base class's freshness-based
  availability must not be inherited. Without a control or a broadcast
  address the button is unavailable, not an error.
- R5 (MUST) Pressing a button calls `control.calibrate(point)` with the
  mapped point; the entity does not change its own state and writes no
  optimistic lock state. A `LockControlError` or transport failure surfaces
  as `HomeAssistantError` with the status message. With a control but no key
  (control is `None`) the press raises `HomeAssistantError` naming the
  missing key, mirroring the lock/select entities (spec 0022). That guard is
  defensive: `available` is already false without a control, so Home
  Assistant's service layer filters the entity before `async_press` runs; the
  branch is covered by a direct entity call.
- R6 (MUST) `Platform.BUTTON` is added to `PLATFORMS` in `__init__.py`;
  runtime wiring (`controls`, `monitor`) already exists and is unchanged.
- R7 (MUST) Translations `entity.button.set_point_open` and
  `entity.button.set_point_closed` exist in `translations/en.json` and
  `translations/ru.json`, with no `[%key:...]` placeholders and no
  `strings.json`.
- R8 (MUST) Tests (TDD, failing before the implementation): the library
  facade is patched at its use site
  (`custom_components.danalock_ble.control.DanalockLock`); a press sends
  exactly one `set_calibration_point` call with the correct argument; a
  device rejection becomes `HomeAssistantError`; a missing address makes the
  buttons unavailable; a device without a control raises a clear error;
  concurrent presses do not interleave (the underlying facade command lock
  serializes; the HA test pins the await/double-press path); unload during an
  in-flight command completes cleanly; the entity registry shape and
  translations are asserted. `tests/test_parallel_updates.py` gains
  `button = 1`, and the entity-suffix expectations in `tests/test_sensors.py`
  include the two button suffixes. Per-module coverage of `button.py` is
  above 95%.
- R9 (MUST) `manifest.json` `version` moves to `0.9.0` (minor; new platform).
- R10 (MUST) No change to setup/unload semantics, the lock control path, the
  select entities, or the config flow.

## Design

- `button.py` mirrors `select.py`/`lock.py`: `async_setup_entry` iterates the
  monitor's device states and builds the two buttons from a module-level
  suffix→point map.
- `DanalockCalibrationButton` extends `DanalockEntity, ButtonEntity`, sets
  `_entity_id_domain = Platform.BUTTON`, and overrides `available`. The
  press body is one `await self._control.calibrate(self._point)`.
- The calibration result is not stored on the entity: the monitor's
  decoded `point_calibrated` flag is the single source of truth.
- Concurrency is bounded by the library facade's per-lock command lock
  (one in-flight command per lock); the platform constant is `1`.

## API

```python
class DanalockControl:
    async def calibrate(self, point: int) -> None: ...


CALIBRATION_POINTS: dict[str, int]  # "set_point_open" -> 0, "set_point_closed" -> 1


class DanalockCalibrationButton(DanalockEntity, ButtonEntity): ...
```

## Test plan

- `tests/test_control.py`: `calibrate` reaches the facade and disconnects;
  the point argument is passed through; a `NOT_PERMITTED` rejection maps to a
  permission message; transport errors and a second token rejection map to
  `LockControlError`; an expired key is refreshed before the command; a
  concurrent double command does not interleave.
- New `tests/test_button.py`: registry shape (entity ids, unique ids, config
  category, enabled by default); a press sends one command with the mapped
  point and leaves the entity state and the lock state untouched; a rejected
  command raises `HomeAssistantError`; without an address the buttons are
  unavailable; without a control the press raises `HomeAssistantError`; two
  concurrent presses serialize; unloading during an in-flight command does
  not hang.
- `tests/test_parallel_updates.py`: `button = 1`.
- `tests/test_sensors.py`: the button suffixes join the expected
  entity-id/unique-id sets.
- `tests/test_translations.py` already enforces the mirrored key tree, so the
  new `entity.button` keys are covered by both language files.

## Acceptance criteria

- All tests green; per-module coverage above 95%; `hassfest` and
  `hacs/action` green; skeptic without `BLOCKING`.
- `manifest.json` is `0.9.0`; tag `v0.9.0` matches; the library pin is
  `pydanalock-ble==0.10.0`.
- The two buttons appear per device with correct ids and translations.

## Out of scope

- Automatic calibration, calibration begin/complete, threshold/latch points,
  and a calibration service;
- changing lock/unlock, the select entities, the update entity, or the
  options/config flow;
- exposing the point-calibrated binary sensor (already exists, spec 0004).

## Status

`approved` (2026-09-12). Implementation on `feat/0008-calibration-buttons`
after `pydanalock-ble==0.10.0` is published (done 2026-09-12). Live button
verification is subject to explicit user confirmation.
