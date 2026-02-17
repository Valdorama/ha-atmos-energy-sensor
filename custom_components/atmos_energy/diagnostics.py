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
        "wna_info": {
            "station_id": coordinator.wna_calculator.station_id,
            "base_load": coordinator.wna_calculator.base_load,
            "heat_factor": coordinator.wna_calculator.heat_factor,
            "commodity_rate": coordinator.wna_calculator.commodity_rate,
        },
        "gcr_info": {
            "current_rate": coordinator.current_gcr_rate,
            "last_fetch": coordinator.gcr_fetcher._last_fetch.isoformat() if coordinator.gcr_fetcher._last_fetch else None,
            "cache_size": len(coordinator.gcr_fetcher._cache),
        },
        "model_info": {
            "base_load": coordinator.base_load,
            "heating_coefficient": coordinator.heating_coeff,
            "balance_temperature": coordinator.balance_temp,
            "r_squared": coordinator.r_squared,
            "history_days": coordinator.history_count,
            "model_trained": coordinator.history_count >= 10,
        },
        "options": dict(entry.options),
    }
