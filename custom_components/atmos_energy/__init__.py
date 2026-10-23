"""The Atmos Energy integration."""
import asyncio
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.const import CONF_USERNAME, CONF_PASSWORD
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.storage import Store
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN, CONF_ACCOUNT_NUMBER, STORAGE_VERSION, history_storage_key, account_selection_issue_id
from .api import AtmosEnergyApiClient
from .coordinator import AtmosEnergyDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["sensor"]

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Atmos Energy from a config entry."""

    username = entry.data[CONF_USERNAME]
    password = entry.data[CONF_PASSWORD]

    client = AtmosEnergyApiClient(
        username, password, source="coordinator", account_number=entry.data.get(CONF_ACCOUNT_NUMBER)
    )
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
        coordinators = hass.data[DOMAIN]
        if not coordinators:
            _LOGGER.error("No Atmos Energy coordinators found to handle service call")
            return

        # The spreadsheet belongs to one meter, so with several entries the caller must say which
        entry_id = call.data.get("config_entry")
        if entry_id:
            if entry_id not in coordinators:
                raise ServiceValidationError(f"No loaded Atmos Energy entry with id {entry_id}")
            coordinator = coordinators[entry_id]
        elif len(coordinators) == 1:
            coordinator = next(iter(coordinators.values()))
        else:
            raise ServiceValidationError(
                "Several Atmos Energy accounts are set up; choose one with the config_entry field"
            )
        await coordinator.async_import_historical_xls(file_path)

    async def handle_refresh_current_usage(call):
        """Handle the refresh_current_usage service call."""
        # Trigger an immediate refresh regardless of the schedule, for every account
        for coordinator in list(hass.data[DOMAIN].values()):
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

async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Delete the entry's history file and any open repair when the entry is deleted."""
    await Store(hass, STORAGE_VERSION, history_storage_key(entry.entry_id)).async_remove()
    ir.async_delete_issue(hass, DOMAIN, account_selection_issue_id(entry.entry_id))
