"""The Atmos Energy integration."""
import asyncio
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.const import CONF_USERNAME, CONF_PASSWORD

from .const import DOMAIN
from .api import AtmosEnergyApiClient
from .coordinator import AtmosEnergyDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["sensor"]

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Atmos Energy from a config entry."""
    
    username = entry.data[CONF_USERNAME]
    password = entry.data[CONF_PASSWORD]
 
    client = AtmosEnergyApiClient(username, password, source="coordinator")
    coordinator = AtmosEnergyDataUpdateCoordinator(hass, client, entry)
 
    # Load history first to get the last update time
    await coordinator._async_load_history()
    
    # Trigger smart refresh that respects the last update time
    hass.async_create_task(coordinator.async_setup_refresh())
 
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator
 
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    
    # Register services
    async def handle_populate_past_usage(call):
        """Handle the populate_past_usage service call."""
        file_path = call.data.get("file_path")
        coordinators = hass.data[DOMAIN].values()
        if not coordinators:
            _LOGGER.error("No Atmos Energy coordinators found to handle service call")
            return
            
        coordinator = next(iter(coordinators))
        await coordinator.async_import_historical_xls(file_path)

    async def handle_refresh_current_usage(call):
        """Handle the refresh_current_usage service call."""
        coordinators = hass.data[DOMAIN].values()
        if not coordinators:
            return
        
        coordinator = next(iter(coordinators))
        # Trigger an immediate refresh regardless of the schedule
        await coordinator.async_refresh()

    hass.services.async_register(DOMAIN, "populate_past_usage", handle_populate_past_usage)
    hass.services.async_register(DOMAIN, "refresh_current_usage", handle_refresh_current_usage)

    # Listen for option changes
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    return True

async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry."""
    await hass.config_entries.async_reload(entry.entry_id)

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        coordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.client.close()

    return unload_ok
