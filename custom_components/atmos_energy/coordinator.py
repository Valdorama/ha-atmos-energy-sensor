"""DataUpdateCoordinator for Atmos Energy."""
import logging
import math
import asyncio
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
    CONF_WEATHER_STATION,
    CONF_WEATHER_ENTITY,
    CONF_AUTO_FETCH_GCR,
    STORAGE_KEY,
    STORAGE_VERSION,
    DEFAULT_BASE_LOAD,
    DEFAULT_HEATING_COEFF,
    DEFAULT_BALANCE_TEMP,
    ATTR_BILLING_PERIOD_START,
    ATTR_USAGE
)
from .api import AtmosEnergyApiClient
from .exceptions import AuthenticationError, APIError, DataParseError
from .wna.calculator import WNACalculator
from .wna.gcr_fetcher import GCRRateFetcher

_LOGGER = logging.getLogger(__name__)

class AtmosEnergyDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching Atmos Energy data."""

    def __init__(self, hass: HomeAssistant, client: AtmosEnergyApiClient, entry: ConfigEntry):
        """Initialize."""
        self.client = client
        self.config_entry = entry
        
        # Get weather station from config
        station_id = entry.options.get(CONF_WEATHER_STATION, "austin")
        
        # Initialize WNA calculator
        self.wna_calculator = WNACalculator(station_id)
        
        # Initialize GCR fetcher
        self.gcr_fetcher = GCRRateFetcher(hass)
        self.current_gcr_rate = None
        
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
            # Load GCR cache
            await self.gcr_fetcher.async_load()
            
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

    def _parse_next_read_date(self, date_str: str | None) -> datetime | None:
        """Parse next meter read date into a datetime object."""
        if not date_str:
            return None
            
        # Try multiple formats
        for fmt in ("%m/%d/%Y", "%a %b %d %H:%M:%S %Z %Y", "%a %b %d %H:%M:%S %Y"):
            try:
                clean_str = date_str
                if " " in date_str and len(date_str.split()) == 6:
                    # Handle verbose format like "Wed Mar 11 00:00:00 CDT 2026"
                    parts = date_str.split()
                    clean_str = f"{parts[0]} {parts[1]} {parts[2]} {parts[3]} {parts[5]}"
                    return datetime.strptime(clean_str, "%a %b %d %H:%M:%S %Y")
                else:
                    return datetime.strptime(clean_str, fmt)
            except Exception:
                continue
        return None

    async def _calculate_projected_add(self, data: dict[str, Any]) -> int:
        """Calculate projected total ADD for the billing period."""
        actual_hdd = data.get("actual_hdd", 0)
        billing_days = data.get("billing_period_days", 0)
        
        next_read_dt = data.get("next_meter_read_dt")
        
        days_remaining = 0
        if next_read_dt:
            try:
                localized_next = dt_util.as_local(next_read_dt)
                now = dt_util.now()
                days_remaining = (localized_next - now).days
            except Exception as e:
                _LOGGER.warning("Error calculating days remaining from %s: %s", next_read_dt, e)
                days_remaining = max(0, 30 - billing_days)
        else:
            # Typical billing period is 30 days
            days_remaining = max(0, 30 - billing_days)
        
        if days_remaining <= 0:
            return actual_hdd
            
        # Get weather forecast if configured
        weather_entity = self.config_entry.options.get(CONF_WEATHER_ENTITY)
        forecast_hdd = 0
        
        if weather_entity:
            try:
                # Use Home Assistant 2024.7+ style forecast call
                response = await self.hass.services.async_call(
                    "weather",
                    "get_forecasts",
                    {"type": "daily", "entity_id": [weather_entity]},
                    blocking=True,
                    return_response=True,
                )
                
                forecast_data = response.get(weather_entity, {}).get("forecast", [])
                
                if forecast_data:
                    # Use min(days_remaining, forecast_length) days
                    days_to_forecast = min(days_remaining, len(forecast_data))
                    
                    for day in forecast_data[:days_to_forecast]:
                        high = day.get("native_temperature") or day.get("temperature")
                        low = day.get("native_temp_low") or day.get("templow")
                        
                        if high is not None and low is not None:
                            avg_temp = (float(high) + float(low)) / 2
                            daily_hdd = max(0, self.balance_temp - avg_temp)
                            forecast_hdd += daily_hdd
                    
                    # If forecast doesn't cover all remaining days, extrapolate
                    if days_to_forecast < days_remaining:
                        avg_forecast_hdd = forecast_hdd / days_to_forecast if days_to_forecast > 0 else 0
                        remaining_days = days_remaining - days_to_forecast
                        forecast_hdd += avg_forecast_hdd * remaining_days
                    
                    _LOGGER.debug(
                        "HDD Projection (Weather): actual=%d, forecast=%d, total=%d",
                        actual_hdd, int(forecast_hdd), actual_hdd + int(forecast_hdd)
                    )
                    return actual_hdd + int(forecast_hdd)
                    
            except Exception as e:
                _LOGGER.warning("Could not get weather forecast for HDD projection: %s", e)
        
        # Fallback: Use historical NDD to estimate remaining days
        current_month = datetime.now().month
        monthly_ndd = self.wna_calculator.get_ndd_for_month(current_month)
        
        if monthly_ndd > 0:
            avg_daily_hdd = monthly_ndd / 30
            forecast_hdd = avg_daily_hdd * days_remaining
            _LOGGER.debug(
                "HDD Projection (Fallback): actual=%d, forecast=%d (NDD based), total=%d",
                actual_hdd, int(forecast_hdd), actual_hdd + int(forecast_hdd)
            )
            return actual_hdd + int(forecast_hdd)
            
        return actual_hdd

    async def _calculate_predicted_usage_7d(self) -> float | None:
        """Calculate predicted usage for next 7 days once."""
        weather_entity = self.config_entry.options.get(CONF_WEATHER_ENTITY)
        if not weather_entity:
            return None
            
        try:
            response = await self.hass.services.async_call(
                "weather",
                "get_forecasts",
                {"type": "daily", "entity_id": [weather_entity]},
                blocking=True,
                return_response=True,
            )
            forecast_data = response.get(weather_entity, {}).get("forecast", [])
            if not forecast_data:
                return None
                
            total_ccf = 0.0
            for day in forecast_data[:7]:
                high = day.get("native_temperature") or day.get("temperature")
                low = day.get("native_temp_low") or day.get("templow")
                if high is not None and low is not None:
                    avg_temp = (float(high) + float(low)) / 2
                    hdd = max(0, self.balance_temp - avg_temp)
                    daily_usage = self.base_load + (self.heating_coeff * hdd)
                    total_ccf += daily_usage
            
            prediction = round(total_ccf, 2)
            _LOGGER.debug("Predicted 7-day usage from coordinator: %s CCF", prediction)
            return prediction
        except Exception as e:
            _LOGGER.warning("Error calculating 7-day predicted usage: %s", e)
            return None

    def calculate_total_cost(
        self, 
        usage: float, 
        include_fixed: bool = True, 
        wna_charge: float = 0.0,
        pro_rate_days: int | None = None
    ) -> dict[str, Any]:
        """Centralized cost calculation logic. Returns detailed breakdown."""
        options = self.config_entry.options
        
        # Base fixed and consumption rate
        fixed = float(options.get("fixed_cost", 25.03)) if include_fixed else 0.0
        if include_fixed and pro_rate_days is not None:
             fixed = (fixed * pro_rate_days) / 30.0
             
        consumption_rate = float(options.get("usage_rate", 0.78))
        consumption_charge = usage * consumption_rate
        
        # GCR charge
        gcr_rate = float(self.current_gcr_rate or options.get("gcr_rate", 1.17))
        gcr_charge = usage * gcr_rate
        
        # URI surcharge
        uri_rate = float(options.get("uri_surcharge", 0.018431))
        uri_charge = usage * uri_rate
        
        # Subtotal before tax
        subtotal = fixed + consumption_charge + wna_charge + gcr_charge + uri_charge
        
        # Tax
        tax_pct = float(options.get("tax_percent", 8.0))
        tax_amount = subtotal * (tax_pct / 100.0)
        total_cost = subtotal + tax_amount
        
        return {
            "total": round(total_cost, 2),
            "breakdown": {
                "fixed_charge": round(fixed, 2),
                "consumption_charge": round(consumption_charge, 2),
                "gcr_charge": round(gcr_charge, 2),
                "wna_charge": round(wna_charge, 2),
                "uri_charge": round(uri_charge, 2),
                "tax_amount": round(tax_amount, 2),
                "subtotal": round(subtotal, 2),
                "rates": {
                    "consumption": consumption_rate,
                    "gcr": round(gcr_rate, 4),
                    "uri": uri_rate,
                    "tax": tax_pct,
                }
            }
        }

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from API."""
        if not self._history:
            await self._async_load_history()

        daily_usage = self.config_entry.data.get(CONF_DAILY_USAGE, True)
        try:
            data = await self.client.get_account_data(daily_usage=daily_usage)
            
            # Parse next meter read date once
            data["next_meter_read_dt"] = self._parse_next_read_date(data.get("next_meter_read_date"))
            
            # NEW: Update GCR rate
            use_auto_gcr = self.config_entry.options.get(CONF_AUTO_FETCH_GCR, True)
            if use_auto_gcr:
                gcr_rate = await self.gcr_fetcher.get_current_rate()
                if gcr_rate:
                    self.current_gcr_rate = gcr_rate
            
            # NEW: Calculate projected ADD and centralized WNA
            if daily_usage:
                data["projected_add"] = await self._calculate_projected_add(data)
                
                # Pre-calculate WNA Charge and components
                usage = data.get(ATTR_USAGE, 0)
                billing_start = data.get(ATTR_BILLING_PERIOD_START)
                billing_month = datetime.now().month
                if billing_start:
                    try:
                        billing_month = datetime.strptime(billing_start.split()[0], "%Y-%m-%d").month
                    except:
                        pass
                
                projected_add = data.get("projected_add", self.wna_calculator.get_ndd_for_month(billing_month))
                
                # Perform the calculation ONCE
                wnaf_cents = self.wna_calculator.calculate_wnaf(billing_month, projected_add)
                wna_charge = self.wna_calculator.calculate_wna_charge(
                    billing_month, projected_add, usage, wnaf_cents=wnaf_cents
                )
                
                data["wna_calculated"] = {
                    "wnaf_rate": f"${wnaf_cents/100:.6f}/CCF",
                    "wnaf_rate_raw": wnaf_cents / 100.0,
                    "wna_charge": wna_charge,
                    "ndd": self.wna_calculator.get_ndd_for_month(billing_month),
                    "add_projected": projected_add,
                    "billing_month": billing_month,
                    "weather_station": self.wna_calculator.station_id,
                }
                
                # Calculate 7-day prediction once
                predicted_usage_7d = await self._calculate_predicted_usage_7d()
                data["predicted_usage_7d"] = predicted_usage_7d
                
                # NEW: Calculate 7-day predicted cost using the same logic
                if predicted_usage_7d is not None:
                    # Apply the current billing cycle's WNA rate to the prediction
                    # This provides the most consistent "forward looking" estimate
                    wnaf_rate = wnaf_cents / 100.0
                    p_wna_charge = predicted_usage_7d * wnaf_rate
                    
                    cost_data = self.calculate_total_cost(
                        predicted_usage_7d, 
                        include_fixed=True, 
                        wna_charge=p_wna_charge,
                        pro_rate_days=7
                    )
                    
                    # Store breakdown for sensor attributes
                    breakdown = cost_data["breakdown"]
                    breakdown["rates"]["wna"] = round(wnaf_rate, 6)
                    
                    data["predicted_cost_7d"] = cost_data["total"]
                    data["predicted_cost_7d_breakdown"] = breakdown

            # Update History if available
            new_history = data.get("history", [])
            if new_history:
                updated = False
                for record in new_history:
                    date_str = record.get("date")
                    if date_str:
                        key = date_str.split(" ")[0]
                        if key not in self._history:
                            self._history[key] = record
                            self._unsaved_keys.add(key)
                            updated = True
                
                # Prune old history (> 90 days)
                cutoff = dt_util.now() - timedelta(days=90)
                keys_to_remove = []
                for date_str in self._history:
                    dt = dt_util.parse_datetime(date_str)
                    if not dt:
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
                    self._unsaved_keys.discard(k)
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
                elif usage > 10000:
                    _LOGGER.warning("Unusually high gas usage detected: %s CCF", usage)
            
            return data
            
        except AuthenticationError as err:
            self.config_entry.async_start_reauth(self.hass)
            raise UpdateFailed(f"Authentication failed: {err}") from err
        except (APIError, DataParseError, aiohttp.ClientError) as err:
            raise UpdateFailed(f"Error communicating with API: {err}") from err
        except Exception as err:
            _LOGGER.exception("Unexpected error updating Atmos Energy data")
            raise UpdateFailed(f"Unexpected error: {err}") from err
        finally:
            self._schedule_next_update()

    def _schedule_next_update(self):
        """Calculate and set next update time based on Atmos update schedule."""
        now = dt_util.now()
        next_update = now.replace(hour=7, minute=0, second=0, microsecond=0)
        if next_update <= now:
            next_update += timedelta(days=1)
        time_until_next = next_update - now
        self.update_interval = time_until_next
        _LOGGER.debug(
            "Next Atmos update scheduled for %s (in %s)",
            next_update.strftime("%Y-%m-%d %H:%M:%S"),
            time_until_next
        )
