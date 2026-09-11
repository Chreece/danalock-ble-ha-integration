"""Live tests against the production cloud and the real lock. Run manually
only:

    .venv/bin/pytest -m live

Credentials come from the environment (DANALOCK_USERNAME,
DANALOCK_PASSWORD; DANALOCK_LOCK_SERIAL, DANALOCK_LOCK_ADDRESS for the lock
tests). Never print or log tokens or key material here.

The lock tests physically actuate the lock (unlock/lock): run them only
after explicit confirmation in the conversation (AGENTS.md §10).
"""

from __future__ import annotations

import os

import pytest

from pydanalock.ble import DanalockLock
from pydanalock.cloud import AsyncDanalockCloud

_USERNAME = os.environ.get("DANALOCK_USERNAME")
_PASSWORD = os.environ.get("DANALOCK_PASSWORD")
_LOCK_SERIAL = os.environ.get("DANALOCK_LOCK_SERIAL")
_LOCK_ADDRESS = os.environ.get("DANALOCK_LOCK_ADDRESS")

requires_credentials = pytest.mark.skipif(
    not (_USERNAME and _PASSWORD),
    reason="DANALOCK_USERNAME/DANALOCK_PASSWORD are not set",
)

requires_lock = pytest.mark.skipif(
    not (_USERNAME and _PASSWORD and _LOCK_SERIAL and _LOCK_ADDRESS),
    reason=(
        "DANALOCK_USERNAME/DANALOCK_PASSWORD/DANALOCK_LOCK_SERIAL/"
        "DANALOCK_LOCK_ADDRESS are not set"
    ),
)


@pytest.mark.live
@requires_credentials
async def test_live_login_devices_and_keys() -> None:
    """Login → devices → keys against the real cloud (spec 0001)."""
    client = AsyncDanalockCloud()
    try:
        await client.login_password(_USERNAME, _PASSWORD)
        summaries = await client.devices()
        assert summaries
        for summary in summaries:
            key = await client.get_key(summary.serial)
            assert key.serial == summary.serial
            assert key.login_blob
            assert len(key.broadcast_key) == 16
    finally:
        await client.aclose()


@pytest.mark.live
@requires_lock
async def test_live_connect_state_unlock_and_lock() -> None:
    """Connect to the real lock, then unlock and lock it (spec 0006 R8).

    This physically actuates the lock; manual run only, after explicit user
    confirmation.
    """
    client = AsyncDanalockCloud()
    try:
        await client.login_password(_USERNAME, _PASSWORD)
        key = await client.get_key(_LOCK_SERIAL)
    finally:
        await client.aclose()

    lock = DanalockLock(
        _LOCK_ADDRESS,
        serial=bytes.fromhex(_LOCK_SERIAL),
        login_token=key.login_blob,
        insecure=True,
    )
    try:
        assert await lock.state() is not None
        await lock.unlock()
        await lock.lock()
    finally:
        await lock.disconnect()
