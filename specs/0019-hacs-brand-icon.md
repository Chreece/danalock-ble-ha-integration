# 0019 — HACS repository brand icon (brands repo submission)

- **Status:** draft (implementation pending approval)
- **Scope:** brand assets for the HACS repository listing plus the external
  submission to `home-assistant/brands`; documentation wording in this
  repository. No runtime code change.

## Summary

HACS renders the integration icon in its repository list from the Home
Assistant Brands CDN, keyed by the integration domain:

`https://brands.home-assistant.io/_/<domain>/icon.png`

HACS 2.0.5 builds this URL from its bundled frontend copy of the HA
`brandsUrl` helper with `useFallback: true` (the HACS frontend bundle is built
from `hacs/frontend` at `20250128065759`, i.e. before the Home Assistant
2026.3 Brands Proxy API). Because `danalock_ble` is not registered in the
`home-assistant/brands` repository, the CDN serves the generic missing-image
placeholder and HACS shows *Icon not available*.

The local `custom_components/danalock_ble/brand/icon.png` shipped by this
repository is a **different** mechanism: it feeds the Home Assistant Brands
Proxy API (`/api/brands/integration/danalock_ble/icon.png`, Home Assistant
≥ 2026.3) that the Home Assistant core UI uses. HACS 2.0.5 does not consume
that local proxy for its repository list, so the local asset alone does not
show up in HACS.

This spec registers the domain in the brands repository so the CDN path
resolves to the real icon, while keeping the local asset.

## Motivation

- The HACS store currently shows a placeholder for the integration, which
  looks unfinished next to integrations that ship a brand icon.
- The HACS 2.0.5 repository-list frontend resolves the icon through the
  brands CDN, so the submission is what makes the icon appear in HACS. (That
  release's bundled `validate/brands.py` checks
  `https://brands.home-assistant.io/domains.json` under `custom`; the CI
  action pinned at `hacs/action@main` checks the local `brand/icon.png`
  first and only falls back to that list, so this submission does not change
  the Action outcome.)
- The requirement is deliberately separated from the HA quality-scale
  `brands` rule, which is already satisfied by the local
  `brand/icon.png` (Home Assistant ≥ 2026.3) and does not require a brands
  repository PR.

## Requirements

- R1 (MUST) Add the following files to `home-assistant/brands` in the
  `custom_integrations/danalock_ble/` directory, via a pull request:
  - `icon.png` — 256×256 pixels,
  - `icon@2x.png` — 512×512 pixels.
- R2 (MUST) The directory name matches the integration domain from
  `manifest.json` (`danalock_ble`); no other directories are added. The
  brands repository forbids symlinks in `custom_integrations/`.
- R3 (MUST) Keep `custom_components/danalock_ble/brand/icon.png` in this
  repository (HA quality-scale `brands` rule; local Brands Proxy API). MAY
  add `custom_components/danalock_ble/brand/icon@2x.png` (512×512) for parity
  with the local proxy's hDPI variant.
- R4 (MUST) The submitted images follow the brands repository image
  specification: PNG, properly (lossless-preferred) compressed, interlacing
  preferred, trimmed of empty edge space, transparency preferred, optimized
  for a light background (dark-optimized variants use the `dark_` prefix and
  are optional), and:
  - `icon.png` 1:1 aspect ratio at 256×256,
  - `icon@2x.png` 1:1 aspect ratio at 512×512.
- R5 (MUST) The assets must not use Home Assistant branded imagery (the
  brands repository rejects images that could be mistaken for an
  official/core integration).
- R6 (SHOULD) Derive `icon.png` from the existing local
  `custom_components/danalock_ble/brand/icon.png` and source
  `icon@2x.png` from the same master artwork at native 512×512 resolution
  (not an upscale) to pass review quality expectations.
- R7 (MUST) No runtime code change; no `manifest.json` version bump and no
  new GitHub release are required for this change (assets/metadata only). If
  a release is cut for other reasons, the assets ride along.
- R8 (SHOULD) Update `AGENTS.md` §4 to document both mechanisms explicitly:
  the local `brand/` directory satisfies the HA `brands` rule without a
  brands PR, while the HACS repository-list icon resolves through the brands
  CDN and therefore does require the `home-assistant/brands` submission. The
  current “no PR to the brands repo needed” sentence must not be read as
  covering the HACS list.
