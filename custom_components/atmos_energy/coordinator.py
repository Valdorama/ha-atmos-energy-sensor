"""DataUpdateCoordinator for Atmos Energy."""
import logging
import math
import asyncio
from datetime import datetime, timedelta
from typing import Any

import aiohttp
import pandas as pd
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    StatisticMetaData,
    StatisticData,
)
from homeassistant.const import UnitOfVolume

from .const import (
    DOMAIN, 
    SCAN_INTERVAL, 
    CONF_OPERATION_MODE,
    CONF_WEATHER_STATION,
    CONF_WEATHER_ENTITY,
    CONF_AUTO_FETCH_GCR,
    CONF_GCR_RATE,
    STORAGE_KEY,
    STORAGE_VERSION,
    DEFAULT_BASE_LOAD,
    DEFAULT_HEATING_COEFF,
    DEFAULT_BALANCE_TEMP,
    ATTR_BILLING_PERIOD_START,
    ATTR_USAGE,
    MODE_MONTHLY,
    MODE_DAILY,
    MODE_DAILY_ADVANCED
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

    async def _async_import_daily_statistics(self, data: dict):
        """Import daily usage statistics with correct timestamps.
        
        This imports historical data into HA's Statistics database with
        the actual date of each reading, not the date we imported it.
        This ensures the Energy Dashboard shows usage on the correct dates.
        """
        history = data.get("history", [])
        if not history:
            _LOGGER.debug("No history to import for statistics")
            return
        
        # Sort history by date to ensure correct cumulative sum
        # Atmos dates are YYYY-MM-DD or MM/DD/YYYY. YYYY-MM-DD is sortable.
        try:
            sorted_history = sorted(history, key=lambda x: x.get("date", ""))
        except Exception:
            sorted_history = history

        # Create unique statistic_id for this account - MUST be domain:identifier
        # We use usage_ prefix and lowercase everything for safety
        entry_id = self.config_entry.entry_id.lower()
        statistic_id = f"{DOMAIN}:usage_{entry_id}"

        # Metadata for the statistics
        metadata = StatisticMetaData(
            has_mean=False,
            has_sum=True,
            name="Atmos Energy Daily Usage",
            source=DOMAIN,
            statistic_id=statistic_id,
            unit_of_measurement=UnitOfVolume.CENTUM_CUBIC_FEET,
            unit_class="volume",
        )

        # Convert history to statistics with cumulative sum
        statistics = []
        cumulative_sum = 0.0
        
        for record in sorted_history:
            date_str = record.get("date")
            usage = record.get("usage")
            
            if not date_str or usage is None:
                continue
            
            try:
                # Use HA's native parser first (ISO 8601)
                date = dt_util.parse_datetime(date_str)
                
                # Fallback to MM/DD/YYYY if the string matches that format
                if date is None:
                    try:
                        # atmos dates are sometimes 02/11/2026
                        date = datetime.strptime(date_str, "%m/%d/%Y")
                    except ValueError:
                        _LOGGER.debug("Could not parse date string for statistics: %s", date_str)
                        continue
                
                # Standard HA daily statistic timestamp: beginning of the day (00:00)
                timestamp = date.replace(hour=0, minute=0, second=0, microsecond=0)
                
                # Convert to UTC for storage
                timestamp_utc = dt_util.as_utc(timestamp)
                
                # Update running total
                usage_val = float(usage)
                cumulative_sum += usage_val
                
                statistics.append(StatisticData(
                    start=timestamp_utc,
                    state=usage_val,      # Point-in-time state (daily usage)
                    sum=cumulative_sum,   # Cumulative total required for Energy Dashboard
                ))
                
            except Exception as e:
                _LOGGER.debug("Could not parse date %s for statistics: %s", date_str, e)
                continue
        
        if not statistics:
            _LOGGER.debug("No valid statistics to import")
            return
        
        # Import into HA statistics database - NOTE: This is a callback, NOT awaitable
        try:
            async_add_external_statistics(
                self.hass,
                metadata,
                statistics,
            )
            
            _LOGGER.info(
                "Imported %d daily usage statistics (latest: %s, cumulative: %.2f CCF)",
                len(statistics),
                statistics[-1]["start"].strftime("%Y-%m-%d") if statistics else "N/A",
                cumulative_sum
            )
        except Exception as e:
            _LOGGER.error("Failed to add external statistics: %s", e)

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

            self.base_load = float(intercept)
            self.heating_coeff = float(slope)
            self.balance_temp = float(balance_temp)
            self.r_squared = float(r2)

            _LOGGER.info(
                "Model updated: Base=%.2f CCF, Heating=%.4f CCF/HDD, Balance=%.1f°F, R²=%.3f (%d points)",
                self.base_load, self.heating_coeff, self.balance_temp, self.r_squared, len(data_points)
            )

            if r2 < 0.5:
                _LOGGER.warning(
                    "Low model fit quality (R²=%.3f). This may indicate: "
                    "(1) Insufficient data, (2) Irregular usage patterns, or (3) Non-heating gas consumption. "
                    "Model will improve with more data.",
                    r2
                )

    def _fit_linear_regression(self, x_values, y_values):
        """Fit a linear model: y = slope * x + intercept."""
        n = len(x_values)
        if n == 0:
            return None, None, None, None
        
        sum_x = sum(x_values)
        sum_y = sum(y_values)
        sum_xx = sum(x * x for x in x_values)
        sum_xy = sum(x * y for x, y in zip(x_values, y_values))
        
        denom = (n * sum_xx - sum_x * sum_x)
        if abs(denom) < 1e-10:
            return None, None, None, None
        
        slope = (n * sum_xy - sum_x * sum_y) / denom
        intercept = (sum_y - slope * sum_x) / n
        
        # Calculate R²
        y_mean = sum_y / n
        y_pred = [slope * x + intercept for x in x_values]
        
        ss_tot = sum((y - y_mean) ** 2 for y in y_values)
        ss_res = sum((y - yp) ** 2 for y, yp in zip(y_values, y_pred))
        
        sse = ss_res
        r_squared = 1 - (ss_res / ss_tot) if ss_tot > 0 else 0.0
        
        return slope, intercept, sse, r_squared

    async def _calculate_predicted_usage_7d(self) -> float | None:
        """Predict gas usage for the next 7 days using weather forecast and regression model."""
        
        # Skip prediction if model isn't trained
        if self.r_squared < 0.3 or len(self._history) < 10:
            _LOGGER.debug("Model not ready for prediction (R²=%.3f, history=%d)", self.r_squared, len(self._history))
            return None

        weather_entity = self.config_entry.options.get(CONF_WEATHER_ENTITY)
        if not weather_entity:
            return None

        if not self.hass.states.get(weather_entity):
            if self.r_squared > 0:
                _LOGGER.warning("Weather entity %s not found, predictions disabled", weather_entity)
            return None

        try:
            response = await self.hass.services.async_call(
                "weather",
                "get_forecasts",
                {"type": "daily", "entity_id": weather_entity},
                blocking=True,
                return_response=True,
            )

            forecast_data = response.get(weather_entity, {}).get("forecast", [])
            if not forecast_data:
                return None

            predicted_usage = 0.0
            forecast_days = min(7, len(forecast_data))

            for day_data in forecast_data[:forecast_days]:
                high_temp = day_data.get("native_temperature") or day_data.get("temperature")
                low_temp = day_data.get("native_temp_low") or day_data.get("templow")

                if high_temp is None or low_temp is None:
                    continue

                avg_temp = (float(high_temp) + float(low_temp)) / 2.0
                hdd = max(0, self.balance_temp - avg_temp)
                daily_usage = self.base_load + (self.heating_coeff * hdd)
                predicted_usage += max(0, daily_usage)

            return round(predicted_usage, 2)

        except Exception as e:
            _LOGGER.debug("Error calculating predicted usage: %s", e)
            return None

    async def _calculate_projected_add(self, data: dict) -> int:
        """Calculate projected Actual Degree Days for billing period.
        
        Combines:
        - Actual HDD from past days (from temperature data in XLS)
        - Forecasted HDD for remaining days (from weather forecast)
        """
        # Get actual HDD from XLS data
        actual_hdd = data.get("actual_hdd", 0)
        billing_days = data.get("billing_period_days", 0)
        
        # Typical billing period is 30 days
        days_remaining = max(0, 30 - billing_days)
        
        if days_remaining == 0:
            return actual_hdd
        
        # Forecast remaining days using weather
        forecast_hdd = 0
        
        weather_entity = self.config_entry.options.get(CONF_WEATHER_ENTITY)
        if not weather_entity:
            # No weather entity - use historical NDD as fallback
            billing_start = data.get(ATTR_BILLING_PERIOD_START)
            billing_month = datetime.now().month
            if billing_start:
                try:
                    billing_month = datetime.strptime(billing_start.split()[0], "%Y-%m-%d").month
                except:
                    pass
            
            monthly_ndd = self.wna_calculator.get_ndd_for_month(billing_month)
            if monthly_ndd > 0:
                avg_daily_hdd = monthly_ndd / 30
                forecast_hdd = avg_daily_hdd * days_remaining
            
            return int(actual_hdd + forecast_hdd)
        
        try:
            # Get weather forecast
            response = await self.hass.services.async_call(
                "weather",
                "get_forecasts",
                {"type": "daily", "entity_id": weather_entity},
                blocking=True,
                return_response=True,
            )
            
            forecast_data = response.get(weather_entity, {}).get("forecast", [])
            
            # Use min(days_remaining, forecast_length) days
            days_to_forecast = min(days_remaining, len(forecast_data))
            
            for day in forecast_data[:days_to_forecast]:
                high = day.get("native_temperature") or day.get("temperature")
                low = day.get("native_temp_low") or day.get("templow")
                
                if high is not None and low is not None:
                    avg_temp = (float(high) + float(low)) / 2
                    daily_hdd = max(0, 65 - avg_temp)
                    forecast_hdd += daily_hdd
            
            # If forecast doesn't cover all remaining days, extrapolate
            if days_to_forecast < days_remaining:
                avg_forecast_hdd = forecast_hdd / days_to_forecast if days_to_forecast > 0 else 0
                remaining_days = days_remaining - days_to_forecast
                forecast_hdd += avg_forecast_hdd * remaining_days
                
        except Exception as e:
            _LOGGER.warning("Could not get weather forecast for HDD projection: %s", e)
            
            # Fallback: Use historical NDD to estimate
            billing_start = data.get(ATTR_BILLING_PERIOD_START)
            billing_month = datetime.now().month
            if billing_start:
                try:
                    billing_month = datetime.strptime(billing_start.split()[0], "%Y-%m-%d").month
                except:
                    pass
            
            monthly_ndd = self.wna_calculator.get_ndd_for_month(billing_month)
            if monthly_ndd > 0:
                avg_daily_hdd = monthly_ndd / 30
                forecast_hdd = avg_daily_hdd * days_remaining
        
        projected_total = actual_hdd + int(forecast_hdd)
        
        _LOGGER.debug(
            "HDD Projection: actual=%d (from %d days), forecast=%d (for %d days), total=%d",
            actual_hdd, billing_days, int(forecast_hdd), days_remaining, projected_total
        )
        
        return projected_total

    def _parse_next_read_date(self, date_str: str | None) -> datetime | None:
        """Parse next meter read date string to datetime with extensive fallbacks."""
        if not date_str:
            return None
        
        # 1. Try HA's ISO parser first
        dt = dt_util.parse_datetime(date_str)
        if dt:
            return dt
            
        # 2. Try common Atmos formats
        # Add the complex format seen in logs: Wed Mar 11 00:00:00 CDT 2026
        # Note: %Z can be unreliable, so we try a few variations
        formats = (
            "%m/%d/%Y", 
            "%Y-%m-%d", 
            "%d-%m-%Y",
            "%a %b %d %H:%M:%S %Z %Y",  # Wed Mar 11 00:00:00 CDT 2026
            "%a %b %d %H:%M:%S %Y",     # Without TZ
            "%b %d %Y",                 # Minimal
        )
        
        for fmt in formats:
            try:
                # If the format contains %Z but the string has an abbreviation Python doesn't like,
                # strptime might still fail. 
                naive_dt = datetime.strptime(date_str, fmt)
                return dt_util.as_local(naive_dt)
            except ValueError:
                continue

        # 3. Last resort: Regex/Split extraction for "Month Day ... Year"
        # Handles "Wed Mar 11 00:00:00 CDT 2026" manual extraction
        try:
            parts = date_str.split()
            if len(parts) >= 6:
                # Expecting: [DayOfWeek, Month, Day, Time, TZ, Year]
                month_str = parts[1]
                day_str = parts[2]
                year_str = parts[5]
                cleaned_date = f"{month_str} {day_str} {year_str}"
                naive_dt = datetime.strptime(cleaned_date, "%b %d %Y")
                return dt_util.as_local(naive_dt)
        except (ValueError, IndexError):
            pass
                
        _LOGGER.debug("Could not parse next meter read date: %s", date_str)
        return None

    def calculate_total_cost(
        self, 
        usage: float, 
        include_fixed: bool = True, 
        wna_charge: float | None = None,
        pro_rate_days: int | None = None
    ) -> dict[str, Any]:
        """Calculate total cost with all components.
        
        Args:
            usage: Usage in CCF
            include_fixed: Include fixed charge
            wna_charge: Pre-calculated WNA charge (if None, will calculate)
            pro_rate_days: If set, pro-rate fixed charge for this many days
        
        Returns:
            Dict with 'total' and 'breakdown' keys
        """
        options = self.config_entry.options
        
        # Fixed charge
        fixed = float(options.get("fixed_cost", 25.03)) if include_fixed else 0.0
        
        # Pro-rate fixed charge if requested
        if pro_rate_days is not None and include_fixed:
            fixed = fixed * (pro_rate_days / 30.0)
        
        # Consumption charge
        consumption_rate = float(options.get("usage_rate", 0.78))
        consumption_charge = usage * consumption_rate
        
        # WNA charge (use provided or calculate)
        if wna_charge is None:
            wna_info = self.data.get("wna_calculated", {}) if self.data else {}
            wna_charge = wna_info.get("wna_charge", 0.0)
        
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

        mode = self.config_entry.data.get(CONF_OPERATION_MODE, MODE_DAILY_ADVANCED)
        daily_usage = mode != MODE_MONTHLY
        
        try:
            data = await self.client.get_account_data(daily_usage=daily_usage)
            
            # Parse next meter read date once
            data["next_meter_read_dt"] = self._parse_next_read_date(data.get("next_meter_read_date"))
            
            # 1. Daily usage specific tasks
            if daily_usage:
                await self._update_gcr_rate()
                await self._async_import_daily_statistics(data)
                
                # 2. Advanced Prediction Logic (Only MODE_DAILY_ADVANCED)
                if mode == MODE_DAILY_ADVANCED:
                    await self._update_advanced_metrics(data)

                # 3. Synchronize History
                await self._sync_history(data)

            # 4. Basic validation
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

    async def _update_gcr_rate(self):
        """Update GCR rate if enabled."""
        use_auto_gcr = self.config_entry.options.get(CONF_AUTO_FETCH_GCR, True)
        if use_auto_gcr:
            try:
                gcr_rate = await self.gcr_fetcher.get_current_rate()
                if gcr_rate:
                    self.current_gcr_rate = gcr_rate
            except Exception as e:
                _LOGGER.debug("Could not fetch GCR rate: %s", e)

    async def _update_advanced_metrics(self, data: dict):
        """Calculate WNA, projections, and predictions."""
        # 1. Recalculate physical model
        self._recalculate_model()
        
        # 2. Projected ADD for WNA
        data["projected_add"] = await self._calculate_projected_add(data)
        
        # 3. Pre-calculate WNA Charge
        usage = data.get(ATTR_USAGE, 0)
        billing_start = data.get(ATTR_BILLING_PERIOD_START)
        billing_month = datetime.now().month
        if billing_start:
            try:
                # String part index 1 is month in YYYY-MM-DD
                billing_month = int(billing_start.split("-")[1])
            except:
                pass
        
        projected_add = data.get("projected_add", self.wna_calculator.get_ndd_for_month(billing_month))
        
        # Perform calculation
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
        
        # 4. Predictions
        predicted_usage_7d = await self._calculate_predicted_usage_7d()
        data["predicted_usage_7d"] = predicted_usage_7d
        
        if predicted_usage_7d is not None:
            p_wna_charge = predicted_usage_7d * (wnaf_cents / 100.0)
            cost_data = self.calculate_total_cost(
                predicted_usage_7d, 
                include_fixed=True, 
                wna_charge=p_wna_charge,
                pro_rate_days=7
            )
            data["predicted_cost_7d"] = cost_data["total"]
            data["predicted_cost_7d_breakdown"] = cost_data["breakdown"]

    async def _sync_history(self, data: dict):
        """Sync new history records and prune old ones."""
        new_history = data.get("history", [])
        if not new_history:
            return
            
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
