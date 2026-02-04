"""Config flow for Atmos Energy integration."""
import logging
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.const import CONF_USERNAME, CONF_PASSWORD
import homeassistant.helpers.config_validation as cv

from .const import DOMAIN
from .api import AtmosEnergyApiClient

_LOGGER = logging.getLogger(__name__)

class AtmosEnergyConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Atmos Energy."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        """Handle the initial step."""
        errors = {}

        if user_input is not None:
            # Validate credentials
            username = user_input[CONF_USERNAME]
            password = user_input[CONF_PASSWORD]

            session = None # api client handles session creation
            client = AtmosEnergyApiClient(username, password)
            
            try:
                await client.login()
                await client.close()
            except Exception:
                errors["base"] = "auth_error"
            else:
                return self.async_create_entry(
                    title=user_input[CONF_USERNAME], data=user_input
                )

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

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Get the options flow for this handler."""
        return AtmosEnergyOptionsFlowHandler(config_entry)


class AtmosEnergyOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options."""

    def __init__(self, config_entry):
        """Initialize options flow."""
        self.config_entry = config_entry

    async def async_step_init(self, user_input=None):
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        "fixed_cost", # Needs to match const but using literal here or import
                        default=self.config_entry.options.get("fixed_cost", 25.03)
                    ): float,
                    vol.Required(
                        "usage_rate", 
                        default=self.config_entry.options.get("usage_rate", 2.40)
                    ): float,
                    vol.Required(
                        "tax_percent", 
                        default=self.config_entry.options.get("tax_percent", 8.0)
                    ): float,
                }
            ),
        )
