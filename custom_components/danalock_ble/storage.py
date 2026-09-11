"""Token persistence over Home Assistant storage (spec 0001 R5).

The pydanalock-cloud library persists tokens through its sync `TokenStorage`
protocol; the integration supplies this backend. Reads are served from
memory (pre-loaded at setup); writes are persisted fire-and-forget so the
sync protocol never blocks the event loop. The per-entry `Store` key is
derived from the entry id, never from the username.
"""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from custom_components.danalock_ble.const import TOKENS_STORE_VERSION
from pydanalock.cloud import TokenData, TokenStorage


def token_store_key(entry_id: str) -> str:
    """Storage key for one entry's tokens."""
    return f"danalock_ble_tokens_{entry_id}"


def _token_to_dict(token: TokenData) -> dict[str, Any]:
    return {
        "access_token": token.access_token,
        "refresh_token": token.refresh_token,
        "expires_at": token.expires_at,
    }


def _token_from_dict(data: Any) -> TokenData | None:
    """Rebuild a token pair from stored JSON; malformed data reads as empty."""
    if not isinstance(data, dict):
        return None
    try:
        return TokenData(
            access_token=data["access_token"],
            refresh_token=data["refresh_token"],
            expires_at=float(data["expires_at"]),
        )
    except (KeyError, TypeError, ValueError):
        return None


class DanalockTokenStorage(TokenStorage):
    """Sync TokenStorage protocol backed by an HA Store (spec 0001 R5)."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._hass = hass
        self._store: Store[dict[str, Any]] = Store(
            hass, TOKENS_STORE_VERSION, token_store_key(entry_id)
        )
        self._token: TokenData | None = None

    async def async_load(self) -> None:
        """Load persisted tokens into memory; called once at setup."""
        data = await self._store.async_load()
        self._token = _token_from_dict(data)

    async def async_save(self, token: TokenData) -> None:
        """Persist a token pair and update memory, awaited.

        Used where the caller must be sure the tokens are on disk before
        continuing (seeding a fresh entry, reauth).
        """
        self._token = token
        await self._store.async_save(_token_to_dict(token))

    async def async_remove(self) -> None:
        """Drop memory and persisted state, awaited (entry removal)."""
        self._token = None
        await self._store.async_remove()

    # Sync TokenStorage protocol consumed by the pydanalock-cloud library.

    def load(self) -> TokenData | None:
        """Return the in-memory token pair."""
        return self._token

    def save(self, token: TokenData) -> None:
        """Record a token pair and persist it fire-and-forget.

        Must run inside the event loop (the library only calls it from
        async code paths).
        """
        self._token = token
        self._hass.async_create_task(self._store.async_save(_token_to_dict(token)))

    def clear(self) -> None:
        """Drop memory and persisted state fire-and-forget."""
        self._token = None
        self._hass.async_create_task(self._store.async_remove())
