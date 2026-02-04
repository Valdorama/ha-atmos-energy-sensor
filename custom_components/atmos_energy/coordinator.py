"""DataUpdateCoordinator for Atmos Energy."""
import logging
import aiohttp
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.core import HomeAssistant
from .const import DOMAIN, SCAN_INTERVAL
from .api import AtmosEnergyApiClient

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
            # Login is handled automatically by the client if session is invalid
            data = await self.client.get_account_data()
            return data
        except Exception as err:
            raise UpdateFailed(f"Error communicating with API: {err}")
