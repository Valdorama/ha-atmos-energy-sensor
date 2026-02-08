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
            "entry_id": entry.entry_id,
            "title": entry.title,
            "version": entry.version,
            "domain": entry.domain,
        },
        "data_config": {
            "username": entry.data.get("username", "")[:3] + "***",
            "has_password": "password" in entry.data,
        },
        "coordinator": {
            "last_update_success": coordinator.last_update_success,
            "last_update_success_time": coordinator.last_update_success_time.isoformat() if coordinator.last_update_success_time else None,
            "update_interval": coordinator.update_interval.total_seconds() if coordinator.update_interval else None,
        },
        "coordinator_data": coordinator.data if coordinator.data else {},
        "options": dict(entry.options),
    }
