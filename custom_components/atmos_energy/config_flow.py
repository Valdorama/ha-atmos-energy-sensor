"""Config flow for Atmos Energy integration."""
import logging
from typing import Any
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.const import CONF_USERNAME, CONF_PASSWORD
from homeassistant.helpers import selector

from .const import (
    DOMAIN, 
    CONF_FIXED_COST, 
    CONF_USAGE_RATE, 
    CONF_TAX_PERCENT, 
    CONF_WEATHER_ENTITY,
    CONF_DAILY_USAGE,
    CONF_WEATHER_STATION,
    CONF_GCR_RATE,
    CONF_URI_SURCHARGE,
    CONF_AUTO_FETCH_GCR
)
from .api import AtmosEnergyApiClient
from .exceptions import AuthenticationError, APIError
from .wna.ndd_data import WEATHER_STATION_CONFIG

_LOGGER = logging.getLogger(__name__)

class AtmosEnergyConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Atmos Energy."""

    VERSION = 1
    
    def __init__(self):
        """Initialize."""
        self._user_data = {}
        self._options = {}

    async def async_step_user(self, user_input=None):
        """Handle the initial step (Credentials)."""
        errors = {}

        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_USERNAME])
            self._abort_if_unique_id_configured()

            username = user_input[CONF_USERNAME]
            password = user_input[CONF_PASSWORD]

            client = AtmosEnergyApiClient(username, password, source="setup")
            
            try:
                await client.login()
                self._user_data = user_input
                if user_input.get(CONF_DAILY_USAGE, True):
                    return await self.async_step_weather_station()
                
                return self.async_create_entry(
                    title=self._user_data[CONF_USERNAME], 
                    data=self._user_data,
                    options={}
                )
            except AuthenticationError:
                errors["base"] = "invalid_auth"
            except (APIError, Exception) as err:
                _LOGGER.exception("Unexpected error during authentication: %s", err)
                errors["base"] = "cannot_connect"
            finally:
                await client.close()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_USERNAME): str,
                    vol.Required(CONF_PASSWORD): str,
                    vol.Required(CONF_DAILY_USAGE, default=True): bool,
                }
            ),
            errors=errors,
        )

    async def async_step_weather_station(self, user_input=None):
        """Step 2: Select weather station."""
        if user_input is not None:
            self._options.update(user_input)
            return await self.async_step_cost()
        
        stations = {id: info["name"] for id, info in WEATHER_STATION_CONFIG.items()}
        return self.async_show_form(
            step_id="weather_station",
            data_schema=vol.Schema({
                vol.Required(CONF_WEATHER_STATION, default="austin"): vol.In(stations),
            }),
        )

    async def async_step_cost(self, user_input=None):
        """Step 3: Handle choosing cost parameters."""
        if user_input is not None:
            self._options.update(user_input)
            return await self.async_step_weather_forecast()

        return self.async_show_form(
            step_id="cost",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_FIXED_COST, default=25.03): vol.All(vol.Coerce(float), vol.Range(min=0)),
                    vol.Required(CONF_USAGE_RATE, default=0.78): vol.All(vol.Coerce(float), vol.Range(min=0)),
                    vol.Required(CONF_AUTO_FETCH_GCR, default=True): bool,
                    vol.Required(CONF_GCR_RATE, default=1.17): vol.All(vol.Coerce(float), vol.Range(min=0)),
                    vol.Required(CONF_URI_SURCHARGE, default=0.018431): vol.All(vol.Coerce(float), vol.Range(min=0)),
                    vol.Required(CONF_TAX_PERCENT, default=8.0): vol.All(vol.Coerce(float), vol.Range(min=0, max=100)),
                }
            ),
        )

    async def async_step_weather_forecast(self, user_input=None):
        """Step 4: Select weather entity."""
        if user_input is not None:
            self._options.update(user_input)
            return self.async_create_entry(
                title=self._user_data[CONF_USERNAME], 
                data=self._user_data,
                options=self._options
            )

        return self.async_show_form(
            step_id="weather_forecast",
            data_schema=vol.Schema({
                vol.Optional(CONF_WEATHER_ENTITY): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="weather")
                ),
            }),
        )

    async def async_step_reauth(self, user_input=None):
        """Handle reauth flow."""
        errors = {}
        if user_input is not None:
            username = self._get_reauth_entry().data[CONF_USERNAME]
            password = user_input[CONF_PASSWORD]
            client = AtmosEnergyApiClient(username, password, source="reauth")
            try:
                await client.login()
                self.hass.config_entries.async_update_entry(
                    self._get_reauth_entry(),
                    data={**self._get_reauth_entry().data, CONF_PASSWORD: password}
                )
                await self.hass.config_entries.async_reload(self._get_reauth_entry().entry_id)
                return self.async_abort(reason="reauth_successful")
            except AuthenticationError:
                errors["base"] = "invalid_auth"
            except Exception:
                errors["base"] = "unknown"
            finally:
                await client.close()
        
        return self.async_show_form(
            step_id="reauth",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}),
            errors=errors,
            description_placeholders={"username": self._get_reauth_entry().data[CONF_USERNAME]},
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Get the options flow for this handler."""
        return AtmosEnergyOptionsFlowHandler(config_entry)


class AtmosEnergyOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options (reconfiguration)."""

    def __init__(self, config_entry):
        """Initialize options flow."""
        self._config_entry = config_entry
        self._options = dict(config_entry.options)
        self._data = dict(config_entry.data)

    async def async_step_init(self, user_input=None):
        """Step 1: Credentials and Toggle."""
        errors = {}
        if user_input is not None:
            # Update local state
            self._data.update({
                CONF_USERNAME: user_input[CONF_USERNAME],
                CONF_PASSWORD: user_input[CONF_PASSWORD],
                CONF_DAILY_USAGE: user_input[CONF_DAILY_USAGE],
            })
            
            # Validate credentials
            client = AtmosEnergyApiClient(self._data[CONF_USERNAME], self._data[CONF_PASSWORD], source="options")
            try:
                await client.login()
                
                # Sync data back to the config entry if credentials changed
                self.hass.config_entries.async_update_entry(self._config_entry, data=self._data)
                
                if self._data[CONF_DAILY_USAGE]:
                    return await self.async_step_weather_station()
                
                return self.async_create_entry(title="", data={})
            except AuthenticationError:
                errors["base"] = "invalid_auth"
            except Exception:
                errors["base"] = "cannot_connect"
            finally:
                await client.close()

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Required(CONF_USERNAME, default=self._data.get(CONF_USERNAME)): str,
                vol.Required(CONF_PASSWORD, default=self._data.get(CONF_PASSWORD)): str,
                vol.Required(CONF_DAILY_USAGE, default=self._data.get(CONF_DAILY_USAGE, True)): bool,
            }),
            errors=errors,
        )

    async def async_step_weather_station(self, user_input=None):
        """Step 2: Region."""
        if user_input is not None:
            self._options.update(user_input)
            return await self.async_step_cost()
        
        stations = {id: info["name"] for id, info in WEATHER_STATION_CONFIG.items()}
        return self.async_show_form(
            step_id="weather_station",
            data_schema=vol.Schema({
                vol.Required(
                    CONF_WEATHER_STATION, 
                    default=self._options.get(CONF_WEATHER_STATION, "austin")
                ): vol.In(stations),
            }),
        )

    async def async_step_cost(self, user_input=None):
        """Step 3: Rates."""
        if user_input is not None:
            self._options.update(user_input)
            return await self.async_step_weather_forecast()

        return self.async_show_form(
            step_id="cost",
            data_schema=vol.Schema({
                vol.Required(CONF_FIXED_COST, default=self._options.get(CONF_FIXED_COST, 25.03)): vol.All(vol.Coerce(float), vol.Range(min=0)),
                vol.Required(CONF_USAGE_RATE, default=self._options.get(CONF_USAGE_RATE, 0.78)): vol.All(vol.Coerce(float), vol.Range(min=0)),
                vol.Required(CONF_AUTO_FETCH_GCR, default=self._options.get(CONF_AUTO_FETCH_GCR, True)): bool,
                vol.Required(CONF_GCR_RATE, default=self._options.get(CONF_GCR_RATE, 1.17)): vol.All(vol.Coerce(float), vol.Range(min=0)),
                vol.Required(CONF_URI_SURCHARGE, default=self._options.get(CONF_URI_SURCHARGE, 0.018431)): vol.All(vol.Coerce(float), vol.Range(min=0)),
                vol.Required(CONF_TAX_PERCENT, default=self._options.get(CONF_TAX_PERCENT, 8.0)): vol.All(vol.Coerce(float), vol.Range(min=0, max=100)),
            }),
        )

    async def async_step_weather_forecast(self, user_input=None):
        """Step 4: Weather Entity."""
        if user_input is not None:
            self._options.update(user_input)
            # Finish and save all options
            return self.async_create_entry(title="", data=self._options)

        return self.async_show_form(
            step_id="weather_forecast",
            data_schema=vol.Schema({
                vol.Optional(
                    CONF_WEATHER_ENTITY,
                    description={"suggested_value": self._options.get(CONF_WEATHER_ENTITY)}
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="weather")
                ),
            }),
        )
