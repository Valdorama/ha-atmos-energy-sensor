import logging
from typing import Any
from homeassistant.components.sensor import SensorEntity, SensorDeviceClass, SensorStateClass
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.const import CONF_USERNAME
from homeassistant.components.weather import (
    ATTR_FORECAST_NATIVE_TEMP,
    ATTR_FORECAST_NATIVE_TEMP_LOW,
    SERVICE_GET_FORECASTS
)

from .const import (
    DOMAIN, 
    ATTR_USAGE, 
    ATTR_AMOUNT_DUE, 
    ATTR_DUE_DATE, 
    ATTR_BILL_DATE,
    ATTR_BILLING_PERIOD_START,
    CONF_WEATHER_ENTITY,
    CONF_DAILY_USAGE,
    ATTR_METER_READ_DATE,
    ATTR_AVG_TEMP,
    ATTR_BILLING_MONTH
)

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the Atmos Energy sensor platform."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    account_id = entry.data.get(CONF_USERNAME, "unknown")
    daily_usage = entry.data.get(CONF_DAILY_USAGE, True)

    if daily_usage:
        entities = [
            AtmosEnergyUsageSensor(coordinator, entry, account_id),
            AtmosEnergyCostSensor(coordinator, entry, account_id),
            AtmosEnergyDaysRemainingSensor(coordinator, entry, account_id),
        ]

        weather_entity = entry.options.get(CONF_WEATHER_ENTITY)
        if weather_entity:
            entities.append(AtmosEnergyPredictedUsageSensor(coordinator, entry, account_id, weather_entity))
            entities.append(AtmosEnergyPredictedCostSensor(coordinator, entry, account_id, weather_entity))
    else:
        entities = [
            AtmosEnergyMonthlyUsageSensor(coordinator, entry, account_id),
        ]

    async_add_entities(entities)


class AtmosEnergyBaseSensor(CoordinatorEntity, SensorEntity):
    """Base class for Atmos Energy sensors."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, entry: ConfigEntry, account_id: str):
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._entry = entry
        self._account_id = account_id

    @property
    def device_info(self) -> dict[str, Any]:
        """Return device information."""
        return {
            "identifiers": {(DOMAIN, self._account_id)},
            "name": "Atmos Energy",
            "manufacturer": "Atmos Energy",
            "model": "Gas Meter",
        }


class AtmosEnergyUsageSensor(AtmosEnergyBaseSensor):
    """Representation of an Atmos Energy Usage Sensor."""

    _attr_device_class = SensorDeviceClass.GAS
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = "CCF"
    _attr_name = "Gas usage (Current Billing Period)"
    _attr_suggested_object_id = f"{DOMAIN}_usage"
    _attr_icon = "mdi:gas-burner"

    def __init__(self, coordinator, entry: ConfigEntry, account_id: str):
        """Initialize the sensor."""
        super().__init__(coordinator, entry, account_id)
        self._attr_unique_id = f"{DOMAIN}_{account_id}_usage"

    @property
    def native_value(self):
        """Return the state of the sensor."""
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get(ATTR_USAGE)

    @property
    def extra_state_attributes(self):
        """Return extra state attributes."""
        if not self.coordinator.data:
            return {"account_id": self._account_id}
            
        return {
            "account_id": self._account_id,
            "last_reading_date": self.coordinator.data.get(ATTR_BILL_DATE),
            "last_reset": self._get_last_reset(),
        }

    def _get_last_reset(self):
        """Estimate the last reset date (start of current billing cycle)."""
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get("billing_period_start")


class AtmosEnergyCostSensor(AtmosEnergyBaseSensor):
    """Representation of an Atmos Energy Cost Sensor."""

    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_state_class = SensorStateClass.TOTAL
    _attr_native_unit_of_measurement = "USD"
    _attr_name = "Estimated cost"
    _attr_suggested_object_id = f"{DOMAIN}_estimated_cost"
    _attr_icon = "mdi:currency-usd"

    def __init__(self, coordinator, entry: ConfigEntry, account_id: str):
        """Initialize the sensor."""
        super().__init__(coordinator, entry, account_id)
        self._attr_unique_id = f"{DOMAIN}_{account_id}_estimated_cost"

    @property
    def native_value(self):
        """Return the estimated cost."""
        if not self.coordinator.data:
            return None
            
        usage = self.coordinator.data.get(ATTR_USAGE)
        if usage is None:
            return None
        
        # Get options or defaults
        fixed = self._entry.options.get("fixed_cost", 25.03)
        rate = self._entry.options.get("usage_rate", 2.40)
        tax_pct = self._entry.options.get("tax_percent", 8.0)
        
        # Calculation
        # Base = Fixed + (Usage * Rate)
        # Total = Base * (1 + Tax/100)
        
        base_cost = fixed + (float(usage) * rate)
        total_cost = base_cost * (1 + (tax_pct / 100.0))
        
        return round(total_cost, 2)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the state attributes."""
        if not self.coordinator.data:
            return {"account_id": self._account_id}
            
        return {
            "account_id": self._account_id,
            "due_date": self.coordinator.data.get(ATTR_DUE_DATE),
            "formula": f"({self._entry.options.get('fixed_cost',25.03)} + (usage * {self._entry.options.get('usage_rate',2.40)})) * {1 + self._entry.options.get('tax_percent',8.0)/100}"
        }