- R9 (SHOULD) Add a short README note (Installation or Troubleshooting) that
  the HACS list icon comes from the brands CDN and may appear with a delay
  after merge: Cloudflare caches for 24 hours and browsers for 7 days.
- R10 (MUST) All wording stays method-neutral (AGENTS §11) and references
  only public sources. The assets contain no device or account data (serial
  number, BLE address, keys, tokens) — brand artwork only.

## Design

Two independent icon paths exist and must not be conflated:

| Consumer | Source | URL / key | Covered by |
|---|---|---|---|
| Home Assistant core UI (config flow, device page, `update` entity picture, brand proxy) | local `custom_components/danalock_ble/brand/` | `/api/brands/integration/danalock_ble/icon.png` | R3 (already present) |
| HACS repository list (HACS 2.0.5 frontend) | HA Brands CDN, domain-keyed | `https://brands.home-assistant.io/_/danalock_ble/icon.png` | R1/R2/R4 |

- URL shape (from the brands repository README): the `/_/` segment selects the
  placeholder-fallback variant; a missing domain returns the placeholder, a
  registered domain returns the real image. Without `/_/` a missing domain
  returns 404.
- Registration is driven by the presence of
  `custom_integrations/danalock_ble/` in `home-assistant/brands`; the deployed
  `domains.json` `custom` list is generated from that directory, so no manual
  `domains.json` edit is part of the PR.
- The brands repository CI validates image dimensions/format. The domain must
  not collide with a core integration (it does not; `custom_integrations/`
  holds thousands of unrelated custom domains and, among Danalock-named
  domains, only the different domain `danalock_cloud` is registered).
- Optional logo files are not required: if the artwork is square, icon images
  suffice and the icon is served as the logo fallback. Submit `logo*.png`
  only if a distinct landscape logo exists.
- Alternative considered (not pursued here): contribute a change to the HACS
  frontend so it uses the HA local Brands Proxy API instead of the CDN. That
  is an upstream HACS change and is out of scope.

## API

None. Documentation and image assets only.

## Test plan

- Pre-merge evidence (recorded in the PR/branch notes):
  - `GET /api/brands/integration/danalock_ble/icon.png` on a live instance
    returns `200`, `image/png` (local proxy works).
  - `https://brands.home-assistant.io/_/danalock_ble/icon.png` returns the
    placeholder (size/hash differs from the local icon); `domains.json`
    `custom` does not contain `danalock_ble`.
  - Asset checks: `icon.png` is 256×256, `icon@2x.png` is 512×512, both PNG
    and 1:1 (e.g. `file` / `identify`).
- Post-merge verification:
  - `https://brands.home-assistant.io/domains.json` lists `danalock_ble` in
    `custom`.
  - `https://brands.home-assistant.io/_/danalock_ble/icon.png` returns the
    submitted `icon.png` (hash match).
  - HACS repository list shows the icon after a cache refresh (Cloudflare up
    to 24 h; browser up to 7 days).
- Repo gate: the change is docs/assets only; `pytest -m "not live"` and the
  existing `hassfest` + `hacs/action` checks stay green.

## Acceptance criteria

- The `home-assistant/brands` PR is merged and `domains.json.custom` contains
  `danalock_ble`.
- The CDN URL `https://brands.home-assistant.io/_/danalock_ble/icon.png`
  resolves to the submitted 256×256 icon; the HACS repository list displays
  it after cache refresh.
- The local `/api/brands/integration/danalock_ble/icon.png` still returns the
  local asset.
- No runtime code, manifest version, or release change is introduced.
- `AGENTS.md` §4 (and the README note) distinguish the local vs CDN icon
  paths; wording is method-neutral and references only public sources.

## Out of scope

- HACS default-store submission (`hacs/default`).
- Any upstream change to HACS frontend icon resolution.
- New brand artwork design; only packaging/submission of existing artwork.
- Logo and dark-theme variants beyond what the brands repository requires
  (add only if a distinct logo exists).
- Changing HA core brand handling or the local Brands Proxy API.

## Status

`draft`. Open questions to resolve before moving to `approved`:

1. Where does the native 512×512 master artwork come from (the repo currently
   ships only the 256×256 local `icon.png` and `assets/banner.png`)?
2. Should a logo (`logo.png` / `logo@2x.png`) be submitted alongside the icon,
   or is the icon sufficient (square artwork)?
3. Should `custom_components/danalock_ble/brand/icon@2x.png` be added locally
   as well (R3, optional)?
