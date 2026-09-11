"""Config flow tests for the danalock integration (spec 0001).

Every scenario drives the real pydanalock.cloud library through an
httpx.MockTransport handler with synthetic fixtures.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType, InvalidData
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.danalock_ble.const import (
    DEFAULT_REFRESH_JITTER_HOURS,
    DEFAULT_REFRESH_PERIOD_HOURS,
    DOMAIN,
)
from tests.conftest import (
    PASSWORD,
    USERNAME,
    CloudHandler,
    patch_cloud_transport,
    stage_pending_tokens,
)


async def start_user_flow(hass: HomeAssistant) -> dict:
    """Start a user config flow and return the shown form."""
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )


async def submit(hass: HomeAssistant, result: dict, data: dict) -> dict:
    """Submit form input and settle the hass instance."""
    result = await hass.config_entries.flow.async_configure(result["flow_id"], data)
    await hass.async_block_till_done()
    return result


async def submit_options(hass: HomeAssistant, result: dict, data: dict) -> dict:
    """Submit options flow input and settle the hass instance."""
    result = await hass.config_entries.options.async_configure(result["flow_id"], data)
    await hass.async_block_till_done()
    return result


def single_entry(hass: HomeAssistant) -> MockConfigEntry:
    """The only danalock entry of the test instance."""
    entries = hass.config_entries.async_entries(DOMAIN)
    assert len(entries) == 1
    return entries[0]


def make_wired_entry(hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch, handler) -> MockConfigEntry:
    """A registered entry backed by the mock handler, set up with staged tokens."""
    patch_cloud_transport(monkeypatch, handler)
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id=USERNAME, data={"username": USERNAME}, title=USERNAME
    )
    entry.add_to_hass(hass)
    stage_pending_tokens(hass, USERNAME)
    return entry


async def test_happy_path_creates_entry_and_loads(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Login + keys succeed: entry created with username data and unique_id."""
    handler = CloudHandler()
    patch_cloud_transport(monkeypatch, handler)

    result = await start_user_flow(hass)
    assert result["type"] == FlowResultType.FORM
    result = await submit(hass, result, {"username": USERNAME, "password": PASSWORD})

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == USERNAME
    assert result["data"] == {"username": USERNAME}
    entry = single_entry(hass)
    assert entry.unique_id == USERNAME
    assert entry.state is ConfigEntryState.LOADED
    # tokens landed in the entry token store, not in entry.data
    stored = hass_storage[f"danalock_ble_tokens_{entry.entry_id}"]["data"]
    assert stored["access_token"]
    assert stored["refresh_token"]
    # the password was never persisted anywhere
    assert PASSWORD not in json.dumps(hass_storage)
    assert PASSWORD not in json.dumps(dict(entry.data))


async def test_invalid_credentials_shows_invalid_auth(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A rejected password grant re-shows the form with invalid_auth."""
    handler = CloudHandler(token_status=400)
    patch_cloud_transport(monkeypatch, handler)

    result = await start_user_flow(hass)
    result = await submit(hass, result, {"username": USERNAME, "password": PASSWORD})

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}
    assert hass.config_entries.async_entries(DOMAIN) == []


async def test_unreachable_cloud_shows_cannot_connect(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A transport failure during key retrieval shows cannot_connect."""
    handler = CloudHandler(devices_error=httpx.ConnectError("connection refused"))
    patch_cloud_transport(monkeypatch, handler)

    result = await start_user_flow(hass)
    result = await submit(hass, result, {"username": USERNAME, "password": PASSWORD})

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}
    assert hass.config_entries.async_entries(DOMAIN) == []


async def test_api_error_shows_cannot_connect(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A non-200 token endpoint answer shows cannot_connect."""
    handler = CloudHandler(token_status=500)
    patch_cloud_transport(monkeypatch, handler)

    result = await start_user_flow(hass)
    result = await submit(hass, result, {"username": USERNAME, "password": PASSWORD})

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}
    assert hass.config_entries.async_entries(DOMAIN) == []


async def test_no_devices_shows_no_keys(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An account without devices shows no_keys and creates no entry."""
    handler = CloudHandler(devices_payload=[])
    patch_cloud_transport(monkeypatch, handler)

    result = await start_user_flow(hass)
    result = await submit(hass, result, {"username": USERNAME, "password": PASSWORD})

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "no_keys"}
    assert hass.config_entries.async_entries(DOMAIN) == []


