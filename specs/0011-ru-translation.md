# 0011 — Russian UI translation

- **Status:** implemented
- **Scope:** `custom_components/danalock_ble/translations/` (English source plus
  Russian), a translation-parity test, AGENTS.md §2 wording, README
- **Depends on:** none (additive; `select.py` is not touched)

## Summary

Add a Russian runtime translation (`translations/ru.json`) that fully mirrors
`translations/en.json`, including per-option state cards for the setting
selects, and enforce key-tree parity with a test so the language files cannot
drift apart. The English UI text is unchanged; the option labels sent to
automations change to slug keys (breaking, recorded in R8).

## Motivation

The integration ships only English translations. Home Assistant picks a
runtime translation by the language of the user profile, but a custom
component without a Russian file shows English to Russian users. The setting
selects expose raw option strings hardcoded in `select.py`; their display is
translated through per-option state cards, and those state cards do not exist
yet even in English, so the mechanism has to be added to `en.json` first and
then translated. Spec 0007 listed RU translations as out of scope ("public
repo stays EN"); this spec revises that for runtime translation files only,
which are user-facing content (like `brand/`), while all other repository
text stays English.

## Requirements

- R1 (MUST) Runtime translations live only in `translations/<BCP47>.json`; there
  is no `strings.json` and no `[%key:...]` placeholder — both are Home
  Assistant core build-time features that do not work in custom components.
- R2 (MUST) The option labels of the setting selects in `select.py` are renamed
  to neutral slug keys (`off`, `5_s`, …, `900_s`, `one`, `two`), and
  `translations/en.json` gains `state` maps under
  `entity.select.{auto_lock,brake_and_go_back,blocked_to_blocked}` mapping each
  slug to its English label (`off` → `Off`, `5_s` → `5 s`, `one` → `One`).
  The device values stay unchanged; only the sent option labels change.
- R3 (MUST) `translations/ru.json` is a full mirror of the `en.json` key tree:
  `config` (abort, error, step), `options`, `services`, and every `entity`
  section, with Russian values.
- R4 (MUST) The Russian state cards translate every option label: `off` →
  `Выкл`, `one` → `Один`, `two` → `Два`, `5_s` → `5 с` (Cyrillic «с»), and so
  on for each seconds preset. The values sent to the device do not change.
- R5 (MUST) Terminology: «Замок», «Заело» (jammed), «Состояние замка»,
  «Уровень батареи», «Сила сигнала»; brand and protocol names are not
  translated (`Danalock`, `RSSI`); follow the Russian localization terminology
  of Home Assistant core where it exists.
- R6 (MUST) A test enforces: `en.json` exists and is valid; every
  `translations/*.json` has a key tree identical to `en.json` (recursive path
  comparison); every leaf is a non-empty string; every select state map covers
  exactly the raw option labels of `select.py`; `ru.json` is not byte-identical
  to `en.json`; no `[%key:...]` and no `strings.json`.
- R7 (MUST) AGENTS.md §2 records that runtime translation files
  (`translations/*.json`) are user-facing content and may be non-English, while
  all other repository text stays English. README states
  `UI languages: English, Russian`.
- R8 (MUST) Additive in effect but breaking for automations that pass the old
  option labels (`5 s`, `Off`): the manifest `version` moves to `0.6.0`; the
  breaking option-label change is recorded in this spec; hassfest and the HACS
  validation stay green.

## Design

- The option labels in `select.py` are neutral slugs because hassfest validates
  translation `state` keys with a lowercase `[a-z0-9-_]` slug validator
  (uppercase and spaces are rejected). The `en.json` cards therefore map each
  slug to its English label (`5_s` → `5 s`), so the English UI is unchanged
  while the same card mechanism translates to Russian.
- `ru.json` mirrors the tree key-for-key. State-card keys are the slug labels
  of `select.SETTING_OPTIONS`, matching the Home Assistant frontend lookup
  `component.danalock_ble.entity.select.<key>.state.<option>`.
- The parity test lives in `tests/test_translations.py` and reads the option
  labels from `select.SETTING_OPTIONS`, so a new preset in `select.py` without
  a matching state card fails the gate instead of silently showing a raw value.
- The only code change is the slug rename in `select.py`; the integration
  already loads translations from `translations/` and picks up `ru.json` from
  the profile language.

## Test plan

- `tests/test_translations.py`: validity, existence of `ru.json`, recursive
  key-tree parity for every `translations/*.json`, non-empty string leaves,
  select state-card coverage against `select.SETTING_OPTIONS`, `ru.json`
  differs from `en.json` (bytes), no `[%key:...]`, no `strings.json`.
- Red first: the tests fail before `ru.json` and the `en.json` state maps
  exist.
- Gate: `.venv/bin/pytest -m "not live"` green; CI hassfest + hacs/action
  green.
- Live acceptance (after release, manual, user confirmation): HA profile
  language → Русский; check the config flow (login, reauth, reconfigure), the
  options flow, entity names, the select state display («Выкл»/«5 с»), and the
  more-info dropdown.

## Acceptance criteria

- All requirements implemented and covered; the parity test fails without
  `ru.json` and the state maps and passes with them; gate green; skeptic
  review without `BLOCKING`; manifest 0.6.0; the live Russian UI acceptance is
  run separately by the user.

## Out of scope

- Languages other than English and Russian.
- Translating logs and exceptions of the `pydanalock-ble`/`pydanalock-cloud`
  libraries (English per spec 0006/§11).
- Translating the more-info dropdown options by another mechanism than state
  cards (selected per the state-card precedent; verified in the live
  acceptance).

## Status

`implemented` (spec approved and merged; the manifest version moves to `0.6.0`).
Prior recorded decisions: 2026-09-09 RU-translation planning conversation —
state-card translation of the select options, a full `ru.json` mirror, a
key-tree parity test enforced by the gate, an AGENTS.md §2 clarification that
runtime translations are user-facing content, and a release — and 2026-09-10 in
this branch: treated after the skeptic review showed that hassfest validates
translation `state` keys as lowercase slugs. The option labels were renamed to
slug keys (`off`, `5_s`, …, `one`, `two`) with human labels supplied by the
en/ru state cards. This is breaking for automations that pass the old labels,
so the version moves to 0.6.0 and the change is recorded here (R8) rather than
shipped as a silent patch. Implemented 2026-09-10: slug labels, en/ru state
cards, `tests/test_translations.py` parity gate, AGENTS/README wording,
manifest 0.5.0 → 0.6.0.
