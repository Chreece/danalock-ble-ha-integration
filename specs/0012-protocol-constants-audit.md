# 0012 — Protocol constants audit

- **Status:** approved
- **Scope:** `custom_components/danalock_ble/` own code plus the vendored
  `pydanalock/{ble,cloud}` snapshot — audit and provenance documentation, no
  behavior change

## Summary

A one-off audit of every literal table and constant in the integration's own
code, classifying each entry as a standard constant (T1), a protocol constant
observed on the wire (T2), an arbitrary opaque table (T3), or a non-public
blob (T4), and documenting the verdict for the entries that are not
self-evident. The same tiering applies to the vendored library snapshot under
`custom_components/danalock_ble/pydanalock/`, which is audited upstream by specs
`0009` (`pydanalock-ble`) and `0007` (`pydanalock-cloud`). The audit result: no
T3 and no T4 entries exist.

## Motivation

The repository must not contain literal tables lifted verbatim from
non-public sources — generated vendor tables or opaque constant blobs are
not acceptable in public code, and neither are certificates, keys, or device
data. The inventory below confirms that the existing constants are either
derived from public standards or are functionally necessary protocol values,
and marks the non-obvious ones with a provenance note in the source so the
classification survives future edits.

## Requirements

- R1 (MUST) Every literal table or multi-value constant in
  `custom_components/danalock_ble/` own code is classified into one of the tiers:
  - T1 — standard: Bluetooth SIG company ids, RFC/IANA values;
  - T2 — protocol constant observed on the wire: IDs, presets, flag names,
    device types;
  - T3 — arbitrary opaque table with no public source (must not exist);
  - T4 — non-public blob: certificates, keys, device/account data (must not
    exist).
- R2 (MUST) The audit finds zero T3 and zero T4 entries in own code and in
  the vendored snapshot. If a future change introduces a T3/T4 literal, it
  must be rejected at review time; the repository owns no automated guard
  for that.
- R3 (MUST) Non-obvious T2 tables carry a short provenance comment in the
  source with method-neutral wording — a protocol constant observed on the
  wire, no source, no method, no reference to non-public material.
- R4 (MUST) Documentation wording obeys the publication policy: only
  ``observed on the wire``, ``public registry``, ``RFC ...``; no reference to
  non-public material and no statement of how the protocol was determined.
- R5 (MUST) The vendored snapshot is not edited by hand: it mirrors the
  pinned library commits of `scripts/vendor.json` via `scripts/vendor.py`
  (own wording in `VENDORED.json`). Its provenance comments come from the
  upstream audits.
- R5a (MUST) Re-vendoring is performed with the merged library audit
  commits: once `pydanalock-ble` 0009 and `pydanalock-cloud` 0007 landed on
  their `master`, the pins in `scripts/vendor.json` advance to those
  commits and `scripts/vendor.py` copies the audited sources (including
  their provenance comments) into the vendored snapshot. Checked in with
  this change so the snapshot matches the audited library state.
- R6 (MUST) The audit is documentation-only: no logical lines of
  `custom_components/` code change, no behavior change, no test change.

## Design

Inventory of the integration's own code (verified against `master` at
2026-09-10; line numbers refer to the post-change sources):

| File:line | Entry | Tier | Note |
|---|---|---|---|
| `select.py:42-84` | `AUTO_LOCK_OPTIONS`, `BRAKE_AND_GO_BACK_OPTIONS`, `BLOCKED_TO_BLOCKED_OPTIONS`, `SETTING_OPTIONS` | T2 | observed device setting presets (spec 0010) |
| `select.py:80-84` | `SETTING_WRITES` | T2 | maps setting names to control methods |
| `broadcast.py:47` | `SETTING_FLAG_NAMES` | T2 | this library's own names for the settings |
| `control.py:53-70` | `_STATUS_MESSAGES` | T1/own | our English user-facing messages, not vendor strings |
| `const.py:28` | `MANUFACTURER_ID = 456` | T1 | Bluetooth SIG company id |
| `binary_sensor.py:97-101` | status binary sensor `(suffix, flag_name)` pairs | T2 | lock-flag names observed on the wire (spec 0005) |
| `__init__.py:52-56` | `PLATFORMS` list | T1/own | platform registration, not protocol data |
| `__init__.py:112-113` | `SUPPORTED_DEVICE_TYPES`, `DEVICE_TYPE_MODELS` | T2 | observed device types |

The vendored snapshot (`custom_components/danalock_ble/pydanalock/{ble,cloud}/*`)
mirrors the pinned `pydanalock-ble`/`pydanalock-cloud` commits; its constants are
classified by the upstream audit specs `0009`/`0007` (T1/T2 only).

Audit scans: no large numeric literal tables, no `fromhex`/`bytes([` blobs
beyond the library code already classified upstream, no lines longer than
200 characters in own `custom_components/` files.

### Source provenance notes

Two non-obvious T2 tables receive an inline comment (code lines unchanged):

- `select.py` setting presets: observed device presets;
- `broadcast.py` `SETTING_FLAG_NAMES`: the names are the library's own
  labels for the settings, whose presets are observed on the wire (the
  flag names themselves are library-defined, not vendor strings).

## Test plan

- No new tests: the audit changes only documentation and comments.
- Gate: `.venv/bin/pytest -m "not live"` green; CI hassfest + `hacs/action`
  green.
- Diff invariant: a manual inspection of `git diff` confirms the only
  `custom_components/` changes are comment lines plus the vendoring
  metadata of `danalock_ble/pydanalock/VENDORED.json` (commit/date provenance);
  no logical code line changed.

## Acceptance criteria

- Spec `0012` documents the tiers and the inventory with zero T3/T4 entries.
- Non-obvious T2 tables carry the provenance comment in own code.
- The vendored snapshot is re-vendored from the merged library audit
  commits, so it carries the upstream provenance comments (R5a).
- No logical code line changed; gates green; skeptic review without
  `BLOCKING`.

## Out of scope

- An automated guard against new T3/T4 tables (explicitly excluded).
- An internal provenance ledger (non-public source material).
- Licenses/disclaimers and publication policy wording (other plans).
- Test fixtures: synthetic and unchanged.

## Status

`approved` (audit outcome verified 2026-09-10: T3/T4 = 0; documentation-only
change; vendored snapshot re-vendored from the merged library audit commits
per R5a).

> Revision (2026-09-10): R5a was first drafted as a SHOULD that defers
> re-vendoring until the library audit branches merge. Once `pydanalock-ble`
> 0009 and `pydanalock-cloud` 0007 landed on their `master`, the deferral no
> longer applies and R5a became a MUST that performs the re-vendoring with
> the merged commit pins. Recorded here to preserve the decision trail.
