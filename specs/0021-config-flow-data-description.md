# 0021 — Bronze `config-flow`: field descriptions and credential selectors

- **Status:** approved (owner decision recorded in the group conversation,
  2026-09-11: close the bronze `config-flow` subcheck fully)
- **Scope:** `custom_components/danalock_ble/config_flow.py` and
  `custom_components/danalock_ble/translations/{en,ru}.json`, plus the
  `manifest.json` version. User-facing config flow only; no entry, setup, or
  storage semantics change.

## Summary

The home-assistant integration quality scale tracks the bronze rule
`config-flow` through two subchecks (`tiers.json`):

- "Uses `data_description` to give context to fields"
- "Uses `ConfigEntry.data` and `ConfigEntry.options` correctly"

The second subcheck was already satisfied. The first was only partially
satisfied: `data_description` existed for the options flow, while the login
steps (`user`, `reauth_confirm`, `reconfigure`) carried only a step-level
`description` (the unofficial-integration disclaimer) and the field labels in
`data`. In addition, the credential fields used bare `str` schemas; the rule's
reasoning asks for "the right selectors at the right place", so username and
password move to `TextSelector` (`email` / `password`).

This spec closes both gaps so the `config-flow` rule is fully satisfied.

## Motivation

- The compliance checklist marks `config-flow` as partial precisely because the
  login forms have no per-field helper text. The rule says per-field context is
  what makes the config flow user-friendly.
- A password rendered by a plain text input is poor UX and can leak the value
  in plain sight; `TextSelectorType.PASSWORD` masks it, and
  `TextSelectorType.EMAIL` gives the right keyboard and autofill hints.
- These are the only remaining items of the rule; everything else in the flow
  (unique_id, reconfigure, reauth, options, data/options split) is already in
  place.

## Requirements

- R1 (MUST) Add a `data_description` object for `username` and `password` to
  the `user`, `reauth_confirm`, and `reconfigure` steps of
  `translations/en.json` and `translations/ru.json`. Each description is a
  non-empty, user-facing sentence that explains the field.
- R2 (MUST) Use `TextSelector` for both credential fields in every login form:
  username as `TextSelectorType.EMAIL` with `autocomplete="username"`,
  password as `TextSelectorType.PASSWORD` with
  `autocomplete="current-password"`. This applies to the `user` step schema
  and to the shared `reauth_confirm`/`reconfigure` schema.
- R3 (MUST) The `translations/en.json` and `translations/ru.json` key trees
  stay identical (spec 0011 R3/R6) and every translation leaf stays a non-empty
  string.
- R4 (MUST) Bump `manifest.json` `version` to `0.8.3` (runtime content:
  translations and config flow). The release tag must equal the manifest
  version (`v0.8.3`).
- R5 (MUST) The local gate (`pytest -m "not live"`) is green; CI `hassfest` and
  `hacs/action` (category: integration) stay green with no ignores.
- R6 (MUST) No behavior change to validation, unique_id normalization, entry
  data, or token handoff. The password stays transient and is never persisted.

## Design

### Selectors

```python
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

USERNAME_SELECTOR = TextSelector(
    TextSelectorConfig(type=TextSelectorType.EMAIL, autocomplete="username")
)
PASSWORD_SELECTOR = TextSelector(
    TextSelectorConfig(
        type=TextSelectorType.PASSWORD, autocomplete="current-password"
    )
)
```

`STEP_USER_DATA_SCHEMA` uses the two selectors, and `_async_revalidation_step`
builds its schema from the same two constants (with the existing username
default). `TextSelector` returns the typed string unchanged, so the existing
`strip().lower()` unique_id normalization and the cloud login path are
unaffected; `TextSelectorType.EMAIL` only selects the frontend input type and
does not reject values that are not email-formatted.

### Translations

`data_description` is added next to `data` in each login step; the step-level
`description` (disclaimer) stays as-is. Home Assistant shows the step
`description` above the form and each `data_description` under its field, so
both are needed. Custom components read runtime translations from
`translations/<lang>.json` (AGENTS §5); no `strings.json` is added.

## API

None. Config flow schema metadata and translation content only.

## Test plan

- TDD: `tests/test_config_flow.py` gains a test that drives the `user`,
  `reauth_confirm`, and `reconfigure` forms and asserts the `username` field
  uses an `email` `TextSelector` and the `password` field a `password`
  `TextSelector` (spec 0021 R2). It fails before the schema change.
- `tests/test_translations.py` gains a test that asserts each login step has a
  non-empty `data_description` for `username` and `password` in `en.json`; the
  existing key-tree parity and non-empty-leaf tests cover `ru.json`
  (spec 0021 R1/R3).
- Regression: the full `pytest -m "not live"` suite stays green; the existing
  config-flow scenarios submit plain strings and must pass through the
  selectors unchanged.

## Acceptance criteria

- Every login form has per-field `data_description`; the group compliance
  checklist marks `config-flow` as fully satisfied.
- The `user`/`reauth_confirm`/`reconfigure` schemas use `TextSelector`
  (`email` / `password`).
- `en.json` and `ru.json` keep identical key trees with non-empty leaves.
- `manifest.json` version is `0.8.3`; `pytest -m "not live"` is green.
- No entry data, token, or unique_id behavior changes.

## Out of scope

- Silver/gold quality-scale work (`PARALLEL_UPDATES`, `log-when-unavailable`,
  `ServiceValidationError`, diagnostics, icons).
- Any change to library pins, storage, or setup/unload logic.
- `integration_type` semantics and other non-blocking audit items (spec 0020
  out-of-scope list).

## Status

`approved` (2026-09-11). Implementation on `feat/0021-config-flow-data-description`:
selectors, translations, tests, version bump, local gate, then the group review
gate. The release `v0.8.3` is tagged after the squash-merge into `master`.
