# AGENTS.md — danalock-ble-ha-integration

Working agreement for humans and AI agents contributing to this repository.

## 1. Role in the group

This repository is part of a group of projects that build a self-hosted
control stack for Danalock V3 smart locks:

| Repository | Role | Visibility | Language |
|---|---|---|---|
| `pydanalock-ble` | BLE client library for the Danalock V3 lock | public | English |
| `pydanalock-cloud` | Cloud API client (OAuth2, devices, keys) | public | English |
| `danalock-ble-ha-integration` (this repo) | Home Assistant custom integration, consumes both libraries | public | English |

This integration depends on both libraries. It must not duplicate their
logic; key material is consumed as opaque bytes (group contract type
`DeviceKey`: serial, login_blob, broadcast_key, validity, permissions).

## 2. Language

All repository text is English: code, comments, docstrings, documentation,
commit messages, and review notes. Runtime translation files under
`custom_components/danalock_ble/translations/*.json` are user-facing content (like
`brand/`) and may be written in another language; everything else stays
English.

## 3. Stack and commands

- Home Assistant custom component under `custom_components/danalock_ble/`.
- Validation: `hassfest` + HACS validation run in CI (`.github/workflows/validate.yml`).
- Local tests use `pytest` with `pytest-homeassistant-custom-component`
  fixtures (added when the first testable feature lands).

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements_dev.txt
.venv/bin/pytest -m "not live"
```

Live tests (real lock / adapter / cloud) carry the `live` marker and are run
manually only.

## 4. HACS compatibility (hard requirements)

The repository must stay installable and validatable by HACS
(https://hacs.xyz/docs/publish/integration). CI (`.github/workflows/validate.yml`)
enforces this; never merge changes that break it.

- Layout: exactly one integration per repository — a single subdirectory under
  `custom_components/`, named after the domain
  (`custom_components/danalock_ble/`). Every file the integration needs at
  runtime lives inside that directory; repo-root files (`hacs.json`,
  `README.md`, `specs/`, workflows) are not runtime content.
- `hacs.json` sits in the repository root. `name` is the only required key.
  Supported keys: `name`, `content_in_root`, `zip_release`, `filename`,
  `hide_default_branch`, `country`, `homeassistant`, `hacs`,
  `persistent_directory`, `render_readme`. Keep the file minimal and do not
  invent keys.
- `manifest.json` must define at least the HACS-required keys `domain`,
  `name`, `documentation`, `issue_tracker`, `codeowners`, `version`, plus the
  keys required by hassfest (§5). `version` must be AwesomeVersion-compatible
  (SemVer) and must equal the tag of the release that ships it.
- Brand assets: `custom_components/danalock_ble/brand/icon.png` must exist —
  HACS validates it directly (Home Assistant ≥ 2026.3 serves local `brand/`
  images; no PR to the brands repo needed). Optional files follow the brands
  conventions: `logo.png`, `dark_icon.png`, `dark_logo.png`, `icon@2x.png`,
  `logo@2x.png` and their `dark_` variants.
- GitHub releases: every change to runtime code ships as a tagged release
  (`vX.Y.Z` — a full GitHub Release, a tag alone is not enough). HACS derives
  the offered version from the latest release tag; without releases it falls
  back to the default branch tip.
- Repository metadata on GitHub: public repository, description filled in,
  topics set, issues enabled.
- CI checks (`hacs/action` with `category: integration`,
  `home-assistant/actions/hassfest`) must stay green. Do not add entries to
  `with.ignore`; if a check ever needs ignoring, record the reason in a spec.
- Inclusion in the HACS default store (`hacs/default`) is optional; if
  pursued, all checks must pass without ignores and at least one release must
  exist (https://hacs.xyz/docs/publish/include).

## 5. Home Assistant integration standards

Follow the official integration development docs
(https://developers.home-assistant.io):

- File structure: runtime files under `custom_components/danalock_ble/`
  (`__init__.py`, `manifest.json`, `config_flow.py`, `const.py`,
  `coordinator.py`, platform files, `services.yaml` when actions are exposed,
  `translations/en.json`). Tests mirror the Home Assistant layout: `tests/`
  at the repository root with `__init__.py` and `conftest.py`.
- Manifest: set `integration_type` (`device` for a lock), `iot_class`
  (`local_push`), `config_flow: true` (implies `config_flow.py`),
  `single_config_entry` when only one entry is supported, `bluetooth` matchers
  for advertisement discovery (`connectable: false` for advertisement-only
  devices), `requirements` pinned with `==` and limited to packages not
  shipped with Home Assistant core, `loggers` for library log filtering.
- Config flow: attach a stable string `unique_id` (lock serial; never an IP
  address or a user-changeable name) with `async_set_unique_id` +
  `_abort_if_unique_id_configured`; support `reconfigure`; trigger `reauth`
  via `ConfigEntryAuthFailed`; use an options flow for optional settings;
  on schema changes bump `VERSION`/`MINOR_VERSION` and implement
  `async_migrate_entry`.
- Setup: verify reachability in `async_setup_entry` and raise
  `ConfigEntryNotReady` (retry) or `ConfigEntryAuthFailed` (reauth) instead of
  failing; implement `async_unload_entry`; keep runtime objects on
  `entry.runtime_data` (not `hass.data`); all I/O async, no blocking calls in
  the event loop.
- Entities: unique IDs, `has_entity_name`, device registry entries with real
  connections/identifiers, availability handling, entity categories; add
  diagnostics support on the way to silver/gold.
- Bluetooth: use the `bluetooth` integration APIs (`async_register_callback`,
  passive/active update processors and coordinators) instead of a private
  scanning loop; advertisements drive passive updates, the DMI connection is
  active.
- Translations: runtime translations come from `translations/en.json`; do not
  use `strings.json` or `[%key:...]` placeholders — both are Home Assistant
  core build-time features that do not work in custom components.
- Quality scale: bronze is the baseline for every feature — `config-flow`,
  `config-flow-test-coverage`, `unique-config-entry`, `test-before-setup`,
  `test-before-configure`, `runtime-data`, `entity-unique-id`,
  `has-entity-name`, `entity-event-setup`, `action-setup`,
  `appropriate-polling`, `common-modules`, `dependency-transparency`,
  `brands`, and the `docs-*` documentation rules; grow toward silver next
  (unloading, reauthentication, full test coverage, log hygiene, parallel
  updates).

## 6. Spec-driven development (SDD)

- Every feature starts with a spec in `specs/NNNN-slug.md` (zero-padded
  number, short slug). Status lifecycle: `draft → approved → implemented →
  superseded`.
- Implementation without an `approved` spec is forbidden. Specs marked
  "implementation pending research" must not be implemented until they move to
  `approved`.
- Spec template: Summary / Motivation / Requirements (MUST/SHOULD) / Design /
  API / Test plan / Acceptance criteria / Out of scope / Status.
- Moving a spec from `draft` to `approved` is a reviewable change (PR or
  recorded decision in the conversation).
- Specs for user-visible features must account for §4/§5: config-flow steps,
  unique IDs, translations, release/versioning impact.

## 7. Test-driven development (TDD)

- Tests are written first and must fail before the implementation exists.
- Entities/config flows are tested with Home Assistant test fixtures and
  synthetic broadcasts; anything touching real hardware stays behind the
  `live` marker.
- `config_flow.py` must have full test coverage (bronze rule).
- A merge requires a fully green run of the gate commands (§3).

## 8. Git process

- Default branch: `master`. Direct commits to `master` are forbidden (the
  initial scaffold import is the only exception).
- One feature = one branch `feat/NNNN-slug` containing the spec, the tests,
  and the implementation together.
- Commit messages follow Conventional Commits (`feat:`, `fix:`, `docs:`,
  `test:`, `chore:`, `refactor:`).
- Merge into `master` only after the review gate (§9), squash-merge, then
  delete the feature branch.
- After merging runtime changes to `master`: tag `vX.Y.Z` matching the
  manifest `version` and publish a GitHub Release (§4).

## 9. Review gate (mandatory)

A feature branch may merge into `master` only when both hold:

1. A full local run is green (§3/§7).
2. A skeptic review returns no `BLOCKING` findings. Invoke the skeptic agent
   (Task tool, subagent `skeptic`, definition in `.kilo/agent/skeptic.md`)
   with a prompt such as:

   > Review branch `feat/NNNN-slug` against its spec `specs/NNNN-slug.md`
   > (diff base: `master`). Follow the checklist in
   > `.kilo/agent/skeptic.md` and answer in the verdict format
   > (`BLOCKING: ...` / `NITS: ...` / `APPROVED`).

`BLOCKING` findings forbid the merge; fix and re-review.

## 10. Secrets and safety

- Your own device and account data are confidential: never include them in
  this public repository — in code, docs, examples, fixtures, or commit
  history. This covers lock serial numbers, device addresses, key material,
  and account data (usernames, passwords, tokens). Use synthetic
  placeholders (`<serial>`, `1a2b3c4d5e6f`); real values live only in Home
  Assistant storage or gitignored `*.local.json` files.
- Live actions against a lock (unlock, lock, enrollment) are executed only
  after explicit user confirmation in the conversation.

## 11. Publication policy (public repository)

- Do not copy text or material from non-public sources into this repo.
- Public documentation is an English description of protocol behavior. Cite
  only this repository's own specs; do not name non-public or third-party
  sources, and do not state or claim how the protocol was determined.
- Everything in this repo is English (§2); keep the disclaimers in place
  (unofficial, not affiliated with Danalock AS, own devices only).

## 12. Release runbook (new versions)

Release only for runtime changes; documentation-only changes ship without a
release.

1. Bump `manifest.json` `version` (SemVer) as part of the feature branch; it
   must equal the release tag. There is no other copy of the version.
2. Feature branch `feat/NNNN-slug` (spec + tests + version bump), gate
   (§3/§7), skeptic, squash-merge into `master`.
3. Push `master`, then create a GitHub Release `vX.Y.Z` matching the manifest
   `version` (a tag alone is not enough: HACS reads the latest release; see §4
   and §8). `hassfest` and `hacs/action` must stay green.
4. Changing a library version means updating the `manifest.json`
   `requirements` pin **and** `requirements_dev.txt`, plus the spec; keep the
   pin compatible with Home Assistant's pinned `httpx` (currently `==0.27.2`).
5. Touch `hacs.json`/`brand/` only when their content changes (§4 rules for
   HACS inclusion and `zip_release`).

## 13. References

- Specs: `specs/` (0001 config flow is the first deliverable).
- Dependency strategy: both `pydanalock-ble` and `pydanalock-cloud` are
  consumed from PyPI (pinned in `manifest.json` and `requirements_dev.txt`;
  specs 0014, 0015). No library is vendored; `scripts/vendor.*` and the
  vendored tree are gone.
- Versioning: SemVer, 0.x while the integration is unstable; manifest
  `version` and the release tag move together (§4).
- HACS publishing rules: https://hacs.xyz/docs/publish/start,
  https://hacs.xyz/docs/publish/integration,
  https://hacs.xyz/docs/publish/include, https://hacs.xyz/docs/publish/action
- Home Assistant integration development:
  https://developers.home-assistant.io/docs/creating_integration_file_structure,
  https://developers.home-assistant.io/docs/creating_integration_manifest,
  https://developers.home-assistant.io/docs/core/integration_quality_scale,
  https://developers.home-assistant.io/docs/core/integration/config_flow
