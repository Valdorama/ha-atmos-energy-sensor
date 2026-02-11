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
    DEFAULT_HEATING_COEFF,
    DEFAULT_BALANCE_TEMP
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
        self._unsaved_keys = set()  # Track keys that need to be saved
        
        # Model coefficients
        self.base_load = DEFAULT_BASE_LOAD
        self.heating_coeff = DEFAULT_HEATING_COEFF
        self.balance_temp = DEFAULT_BALANCE_TEMP
        self.r_squared = 0.0
        self._last_optimization_count = 0  # Track when we last did full optimization
        
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
        """Save only new history to storage (incremental mode)."""
        # Only save if we have new data
        if not self._unsaved_keys:
            return
        
        try:
            stored = await self._store.async_load() or {}
            history = stored.get("history", {})
            
            # Only update new keys
            for key in self._unsaved_keys:
                if key in self._history:
                    history[key] = self._history[key]
            
            await self._store.async_save({"history": history})
            self._unsaved_keys.clear()
            _LOGGER.debug("Saved history to storage")
        except Exception as e:
            _LOGGER.error("Failed to save Atmos history: %s", e)

    def _recalculate_model(self):
        """Calculate Base Load, Heating Coefficient, and Balance Temp using Linear Regression."""
        if len(self._history) < 10:
            _LOGGER.debug("Insufficient history (%d days) for regression, using defaults", len(self._history))
            self.base_load = DEFAULT_BASE_LOAD
            self.heating_coeff = DEFAULT_HEATING_COEFF
            self.balance_temp = DEFAULT_BALANCE_TEMP
            self.r_squared = 0.0
            return

        # Prepare raw data points (Date, Temp, Usage)
        # Use dict copy to prevent race condition during iteration
        data_points = []
        for date_str, record in dict(self._history).items():
            usage = record.get("usage", 0.0)
            avg_temp = record.get("avg_temp")
            if usage > 0.0 and avg_temp is not None:
                data_points.append((avg_temp, usage))

        if len(data_points) < 10:
            return

        current_count = len(data_points)
        needs_full_optimization = (current_count - self._last_optimization_count) >= 10
        
        if needs_full_optimization:
            # Full grid search only with significant new data
            best_sse = float('inf')
            best_model = None
            
            # Coarser search: 1°F steps instead of 0.5°F (21 iterations instead of 41)
            for temp_candidate in range(55, 76):
                x_values = [max(0, temp_candidate - pt[0]) for pt in data_points]
                y_values = [pt[1] for pt in data_points]
                
                slope, intercept, sse, r2 = self._fit_linear_regression(x_values, y_values)
                
                if slope is not None and sse < best_sse:
                    best_sse = sse
                    best_model = (slope, intercept, float(temp_candidate), r2)
            
            self._last_optimization_count = current_count
        else:
            # Quick update with existing balance temp
            x_values = [max(0, self.balance_temp - pt[0]) for pt in data_points]
            y_values = [pt[1] for pt in data_points]
            slope, intercept, sse, r2 = self._fit_linear_regression(x_values, y_values)
            
            if slope is not None:
                best_model = (slope, intercept, self.balance_temp, r2)
            else:
                return

        if best_model:
            slope, intercept, balance_temp, r2 = best_model
            
            # Improved Slope Clamping & Logging
            if slope < 0:
                _LOGGER.warning(
                    "Negative heating coefficient (%.4f) detected - usage increases in warmer weather. Clamping to 0.", 
                    slope
                )
                slope = 0.0
            elif slope < 0.01:
                _LOGGER.debug("Very low heating coefficient (%.4f) - minimal heating load", slope)

            if intercept < 0:
                intercept = 0.1  # Minimum base load
            
            # Validate balance temperature range
            if balance_temp < 50 or balance_temp > 80:
                _LOGGER.warning(
                    "Learned balance temperature (%.1f°F) is outside normal range (50-80°F). Using default %.1f°F instead.",
                    balance_temp, DEFAULT_BALANCE_TEMP
                )
                balance_temp = DEFAULT_BALANCE_TEMP
            elif balance_temp < 58 or balance_temp > 72:
                _LOGGER.info(
                    "Unusual balance temperature (%.1f°F) detected. This may indicate: "
                    "(1) Unique home characteristics, (2) Insufficient data, or (3) Non-heating gas usage patterns.",
                    balance_temp
                )

            self.heating_coeff = round(slope, 4)
            self.base_load = round(intercept, 2)
            self.balance_temp = balance_temp
            self.r_squared = round(r2, 4)
            
            # Log model fit quality
            optimization_type = "full" if needs_full_optimization else "quick"
            _LOGGER.info(
                "Updated Model: R²=%.3f, Base=%.2f CCF, Coeff=%.4f, Balance=%.1f°F (N=%d, %s)",
                self.r_squared, self.base_load, self.heating_coeff, self.balance_temp, 
                len(data_points), optimization_type
            )
            
            # Handle negative R² (model worse than mean)
            if self.r_squared < 0:
                _LOGGER.error(
                    "Model has negative R² (%.3f) - fit is worse than average. "
                    "Your gas usage may not correlate with temperature at all.",
                    self.r_squared
                )
            elif self.r_squared < 0.5:
                _LOGGER.warning(
                    "Poor model fit (R²=%.3f). Gas usage may not correlate well with temperature.", 
                    self.r_squared
                )

    def _fit_linear_regression(self, x_values, y_values):
        """Fit a linear regression and return slope, intercept, SSE, and R2."""
        n = len(x_values)
        sum_x = sum(x_values)
        sum_y = sum(y_values)
        sum_xy = sum(x*y for x, y in zip(x_values, y_values))
        sum_x2 = sum(x*x for x in x_values)
        
        denominator = (n * sum_x2 - sum_x**2)
        if denominator == 0:
            return None, None, float('inf'), 0
            
        slope = (n * sum_xy - sum_x * sum_y) / denominator
        intercept = (sum_y - slope * sum_x) / n
        
        # Calculate SSE and R2
        y_mean = sum_y / n
        ss_tot = sum((y - y_mean)**2 for y in y_values)
        
        sse = 0.0
        for x, y in zip(x_values, y_values):
            prediction = intercept + slope * x
            sse += (y - prediction)**2
            
        r_squared = 1 - (sse / ss_tot) if ss_tot > 0 else 0
        
        return slope, intercept, sse, r_squared

    @property
    def history_count(self) -> int:
        """Return number of days in history."""
        return len(self._history)


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
                            self._unsaved_keys.add(key)  # Track for incremental save
                            updated = True
                
                # Prune old history (> 90 days)
                cutoff = dt_util.now() - timedelta(days=90)
                keys_to_remove = []
                for date_str in self._history:
                    # Suggestion 5: Better date parsing
                    dt = dt_util.parse_datetime(date_str)
                    if not dt:
                        # Fallback for keys like YYYY-MM-DD
                        for fmt in ("%Y-%m-%d", "%m/%d/%Y"):
                            try:
                                naive_dt = datetime.strptime(date_str, fmt)
                                dt = dt_util.as_local(naive_dt)
                                break
                            except ValueError:
                                pass
                    
                    if dt and dt < cutoff:
                        keys_to_remove.append(date_str)
                
                for k in keys_to_remove:
                    del self._history[k]
                    self._unsaved_keys.discard(k)  # Remove from unsaved if it was there
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
        finally:
            # Smart scheduling: Atmos updates data around 6-7 AM local time
            # Schedule next update for 7 AM next day
            self._schedule_next_update()

    def _schedule_next_update(self):
        """Calculate and set next update time based on Atmos update schedule."""
        now = dt_util.now()
        
        # Target update time: 7 AM local time (Atmos typically updates around 6 AM)
        next_update = now.replace(hour=7, minute=0, second=0, microsecond=0)
        
        # If it's already past 7 AM today, schedule for 7 AM tomorrow
        if next_update <= now:
            next_update += timedelta(days=1)
        
        # Calculate time until next update
        time_until_next = next_update - now
        
        # Update the coordinator's update interval
        self.update_interval = time_until_next
        
        _LOGGER.debug(
            "Next Atmos update scheduled for %s (in %s)",
            next_update.strftime("%Y-%m-%d %H:%M:%S"),
            time_until_next
        )
