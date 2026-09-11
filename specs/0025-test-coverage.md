# 0025 — Silver `test-coverage`: CI gate and above 95% per-module coverage

- **Status:** approved (owner decision recorded in the group conversation,
  2026-09-11: enforce the Silver rule strictly, add coverage to CI, close the
  `lock.py` gap; no manifest bump and no release)
- **Scope:** CI (`.github/workflows/validate.yml`), test tooling
  (`.coveragerc`, `requirements_dev.txt`, `scripts/check_module_coverage.py`),
  tests under `tests/`, `.gitignore`, and a README testing note. No runtime
  behavior change and no `manifest.json` version bump.

## Summary

The Silver rule `test-coverage` requires above 95% test coverage for every
integration module, with no exceptions. The suite already exists (239 passed,
2 deselected) and reports 98% TOTAL under branch coverage with the standard
Home Assistant `exclude_lines`, but CI never runs it and no coverage floor is
enforced, so the rule is only partially satisfied. This spec adds coverage
configuration, pins the coverage tooling, runs the suite in CI, and enforces a
strict per-module floor of more than 95%.

Measured baseline (`branch=True`, `exclude_lines` for `if TYPE_CHECKING:`,
`raise NotImplementedError`, `if sys.version_info`, `pragma: no cover`): TOTAL
98% (1130 statements, 16 missed; 212 branches, 14 partial). Twelve of the
thirteen modules are already above 95%. `lock.py` is the only module at exactly
95% (68 statements, 2 missed; 10 branches, 2 partial), which is not "above
95%". The other uncovered lines are cheap, testable gaps in
`binary_sensor.py`, `control.py`, `keymanager.py`, `select.py`, and `update.py`.

## Motivation

- Coverage regresses silently without a CI job; the rule exists to prevent
  regressions and to let new developers change the code safely.
- A single global threshold cannot detect one under-covered module hidden by a
  high total; enforcement must be per module. The one current offender,
  `lock.py`, sits exactly on the boundary and would pass a ">= 95%" gate.
- The suite already exists and is fast (~17 s); wiring it into CI and pinning
  the tooling makes the rule reproducible.
- The `if TYPE_CHECKING:` / `NotImplementedError` / `sys.version_info` idioms
  are never executable in the runtime path; without `exclude_lines` they
  artificially depress module percentages and would force meaningless tests.

## Requirements

- R1 (MUST) Add a committed `.coveragerc` with `branch = True`,
  `source = custom_components/danalock_ble`, and `exclude_lines` for
  `pragma: no cover`, `if TYPE_CHECKING:`, `raise NotImplementedError`, and
  `if sys.version_info`.
- R2 (MUST) Pin `pytest-cov==6.0.0` and `coverage==7.6.8` in
  `requirements_dev.txt` (the versions already used by the local venv).
- R3 (MUST) Add a `tests` job to `.github/workflows/validate.yml` that checks
  out, installs Python 3.12 plus `requirements_dev.txt`, runs
  `pytest -m "not live"` with coverage, and fails on a global
  `--cov-fail-under=95`. The existing `hassfest` and `hacs` jobs stay
  unchanged. The pip-cache step is gated with `if: ${{ !env.ACT }}` so the
  local `act` runner stays green.
- R4 (MUST) Enforce the rule per module: after the test run, `coverage json`
  plus `scripts/check_module_coverage.py --min 95` reads the coverage report
  and exits non-zero if any integration module has
  `percent_covered <= 95.0`. The script is stdlib-only and prints a table.
- R5 (MUST) Add the tests needed so every integration module is strictly above
  95%: `lock.py` (required), plus the cheap gaps in `binary_sensor.py`,
  `control.py`, `keymanager.py`, `select.py`, and `update.py`. Branch coverage
  stays enabled.
- R6 (MUST) `live` tests remain deselected (`-m "not live"`) and never run in
  CI.
- R7 (MUST) `make ci-ha` (which runs the whole `validate.yml`) stays green,
  including the new `tests` job. The workspace `Makefile` is not edited.