class AtmosEnergyDaysRemainingSensor(AtmosEnergyBaseSensor):
    """Representation of an Atmos Energy Days Remaining Sensor."""

    _attr_name = "Days remaining in billing period"
    _attr_suggested_object_id = f"{DOMAIN}_days_remaining"
    _attr_icon = "mdi:calendar-clock"
    _attr_native_unit_of_measurement = "days"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, entry: ConfigEntry, account_id: str):
        """Initialize the sensor."""
        super().__init__(coordinator, entry, account_id)
        self._attr_unique_id = f"{DOMAIN}_{account_id}_days_remaining"

    @property
    def native_value(self):
        """Return the number of days remaining."""
        if not self.coordinator.data:
            return None
            
        start_date_str = self.coordinator.data.get(ATTR_BILLING_PERIOD_START)
        if not start_date_str:
            return None
            
        try:
            from datetime import datetime, timedelta
            from homeassistant.util import dt as dt_util
            
            # Pandas dates often look like '2026-02-01 00:00:00' or '2026-02-01'
            # dt_util.parse_datetime handles most common ISO formats
            start_date = dt_util.parse_datetime(start_date_str)
            if not start_date:
                # Fallback to common formats seen in Atmos XLS
                for fmt in ("%Y-%m-%d", "%m/%d/%Y"):
                    try:
                        start_date = datetime.strptime(start_date_str.split()[0], fmt)
                        break
                    except ValueError:
                        continue
            
            if not start_date:
                return None
            
            # Ensure start_date is aware if it's not
            if start_date.tzinfo is None:
                start_date = dt_util.as_local(start_date)
                
            now = dt_util.now()
            
            # Assume 30 day billing cycle from the start date
            target_date = start_date + timedelta(days=30)
            remaining = (target_date - now).days
            
            return max(0, remaining)
        except Exception as e:
            _LOGGER.error("Error calculating remaining days from '%s': %s", start_date_str, e)
            return None


