# 0014 — consume pydanalock.cloud from PyPI (de-vendor cloud)

- **Status:** implemented
- **Scope:** HA integration dependency on `pydanalock-cloud`; remove the
  vendored `pydanalock.cloud` snapshot; integration version 0.8.0

## Summary

Switch the cloud dependency from the vendored `pydanalock.cloud` snapshot to
the published `pydanalock-cloud` distribution on PyPI (`==0.5.1`), imported as
the top-level `pydanalock.cloud` namespace. The vendored `pydanalock.ble`
snapshot stays in place until `pydanalock-ble` is published.

## Motivation

Home Assistant's `dependency-transparency` quality-scale rule requires a
dependency to be available on PyPI and published from a public CI. The cloud
library is now on PyPI (0.5.1); keeping a second, vendored copy duplicates the
library and risks drift. `pydanalock-cloud` 0.5.1 requires `httpx>=0.27`, which
resolves against Home Assistant's `httpx==0.27.2`. `pydanalock-ble` has no PyPI
release yet, so its vendoring stays.

## Requirements

- R1 (MUST) `manifest.json` sets
  `"requirements": ["pydanalock-cloud==0.5.1"]`, `"version": "0.8.0"`,
  `"codeowners": ["@buggy-shep"]`, and `documentation`/`issue_tracker`
  pointing at `https://github.com/buggy-shep/danalock-ble-ha-integration`.
  `domain`, `name`, `bluetooth`, `config_flow`, `integration_type`, `iot_class`
  are unchanged.
- R2 (MUST) Every cloud import uses the top-level `pydanalock.cloud` package
  (`from pydanalock.cloud import ...`, `pydanalock.cloud.models`); no
  `custom_components.danalock_ble.pydanalock.cloud` reference remains in code,
  tests, or scripts.
- R3 (MUST) `custom_components/danalock_ble/pydanalock/cloud/` is removed;
  `custom_components/danalock_ble/pydanalock/ble/` stays; the `cloud` entry is
  removed from `pydanalock/VENDORED.json`.
- R4 (MUST) `scripts/vendor.py` removes the vendored directory of a disabled
  pin and excludes it from provenance; `scripts/vendor.json` marks
  `"cloud": {"enabled": false}` (its pin is kept as history).
- R5 (MUST) `requirements_dev.txt` pins `pydanalock-cloud==0.5.1` so the local
  test environment imports the installed distribution.
- R6 (MUST) The gate is green: `.venv/bin/pytest -m "not live"`, and CI keeps
  `hassfest` + `hacs/action` green.
- R7 (MUST) Tests prove the installed `pydanalock.cloud` is 0.5.1 and the
  vendored `pydanalock.ble` is still 0.9.0; the public API surface is
  unchanged.
- R8 (MUST) `DeviceKey` remains opaque bytes at the library boundary; no
  library logic is duplicated in the integration.

## Design

- The integration's `error`/`models`/`storage` imports point at
  `pydanalock.cloud`; Home Assistant installs the pinned requirement at setup
  (into `deps/` or the venv). `httpx` already ships with Home Assistant core.
- The vendored tree keeps only `pydanalock/ble/`; `pydanalock/` remains a
  PEP 420 namespace portion without `__init__.py`. `pydanalock.cloud`
  (site-packages) and `custom_components.danalock_ble.pydanalock.ble` have
  distinct import roots, so there is no namespace collision.
- `scripts/vendor.py` gains a disabled-pin cleanup branch so a future
  `vendored` run cannot resurrect `cloud` from the checkout.
- A shared, installed package means the integration no longer carries the
  cloud source; the component remains installable by HACS because Home
  Assistant resolves `requirements`.

## API

No user-facing API change. Component setup, config flow, entities, and
services are unchanged.

## Test plan

- Import surface: `pydanalock.cloud` version 0.5.1; vendored
  `pydanalock.ble` version 0.9.0.
- Full integration suite against the installed cloud package with the existing
  `httpx.MockTransport` fixtures.
- `git grep` boundary check for
  `custom_components.danalock_ble.pydanalock.cloud`.
- CI: `hassfest` and `hacs/action` (integration) on the branch.

## Acceptance criteria

- R1–R8 hold.
- A clean checkout with `.venv/bin/pip install -r requirements_dev.txt` passes
  `pytest -m "not live"` without the vendored cloud directory.
- Home Assistant setup installs `pydanalock-cloud==0.5.1` from PyPI.

## Out of scope

- De-vendoring `pydanalock.ble` (separate plan, after `pydanalock-ble` is on
  PyPI).
- Rename-provenance scrub and the public push/release of this repository
  (the publication plan, sessions H2/P2).
- Any change to the library or its API.

## Status

`implemented` (de-vendoring merged; integration version 0.8.0). The statements
that keep the vendored `pydanalock.ble` snapshot in place describe the 0014
state and are superseded by spec 0015, which de-vendors BLE as well.