async def test_unexpected_error_shows_unknown(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An untyped failure shows unknown instead of crashing the flow."""
    handler = CloudHandler(unexpected_error=RuntimeError("boom"))
    patch_cloud_transport(monkeypatch, handler)

    result = await start_user_flow(hass)
    result = await submit(hass, result, {"username": USERNAME, "password": PASSWORD})

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "unknown"}
    assert hass.config_entries.async_entries(DOMAIN) == []


@pytest.mark.parametrize(
    "username", [USERNAME, "USER@EXAMPLE.COM", "  user@example.com  "]
)
async def test_second_login_same_account_aborts(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch, username: str
) -> None:
    """Re-login for the same account aborts, regardless of case/whitespace."""
    handler = CloudHandler()
    patch_cloud_transport(monkeypatch, handler)

    first = await start_user_flow(hass)
    first = await submit(hass, first, {"username": USERNAME, "password": PASSWORD})
    assert first["type"] == FlowResultType.CREATE_ENTRY

    second = await start_user_flow(hass)
    second = await submit(hass, second, {"username": username, "password": PASSWORD})
    assert second["type"] == FlowResultType.ABORT
    assert second["reason"] == "already_configured"
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1


async def test_username_is_normalized_for_unique_id(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Whitespace/case are cleaned for the unique_id; the cloud login uses
    the stripped original, device names never contain the username."""
    handler = CloudHandler()
    patch_cloud_transport(monkeypatch, handler)

    result = await start_user_flow(hass)
    result = await submit(
        hass, result, {"username": "User+Tag@Example.COM ", "password": PASSWORD}
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    entry = single_entry(hass)
    assert entry.unique_id == "user+tag@example.com"
    assert entry.data == {"username": "User+Tag@Example.COM"}
    assert entry.title == "User+Tag@Example.COM"
    assert handler.password_grants[0]["username"] == "User+Tag@Example.COM"


async def test_reauth_flow_updates_tokens(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A reauth flow revalidates and updates the existing entry in place."""
    handler = CloudHandler()
    entry = make_wired_entry(hass, monkeypatch, handler)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    store_key = f"danalock_ble_tokens_{entry.entry_id}"
    assert store_key in hass_storage

    result = await entry.start_reauth_flow(hass)
    assert result["type"] == FlowResultType.FORM
    result = await submit(hass, result, {"username": USERNAME, "password": PASSWORD})

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert single_entry(hass) is entry
    assert entry.unique_id == USERNAME
    assert entry.data == {"username": USERNAME}
    assert entry.state is ConfigEntryState.LOADED
    assert store_key in hass_storage


async def test_reauth_with_new_username_moves_unique_id(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reauth with a changed username updates unique_id and entry data."""
    handler = CloudHandler()
    old = "old@example.com"
    patch_cloud_transport(monkeypatch, handler)
    entry = MockConfigEntry(domain=DOMAIN, unique_id=old, data={"username": old})
    entry.add_to_hass(hass)
    stage_pending_tokens(hass, old)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    patch_cloud_transport(monkeypatch, handler)
    result = await entry.start_reauth_flow(hass)
    result = await submit(hass, result, {"username": USERNAME, "password": PASSWORD})

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.unique_id == USERNAME
    assert entry.data == {"username": USERNAME}
    assert entry.state is ConfigEntryState.LOADED


async def test_reauth_to_taken_username_aborts(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reauth onto an already-configured account aborts."""
    handler = CloudHandler()
    patch_cloud_transport(monkeypatch, handler)
    entries: list[MockConfigEntry] = []
    for username in (USERNAME, "other@example.com"):
        entry = MockConfigEntry(
            domain=DOMAIN, unique_id=username, data={"username": username}, title=username
        )
        entry.add_to_hass(hass)
        stage_pending_tokens(hass, username)
        entries.append(entry)
        assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    patch_cloud_transport(monkeypatch, handler)
    reauth_entry = next(
        e for e in hass.config_entries.async_entries(DOMAIN) if e.unique_id == USERNAME
    )
    result = await reauth_entry.start_reauth_flow(hass)
    result = await submit(
        hass, result, {"username": "other@example.com", "password": PASSWORD}
    )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert len(hass.config_entries.async_entries(DOMAIN)) == 2


async def test_reauth_with_invalid_credentials_reshows_form(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed reauth validation re-shows the form with a typed error."""
    handler = CloudHandler()
    entry = make_wired_entry(hass, monkeypatch, handler)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    patch_cloud_transport(monkeypatch, CloudHandler(token_status=400))
    result = await entry.start_reauth_flow(hass)
    result = await submit(hass, result, {"username": USERNAME, "password": "wrong"})

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"
    assert result["errors"] == {"base": "invalid_auth"}


async def test_reconfigure_with_unreachable_cloud_reshows_form(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed reconfigure validation re-shows the form with a typed error."""
    handler = CloudHandler()
    entry = make_wired_entry(hass, monkeypatch, handler)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    patch_cloud_transport(
        monkeypatch,
        CloudHandler(devices_error=httpx.ConnectError("connection refused")),
    )
    result = await entry.start_reconfigure_flow(hass)
    result = await submit(hass, result, {"username": USERNAME, "password": PASSWORD})

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "reconfigure"
    assert result["errors"] == {"base": "cannot_connect"}


async def test_reconfigure_flow_updates_entry(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A reconfigure flow revalidates and updates the existing entry."""
    handler = CloudHandler()
    entry = make_wired_entry(hass, monkeypatch, handler)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    patch_cloud_transport(monkeypatch, handler)
    result = await entry.start_reconfigure_flow(hass)
    assert result["type"] == FlowResultType.FORM
    result = await submit(hass, result, {"username": USERNAME, "password": PASSWORD})

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert single_entry(hass) is entry
    assert entry.state is ConfigEntryState.LOADED
    assert entry.data == {"username": USERNAME}


# --- options flow (specs 0007, 0010) ------------------------------------------


async def test_options_flow_defaults_are_applied_on_empty_submission(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Submitting the untouched form stores the default options (R8)."""
    handler = CloudHandler()
    entry = make_wired_entry(hass, monkeypatch, handler)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "init"

    result = await submit_options(hass, result, {})

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"] == {
        "refresh_period_hours": DEFAULT_REFRESH_PERIOD_HOURS,
        "refresh_jitter_hours": DEFAULT_REFRESH_JITTER_HOURS,
    }
    assert entry.options == {
        "refresh_period_hours": DEFAULT_REFRESH_PERIOD_HOURS,
        "refresh_jitter_hours": DEFAULT_REFRESH_JITTER_HOURS,
    }


async def test_options_flow_saves_values_and_reloads(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Saved options update the entry and reload it (R8)."""
    handler = CloudHandler()
    entry = make_wired_entry(hass, monkeypatch, handler)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert handler.requests.count(("GET", "/devices/v1/login_tokens")) == 1

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await submit_options(
        hass, result, {"refresh_period_hours": 24, "refresh_jitter_hours": 3}
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"] == {
        "refresh_period_hours": 24,
        "refresh_jitter_hours": 3,
    }
    assert entry.options == {
        "refresh_period_hours": 24,
        "refresh_jitter_hours": 3,
    }
    assert entry.state is ConfigEntryState.LOADED
    # the update listener reloaded the entry
    assert handler.requests.count(("GET", "/devices/v1/login_tokens")) == 2


async def test_options_flow_rejects_out_of_range_values(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Out-of-range hours are rejected with InvalidData; a valid
    resubmission still finishes the flow (R8)."""
    handler = CloudHandler()
    entry = make_wired_entry(hass, monkeypatch, handler)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)

    with pytest.raises(InvalidData):
        await hass.config_entries.options.async_configure(
            result["flow_id"],
            {"refresh_period_hours": 721, "refresh_jitter_hours": 6},
        )
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    with pytest.raises(InvalidData):
        await hass.config_entries.options.async_configure(
            result["flow_id"],
            {"refresh_period_hours": 12, "refresh_jitter_hours": 25},
        )
    await hass.async_block_till_done()

    # a second flow with valid values finishes
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await submit_options(
        hass, result, {"refresh_period_hours": 24, "refresh_jitter_hours": 3}
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert entry.options == {
        "refresh_period_hours": 24,
        "refresh_jitter_hours": 3,
    }


async def test_options_flow_accepts_zero_and_maxima(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0 and the documented maxima are accepted (spec 0010 R5)."""
    handler = CloudHandler()
    entry = make_wired_entry(hass, monkeypatch, handler)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await submit_options(
        hass, result, {"refresh_period_hours": 0, "refresh_jitter_hours": 0}
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert entry.options == {"refresh_period_hours": 0, "refresh_jitter_hours": 0}

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await submit_options(
        hass, result, {"refresh_period_hours": 720, "refresh_jitter_hours": 24}
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert entry.options == {"refresh_period_hours": 720, "refresh_jitter_hours": 24}


async def test_options_flow_prefills_converted_hours_from_legacy_minutes(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An entry saved with minute keys prefills the form with the converted
    hours and stores hour keys only on save (spec 0010 R6)."""
    handler = CloudHandler()
    patch_cloud_transport(monkeypatch, handler)
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=USERNAME,
        data={"username": USERNAME},
        title=USERNAME,
        options={"refresh_period_minutes": 360, "refresh_jitter_minutes": 120},
    )
    entry.add_to_hass(hass)
    stage_pending_tokens(hass, USERNAME)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    # submitting the untouched form stores exactly the effective hours
    result = await submit_options(hass, result, {})

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert entry.options == {
        "refresh_period_hours": 6,
        "refresh_jitter_hours": 2,
    }
    assert "refresh_period_minutes" not in entry.options
    assert "refresh_jitter_minutes" not in entry.options