- R8 (SHOULD) Add a short README testing note describing the local coverage
  command and the per-module floor; do not add an external coverage service or
  a dynamic badge.
- R9 (MUST) Treat this change as non-runtime: no `manifest.json` version bump
  and no release, unless the work turns out to require runtime source changes.

## Design

### Coverage configuration (`.coveragerc`)

```ini
[run]
branch = True
source = custom_components/danalock_ble

[report]
exclude_lines =
    pragma: no cover
    if TYPE_CHECKING:
    raise NotImplementedError
    if sys.version_info
```

`pytest.ini` is untouched. `[report] fail_under` is intentionally not set: the
global threshold is passed as `--cov-fail-under=95` and per-module enforcement
lives in the script, avoiding two sources of truth for the same number.

### Per-module enforcement (`scripts/check_module_coverage.py`)

A small stdlib-only script (`argparse`, `json`, `sys`) that reads the JSON
report produced by `coverage json`, prints one `module percent` line per file,
and exits `1` listing every module with `percent_covered <= 95.0`. Defaults:
`coverage.json`, `--min 95`. A per-module check is required because a global
`--cov-fail-under` is computed over TOTAL and hides a single weak module.

### CI job

```yaml
  tests:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: pip
          cache-dependency-path: requirements_dev.txt
        if: ${{ !env.ACT }}
      - uses: actions/setup-python@v5
        if: ${{ env.ACT }}
        with:
          python-version: "3.12"
      - name: Install test dependencies
        run: |
          python -m pip install --upgrade pip
          pip install -r requirements_dev.txt
      - name: Run tests with coverage
        run: |
          pytest -m "not live" \
            --cov=custom_components/danalock_ble \
            --cov-report=term-missing \
            --cov-fail-under=95
      - name: Enforce per-module coverage above 95%
        run: |
          coverage json -o coverage.json
          python scripts/check_module_coverage.py coverage.json --min 95
```

Python 3.12 is pinned because `pytest-homeassistant-custom-component==0.13.205`
carries `homeassistant==2025.1.4` and both require Python >= 3.12; the
measurements here were taken on 3.12.3.

### Test additions (TDD)

Eleven focused unit tests close the reachable gaps:

| Test | Module lines | Scenario |
|---|---|---|
| `tests/test_lock.py::test_lock_entity_without_report_reports_unknown` | `lock.py:55,62` | lock entity without a decoded report returns `None` from `is_locked` / `extra_state_attributes` |
| `tests/test_sensors.py::test_status_binary_sensor_without_report_is_unknown` | `binary_sensor.py:65` | status binary sensor without a report returns `None` |
| `tests/test_control.py::test_retry_nothing_changed_is_success` | `control.py:134` | retry after `TOKEN_VALIDITY` reports `NOTHING_CHANGED` → no error |
| `tests/test_control.py::test_retry_transport_error_is_wrapped` | `control.py:136-142` | retry transport error → `LockControlError` |
| `tests/test_control.py::test_device_information_retry_transport_error_is_wrapped` | `control.py:283-289` | `device_information` retry transport error → `LockControlError` |
| `tests/test_keymanager.py::test_run_cycle_tolerates_refresh_failure` | `keymanager.py:204-205` | a failing `refresh_all` is logged, the cycle does not re-arm |
| `tests/test_lock.py::test_select_value_for_rejects_unknown_option` | `select.py:145` | unknown option raises |
| `tests/test_update.py::test_cycle_without_hass_returns_immediately` | `update.py:219` | cycle without `hass` returns before work |
| `tests/test_update.py::test_cycle_skips_write_when_removed_mid_refresh` | `update.py:228` | entity removed mid-refresh writes no state and does not re-arm |
| `tests/test_update.py::test_refresh_retries_when_cloud_client_closed` | `update.py:241-245` | `RuntimeError` (shutdown race) returns the retry interval |
| (bonus, only if needed) branch-only gaps | `__init__.py`, `broadcast.py`, `entity.py`, `sensor.py` | modules already >95% |

