<div align="center">

# Danalock Bluetooth — Home Assistant integration

<!-- prettier-ignore-start -->
[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg?style=for-the-badge)](https://hacs.xyz/docs/publish/start)
[![Latest release](https://img.shields.io/github/v/release/buggy-shep/danalock-ble-ha-integration?style=for-the-badge)](https://github.com/buggy-shep/danalock-ble-ha-integration/releases)
[![CI](https://img.shields.io/github/actions/workflow/status/buggy-shep/danalock-ble-ha-integration/validate.yml?branch=master&style=for-the-badge)](https://github.com/buggy-shep/danalock-ble-ha-integration/actions/workflows/validate.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg?style=for-the-badge)](LICENSE)
[![Issues](https://img.shields.io/github/issues/buggy-shep/danalock-ble-ha-integration?style=for-the-badge)](https://github.com/buggy-shep/danalock-ble-ha-integration/issues)
[![Stars](https://img.shields.io/github/stars/buggy-shep/danalock-ble-ha-integration?style=for-the-badge)](https://github.com/buggy-shep/danalock-ble-ha-integration/stargazers)
<!-- prettier-ignore-end -->

An unofficial Home Assistant integration for Danalock V3 smart locks over
Bluetooth Low Energy: local lock control and monitoring, linked to your
Danalock cloud account.

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=buggy-shep&repository=danalock-ble-ha-integration&category=integration)
[![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=danalock_ble)

<img src="custom_components/danalock_ble/brand/icon.png" width="120" alt="Danalock Bluetooth integration icon">

</div>

> **Disclaimer.** This project is unofficial and is not affiliated with,
> endorsed by, or sponsored by Danalock AS or Poly-Control. It is built for
> interoperability with devices you own. Use it at your own risk; only control
> devices you are authorized to control.

## Why this integration

The Danalock V3 comes in three variants, and every one of them has Bluetooth:
BLE-only (BTHK), Z-Wave + BLE (BTZE), and ZigBee + BLE (BTZB). The Z-Wave and
ZigBee sides are already supported natively in Home Assistant. This integration
uses only the Bluetooth side, so it is meant to work with all three variants.
It was written because the Z-Wave path of the author's Z-Wave + BLE (BTZE) lock
was slow and unreliable in practice: a real lock or unlock could take up to
around 30 seconds to take effect, state refreshes lagged behind, and the
`zwave_js.refresh_value` workaround did not really help.

This integration lets Home Assistant work with the lock the way the native
Danalock app does: commands complete in seconds, and Home Assistant sees
`locking`/`unlocking` while a command runs plus an optimistic state until the
lock's next advertisement confirms the real state. Dashboards and automations
feel interactive instead of waiting on a slow round trip.

The integration talks directly to the lock over BLE using the same account
keys the official app uses. State is derived from the lock's own BLE
advertisements; control and settings use the account keys.

- **Compatibility.** Verified against the Z-Wave + BLE variant (BTZE). Because
  the integration only uses Bluetooth, the BLE-only (BTHK) and ZigBee + BLE
  (BTZB) variants should work too, but they have not been verified; feedback
  from owners of those variants is welcome.
- **Scope.** The integration deliberately implements only the everyday, safe
  operations on a lock you own. Installing firmware and managing the cloud
  account are intentionally left to the official Danalock app.
- **Privacy.** Commands and state handling run locally over Bluetooth. The
  cloud is contacted only for account login, key retrieval, and the
  firmware-version lookup.

## Features

- **Local lock control** — `lock` and `unlock` over BLE, with in-progress
  states and an optimistic state that is confirmed by the lock's next
  advertisement.
- **Passive state monitoring** — lock state, battery level, signal strength
  (percent and dBm), update counter, and device flags, decoded from the lock's
  advertisements roughly every 2 seconds. No private scanning loop; the shared
  Home Assistant Bluetooth machinery is used. Key validity
  (`key_valid_until`) comes from the cloud key metadata.
- **Lock settings** — *Auto lock*, *Brake and go back*, and *Blocked to
  blocked* (end-to-end mode) as selects with verified writes (the setting is
  written and read back in the same BLE session). The displayed value follows
  the lock's own GATT report.
- **Automatic key refresh** — a lazy pre-check before commands, a retry once
  when the lock rejects the login token, and a background refresh cycle whose
  period and jitter are configurable in hours.
- **Firmware version reporting** — an `update` entity that compares the
  installed firmware (read over BLE) with the latest version (from the
  cloud). Read-only: nothing is ever installed. A
  `danalock_ble.check_firmware_updates` action runs a check on demand.
- **Cloud account linking** — log in once; keys are fetched at setup and
  reload. The password is never stored.
- **Standards-based** — uses Home Assistant's Bluetooth APIs and
  `local_push`, with UI translations for English and Russian.

## Installation

### Requirements

- Home Assistant with the Bluetooth integration running and a working
  Bluetooth adapter.
- A Danalock cloud account with key grants on the locks you want to control.
- Internet access for the cloud login, key retrieval, and firmware-version
  lookup.
- Tested on Home Assistant 2026.9 (2026.9.1); other versions have not been
  validated.

### HACS

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=buggy-shep&repository=danalock-ble-ha-integration&category=integration)

1. In HACS, open the three-dot menu → *Custom repositories*, add
   `https://github.com/buggy-shep/danalock-ble-ha-integration` with category
   *Integration* (the button above pre-fills this), then search for *Danalock
   Bluetooth* and install it.
2. Restart Home Assistant.
3. Add the integration via *Settings → Devices & Services → Add Integration →
   Danalock Bluetooth* (or use the button below).

[![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=danalock_ble)

### Manual

<details>
<summary>Manual installation steps</summary>

1. Copy the `custom_components/danalock_ble/` directory into your Home
   Assistant `config/custom_components/` directory.
2. Restart Home Assistant.
3. Add the integration via *Settings → Devices & Services → Add Integration →
   Danalock Bluetooth*.

Home Assistant downloads and installs the two pinned PyPI dependencies
(`pydanalock-ble` and `pydanalock-cloud`) the first time the integration is
set up, so an internet connection is required.

</details>

### First setup

Enter your Danalock cloud account credentials. The integration validates the
login and fetches the account keys; devices are created only after the keys
have been obtained.

## Configuration

| Parameter | Description |
|---|---|
| Username | Your Danalock cloud account (email) |
| Password | Your Danalock cloud password (used only to log in; never stored) |

- Token pairs are stored in Home Assistant's internal storage, keyed per
  config entry.
- Device keys are not persisted: they are fetched from the cloud at every
  setup and reload. If the cloud is unreachable, the integration retries
  until it becomes available.
- Each cloud account can be added once; device identifiers come from the lock
  serial numbers.
- Re-authenticate or reconfigure the entry from *Settings → Devices & Services
  → Danalock Bluetooth*. Changing the credentials reloads the entry.
- Individual devices can be deleted from the entry; they are recreated from
  the cloud device list at the next setup.

### Options

Open the integration's *Configure* dialog to manage the background key
refresh:

| Option | Default | Range | Description |
|---|---|---|---|
| `refresh_period_hours` | 12 | 0–720 | Interval between background key refreshes, in hours. `0` disables the background refresh. |
| `refresh_jitter_hours` | 6 | 0–24 | Random delay of up to this many hours added to each interval. `0` disables the jitter. |

## Entities

One device is created per lock with a key. Entity ids are serial-prefixed and
independent of the cloud device name, with the pattern
`<domain>.danalock_ble_<serial>_<suffix>`; friendly names use the cloud device
name when available.

| Platform | Entity id | Category | Enabled by default |
|---|---|---|---|
| `lock` | `lock.danalock_ble_<serial>_lock` | — | yes |
| `binary_sensor` | `binary_sensor.danalock_ble_<serial>_lock_state` | — | yes |
| `binary_sensor` | `binary_sensor.danalock_ble_<serial>_jammed` | diagnostic | yes |
| `binary_sensor` | `binary_sensor.danalock_ble_<serial>_auto_calibrated` | diagnostic | yes |
| `binary_sensor` | `binary_sensor.danalock_ble_<serial>_point_calibrated` | diagnostic | yes |
| `binary_sensor` | `binary_sensor.danalock_ble_<serial>_twist_assist` | diagnostic | no |
| `sensor` | `sensor.danalock_ble_<serial>_battery_level` | diagnostic | yes |
| `sensor` | `sensor.danalock_ble_<serial>_signal_strength` | diagnostic | yes |
| `sensor` | `sensor.danalock_ble_<serial>_rssi` | diagnostic | yes |
| `sensor` | `sensor.danalock_ble_<serial>_update_counter` | diagnostic | yes |
| `sensor` | `sensor.danalock_ble_<serial>_key_valid_until` | diagnostic | yes |
| `update` | `update.danalock_ble_<serial>_firmware` | diagnostic | yes |
| `select` | `select.danalock_ble_<serial>_auto_lock` | config | no |
| `select` | `select.danalock_ble_<serial>_brake_and_go_back` | config | no |
| `select` | `select.danalock_ble_<serial>_blocked_to_blocked` | config | no |

- `lock_state` is the real broadcast state: `on` means unlocked.
- `twist_assist` is the lock's last physical operation — a transient flag, so
  it is disabled by default.
- `signal_strength` is an integer percent; the raw RSSI in dBm rides both as
  its own `rssi` sensor and as an attribute of `signal_strength`.
- `key_valid_until` and `firmware` carry cloud-derived values.
- Select options are stable slug keys for automations (`off`, `5_s`, `one`,
  …); the human-readable names come from the integration translations:
  - `auto_lock` — `off` or `5_s` … `900_s` (5–900 seconds).
  - `brake_and_go_back` — `off` or `3_s` … `60_s` (3–60 seconds).
  - `blocked_to_blocked` — `off`, `one`, or `two` (end-to-end mode; the
    historical entity name is kept for stable entity ids).

Entities become `unavailable` after about 5 minutes without a valid
advertisement, except `key_valid_until` and `firmware`, which are
cloud-derived.

## Actions

The integration exposes one action:

### `danalock_ble.check_firmware_updates`

Checks the installed firmware of the targeted Danalock locks against the
latest published version.

```yaml
action: danalock_ble.check_firmware_updates
target:
  entity_id: update.danalock_ble_<serial>_firmware
```

Calling the core `homeassistant.update_entity` action on a firmware entity
also triggers an immediate check.

## Automation examples

Lock the door when everyone leaves and unlock it when someone arrives:

```yaml
automation:
  - alias: Lock the door when everyone leaves, unlock on arrival
    triggers:
      - trigger: state
        entity_id: group.family
        to: "not_home"
        id: away
      - trigger: state
        entity_id: group.family
        to: "home"
        id: arrival
    actions:
      - choose:
          - conditions:
              - condition: trigger
                id: away
            sequence:
              - action: lock.lock
                target:
                  entity_id: lock.danalock_ble_<serial>_lock
          - conditions:
              - condition: trigger
                id: arrival
            sequence:
              - action: lock.unlock
                target:
                  entity_id: lock.danalock_ble_<serial>_lock
```

Get a notification when the lock reports a jam:

```yaml
automation:
  - alias: Warn when the lock is jammed
    triggers:
      - trigger: state
        entity_id: binary_sensor.danalock_ble_<serial>_jammed
        to: "on"
    actions:
      - action: notify.persistent_notification
        data:
          title: Danalock
          message: The lock reports a jam.
```

Monitor the battery level and radio signal on a dashboard:

```yaml
type: entities
title: Danalock
entities:
  - entity: sensor.danalock_ble_<serial>_battery_level
  - entity: sensor.danalock_ble_<serial>_signal_strength
  - entity: sensor.danalock_ble_<serial>_rssi
```

## Troubleshooting

| Symptom | What to do |
|---|---|
| *Invalid username or password* | Check your Danalock cloud credentials. |
| *Failed to connect to the Danalock cloud* | Check the network connectivity of your Home Assistant instance and retry. |
| *No devices with keys were found* | Your account has no devices, or the devices have no key grants. Verify in the official Danalock app that the locks are visible and shared with your account. |
| A command fails and the entity stays `unavailable` | The lock must advertise at least once so its Bluetooth address becomes known. Move it within radio range and wait for an advertisement. |
| A command fails with `Failed to connect` | The lock must be within radio range when the command runs; try again after the next advertisement. |
| The cloud is unreachable at startup | Devices stay unavailable until the cloud is reachable again. Check connectivity and reload the entry. |
| The entry asks you to log in again | The stored refresh token was rejected; re-authenticate from the entry menu. |
| A lock rejects the login token | Reload the config entry to fetch a fresh key from the cloud. |
| A setting shows `unknown` | The lock's advertisements carry only an on/off flag per setting, never the configured seconds or mode. The value is read over GATT when the select is enabled and after every write. |
| Using a different cloud account | Reconfigure or re-authenticate the entry, or remove it and add it again. |

## Known limitations

- Commands need a fresh advertisement first: a lock that has never advertised
  since Home Assistant started cannot be addressed yet.
- The Danalock cloud must be reachable on Home Assistant start; a restart
  without internet access delays the devices until the cloud is reachable
  again.
- A lock can reject the login token; reload the entry to fetch a fresh key.
- Advertisements carry only an on/off flag per setting, not the configured
  seconds or mode, so a value changed by another authority may show the last
  known value until the next GATT read.

## Security

> **Security notice.** The BLE session to the lock currently runs in insecure
> mode, without certificate-chain verification. Verifying the chain would
> require shipping the lock's certificate/CA inside this public repository,
> which we consider a bad idea (it is secret device material). The identity of
> the lock is still checked against its serial number, but an attacker
> positioned between Home Assistant and the lock could impersonate the lock
> and capture the session login token. Use the control features only on radio
> environments you trust.

## Roadmap

Planned work — not implemented yet, no timeline or version promised:

- **Manual calibration.** Automatic calibration does not always produce a
  satisfactory result; a manual calibration flow is planned.
- **Diagnostics support** (a quality-scale gold step) is planned.

## Removal

- *Settings → Devices & Services → Danalock Bluetooth → three-dot menu →
  Delete*. This removes the config entry and its stored tokens.
- To uninstall the integration completely, delete the
  `custom_components/danalock_ble/` directory and restart Home Assistant.

## Development

- Spec-first development: the specifications live in [`specs/`](specs/).
- Test-driven development: tests live in `tests/` and run with
  `pytest -m "not live"`; hardware-dependent tests carry the `live` marker
  and are run manually only.
- Contribution rules and the review gate are described in
  [`AGENTS.md`](AGENTS.md).
- CI runs `hassfest` and `hacs/action` on every push and pull request — see
  [`.github/workflows/validate.yml`](.github/workflows/validate.yml).

## Related projects

- [`pydanalock-ble`](https://github.com/buggy-shep/pydanalock-ble) — BLE
  client library (protocol, frames, RPD/TLS, advertising).
- [`pydanalock-cloud`](https://github.com/buggy-shep/pydanalock-cloud) —
  Danalock cloud API client (OAuth2, devices, keys).

Both are consumed by this integration and installed from PyPI.

## License

[MIT](LICENSE)
