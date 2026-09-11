"""Token storage tests over Home Assistant storage (spec 0001 R5)."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

from custom_components.danalock_ble.storage import DanalockTokenStorage
from tests.conftest import make_token


async def test_roundtrip_through_hass_storage(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    """save → load roundtrips through the hass storage backend."""
    token = make_token()
    storage = DanalockTokenStorage(hass, "entry-1")

    await storage.async_save(token)
    assert storage.load() == token

    reloaded = DanalockTokenStorage(hass, "entry-1")
    await reloaded.async_load()
    assert reloaded.load() == token


async def test_sync_save_persists(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    """The sync protocol records the token in memory and persists it."""
    storage = DanalockTokenStorage(hass, "entry-2")
    token = make_token()

    storage.save(token)
    assert storage.load() == token

    await hass.async_block_till_done()
    stored = hass_storage["danalock_ble_tokens_entry-2"]
    assert stored["data"]["access_token"] == token.access_token
    assert stored["data"]["refresh_token"] == token.refresh_token
    assert stored["data"]["expires_at"] == token.expires_at
    assert stored["version"] == 1


async def test_clear_removes_persisted_tokens(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    """clear() drops memory and persisted state."""
    storage = DanalockTokenStorage(hass, "entry-3")
    storage.save(make_token())
    await hass.async_block_till_done()
    assert "danalock_ble_tokens_entry-3" in hass_storage

    storage.clear()
    await hass.async_block_till_done()
    assert storage.load() is None
    assert "danalock_ble_tokens_entry-3" not in hass_storage


async def test_separate_stores_per_entry(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    """Each entry gets its own isolated store keyed by entry_id."""
    first = DanalockTokenStorage(hass, "entry-a")
    second = DanalockTokenStorage(hass, "entry-b")
    token_a = make_token(access="token-a")
    token_b = make_token(access="token-b")

    await first.async_save(token_a)
    await second.async_save(token_b)

    assert first.load() == token_a
    assert second.load() == token_b


async def test_malformed_store_data_reads_as_empty(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    """Corrupt persisted data is treated as "no tokens", never as a crash."""
    hass_storage["danalock_ble_tokens_entry-4"] = {
        "version": 1,
        "data": {"access_token": 42, "refresh_token": None},
    }

    storage = DanalockTokenStorage(hass, "entry-4")
    await storage.async_load()

    assert storage.load() is None