### `_value_for` cross-spec note (specs 0022 / 0025 ordering)

Spec 0022 (`action-exceptions`) merges before 0025 and changes
`DanalockSettingSelectEntity._value_for` to raise `ServiceValidationError`
instead of a bare `ValueError`. Before the wave rebase the test accepted
`pytest.raises((ValueError, ServiceValidationError))` to stay green while the
branch was still cut from the pre-0022 `master`.

**Adjustment applied (Phase 2 rebase):** 0025 is now rebased onto post-0022
`master`, where `_value_for` raises `ServiceValidationError`. The test
expectation is tightened to `ServiceValidationError` alone and the docstring
records the final contract.

### Firmware cycle cross-spec note (spec 0023)

Spec 0023 (`log-when-unavailable`, merged before the rebase) changed
`DanalockFirmwareUpdateEntity._async_cycle` / `_async_refresh` to return the
internal `_CycleOutcome` (delay plus optional failure) instead of a bare
`timedelta`. The two 0025 tests that stub or read the refresh return value are
adapted accordingly: the mid-refresh stub returns `_CycleOutcome(...)` and the
closed-client test asserts `outcome.next_delay` / `outcome.failed`. Coverage
intent (the removed-in-flight early return and the shutdown-race `RuntimeError`
branch) is unchanged.

### CI flake stabilization (broadcast timer test)

The new coverage CI job exposed a pre-existing timing flake in
`tests/test_broadcast.py::test_refresh_timer_fires_and_unload_cancels_it`
(2 of 40 local runs under coverage before the fix). Root cause:
`async_track_time_interval` dispatches a non-`@callback` sync action
(`DanalockBroadcastMonitor._refresh_states`) through the executor as a
*background* job, and `hass.async_block_till_done()` does not wait for
background tasks by default; the assertion therefore raced the executor
thread. The test now awaits
`hass.async_block_till_done(wait_background_tasks=True)` after each
`async_fire_time_changed`, so the fired poll is guaranteed to have run before
the state is read. The dedup, interval-timer and unload-cancel assertions are
unchanged (still asserting the real manager history). The sibling
`test_background_refresh_runs_periodically` is not affected: the key manager
uses `async_track_point_in_time`, which dispatches its cycle as a normal task
that `async_block_till_done()` already awaits.

## API

None. Test and CI tooling only.

## Test plan

- Add the eleven tests above; confirm each previously-uncovered line now
  executes and no `live` test is added.
- Run the local gate:
  `pytest -m "not live" --cov=custom_components/danalock_ble --cov-branch --cov-report=term-missing`
  and confirm every module is strictly above 95% (TOTAL >= 98%).
- Run `coverage json` plus `scripts/check_module_coverage.py coverage.json
  --min 95` and confirm exit 0; run the same script on a synthetic JSON with a
  95.0% module and confirm exit 1.
- Re-run after the wave rebase (post-0022/0024) and re-measure.

## Acceptance criteria

- Every integration module is strictly above 95% under branch coverage.
- GitHub CI runs the `tests` job green alongside `hassfest` and `hacs`; the
  pip-cache step is `act`-gated.
- `--cov-fail-under=95` and the per-module checker both gate the job;
  `make ci-ha` runs the whole workflow green.
- No runtime behavior, library pin, or manifest version change; no release.

## Out of scope

- `strict-typing`/mypy, ruff, and any other Platinum work.
- Dynamic/external coverage badges or uploading coverage to a service.
- New runtime features; changes to `hassfest`/`hacs` configuration.
- Removing or rewriting the existing suite; converting `live` tests to run in
  CI.

## Status

`approved` (2026-09-11). Implementation on `feat/0025-test-coverage`: tests,
`.coveragerc`, `scripts/check_module_coverage.py`, CI `tests` job, README note,
local gate, then the group review gate. No manifest bump, no release.
