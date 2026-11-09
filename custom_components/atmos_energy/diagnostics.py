"""Diagnostics support for Atmos Energy."""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN

# Users attach diagnostics to public issues. The entry title is the login
# username, plus the street address for multi-account logins.
TO_REDACT = {"title", "unique_id", "username", "password", "account_number", "service_address"}

async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    
    diagnostics = {
        "entry": {
            "entry_id": entry.entry_id,
            "title": entry.title,
            "unique_id": entry.unique_id,
            "version": entry.version,
            "domain": entry.domain,
        },
        "data_config": {
            "username": entry.data.get("username"),
            "has_password": "password" in entry.data,
            "account_number": entry.data.get("account_number"),
            "has_service_address": bool(entry.data.get("service_address")),
        },
        "coordinator": {
            "last_update_success": coordinator.last_update_success,
            "last_update": coordinator.last_update.isoformat() if coordinator.last_update else None,
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
            "region": coordinator.region,
            "current_rate": coordinator.current_gcr_rate,
            "last_fetch": coordinator.gcr_fetcher._last_fetch.isoformat() if coordinator.gcr_fetcher._last_fetch else None,
            "cache_size": len(coordinator.gcr_fetcher._cache),
        },
        "published_rates": {
            "updated": (coordinator.published_rates.doc or {}).get("updated"),
            "last_fetch": coordinator.published_rates._last_fetch.isoformat() if coordinator.published_rates._last_fetch else None,
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
    return async_redact_data(diagnostics, TO_REDACT)
