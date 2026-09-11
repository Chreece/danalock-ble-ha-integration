"""Config flow for the Danalock Bluetooth integration (specs 0001, 0007,
0010).

The flow validates the cloud login and key retrieval before creating the
entry. Entry identity is the username, normalized with `strip().lower()`
(email normalization per the HA config-flow rules); the username appears
only in the entry unique_id, entry data, and entry title. The options flow
configures the background key refresh (period and jitter, in hours).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import httpx
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import HomeAssistant, callback

from custom_components.danalock_ble.const import (
    CONF_PASSWORD,
    CONF_REFRESH_JITTER_HOURS,
    CONF_REFRESH_PERIOD_HOURS,
    CONF_USERNAME,
    DEFAULT_REFRESH_JITTER_HOURS,
    DEFAULT_REFRESH_PERIOD_HOURS,
    DOMAIN,
    LEGACY_REFRESH_JITTER_MIN,
    LEGACY_REFRESH_PERIOD_MIN,
    MAX_REFRESH_JITTER_HOURS,
    MAX_REFRESH_PERIOD_HOURS,
    PENDING_TOKENS,
    option_refresh_hours,
)
from pydanalock.cloud import (
    AuthError,
    DanalockCloudError,
    MemoryTokenStorage,
    TokenData,
)
from custom_components.danalock_ble.storage import DanalockTokenStorage

from . import new_cloud_client

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
    }
)


class _ValidationError(Exception):
    """A validation outcome that maps to a typed flow error base."""

    def __init__(self, base: str) -> None:
        super().__init__(base)
        self.base = base


def normalized_username(username: str) -> str:
    """Entry identity form of a username (spec 0001 R6)."""
    return username.strip().lower()


def stage_pending_tokens(hass: HomeAssistant, unique_id: str, token: TokenData) -> None:
    """Hand validated tokens to the first setup (no entry store exists yet).

    The stage is consumed by `async_setup_entry`; `async_remove_entry`
    drops leftovers for entries that never reached setup.
    """
    hass.data.setdefault(DOMAIN, {}).setdefault(PENDING_TOKENS, {})[unique_id] = token


class DanalockConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the Danalock cloud login config flow."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> DanalockOptionsFlow:
        """Create the options flow handler (spec 0007 R8)."""
        return DanalockOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial login step."""
        errors: dict[str, str] = {}
        if user_input is not None:
            username = user_input[CONF_USERNAME].strip()
            unique_id = normalized_username(username)
            await self.async_set_unique_id(unique_id)
            self._abort_if_unique_id_configured()
            try:
                token = await self._async_validate(
                    username, user_input[CONF_PASSWORD]
                )
            except _ValidationError as err:
                errors["base"] = err.base
            else:
                stage_pending_tokens(self.hass, unique_id, token)
                return self.async_create_entry(title=username, data={"username": username})
        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Start reauthentication after a ConfigEntryAuthFailed."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for credentials again and update the existing entry."""
        return await self._async_revalidation_step(
            self._get_reauth_entry(), "reauth_confirm", user_input
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Revalidate the account and update the existing entry."""
        return await self._async_revalidation_step(
            self._get_reconfigure_entry(), "reconfigure", user_input
        )

    async def _async_revalidation_step(
        self,
        entry: config_entries.ConfigEntry,
        step_id: str,
        user_input: dict[str, Any] | None,
    ) -> ConfigFlowResult:
        """Shared reauth/reconfigure form: revalidate, persist, update.

        The username is normalized the same way as in the user step, so the
        dedupe fires regardless of input case or surrounding whitespace
        (spec 0001 R4/R6). A changed username moves the unique_id.
        """
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_USERNAME, default=entry.data.get(CONF_USERNAME, "")
                ): str,
                vol.Required(CONF_PASSWORD): str,
            }
        )
        errors: dict[str, str] = {}
        if user_input is not None:
            username = user_input[CONF_USERNAME].strip()
            unique_id = normalized_username(username)
            if unique_id != entry.unique_id:
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured()
            try:
                token = await self._async_validate(
                    username, user_input[CONF_PASSWORD]
                )
            except _ValidationError as err:
                errors["base"] = err.base
            else:
                return await self._async_update_after_revalidation(
                    entry, username, token
                )
        return self.async_show_form(step_id=step_id, data_schema=schema, errors=errors)

    async def _async_validate(self, username: str, password: str) -> TokenData:
        """Run login and key retrieval with the real library.

        Returns the validated token pair; failures map to typed error bases
        (spec 0001 R1). The client is always closed, also on failure.
        """
        memory = MemoryTokenStorage()
        client = await new_cloud_client(self.hass, memory)
        try:
            try:
                await client.login_password(username, password)
                summaries = await client.devices()
            except AuthError as err:
                raise _ValidationError("invalid_auth") from err
            except (httpx.HTTPError, DanalockCloudError) as err:
                raise _ValidationError("cannot_connect") from err
            except Exception as err:  # noqa: BLE001 - surfaced as "unknown"
                raise _ValidationError("unknown") from err
            if not summaries:
                raise _ValidationError("no_keys")
        finally:
            await client.aclose()
        return memory.load()  # login saved a token; reaching here proves it

    async def _async_update_after_revalidation(
        self,
        entry: config_entries.ConfigEntry,
        username: str,
        token: TokenData,
    ) -> ConfigFlowResult:
        """Persist fresh tokens and update the existing entry in place.

        The tokens are saved with an awaited store write before the reload
        is scheduled, so setup always sees them. A changed username moves
        the unique_id (dedupe was checked by the caller).
        """
        await DanalockTokenStorage(self.hass, entry.entry_id).async_save(token)
        unique_id = normalized_username(username)
        if unique_id != entry.unique_id:
            return self.async_update_reload_and_abort(
                entry,
                unique_id=unique_id,
                title=username,
                data={"username": username},
            )
        return self.async_update_reload_and_abort(
            entry,
            title=username,
            data={"username": username},
        )


class DanalockOptionsFlow(OptionsFlow):
    """Handle the background key-refresh options (specs 0007 R8, 0010 R5)."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the key refresh options (hours)."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        options = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_REFRESH_PERIOD_HOURS,
                    default=option_refresh_hours(
                        options,
                        CONF_REFRESH_PERIOD_HOURS,
                        DEFAULT_REFRESH_PERIOD_HOURS,
                        LEGACY_REFRESH_PERIOD_MIN,
                    ),
                ): vol.All(
                    vol.Coerce(int),
                    vol.Range(min=0, max=MAX_REFRESH_PERIOD_HOURS),
                ),
                vol.Required(
                    CONF_REFRESH_JITTER_HOURS,
                    default=option_refresh_hours(
                        options,
                        CONF_REFRESH_JITTER_HOURS,
                        DEFAULT_REFRESH_JITTER_HOURS,
                        LEGACY_REFRESH_JITTER_MIN,
                    ),
                ): vol.All(
                    vol.Coerce(int),
                    vol.Range(min=0, max=MAX_REFRESH_JITTER_HOURS),
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
