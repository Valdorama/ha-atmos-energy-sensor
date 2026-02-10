"""DataUpdateCoordinator for Atmos Energy."""
import logging
import math
from datetime import datetime, timedelta
from typing import Any

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    DOMAIN, 
    SCAN_INTERVAL, 
    CONF_DAILY_USAGE,
    STORAGE_KEY,
    STORAGE_VERSION,
    DEFAULT_BASE_LOAD,
    DEFAULT_HEATING_COEFF
)
from .api import AtmosEnergyApiClient
from .exceptions import AuthenticationError, APIError, DataParseError

_LOGGER = logging.getLogger(__name__)

class AtmosEnergyDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching Atmos Energy data."""

    def __init__(self, hass: HomeAssistant, client: AtmosEnergyApiClient, entry: ConfigEntry):
        """Initialize."""
        self.client = client
        self.config_entry = entry
        
        # Persistent storage for history
        self._store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self._history = {} # Keyed by date string YYYY-MM-DD
        
        # Model coefficients
        self.base_load = DEFAULT_BASE_LOAD
        self.heating_coeff = DEFAULT_HEATING_COEFF
        
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=SCAN_INTERVAL,
        )

    async def _async_load_history(self):
        """Load history from storage."""
        try:
            stored = await self._store.async_load()
            if stored:
                self._history = stored.get("history", {})
                _LOGGER.debug("Loaded %d days of history from storage", len(self._history))
                self._recalculate_model()
        except Exception as e:
            _LOGGER.warning("Failed to load Atmos history: %s", e)

    async def _async_save_history(self):
        """Save history to storage."""
        try:
            await self._store.async_save({"history": self._history})
        except Exception as e:
            _LOGGER.error("Failed to save Atmos history: %s", e)

    def _recalculate_model(self):
        """Calculate Base Load and Heating Coefficient using Linear Regression."""
        if len(self._history) < 10:
            _LOGGER.debug("Insufficient history (%d days) for regression, using defaults", len(self._history))
            self.base_load = DEFAULT_BASE_LOAD
            self.heating_coeff = DEFAULT_HEATING_COEFF
            return

        # Prepare data points (X=HDD, Y=Usage)
        x_values = [] # HDD
        y_values = [] # Usage
        
        # Outlier filtering thresholds (basic sanity check)
        # We ignore days with 0 usage (vacation/error)
        
        for date_str, record in self._history.items():
            usage = record.get("usage", 0.0)
            avg_temp = record.get("avg_temp")
            
            if usage <= 0.0 or avg_temp is None:
                continue
                
            hdd = max(0, 65 - avg_temp)
            
            x_values.append(hdd)
            y_values.append(usage)

        n = len(x_values)
        if n < 10:
            return

        # Simple Linear Regression: Y = mx + b
        # m = (N*Σxy - Σx*Σy) / (N*Σx² - (Σx)²)
        # b = (Σy - m*Σx) / N
        
        sum_x = sum(x_values)
        sum_y = sum(y_values)
        sum_xy = sum(x*y for x, y in zip(x_values, y_values))
        sum_x2 = sum(x*x for x in x_values)
        
        denominator = (n * sum_x2 - sum_x**2)
        
        if denominator == 0:
            _LOGGER.warning("Cannot calculate regression: denominator is zero")
            return

        slope = (n * sum_xy - sum_x * sum_y) / denominator
        intercept = (sum_y - slope * sum_x) / n
        
        # Sanity bounds for the model
        # Slope (heating coeff) should be positive (colder = more gas)
        # Intercept (base load) should be positive
        
        if slope < 0.01: 
             # If slope is negative or tiny, it means usage doesn't correlate with cold. 
             # This happens in summer. We might just stick to defaults or clamp it.
             slope = max(0.0, slope)
             
        if intercept < 0:
            intercept = 0.1 # Minimum base load

        self.heating_coeff = round(slope, 4)
        self.base_load = round(intercept, 2)
        
        _LOGGER.debug("Updated Regression Model (N=%d): Base Load=%.2f, Heat Coeff=%.4f", n, self.base_load, self.heating_coeff)


    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from API."""
        if not self._history:
            await self._async_load_history()

        daily_usage = self.config_entry.data.get(CONF_DAILY_USAGE, True)
        try:
            data = await self.client.get_account_data(daily_usage=daily_usage)
            
            # Update History if available
            new_history = data.get("history", [])
            if new_history:
                updated = False
                for record in new_history:
                    date_str = record.get("date")
                    # 'date' in XLS is often YYYY-MM-DD HH:MM:SS, let's normalize to YYYY-MM-DD
                    if date_str:
                        key = date_str.split(" ")[0]
                        if key not in self._history:
                            self._history[key] = record
                            updated = True
                
                # Prune old history (> 90 days)
                cutoff = dt_util.now() - timedelta(days=90)
                keys_to_remove = []
                for date_str in self._history:
                    try:
                        # Attempt to parse
                        # Handle varied formats
                        dt = None
                        for fmt in ("%Y-%m-%d", "%m/%d/%Y"):
                            try:
                                dt = datetime.strptime(date_str, fmt).replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)
                                break
                            except ValueError:
                                pass
                        
                        if dt and dt < cutoff:
                            keys_to_remove.append(date_str)
                    except Exception:
                        pass # Keep if we can't parse
                
                for k in keys_to_remove:
                    del self._history[k]
                    updated = True

                if updated:
                    self._recalculate_model()
                    await self._async_save_history()

            # Basic validation
            usage = data.get("usage")
            if usage is not None:
                if usage < 0:
                    _LOGGER.warning("Negative usage value received: %s. Setting to 0", usage)
                    data["usage"] = 0.0
                elif usage > 10000: # Relaxed sanity check (10,000 CCF)
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
