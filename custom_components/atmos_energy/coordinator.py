"""DataUpdateCoordinator for Atmos Energy."""
import logging
import aiohttp
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.core import HomeAssistant

from .const import DOMAIN, SCAN_INTERVAL
from .api import AtmosEnergyApiClient
from .exceptions import AuthenticationError, APIError, DataParseError

_LOGGER = logging.getLogger(__name__)

class AtmosEnergyDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching Atmos Energy data."""

    def __init__(self, hass: HomeAssistant, client: AtmosEnergyApiClient):
        """Initialize."""
        self.client = client
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=SCAN_INTERVAL,
        )

    async def _async_update_data(self):
        """Fetch data from API."""
        try:
            data = await self.client.get_account_data()
            
            # Basic validation
            usage = data.get("usage")
            if usage is not None:
                if usage < 0:
                    _LOGGER.warning("Negative usage value received: %s. Setting to 0", usage)
                    data["usage"] = 0.0
                elif usage > 5000: # Sanity check: 5000 CCF is a massive amount of gas
                    _LOGGER.warning("Unusually high gas usage detected: %s CCF", usage)
            
            return data
            
        except AuthenticationError as err:
            # Trigger reauth flow
            self.config_entry.async_start_reauth(self.hass)
            raise UpdateFailed(f"Authentication failed: {err}") from err
        except (APIError, DataParseError, aiohttp.ClientError) as err:
            raise UpdateFailed(f"Error communicating with API: {err}") from err
        except Exception as err:
            _LOGGER.exception("Unexpected error updating Atmos Energy data")
            raise UpdateFailed(f"Unexpected error: {err}") from err
