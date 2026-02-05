"""Config flow for Atmos Energy integration."""
import logging
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.const import CONF_USERNAME, CONF_PASSWORD
import homeassistant.helpers.config_validation as cv

from .const import DOMAIN
from .api import AtmosEnergyApiClient
from .exceptions import AuthenticationError, APIError

_LOGGER = logging.getLogger(__name__)

class AtmosEnergyConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Atmos Energy."""

    VERSION = 1

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

            session = None # api client handles session creation
            client = AtmosEnergyApiClient(username, password)
            
            try:
                await client.login()
                return self.async_create_entry(
                    title=user_input[CONF_USERNAME], data=user_input
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
                }
            ),
            errors=errors,
        )

    async def async_step_reauth(self, user_input=None):
        """Handle reauth flow."""
        errors = {}
        
        if user_input is not None:
            username = self._get_reauth_entry().data[CONF_USERNAME]
            password = user_input[CONF_PASSWORD]
            
            client = AtmosEnergyApiClient(username, password)
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
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        "fixed_cost",
                        default=self._config_entry.options.get("fixed_cost", 25.03)
                    ): vol.All(vol.Coerce(float), vol.Range(min=0, max=1000)),
                    vol.Required(
                        "usage_rate", 
                        default=self._config_entry.options.get("usage_rate", 2.40)
                    ): vol.All(vol.Coerce(float), vol.Range(min=0, max=100)),
                    vol.Required(
                        "tax_percent", 
                        default=self._config_entry.options.get("tax_percent", 8.0)
                    ): vol.All(vol.Coerce(float), vol.Range(min=0, max=100)),
                }
            ),
        )
