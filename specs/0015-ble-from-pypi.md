# 0015 — consume pydanalock.ble from PyPI (de-vendor ble)

- **Status:** implemented
- **Scope:** HA integration dependency on `pydanalock-ble`; remove the
  vendored `pydanalock.ble` snapshot and the vendoring tooling; integration
  version 0.8.0

## Summary

Switch the BLE dependency from the vendored `pydanalock.ble` snapshot to the
published `pydanalock-ble` distribution on PyPI (`==0.9.0`), imported as the
top-level `pydanalock.ble` namespace. Both libraries are now consumed from
PyPI; the vendored tree and `scripts/vendor.*` are removed.

## Motivation

Home Assistant's `dependency-transparency` quality-scale rule requires a
dependency to be available on PyPI and published from a public CI. The BLE
library is now on PyPI (0.9.0); keeping a vendored copy duplicates the library
and risks drift. 0.9.0 exposes `FORMAT_VERSION_STATE` from the package root
(library spec 0012), so the integration no longer needs to import the private
`pydanalock.ble._advertising` module.

## Requirements

- R1 (MUST) `manifest.json` sets
  `"requirements": ["pydanalock-ble==0.9.0", "pydanalock-cloud==0.5.1"]`
  (alphabetical order) and `"version": "0.8.0"`; `"after_dependencies":
  ["bluetooth"]` is declared (hassfest `DEPENDENCIES` check); `codeowners`,
  `documentation`, `issue_tracker`, `domain`, `name`, `bluetooth`,
  `config_flow`, `integration_type`, `iot_class` stay unchanged, and the
  manifest key order (`domain`, `name`, then alphabetical) is preserved.
- R2 (MUST) Every BLE import uses the top-level `pydanalock.ble` package; no
  production or test import of `custom_components.danalock_ble.pydanalock`
  remains (the negative assertion in the package smoke test is exempt).
  `FORMAT_VERSION_STATE` and `DEVICE_FLAG_BITS` are imported from the package
  root, not from `pydanalock.ble._advertising`.
- R3 (MUST) `custom_components/danalock_ble/pydanalock/` (including
  `VENDORED.json`) is removed; `scripts/vendor.py` and `scripts/vendor.json`
  are removed, and the empty `scripts/` directory is dropped.
- R4 (MUST) `requirements_dev.txt` pins `pydanalock-ble==0.9.0` (and keeps
  `pydanalock-cloud==0.5.1`) so the local test environment imports the
  installed distribution.
- R5 (MUST) The gate is green: `.venv/bin/pytest -m "not live"`, and CI keeps
  `hassfest` + `hacs/action` green.
- R6 (MUST) Tests prove the installed `pydanalock.ble` is 0.9.0 and its public
  API (including `FORMAT_VERSION_STATE`) imports from the package root, and
  that the vendored package is gone.
- R7 (MUST) `DeviceKey` remains opaque bytes at the library boundary; no
  library logic is duplicated in the integration.
- R8 (MUST) `README.md` is not modified by this change (owned by the separate
  README/minor plan).

## Design

- The integration's BLE imports point at `pydanalock.ble`; Home Assistant
  installs the pinned requirement at setup (into `deps/` or the venv).
- The vendored tree is deleted; there is no PEP 420 namespace portion under
  `custom_components/danalock_ble/` any more. `pydanalock.ble` and
  `pydanalock.cloud` come from site-packages and share the `pydanalock`
  namespace.
- `scripts/vendor.py`/`scripts/vendor.json`/`VENDORED.json` have no remaining
  purpose once both packages are installed from PyPI and are removed with the
  snapshot.
- A shared, installed package means the integration no longer carries the BLE
  source; the component remains installable by HACS because Home Assistant
  resolves `requirements`.

## API

No user-facing API change. Component setup, config flow, entities, and
services are unchanged.

## Test plan

- Import surface: `pydanalock.ble` version 0.9.0 exported from the package
  root; `pydanalock.cloud` version 0.5.1.
- `importlib.util.find_spec("custom_components.danalock_ble.pydanalock")`
  returns `None`.
- Full integration suite against the installed BLE package.
- `git grep` boundary check for
  `custom_components.danalock_ble.pydanalock` and `scripts/vendor`.
- CI: `hassfest` and `hacs/action` (integration) on the branch.

## Acceptance criteria

- R1–R8 hold.
- A clean checkout with `.venv/bin/pip install -r requirements_dev.txt` passes
  `pytest -m "not live"` without the vendored ble directory.
- Home Assistant setup installs `pydanalock-ble==0.9.0` from PyPI.

## Out of scope

- Rename-provenance scrub and the public push/release of this repository
  (session H2/P2).
- `README.md` changes (separate README/minor plan).
- Any change to the libraries or their APIs.

## Status

`implemented` (de-vendoring merged; integration version 0.8.0)
