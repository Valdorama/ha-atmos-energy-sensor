"""Diagnostics support for Atmos Energy."""
from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN

async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    
    return {
        "entry": {
            "title": entry.title,
            "version": entry.version,
            "domain": entry.domain,
        },
        "data": {
            "username": entry.data.get("username", "")[:3] + "***",
            "last_update_success": coordinator.last_update_success,
        },
        "coordinator_data": coordinator.data if coordinator.data else {},
        "options": dict(entry.options),
    }
