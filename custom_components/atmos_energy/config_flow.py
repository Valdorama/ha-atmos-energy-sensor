"""Config flow for Atmos Energy integration."""
import logging
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.const import CONF_USERNAME, CONF_PASSWORD
import homeassistant.helpers.config_validation as cv

from homeassistant.helpers import selector
from .const import (
    DOMAIN, 
    CONF_FIXED_COST, 
    CONF_USAGE_RATE, 
    CONF_TAX_PERCENT, 
    CONF_WEATHER_ENTITY,
    CONF_DAILY_USAGE
)
from .api import AtmosEnergyApiClient
from .exceptions import AuthenticationError, APIError

_LOGGER = logging.getLogger(__name__)

class AtmosEnergyConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Atmos Energy."""

    VERSION = 1
    
    def __init__(self):
        """Initialize."""
        self._user_data = {}

    async def async_step_user(self, user_input=None):
        """Handle the initial step."""
        errors = {}

        if user_input is not None:
            # Prevent duplicates
            await self.async_set_unique_id(user_input[CONF_USERNAME])
            self._abort_if_unique_id_configured()

            # Validate credentials
            username = user_input[CONF_USERNAME]
            password = user_input[CONF_PASSWORD]

            client = AtmosEnergyApiClient(username, password, source="setup")
            
            try:
                await client.login()
                self._user_data = user_input
                if user_input.get(CONF_DAILY_USAGE, True):
                    return await self.async_step_cost()
                
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

    async def async_step_cost(self, user_input=None):
        """Handle choosing cost parameters."""
        if user_input is not None:
            return self.async_create_entry(
                title=self._user_data[CONF_USERNAME], 
                data=self._user_data,
                options=user_input
            )

        return self.async_show_form(
            step_id="cost",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_FIXED_COST, default=25.03): vol.All(
                        vol.Coerce(float), vol.Range(min=0, max=1000)
                    ),
                    vol.Required(CONF_USAGE_RATE, default=2.40): vol.All(
                        vol.Coerce(float), vol.Range(min=0, max=100)
                    ),
                    vol.Required(CONF_TAX_PERCENT, default=8.0): vol.All(
                        vol.Coerce(float), vol.Range(min=0, max=100)
                    ),
                    vol.Optional(CONF_WEATHER_ENTITY): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain="weather")
                    ),
                }
            ),
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
                _LOGGER.exception("Unexpected error during reauth")
                errors["base"] = "unknown"
            finally:
                await client.close()
        
        return self.async_show_form(
            step_id="reauth",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}),
            errors=errors,
            description_placeholders={
                "username": self._get_reauth_entry().data[CONF_USERNAME]
            },
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Get the options flow for this handler."""
        return AtmosEnergyOptionsFlowHandler(config_entry)


class AtmosEnergyOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options."""

    def __init__(self, config_entry):
        """Initialize options flow."""
        self._config_entry = config_entry

    async def async_step_init(self, user_input=None):
        """Manage the options."""
        errors = {}
        if user_input is not None:
            # Validate credentials if they changed
            username = user_input.get(CONF_USERNAME)
            password = user_input.get(CONF_PASSWORD)
            
            client = AtmosEnergyApiClient(username, password, source="options")
            try:
                await client.login()
                # If credentials changed, update the config entry data as well
                if (username != self._config_entry.data.get(CONF_USERNAME) or 
                    password != self._config_entry.data.get(CONF_PASSWORD)):
                    self.hass.config_entries.async_update_entry(
                        self._config_entry,
                        data={
                            **self._config_entry.data,
                            CONF_USERNAME: username,
                            CONF_PASSWORD: password,
                        }
                    )
                return self.async_create_entry(title="", data=user_input)
            except AuthenticationError:
                errors["base"] = "invalid_auth"
            except Exception:
                _LOGGER.exception("Unexpected error during options validation")
                errors["base"] = "cannot_connect"
            finally:
                await client.close()

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_USERNAME,
                        default=self._config_entry.data.get(CONF_USERNAME)
                    ): str,
                    vol.Required(
                        CONF_PASSWORD,
                        default=self._config_entry.data.get(CONF_PASSWORD)
                    ): str,
                    vol.Required(
                        CONF_FIXED_COST,
                        default=self._config_entry.options.get(CONF_FIXED_COST, 25.03)
                    ): vol.All(vol.Coerce(float), vol.Range(min=0, max=1000)),
                    vol.Required(
                        CONF_USAGE_RATE, 
                        default=self._config_entry.options.get(CONF_USAGE_RATE, 2.40)
                    ): vol.All(vol.Coerce(float), vol.Range(min=0, max=100)),
                    vol.Required(
                        CONF_TAX_PERCENT, 
                        default=self._config_entry.options.get(CONF_TAX_PERCENT, 8.0)
                    ): vol.All(vol.Coerce(float), vol.Range(min=0, max=100)),
                    vol.Optional(
                        CONF_WEATHER_ENTITY,
                        description={"suggested_value": self._config_entry.options.get(CONF_WEATHER_ENTITY)}
                    ): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain="weather")
                    ),
                }
            ),
            errors=errors,
        )
