"""DataUpdateCoordinator for Atmos Energy."""
import logging
import math
import asyncio
import os
from datetime import datetime, timedelta
from typing import Any

import aiohttp
import pandas as pd
from homeassistant.config_entries import ConfigEntry
from homeassistant.exceptions import ConfigEntryAuthFailed, ServiceValidationError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util, slugify
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    statistics_during_period,
    get_last_statistics,
    StatisticMetaData,
    StatisticData,
)
from homeassistant.const import UnitOfVolume, CONF_USERNAME

from .const import (
    DOMAIN, 
    SCAN_INTERVAL, 
    CONF_OPERATION_MODE,
    CONF_WEATHER_STATION,
    CONF_WEATHER_ENTITY,
    CONF_AUTO_FETCH_GCR,
    CONF_GCR_RATE,
    CONF_REGION,
    CONF_FIXED_COST_SUMMER,
    CONF_USE_PUBLISHED_RATES,
    STORAGE_KEY,
    STORAGE_VERSION,
    CONF_ACCOUNT_NUMBER,
    CONF_SERVICE_ADDRESS,
    CONF_BACKFILL_HISTORY,
    history_storage_key,
    account_selection_issue_id,
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
from .exceptions import AuthenticationError, APIError, DataParseError, AccountSelectionError
from .wna.calculator import WNACalculator
from .gas_cost import GasCostFetcher
from .published_rates import PublishedRates
from .rates_data import base_rates, wna_params
from .regions import DEFAULT_REGION, SUMMER_MONTHS, WNA_REGION, get_region

_LOGGER = logging.getLogger(__name__)

# A day's usage above both of these (CCF, and times the bill period's typical
# day) is taken to be a meter reading the portal put in the consumption column.
_METER_READ_MIN = 100.0
_METER_READ_FACTOR = 20.0

def _bill_month(billing_month: str | None) -> datetime | None:
    """The first of a daily record's billing month, e.g. "Oct 26" -> 2026-10-01."""
    if not billing_month:
        return None
    try:
        return datetime.strptime(billing_month.strip(), "%b %y")
    except ValueError:
        return None


# Serializes the one-time move of the old shared history file, so only one entry claims it
_LEGACY_STORE_LOCK = asyncio.Lock()

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
        
        # Monthly gas cost (GCR / GCA / PGA) for the entry's rate region
        self.region = entry.options.get(CONF_REGION, DEFAULT_REGION)
        self.gcr_fetcher = GasCostFetcher(hass, self.region, get_region(self.region).gas_cost)
        self.current_gcr_rate = None
        self._wna_info: dict[str, Any] = {}  # Latest WNA result, before self.data is replaced
        # Yearly tariff values (rates.json), checked weekly
        self.published_rates = PublishedRates(hass)

        # Persistent storage for history, one file per entry (each entry is one meter)
        self._store = Store(hass, STORAGE_VERSION, history_storage_key(entry.entry_id))
        self._history = {} # Keyed by date string YYYY-MM-DD
        self._unsaved_keys = set()  # Track keys that need to be saved
        
        self.base_load = DEFAULT_BASE_LOAD
        self.heating_coeff = DEFAULT_HEATING_COEFF
        self.balance_temp = DEFAULT_BALANCE_TEMP
        self.r_squared = 0.0
        self._last_optimization_count = 0
        self.last_update: datetime | None = None
        # Set once every past billing period the portal offers has been imported
        self._backfill_done = False
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=SCAN_INTERVAL,
        )

    @property
    def history_count(self) -> int:
        """Days of usage history stored for this entry."""
        return len(self._history)

    def _street(self) -> str:
        """First line of the service address; set only for multi-account logins."""
        return self.config_entry.data.get(CONF_SERVICE_ADDRESS, "").split(",")[0]

    def _statistic_label(self) -> str:
        """Tells this entry's statistics apart in the Energy dashboard."""
        return self._street() or self.config_entry.data.get(CONF_USERNAME, "unknown")

    async def _async_migrate_legacy_store(self):
        """Move the old shared history file into this entry's store, once.

        Only entries created before multi-account support can own it; those keep the
        username as their unique_id, while new multi-account entries use the account number.
        """
        if self.config_entry.unique_id == self.config_entry.data.get(CONF_ACCOUNT_NUMBER):
            return
        async with _LEGACY_STORE_LOCK:
            if await self._store.async_load() is not None:
                return
            legacy = Store(self.hass, STORAGE_VERSION, STORAGE_KEY)
            stored = await legacy.async_load()
            if stored is None:
                return
            await self._store.async_save(stored)
            await legacy.async_remove()
            _LOGGER.info("Moved shared Atmos Energy history into the store for entry %s", self.config_entry.entry_id)

    async def _async_load_history(self):
        """Load history from storage."""
        try:
            await self._async_migrate_legacy_store()

            # Load GCR cache
            await self.gcr_fetcher.async_load()
            await self.published_rates.async_load()
            self._apply_published_wna()

            stored = await self._store.async_load()
            if stored:
                self._history = stored.get("history", {})
                self._backfill_done = stored.get("backfill_done", False)
                last_update_str = stored.get("last_update")
                if last_update_str:
                    try:
                        self.last_update = dt_util.parse_datetime(last_update_str)
                    except Exception:
                        pass
                
                # Restore full data state if update is recent
                self.data = stored.get("data", {})
                
                _LOGGER.debug("Loaded %d days of history and last update (%s) from storage", len(self._history), self.last_update)
                self._recalculate_model()
        except Exception as e:
            _LOGGER.warning("Failed to load Atmos history: %s", e)

    async def _async_save_history(self):
        """Save history and metadata to storage."""
        
        try:
            stored = await self._store.async_load() or {}
            history = stored.get("history", {})
            
            for key in self._unsaved_keys:
                if key in self._history:
                    history[key] = self._history[key]
            
            payload = {"history": history, "backfill_done": self._backfill_done}
            if self.last_update:
                payload["last_update"] = self.last_update.isoformat()
            
            # Persist the full data dictionary (usage, WNA, predictions, etc.)
            if self.data:
                payload["data"] = self.data
                
            await self._store.async_save(payload)
            self._unsaved_keys.clear()
            _LOGGER.debug("Saved history and metadata to storage")
        except Exception as e:
            _LOGGER.error("Failed to save Atmos history: %s", e)

    async def _async_import_daily_statistics(self, data: dict):
        """Import daily usage statistics with correct timestamps."""
        history = data.get("history", [])
        if not history:
            return
        
        await self._async_import_statistics_list(history)

    async def _async_import_statistics_list(self, history: list[dict]):
        """Import a list of usage records into the Statistics API."""
        if not history:
            return

        # 1. Parse dates and sort chronologically (not alphabetically!)
        parsed_history = []
        for entry in history:
            date_str = entry.get("date", "")
            dt = self._parse_date(date_str)
            if dt:
                # Store parsed date for sorting but keep it naive/normalized to date
                parsed_history.append({**entry, "_dt": dt})
        
        if not parsed_history:
            _LOGGER.warning("No valid dates found in import batch")
            return

        # Sort by the actual datetime objects
        parsed_history.sort(key=lambda x: x["_dt"])
        sorted_history = parsed_history

        # External statistic IDs must be valid slugs: lowercase, start with a letter.
        # Config entry IDs can start with digits and contain uppercase, so we slugify
        # and prefix with "usage_" to guarantee a letter-first slug.
        safe_entry_id = slugify(self.config_entry.entry_id)
        statistic_id = f"{DOMAIN}:usage_{safe_entry_id}"

        # Try to find the current cumulative sum from the database
        # to ensure we don't cause negative spikes in the Energy Dashboard.
        cumulative_sum = 0.0
        try:
            # We need the sum from the point JUST BEFORE our new data starts.
            # first_dt is the earliest record in this batch
            first_dt = sorted_history[0]["_dt"]
            
            # Force to midnight LOCAL, then convert to UTC (matching the loop below)
            # This ensures the search cutoff matches the record timestamps.
            start_of_import = dt_util.as_utc(datetime(first_dt.year, first_dt.month, first_dt.day))
            
            _LOGGER.debug(
                "Checking for predecessor sum before %s (statistic_id: %s)", 
                start_of_import, statistic_id
            )

            # 2. Query for the last statistic BEFORE this start_of_import.
            # We look back up to 1 year to find a predecessor. 
            # statistics_during_period returns a dict of lists of StatisticData.
            stats = await get_instance(self.hass).async_add_executor_job(
                statistics_during_period,
                self.hass,
                start_of_import - timedelta(days=365),
                start_of_import,
                {statistic_id},
                "day",
                None,
                {"sum"}
            )
            
            if stats and statistic_id in stats and stats[statistic_id]:
                # We want the last record that is STRICTLY before the start of our current day
                # Filter to ensure we don't accidentally pick up a record from the current batch 
                # that might exist from a previous failed or partial import.
                valid_stats = []
                for s in stats[statistic_id]:
                    start_val = s.get("start")
                    if isinstance(start_val, (int, float)):
                        start_dt = dt_util.utc_from_timestamp(start_val)
                    else:
                        start_dt = start_val
                        
                    if start_dt and start_dt < start_of_import:
                        valid_stats.append(s)
                
                if valid_stats:
                    last_record = valid_stats[-1]
                    cumulative_sum = last_record.get("sum", 0.0)
                    _LOGGER.debug(
                        "Found predecessor sum for %s: %.2f (from %s). Batch starts at %s", 
                        statistic_id, cumulative_sum, last_record.get("start"), start_of_import
                    )
                else:
                    _LOGGER.debug("No records found strictly before %s in search window", start_of_import)
            else:
                valid_stats = []

            if not valid_stats:
                # 3. Fallback: If no predecessor found in window, check absolute latest
                last_stats = await get_instance(self.hass).async_add_executor_job(
                    get_last_statistics, self.hass, 1, statistic_id, True, {"sum"}
                )
                if last_stats and statistic_id in last_stats:
                    latest_record = last_stats[statistic_id][0]
                    
                    # Normalize start for comparison
                    l_start = latest_record.get("start")
                    if isinstance(l_start, (int, float)):
                        l_start_dt = dt_util.utc_from_timestamp(l_start)
                    else:
                        l_start_dt = l_start

                    # Only use it if it's strictly before our batch starts
                    if l_start_dt and l_start_dt < start_of_import:
                        cumulative_sum = latest_record.get("sum", 0.0)
                        _LOGGER.debug(
                            "Found predecessor from absolute latest: %.2f (from %s)",
                            cumulative_sum, latest_record.get("start")
                        )
                    else:
                        _LOGGER.debug(
                            "Absolute latest record (%s) is at or after batch start (%s). Ignoring.",
                            latest_record.get("start"), start_of_import
                        )
                        cumulative_sum = 0.0
                else:
                    _LOGGER.info("No prior statistics found for %s. Starting new timeline at 0.0.", statistic_id)
                    
        except Exception as e:
            _LOGGER.error("Logic error determining continuity sum for %s: %s", statistic_id, e)
            # We still continue with cumulative_sum = 0.0 to avoid crashing the whole update

        metadata = StatisticMetaData(
            has_mean=False,
            mean_type=0,
            has_sum=True,
            name=f"Atmos Energy Daily Usage ({self._statistic_label()})",
            source=DOMAIN,  # External statistics must use the integration domain
            statistic_id=statistic_id,
            unit_of_measurement=UnitOfVolume.CENTUM_CUBIC_FEET,
            unit_class="volume",
        )

        statistics = []
        
        for record in sorted_history:
            dt = record["_dt"]
            usage = record.get("usage")
            
            if usage is None:
                continue
            
            try:
                # dt is already a naive datetime at midnight (from _parse_date)
                timestamp_utc = dt_util.as_utc(dt)
                
                usage_val = float(usage)
                cumulative_sum += usage_val
                
                statistics.append(StatisticData(
                    start=timestamp_utc,
                    state=usage_val,
                    sum=cumulative_sum,
                ))
            except Exception:
                continue
        
        if not statistics:
            return

        try:
            async_add_external_statistics(self.hass, metadata, statistics)
            _LOGGER.info(
                "Successfully imported %d daily usage statistics to %s",
                len(statistics),
                statistic_id
            )
        except Exception as e:
            _LOGGER.error("Failed to add external statistics: %s", e)

        # ===== IMPORT COST STATISTICS ====================================
        # Build a parallel cost statistic so the Energy Dashboard can show
        # accurate daily costs (WNA + GCR + URI + tax) instead of a simple
        # price-per-CCF multiplication.
        cost_statistic_id = f"{DOMAIN}:cost_{safe_entry_id}"

        # Determine the predecessor cost cumulative sum (same pattern as usage).
        cost_cumulative_sum = 0.0
        try:
            cost_stats = await get_instance(self.hass).async_add_executor_job(
                statistics_during_period,
                self.hass,
                start_of_import - timedelta(days=365),
                start_of_import,
                {cost_statistic_id},
                "day",
                None,
                {"sum"}
            )

            if cost_stats and cost_statistic_id in cost_stats and cost_stats[cost_statistic_id]:
                valid_cost_stats = [
                    s for s in cost_stats[cost_statistic_id]
                    if (
                        lambda sv: sv < start_of_import
                    )(
                        dt_util.utc_from_timestamp(s["start"])
                        if isinstance(s.get("start"), (int, float))
                        else s["start"]
                    )
                ]
                if valid_cost_stats:
                    cost_cumulative_sum = valid_cost_stats[-1].get("sum", 0.0)
                    _LOGGER.debug("Found predecessor cost sum for %s: $%.2f", cost_statistic_id, cost_cumulative_sum)

            if cost_cumulative_sum == 0.0:
                last_cost_stats = await get_instance(self.hass).async_add_executor_job(
                    get_last_statistics, self.hass, 1, cost_statistic_id, True, {"sum"}
                )
                if last_cost_stats and cost_statistic_id in last_cost_stats:
                    latest_cost = last_cost_stats[cost_statistic_id][0]
                    c_start = latest_cost.get("start")
                    if isinstance(c_start, (int, float)):
                        c_start = dt_util.utc_from_timestamp(c_start)
                    # Same rule as the usage sum: a record inside this batch gets
                    # rewritten below, so it can't be the predecessor.
                    if c_start and c_start < start_of_import:
                        cost_cumulative_sum = latest_cost.get("sum", 0.0)
                        _LOGGER.debug("Fallback cost predecessor sum: $%.2f", cost_cumulative_sum)
                    else:
                        _LOGGER.debug(
                            "Latest cost record (%s) is at or after batch start (%s). Ignoring.",
                            latest_cost.get("start"), start_of_import
                        )

        except Exception as e:
            _LOGGER.debug("Could not determine predecessor cost sum for %s: %s", cost_statistic_id, e)

        # Build cost StatisticData list. A billed day is priced like its bill:
        # gas cost for the bill's month and the customer charge spread over
        # the bill period's days. Days of the open current period have no
        # billing month yet; they use their own month and 30 days until the
        # period is billed and re-imported.
        period_days: dict[str, int] = {}
        for record in sorted_history:
            if month := record.get("billing_month"):
                period_days[month] = period_days.get(month, 0) + 1
        cost_statistics = []
        for record in sorted_history:
            dt = record["_dt"]
            usage = record.get("usage")
            if usage is None:
                continue
            try:
                usage_val = float(usage)
                month = record.get("billing_month")
                daily_cost = self._calculate_daily_cost_for_date(
                    usage_val, dt, _bill_month(month), period_days.get(month, 30)
                )
                cost_cumulative_sum += daily_cost
                timestamp_utc = dt_util.as_utc(dt)
                cost_statistics.append(StatisticData(
                    start=timestamp_utc,
                    state=daily_cost,
                    sum=cost_cumulative_sum,
                ))
            except Exception:
                continue

        if cost_statistics:
            cost_metadata = StatisticMetaData(
                has_mean=False,
                mean_type=0,
                has_sum=True,
                name="Atmos Energy Daily Cost" + (f" ({self._street()})" if self._street() else ""),
                source=DOMAIN,
                statistic_id=cost_statistic_id,
                unit_of_measurement="USD",
                unit_class=None,
            )
            try:
                async_add_external_statistics(self.hass, cost_metadata, cost_statistics)
                _LOGGER.info(
                    "Successfully imported %d daily cost statistics to %s (total: $%.2f)",
                    len(cost_statistics),
                    cost_statistic_id,
                    cost_cumulative_sum,
                )
            except Exception as e:
                _LOGGER.error("Failed to add cost statistics: %s", e)
        else:
            _LOGGER.debug("No cost statistics to import for %s", cost_statistic_id)

    async def async_import_historical_xls(self, file_path: str):
        """Import historical daily usage from a local XLS file."""
        _LOGGER.info("Starting historical data import from: %s", file_path)

        # Resolving the path and reading the file both touch the disk, so both run in the executor
        def read_file():
            path = self._resolve_import_path(file_path)
            try:
                with open(path, 'rb') as f:
                    return f.read()
            except (FileNotFoundError, IsADirectoryError) as err:
                raise ServiceValidationError(f"Historical XLS file not found: {file_path}") from err

        content = await self.hass.async_add_executor_job(read_file)

        try:
            # Use the API client's parsing logic (it doesn't require a live session)
            # Use public helper method if available, or call protected one
            data = await self.client._parse_xls_data(content)
            history = data.get("history", [])
            
            if history:
                _LOGGER.info("Found %d records in %s, importing...", len(history), file_path)
                history = await self._async_repair_meter_read_days(history)
                await self._async_import_statistics_list(history)
            else:
                _LOGGER.warning("No usage records found in %s. Ensure this is a DAILY usage spreadsheet.", file_path)
                
        except Exception as e:
            _LOGGER.error("Failed to import historical data from %s: %s", file_path, e)
            raise

    def _resolve_import_path(self, file_path: str) -> str:
        """The real path of a spreadsheet to import. Blocking: run it in the executor.

        A relative path is taken from the config folder. The file must be inside the
        config folder or a folder in allowlist_external_dirs; symlinks and ".." are
        resolved first, so neither can point outside them.
        """
        config_dir = os.path.realpath(self.hass.config.config_dir)
        # join() ignores config_dir when file_path is absolute
        path = os.path.realpath(os.path.join(config_dir, file_path))
        try:
            in_config = os.path.commonpath([path, config_dir]) == config_dir
        except ValueError:  # different drives
            in_config = False
        if in_config or self.hass.config.is_allowed_path(path):
            return path
        raise ServiceValidationError(
            f"{file_path} is outside the Home Assistant config folder and allowlist_external_dirs"
        )

    def _parse_date(self, date_str: str) -> datetime | None:
        """Parse Atmos date string into datetime object."""
        if not date_str:
            return None
            
        # Try common Atmos formats
        for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S"):
            try:
                dt = datetime.strptime(date_str, fmt)
                # Normalize to midnight for daily usage
                return datetime(dt.year, dt.month, dt.day)
            except ValueError:
                continue
                
        # Fallback to dt_util
        dt = dt_util.parse_datetime(date_str)
        if dt:
            return datetime(dt.year, dt.month, dt.day)
        return None

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

    def _calculate_daily_cost_for_date(
        self,
        usage: float,
        day: datetime | None = None,
        bill_month: datetime | None = None,
        period_days: float = 30.0,
    ) -> float:
        """Calculate the accurate cost for a single day's usage.

        Reuses calculate_total_cost with pro_rate_days=1 to correctly
        pro-rate the fixed monthly charge for one day, while applying
        full WNA, GCR, URI and tax on the variable portion.

        Args:
            usage: Daily gas usage in CCF.
            day: The day being priced, so it gets that month's gas cost
                and customer charge. None prices it at today's rates.
            bill_month: The month of the bill the day is on, whose gas cost
                applies; None uses the day's own month.
            period_days: Days in the day's bill period.

        Returns:
            Cost in USD for that day's usage, unrounded so a bill period's
            days add up to its bill.
        """
        result = self.calculate_total_cost(
            usage=usage,
            include_fixed=True,
            pro_rate_days=1,
            when=day,
            gas_cost_month=bill_month,
            period_days=period_days,
        )
        return result["total_exact"]

    def _gas_cost_rate(self, when: datetime | None) -> float:
        """Gas cost $/CCF: the fetched rate for `when`'s month, else the current one, else the option."""
        if when is not None and self.config_entry.options.get(CONF_AUTO_FETCH_GCR, True):
            rate = self.gcr_fetcher.rate_for(when.year, when.month)
            if rate is not None:
                return rate
        return float(self.current_gcr_rate or self.config_entry.options.get(CONF_GCR_RATE, 1.17))

    def _wna_rate(self, when: datetime | None) -> float:
        """WNA $/CCF for a day (Mid-Tex advanced mode only; 0 elsewhere).

        The factor is projected for the current billing period, so it only applies to
        days in that period. Earlier periods' factors aren't known, so their days get none.
        """
        wna_info = self._wna_info or (self.data.get("wna_calculated", {}) if self.data else {})
        rate = float(wna_info.get("wnaf_rate_raw", 0.0))
        if when is not None and rate:
            period_start = self._parse_date(wna_info.get("billing_period_start") or "")
            if period_start is None or when.date() < period_start.date():
                return 0.0
        return rate

    def _base_rates(self, day: datetime) -> dict[str, Any]:
        """Customer charges and consumption rate in effect on `day`.

        From the published rates file when the entry uses it (and it covers the region),
        otherwise the entry's own options.
        """
        options = self.config_entry.options
        if options.get(CONF_USE_PUBLISHED_RATES, False):
            published = base_rates(self.published_rates.doc, self.region, day.date())
            if published:
                return {
                    "fixed_cost": published["fixed_cost"],
                    "fixed_cost_summer": published.get("fixed_cost_summer"),
                    "usage_rate": published["usage_rate"],
                }
        return {
            "fixed_cost": options.get("fixed_cost", 24.40),
            "fixed_cost_summer": options.get(CONF_FIXED_COST_SUMMER),
            "usage_rate": options.get("usage_rate", 0.78),
        }

    def _apply_published_wna(self):
        """Use the published Mid-Tex WNA parameters for this entry's weather station, if any."""
        if self.region != WNA_REGION:
            return
        params = wna_params(self.published_rates.doc, WNA_REGION, self.wna_calculator.station_id)
        if params:
            self.wna_calculator.apply_published(params)

    def calculate_total_cost(
        self,
        usage: float,
        include_fixed: bool = True,
        wna_charge: float | None = None,
        pro_rate_days: int | None = None,
        when: datetime | None = None,
        gas_cost_month: datetime | None = None,
        period_days: float = 30.0,
    ) -> dict[str, Any]:
        """Calculate total cost with all components.

        Args:
            usage: Usage in CCF
            include_fixed: Include fixed charge
            wna_charge: Pre-calculated WNA charge (if None, will calculate)
            pro_rate_days: If set, pro-rate fixed charge for this many days
            when: Date being priced, for its month's gas cost and seasonal
                customer charge (None: today)
            gas_cost_month: Month whose gas cost applies, when it isn't
                `when`'s: a bill charges its own month's rate for every day
            period_days: Days the fixed charge is spread over

        Returns:
            Dict with 'total' and 'breakdown' keys
        """
        options = self.config_entry.options
        day = when or dt_util.now()
        base = self._base_rates(day)

        # Fixed charge, with a separate May-September value in regions that have one
        fixed = base["fixed_cost"]
        if day.month in SUMMER_MONTHS and base["fixed_cost_summer"] is not None:
            fixed = base["fixed_cost_summer"]
        fixed = float(fixed) if include_fixed else 0.0

        # Pro-rate fixed charge if requested
        if pro_rate_days is not None and include_fixed:
            fixed = fixed * (pro_rate_days / period_days)

        # Consumption charge
        consumption_rate = float(base["usage_rate"])
        consumption_charge = usage * consumption_rate
        
        # WNA charge
        # If a total wna_charge is strictly provided (e.g. for a full bill), use it.
        # Otherwise, calculate it proportionally using wna_factor (rate per CCF) so 
        # we don't accidentally apply a full month's WNA charge to a single day.
        if wna_charge is not None:
            pass # Use the explicit charge provided
        else:
            wna_charge = usage * self._wna_rate(when)
        
        # Gas cost charge (GCR / GCA / PGA)
        gcr_rate = self._gas_cost_rate(gas_cost_month or when)
        gcr_charge = usage * gcr_rate

        # Other per-CCF charges (Mid-Tex: Winter Storm Uri surcharge)
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
            "total_exact": total_cost,
            "breakdown": {
                "fixed_charge": round(fixed, 2),
                "consumption_charge": round(consumption_charge, 2),
                "wna_charge": round(wna_charge, 2),
                "gcr_charge": round(gcr_charge, 2),
                "uri_charge": round(uri_charge, 2),
                "tax_amount": round(tax_amount, 2),
                "subtotal": round(subtotal, 2),
                "rates": {
                    "consumption": consumption_rate,
                    "gcr": round(gcr_rate, 4),
                    "uri": uri_rate,
                    "tax": tax_pct,
                }
            },
            "variable_rate_per_ccf": round((consumption_charge + wna_charge + gcr_charge + uri_charge + tax_amount) / usage, 4) if usage > 0 else 0.0
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
                if await self.published_rates.async_refresh():
                    self._apply_published_wna()

                # Past billing periods go to statistics and the model only;
                # data["history"] stays the current period.
                history = await self._async_add_past_periods(data.get("history", []))
                history = await self._async_repair_meter_read_days(history)
                current_dates = {r.get("date") for r in data.get("history", [])}
                if any("reported_usage" in r and r.get("date") in current_dates for r in history):
                    data["usage"] = sum(r["usage"] for r in history if r.get("date") in current_dates)

                # 2. Advanced Prediction Logic (Only MODE_DAILY_ADVANCED).
                # Before the statistics import, so daily costs get this update's WNA factor.
                if mode == MODE_DAILY_ADVANCED:
                    await self._update_advanced_metrics(data)

                await self._async_import_statistics_list(history)

                # 3. Synchronize History
                await self._sync_history({**data, "history": history})

            # 4. Basic validation
            usage = data.get("usage")
            if usage is not None:
                if usage < 0:
                    _LOGGER.warning("Negative usage value received: %s. Setting to 0", usage)
                    data["usage"] = 0.0
                elif usage > 10000:
                    _LOGGER.warning("Unusually high gas usage detected: %s CCF", usage)
            
            self.last_update = dt_util.now()
            await self._async_save_history()
            ir.async_delete_issue(self.hass, DOMAIN, account_selection_issue_id(self.config_entry.entry_id))
            return data
            
        except AuthenticationError as err:
            self.config_entry.async_start_reauth(self.hass)
            raise ConfigEntryAuthFailed(f"Authentication failed: {err}") from err
        except AccountSelectionError as err:
            # Setup has already succeeded by the time the first fetch runs, so without
            # this the reason only reaches the log and the entry looks healthy.
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                account_selection_issue_id(self.config_entry.entry_id),
                is_fixable=False,
                severity=ir.IssueSeverity.ERROR,
                translation_key="account_selection",
                translation_placeholders={"title": self.config_entry.title, "error": str(err)},
            )
            raise UpdateFailed(f"Error communicating with API: {err}") from err
        except (APIError, DataParseError, aiohttp.ClientError) as err:
            raise UpdateFailed(f"Error communicating with API: {err}") from err
        except Exception as err:
            _LOGGER.exception("Unexpected error updating Atmos Energy data")
            raise UpdateFailed(f"Unexpected error: {err}") from err
        finally:
            self._schedule_next_update()

    async def _async_add_past_periods(self, current: list[dict]) -> list[dict]:
        """Return past billing periods' daily records followed by the current period's.

        The first time, every past period the portal offers is fetched (about two
        years). After that only the latest past period is, so days that leave the
        current period at a meter read are still imported if Home Assistant missed
        the last update before it. Portal errors here never fail the update; the
        current period is still imported.
        """
        try:
            periods = await self.client.get_billing_periods()
            full_backfill = self._backfill_wanted()
            wanted = periods if full_backfill else periods[:1]
            # Oldest first, so a day in two periods keeps the newer period's value
            past = await self.client.get_daily_history(list(reversed(wanted)))
        # AuthenticationError is included on purpose: the current period was already
        # fetched this update, so a login-looking page from the extra pages (an
        # unfamiliar layout in another region, say) must not fail it or start reauth.
        except (APIError, AuthenticationError, DataParseError, aiohttp.ClientError, asyncio.TimeoutError) as err:
            _LOGGER.warning("Could not fetch past billing periods, importing the current one only: %s", err)
            return current

        if periods and full_backfill:
            self._backfill_done = True
            _LOGGER.info("Backfilled %d days from %d past billing periods", len(past), len(periods))
        return self._merge_history(past + current)

    def _backfill_wanted(self) -> bool:
        """True while the one-time bulk backfill is enabled for this entry and hasn't run."""
        return bool(self.config_entry.options.get(CONF_BACKFILL_HISTORY, False)) and not self._backfill_done

    async def _async_repair_meter_read_days(self, records: list[dict]) -> list[dict]:
        """Replace days whose usage is really a meter reading.

        The portal's daily download sometimes puts the meter reading in the
        consumption column (seen: 09/28/2026 = 4947, 09/29/2026 = 5056 for a
        home using 1-2 CCF a day). Each such day gets an equal share of what the
        bill period's billed usage (monthly download) leaves after its good days,
        or the period's typical day when the billed usage isn't available.
        """
        by_month: dict[str | None, list[int]] = {}
        for i, record in enumerate(records):
            by_month.setdefault(record.get("billing_month"), []).append(i)

        repaired = list(records)
        billed: dict[str, float] | None = None
        for month, indexes in by_month.items():
            values = sorted(float(records[i]["usage"]) for i in indexes)
            typical = values[len(values) // 2]
            limit = max(_METER_READ_MIN, _METER_READ_FACTOR * typical)
            bad = [i for i in indexes if float(records[i]["usage"]) > limit]
            if not bad:
                continue

            if billed is None:
                try:
                    billed = await self.client.get_billed_usage()
                except (APIError, DataParseError, aiohttp.ClientError, asyncio.TimeoutError) as err:
                    _LOGGER.warning("Could not fetch billed usage to repair daily data: %s", err)
                    billed = {}
            good_total = sum(float(records[i]["usage"]) for i in indexes if i not in bad)
            month_billed = billed.get(month) if month else None
            if month_billed is not None and month_billed >= good_total:
                fill = (month_billed - good_total) / len(bad)
            else:
                fill = typical
            for i in bad:
                _LOGGER.warning(
                    "Atmos reported %s CCF for %s, which looks like a meter reading; using %.2f CCF",
                    records[i]["usage"], records[i].get("date"), fill,
                )
                repaired[i] = {**records[i], "usage": fill, "reported_usage": records[i]["usage"]}
        return repaired

    def _merge_history(self, records: list[dict]) -> list[dict]:
        """Drop repeated days, keeping each day's last record, in date order."""
        by_day: dict[datetime, dict] = {}
        for record in records:
            day = self._parse_date(record.get("date", ""))
            if day is not None:
                by_day[day] = record
        return [by_day[day] for day in sorted(by_day)]

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
        """Calculate WNA (Mid-Tex only), projections, and predictions."""
        # 1. Recalculate physical model
        self._recalculate_model()

        usage = data.get(ATTR_USAGE, 0)
        billing_start = data.get(ATTR_BILLING_PERIOD_START)
        billing_month = datetime.now().month
        # The portal gives "YYYY-MM-DD ..." or "MM/DD/YYYY"
        billing_start_dt = self._parse_date(str(billing_start).split(" ")[0]) if billing_start else None
        if billing_start_dt:
            billing_month = billing_start_dt.month

        # 2. WNA charge. Its parameters are modelled for Mid-Tex only; other regions get none.
        if self.region == WNA_REGION:
            data["projected_add"] = await self._calculate_projected_add(data)
            projected_add = data.get("projected_add", self.wna_calculator.get_ndd_for_month(billing_month))
            wnaf_cents = self.wna_calculator.calculate_wnaf(billing_month, projected_add)
            wna_charge = self.wna_calculator.calculate_wna_charge(
                billing_month, projected_add, usage, wnaf_cents=wnaf_cents
            )
            ndd = self.wna_calculator.get_ndd_for_month(billing_month)
            station = self.wna_calculator.station_id
        else:
            projected_add, wnaf_cents, wna_charge, ndd, station = None, 0.0, 0.0, 0, None

        data["wna_calculated"] = {
            "wnaf_rate": f"${wnaf_cents/100:.6f}/CCF",
            "wnaf_rate_raw": wnaf_cents / 100.0,
            "wna_charge": wna_charge,
            "ndd": ndd,
            "add_projected": projected_add,
            "billing_month": billing_month,
            # The period wnaf_rate_raw applies to (daily cost statistics)
            "billing_period_start": billing_start_dt.strftime("%Y-%m-%d") if billing_start_dt else None,
            "weather_station": station,
        }
        self._wna_info = data["wna_calculated"]
        
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
                # Also replace a stored day whose usage changed, e.g. a repaired meter-read day
                if key not in self._history or self._history[key].get("usage") != record.get("usage"):
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

    async def async_setup_refresh(self):
        """Setup refresh logic for startup."""
        _LOGGER.debug("Starting Atmos Energy startup refresh check")
        
        # Ensure model is trained if history exists
        if self._history and not hasattr(self, "base_load"):
             self._recalculate_model()

        now = dt_util.now()
        
        # Calculate when the most recent scheduled update SHOULD have happened
        # (the 8 AM today, or 8 AM yesterday if it's not 8 AM yet)
        scheduled_today = now.replace(hour=8, minute=0, second=0, microsecond=0)
        reference_time = scheduled_today
        if now < scheduled_today:
             reference_time = scheduled_today - timedelta(days=1)
             
        # If we have a last_update AND data, and it's AFTER the reference_time, 
        # then we've already updated for this cycle.
        backfill_pending = (
            self._backfill_wanted()
            and self.config_entry.data.get(CONF_OPERATION_MODE, MODE_DAILY_ADVANCED) != MODE_MONTHLY
        )
        if self.last_update and self.data and self.last_update >= reference_time and not backfill_pending:
             _LOGGER.info(
                 "Skipping startup fetch; last update (%s) is recent. Next update at 08:00.",
                 self.last_update.strftime("%Y-%m-%d %H:%M:%S")
             )
             self._schedule_next_update()
             # Notify listeners so sensors pick up the restored data immediately
             self.async_update_listeners()
             return

        _LOGGER.info("Last update (%s) is outdated or missing. Fetching fresh data.", 
                     self.last_update.strftime("%Y-%m-%d %H:%M:%S") if self.last_update else "None")
        await self.async_refresh()

    def _schedule_next_update(self):
        """Calculate and set next update time based on Atmos update schedule."""
        now = dt_util.now()
        next_update = now.replace(hour=8, minute=0, second=0, microsecond=0)
        if next_update <= now:
            next_update += timedelta(days=1)
        time_until_next = next_update - now
        self.update_interval = time_until_next
        _LOGGER.debug(
            "Next Atmos update scheduled for %s (in %s)",
            next_update.strftime("%Y-%m-%d %H:%M:%S"),
            time_until_next
        )