class AtmosEnergyPredictedUsageSensor(AtmosEnergyBaseSensor):
    """Sensor that predicts gas usage for the next 7 days based on weather forecast."""

    _attr_device_class = SensorDeviceClass.GAS
    _attr_state_class = SensorStateClass.TOTAL
    _attr_native_unit_of_measurement = "CCF"
    _attr_name = "Predicted Gas Usage (Next 7 Days)"
    _attr_suggested_object_id = f"{DOMAIN}_predicted_usage_7d"
    _attr_icon = "mdi:chart-bell-curve"
    _attr_should_poll = True

    def __init__(self, coordinator, entry: ConfigEntry, account_id: str, weather_entity: str):
        """Initialize the sensor."""
        super().__init__(coordinator, entry, account_id)
        self._weather_entity = weather_entity
        self._attr_unique_id = f"{DOMAIN}_{account_id}_predicted_usage_7d"
        # Coefficients from regression analysis
        self._base_load = 1.23
        self._heating_coeff = 0.097
        self._last_forecast_value = None

    async def async_added_to_hass(self) -> None:
        """When entity is added to hass."""
        await super().async_added_to_hass()
        # Initial update
        await self.async_update()
        self.async_write_ha_state()

    async def async_update(self):
        """Update the sensor using the weather service."""
        # Note: We do NOT call super().async_update() here because that triggers the 
        # DataUpdateCoordinator which fetches expensive XLS data.
        # This sensor updates independently based on weather data.
        
        _LOGGER.debug("Updating predicted usage sensor using %s", self._weather_entity)
        try:
            # Check if weather entity exists to avoid errors
            if not self.hass.states.get(self._weather_entity):
                _LOGGER.warning("Weather entity %s not found", self._weather_entity)
                return

            response = await self.hass.services.async_call(
                "weather",
                "get_forecasts",
                {"type": "daily", "entity_id": self._weather_entity},
                blocking=True,
                return_response=True,
            )
            
            _LOGGER.debug("Forecast response for %s: %s", self._weather_entity, response)
            
            if not response or self._weather_entity not in response:
                _LOGGER.warning("No forecast response for %s", self._weather_entity)
                return

            forecast_data = response[self._weather_entity].get("forecast", [])
            if not forecast_data:
                _LOGGER.warning("Empty forecast data for %s", self._weather_entity)
                return
            
            total_ccf = 0.0
            # Calculate for next 7 days
            for day in forecast_data[:7]:
                # Try native keys first, fall back to standard keys
                high = day.get(ATTR_FORECAST_NATIVE_TEMP) or day.get("temperature")
                low = day.get(ATTR_FORECAST_NATIVE_TEMP_LOW) or day.get("templow")
                
                if high is not None and low is not None:
                    avg_temp = (float(high) + float(low)) / 2
                    hdd = max(0, 65 - avg_temp)
                    daily_usage = self._base_load + (self._heating_coeff * hdd)
                    total_ccf += daily_usage
                else:
                    _LOGGER.debug("Skipping day in forecast due to missing temp data: %s", day)
            
            self._last_forecast_value = round(total_ccf, 2)
            _LOGGER.debug("Predicted 7-day usage: %s CCF", self._last_forecast_value)
            
        except Exception as e:
             _LOGGER.error("Error updating gas prediction from %s: %s", self._weather_entity, e)

    @property
    def native_value(self):
        """Return the state of the sensor."""
        return self._last_forecast_value

class AtmosEnergyPredictedCostSensor(AtmosEnergyPredictedUsageSensor):
    """Sensor that predicts gas cost for the next 7 days."""

    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_state_class = SensorStateClass.TOTAL
    _attr_native_unit_of_measurement = "USD"
    _attr_name = "Predicted Gas Cost (Next 7 Days)"
    _attr_suggested_object_id = f"{DOMAIN}_predicted_cost_7d"
    _attr_icon = "mdi:currency-usd"

    def __init__(self, coordinator, entry: ConfigEntry, account_id: str, weather_entity: str):
        """Initialize the sensor."""
        # This inherits logic from usage sensor, but applies rate at the end
        super().__init__(coordinator, entry, account_id, weather_entity)
        self._attr_unique_id = f"{DOMAIN}_{account_id}_predicted_cost_7d"

    @property
    def native_value(self):
        """Return the estimated cost."""
        usage = super().native_value
        if usage is None:
            return None
        
        rate = self._entry.options.get("usage_rate", 2.40)
        # Note: We don't include fixed costs here as those are monthly/per-bill
        # We also don't include tax yet as that applies to the total bill, 
        # but for simple "next 7 days cost" usage * rate is the most useful metric.
        
        return round(usage * rate, 2)


class AtmosEnergyMonthlyUsageSensor(AtmosEnergyBaseSensor):
    """Representation of an Atmos Energy Monthly Usage Sensor."""

    _attr_device_class = None
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "CCF"
    _attr_name = "Gas Usage (Previous Billing Period)"
    _attr_suggested_object_id = f"{DOMAIN}_monthly_usage"
    _attr_icon = "mdi:gas-burner"

    def __init__(self, coordinator, entry: ConfigEntry, account_id: str):
        """Initialize the sensor."""
        super().__init__(coordinator, entry, account_id)
        self._attr_unique_id = f"{DOMAIN}_{account_id}_monthly_usage"

    @property
    def native_value(self):
        """Return the state of the sensor."""
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get(ATTR_USAGE)

    @property
    def extra_state_attributes(self):
        """Return extra state attributes."""
        if not self.coordinator.data:
            return {"account_id": self._account_id}
            
        return {
            "account_id": self._account_id,
            ATTR_BILL_DATE: self.coordinator.data.get(ATTR_BILL_DATE),
            ATTR_METER_READ_DATE: self.coordinator.data.get(ATTR_METER_READ_DATE),
            ATTR_AVG_TEMP: self.coordinator.data.get(ATTR_AVG_TEMP),
            ATTR_BILLING_MONTH: self.coordinator.data.get(ATTR_BILLING_MONTH),
        }

