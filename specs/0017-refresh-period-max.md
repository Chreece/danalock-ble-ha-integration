# 0017 — Cap the background key-refresh period at 24 hours

- **Status:** draft (implementation pending; see Status)
- **Scope:** the `refresh_period_hours` option of the background key-refresh
  cycle; the option maximum changes from 720 to 24 hours. No runtime change is
  made by this spec yet.

## Summary

The background key-refresh period is configured by `refresh_period_hours`:
default 12, range 0–720, where `0` disables the cycle. The maximum (720 hours
= 30 days) is unhelpfully large for a setting whose purpose is keeping the
device keys fresh, and it is easy to set by accident. This spec caps the
effective maximum at 24 hours while keeping the default at 12 and `0` as
"disabled". The option is clamped on read for existing entries, so no stored
options are rewritten.

## Motivation

`refresh_period_hours` was introduced by
`specs/0010-update-check-and-hours.md` as a unit change from the legacy
`refresh_period_minutes` keys (default 720 minutes, range 0–43200 minutes).
The upper bound was carried over mechanically as 43200 / 60 = 720 hours,
without re-checking whether a 30-day interval is a meaningful maximum.
A key-refresh cycle that runs at most once a month no longer fulfills the
purpose of the option, and the wide range invites misconfiguration in the UI.
The natural ceiling for an hourly refresh setting is a day: `0–24` hours.

This is a future fix, deliberately recorded as a spec only. It must not be
implemented before the pending publication of the current version (see
Status).

## Requirements

- R1 (MUST) `const.py` sets `MAX_REFRESH_PERIOD_HOURS = 24`.
  `DEFAULT_REFRESH_PERIOD_HOURS` stays `12`; `MAX_REFRESH_JITTER_HOURS` stays
  `24` and `DEFAULT_REFRESH_JITTER_HOURS` stays `6`.
- R2 (MUST) The options flow accepts `refresh_period_hours` in `0..24`
  (integer), and `0` keeps disabling the background cycle. The schema is
  derived from the constant, so it must not hard-code the old `720`.
- R3 (MUST) The options form prefill and the key manager read the effective
  value through the same clamped path: a stored or legacy value above 24 is
  treated as `24`, so the form validates (its default never exceeds the
  maximum) and the scheduler never arms a cycle longer than 24 hours plus the
  jitter. The clamp applies to the legacy minute conversion as well
  (`minutes // 60`, then clamped).
- R4 (MUST) Setup does not write to `entry.options`: existing entries keep
  their stored values, and the clamp is applied on read only. A later
  user-initiated save persists the clamped hour value.
- R5 (MUST) `0` still disables; values `1..24` schedule a refresh at that
  interval; the default for entries without options stays 12.
- R6 (MUST) `translations/en.json` keeps the "(hours)" wording; no new keys.
  If the stored maximum is mentioned anywhere user-facing, it is updated to
  24.
- R7 (MUST) The README option table is updated from `0–720` to `0–24` when
  this spec is implemented.
- R8 (MUST) The manifest version is bumped at implementation time per
  AGENTS §12 (a runtime behavior change), together with the release tag.

## Design

- The clamp belongs in the shared helper that resolves an effective hour
  value, `option_refresh_hours`. The helper gains a `max_hours` parameter and
  returns the resolved value clamped to `0..max_hours`; the clamp is applied
  after the legacy minute conversion. Both call sites already go through that
  helper: the options-flow prefill and `keymanager.start()` pass the matching
  constant (`MAX_REFRESH_PERIOD_HOURS` for the period, `MAX_REFRESH_JITTER_HOURS`
  for the jitter), so the options form and the scheduler cannot diverge.
- The options flow schema uses `vol.Range(min=0, max=MAX_REFRESH_PERIOD_HOURS)`,
  so lowering the constant is sufficient once the prefill is clamped.
- Because the clamp is on read, a user with a legacy `refresh_period_minutes`
  entry of 43200 minutes (720 hours) sees 24 in the form and schedules a
  24-hour cycle from the next start, without a write. Saving the form then
  writes the hour key.
- The jitter maximum is unchanged: a 24-hour period plus up to 24 hours of
  jitter means the next run can be up to 48 hours away, which is an accepted
  combination, not a second change.

## API

No public API change. The option name, default, and `0`-disables semantics
are unchanged; only the effective maximum tightens.

Internally, `option_refresh_hours` in `const.py` gains the `max_hours`
parameter described in Design; the options-flow prefill and
`keymanager.start()` pass the period and jitter maxima respectively. No other
interface changes.

## Test plan

- `option_refresh_hours`: an hour value above 24 resolves to 24; a legacy
  minute value of 43200 resolves to 24; `0` resolves to `0` (disabled); the
  default resolves to 12; a value within range is returned unchanged.
- Options flow: the schema rejects `25` and prefills `24` for a config entry
  whose stored `refresh_period_hours` is `720` (or whose legacy minutes are
  43200), so the form submits without a validation error.
- Key manager: an entry clamped from 720 schedules the next cycle after 24
  hours plus jitter, not after 30 days.
- Full gate `pytest -m "not live"` green; `hassfest` and `hacs/action` stay
  green.

## Acceptance criteria

- R1–R8 hold.
- With a fresh entry, the options form offers `0..24` and the default is 12.
- With a legacy entry above the new maximum, the integration still loads, the
  form validates, and the effective interval is at most 24 hours plus jitter.
- No write to `entry.options` happens during setup or migration.

## Out of scope

- Changing the jitter range or default.
- Sub-hour periods or a separate "refresh now" action for keys.
- Removing the background refresh option.
- Rewriting stored options during setup (the clamp is read-only).
- Implementation before the pending publication window (see Status).

## Status

`draft` — implementation pending. Per the spec-driven process, a `draft` spec
must not be implemented until it moves to `approved`. The intended target is
the first release after the current publication work; the manifest version
bump and README update happen on the implementing branch (R7/R8).

The number `0017` avoids the duplicated `0010`: `0015` belongs to the
dependency-change spec and `0016` was reserved for a possible renumbering of
the duplicated `0010` update-check spec. That renumbering was **not**
performed (the `spec 0010` references in code and tests are ambiguous between
the two specs, so they were left as-is); `0016` therefore stays unused and
this spec continues past it.
