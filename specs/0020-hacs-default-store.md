# 0020 — HACS default store submission and Bluetooth/CONFIG_SCHEMA fixes

- **Status:** approved (owner decision recorded in the group conversation,
  2026-09-11: "Default store + fix P0")
- **Scope:** `custom_components/danalock_ble/manifest.json` and `__init__.py`
  plus the `hacs`-visible metadata; external submission to `hacs/default` and a
  GitHub release. No user-facing behavior change: an unused discovery matcher
  is removed, the Bluetooth dependency becomes explicit, and `CONFIG_SCHEMA` is
  declared.

## Summary

The repository is already installable through HACS as a custom repository.
This spec covers the remaining step: inclusion in the HACS default store
(`hacs/default`), so users find the integration without adding a custom
repository.

The compliance audit of 2026-09-11 found two P0 issues that must be closed
before the submission:

1. `manifest.json` declares a Bluetooth discovery matcher
   (`{"manufacturer_id": 456, "connectable": false}`) without a matching
   `async_step_bluetooth` in `config_flow.py`. Home Assistant starts a
   discovery flow for every matching advertisement that fails with
   `UnknownStep` (log noise). Advertisement monitoring is done independently
   through `async_register_callback` (`broadcast.py`), so the matcher is not
   needed.
2. `__init__.py` implements `async_setup` but does not define `CONFIG_SCHEMA`,
   which hassfest reports as a warning.

## Motivation

- HACS's inclusion criteria require a green HACS Action and a green hassfest
  before a default-store PR, and at least one full GitHub release after the
  checks pass.
- Leaving the discovery matcher in place keeps a real runtime defect (a failing
  background discovery flow per advertisement) in a repository that HACS
  reviewers and users will exercise.
- `after_dependencies: ["bluetooth"]` orders setup when Bluetooth is
  configured. It is kept deliberately: the config entry represents a cloud
  account, and setup must succeed on a host without a Bluetooth adapter so the
  account and its devices stay visible; BLE features degrade with a warning
  (`broadcast.py`). A hard `dependencies: ["bluetooth"]` would make entry setup
  fail entirely when the Bluetooth integration cannot be set up (also observed
  in the socket-restricted test harness). The decision is to keep graceful
  degradation.

## Requirements

- R1 (MUST) Remove the `bluetooth` discovery matcher from
  `custom_components/danalock_ble/manifest.json`. Discovery by Bluetooth
  advertisement is not part of the design (a config entry represents a cloud
  account, not a device), so no `async_step_bluetooth` is added.
- R2 (MUST) Keep `after_dependencies: ["bluetooth"]` in `manifest.json` and do
  not introduce a hard `dependencies: ["bluetooth"]`: the cloud-account entry
  must still load on a host without a Bluetooth adapter (graceful degradation;
  the hard form fails entry setup when Bluetooth cannot be set up).
- R3 (MUST) Define a module-level
  `CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)` in
  `custom_components/danalock_ble/__init__.py`; import
  `homeassistant.helpers.config_validation as cv`.
- R4 (MUST) Bump `manifest.json` `version` to `0.8.2`. The release tag must
  equal the manifest version (`v0.8.2`).
- R5 (MUST) The local gate (`pytest -m "not live"`) is green, and CI
  `hassfest` + `hacs/action` (category: integration) are green with no
  `with.ignore` entries and no remaining content warnings.
- R6 (MUST) Submit the repository to the HACS default store: add
  `"buggy-shep/danalock-ble-ha-integration"` to the `integration` list in
  `hacs/default`, in alphabetical position, as a pull request opened from the
  owner's (`buggy-shep`) account and filled according to the repository's pull
  request template. The default-store PR is opened only after R4/R5 hold.
- R7 (SHOULD) After the `hacs/default` PR is merged, update the README HACS
  badge from "Custom" to the default-store wording. This is a follow-up and is
  not part of this change.
- R8 (MUST) No secrets, device or account data. Wording stays method-neutral
  and references only public sources (repository AGENTS §11).
- R9 (SHOULD) Record the submission status in the group meta-repository
  `AGENTS.md` publication section.

## Design

### manifest.json

```json
{
  "domain": "danalock_ble",
  "name": "Danalock Bluetooth",
  "after_dependencies": ["bluetooth"],
  ...
}
```

The `bluetooth` matcher key is removed entirely; `after_dependencies` stays
as-is (R2). No other manifest keys change.

### CONFIG_SCHEMA

`config_entry_only_config_schema(DOMAIN)` is Home Assistant's standard schema
for an integration that defines `async_setup`/`setup` but only supports config
entry setup. It removes the hassfest warning without enabling YAML setup.

### HACS default submission

- Entry: `"buggy-shep/danalock-ble-ha-integration"` in the `integration` file
  of `hacs/default`, sorted alphabetically. It belongs between
  `"buggedcom/Enion-hass"` and `"bullitt186/ha-omada-open-api"`.
- The PR body follows `.github/pull_request_template.md` of `hacs/default`.
- Reviewer automated checks (see HACS `publish/include.md`): brands directory
  present (`brand/icon.png`), valid manifest, HACS validation, `hacs.json`
  `name`, active repository, at least one release, owner submitter, valid JSON,
  sorted list.

## API

None. Manifest/schema metadata and an external repository-list entry only.

## Test plan

- TDD: `tests/test_manifest.py` is written first and fails on the pre-change
  manifest/module.
  - `bluetooth` is absent from `manifest.json`.
  - `after_dependencies` contains `"bluetooth"` and `bluetooth` is not a hard
    dependency.
  - `custom_components.danalock_ble.CONFIG_SCHEMA` is defined.
- Regression: the full `pytest -m "not live"` suite stays green, including
  `tests/test_init.py` setup/unload tests that exercise `async_setup`.
- CI: `hassfest` and `hacs/action` are green on the release commit with no
  warnings and no ignores.
- External: the `hacs/default` PR passes the automated checks.

## Acceptance criteria

- `manifest.json` has no `bluetooth` matcher, keeps
  `after_dependencies: ["bluetooth"]`, and has version `0.8.2`.
- `CONFIG_SCHEMA` exists and the hassfest content warning is gone.
- `pytest -m "not live"` is green.
- GitHub Release `v0.8.2` exists on `master` after CI is green.
- A PR against `hacs/default` adds the repository to `integration` in sorted
  position, opened from `buggy-shep`.
- Group `AGENTS.md` records the default-store submission.

## Out of scope

- The `home-assistant/brands` CDN submission (spec 0019) and the HACS list
  icon.
- Changing `integration_type` from `device` to `hub`, `loggers`, or other
  non-blocking audit items (audit §4).
- Silver/gold quality-scale work (`PARALLEL_UPDATES`, `ServiceValidationError`,
  diagnostics, icons, translated exceptions).
- Any change to library pins or vendoring.

## Status

`approved` (2026-09-11). Implementation on `feat/0020-hacs-default-store`:
tests, P0 fixes, version bump, local gate, skeptic review, squash-merge, release
`v0.8.2`, then the `hacs/default` pull request.
