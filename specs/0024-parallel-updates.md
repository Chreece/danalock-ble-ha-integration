# 0024 — Silver `parallel-updates`: explicit per-platform limits

- **Status:** implemented
- **Scope:** module-level `PARALLEL_UPDATES` in the five entity platform modules,
  a new test module, and the manifest version. No setup/unload, entity,
  translation, or dependency changes.

## Summary

The Home Assistant quality-scale rule `parallel-updates` requires an explicit
per-platform limit for request parallelism. The integration currently defines
no `PARALLEL_UPDATES` constant anywhere, so Home Assistant falls back to its
legacy default and the integration does not state how concurrent entity
updates and action calls against a lock and a cloud account are bounded.

## Motivation

- The compliance checklist marks `parallel-updates` as not satisfied.
- Action-bearing platforms (`lock`, `select`) and the self-scheduled
  `update` entity perform outbound cloud/BLE I/O that must not overlap.
- Home Assistant reads `PARALLEL_UPDATES` from the platform module
  (`EntityPlatform`), so the constant must be defined per module, not on a
  shared base class.

## Requirements

- R1 (MUST) Set a module-level `PARALLEL_UPDATES` constant in each platform
  module:
  - `binary_sensor.py` = 0
  - `sensor.py` = 0
  - `lock.py` = 1
  - `select.py` = 1
  - `update.py` = 1
- R2 (MUST) The constant is defined in the platform module itself (present in
  `module.__dict__`); a value on the shared `DanalockEntity` base is not
  sufficient and must not be the only definition.
- R3 (MUST) Each constant carries a one-line comment stating why the platform
  uses `0` (centralized inbound push, no outbound action) or `1` (outbound
  lock/select/update I/O against one device/account).
- R4 (MUST) No behavioral change: entity IDs, unique IDs, setup/unload,
  availability, translations, services, and dependencies are untouched.
- R5 (MUST) TDD: a new `tests/test_parallel_updates.py` asserts the value for
  every platform module and that the constant is module-level; the tests fail
  before the constants exist.
- R6 (MUST) Bump `manifest.json` `version` to `0.8.4` (patch; runtime
  content); the release tag must equal the manifest version (`v0.8.4`). This
  spec is implemented only after 0021/v0.8.3 is merged, so the baseline is
  `0.8.3`.
- R7 (MUST) The local gate (`pytest -m "not live"`) is green; CI `hassfest`
  and `hacs/action` (category: integration) stay green with no ignores.
- R8 (SHOULD) A structural test asserts, per platform module, that the
  module-level `PARALLEL_UPDATES` constant is present and equals the value in R1
  (`0`/`1`). The test reads `module.__dict__`; it does not depend on Home
  Assistant `EntityPlatform`/`hass.data[DATA_ENTITY_PLATFORM]` internals.

## Design

Each platform module gets the constant next to its existing module-level
constants:

```python
# Read-only state pushed by the shared broadcast monitor; no outbound action.
PARALLEL_UPDATES = 0
```

```python
# Outbound BLE action against one device; serialize one session at a time.
PARALLEL_UPDATES = 1
```

Rationale per platform:
- `binary_sensor`, `sensor`: read-only, values are pushed by the shared
  broadcast monitor, no `async_update`, no actions -> 0.
- `lock`, `select`: actions run `connect -> command -> disconnect` on the
  shared per-device control -> 1.
- `update`: the entity is read-only but self-schedules and performs a cloud
  probe plus a BLE read in `async_update`; its data is not centralized by a
  coordinator -> 1.

Cross-platform note: the constant is a per-platform semaphore. Concurrency
between `lock` and `select` for the same device is bounded by the shared
`DanalockControl` and its facade command lock, not by the constant.

## API

None. No new services, entities, options, or translations.

## Test plan

- Add `tests/test_parallel_updates.py`:
  - `test_platform_module_sets_parallel_updates` (parametrized over the five
    modules and expected values) reads `module.__dict__["PARALLEL_UPDATES"]`.
  - `test_parallel_updates_is_defined_per_module_not_inherited` asserts the
    constant is absent from `entity.DanalockEntity` and present in every
    platform module.
  - `test_parallel_updates_values_are_structural` (R8) reads each platform
    module's `__dict__` and asserts the expected `0`/`1`; no HA
    `EntityPlatform` internals.
- Regression: the full `pytest -m "not live"` suite stays green.

## Acceptance criteria

- All five platform modules expose the specified module-level
  `PARALLEL_UPDATES`.
- The compliance checklist row `parallel-updates` moves to satisfied.
- `manifest.json` is `0.8.4`; tag `v0.8.4` matches.
- `pytest -m "not live"` and the local CI workflow are green.
- No behavior, entity, or translation change.

## Out of scope

- `action-exceptions`, `log-when-unavailable`, `test-coverage`, and any other
  quality-scale rule.
- `inject-websession`, `strict-typing`, diagnostics, icons.
- Changing the shared `DanalockEntity` base or introducing a coordinator.
- Library pins, storage, and setup/unload logic.

## Status

`approved` (2026-09-11). Implementation on `feat/0024-parallel-updates` after
v0.8.3 is merged; release `v0.8.4` after the squash-merge into `master`.
