import logging
from typing import Any
from homeassistant.components.sensor import SensorEntity, SensorDeviceClass, SensorStateClass
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.const import (
    CONF_USERNAME,
)
from homeassistant.util import dt as dt_util

from .const import (
    DOMAIN, 
    ATTR_USAGE, 
    ATTR_DUE_DATE, 
    ATTR_BILL_DATE,
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
            entities.append(AtmosEnergyPredictedUsageSensor(coordinator, entry, account_id))
            entities.append(AtmosEnergyPredictedCostSensor(coordinator, entry, account_id))
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
            "last_reset": self.coordinator.data.get("billing_period_start"),
        }


class AtmosEnergyCostSensor(AtmosEnergyBaseSensor):
    """Representation of an Atmos Energy Cost Sensor with WNA."""

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
        """Return the estimated cost with WNA."""
        if not self.coordinator.data:
            return None
            
        usage = self.coordinator.data.get(ATTR_USAGE)
        if usage is None:
            return None
        
        # Get pre-calculated WNA charge from coordinator
        wna_info = self.coordinator.data.get("wna_calculated", {})
        wna_charge = wna_info.get("wna_charge", 0.0)
        
        # Use centralized cost calculation
        cost_data = self.coordinator.calculate_total_cost(
            float(usage), 
            include_fixed=True, 
            wna_charge=wna_charge
        )
        return cost_data["total"]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the state attributes with user-friendly breakdown."""
        if not self.coordinator.data:
            return {"account_id": self._account_id}
            
        usage = self.coordinator.data.get(ATTR_USAGE, 0)
        wna_info = self.coordinator.data.get("wna_calculated", {})
        
        # Recalculate cost data to get breakdown
        cost_data = self.coordinator.calculate_total_cost(
            float(usage), 
            include_fixed=True, 
            wna_charge=wna_info.get("wna_charge", 0.0)
        )
        b = cost_data["breakdown"]
        r = b["rates"]

        # GCR source info
        gcr_source = "auto-fetched" if self.coordinator.current_gcr_rate else "manual"
        gcr_last_updated = self.coordinator.gcr_fetcher._last_fetch if gcr_source == "auto-fetched" else None

        return {
            "account_id": self._account_id,
            "due_date": self.coordinator.data.get(ATTR_DUE_DATE),
            "cost_breakdown": {
                "Customer Charge": f"${b['fixed_charge']:.2f}",
                "Consump Chrg": f"${b['consumption_charge']:.2f} (@ ${r['consumption']:.5f})",
                "Rider GCR": f"${b['gcr_charge']:.2f} (@ ${r['gcr']:.5f})",
                "Rider WNA": f"${b['wna_charge']:.2f} (@ {wna_info.get('wnaf_rate', '$0.00/CCF')})",
                "Winter Storm Uri Surcharge": f"${b['uri_charge']:.2f} (@ ${r['uri']:.5f})",
                "Subtotal": f"${b['subtotal']:.2f}",
                "TAX/FEE CHARGE TOTAL": f"${b['tax_amount']:.2f} ({r['tax']:.2f}%)",
            },
            "wna_details": {
                "Historical Normal HDD (NDD)": wna_info.get("ndd"),
                "Projected Actual HDD (ADD)": wna_info.get("add_projected"),
                "Weather Station": wna_info.get("weather_station", "unknown").capitalize(),
            },
            "gcr_source": gcr_source,
            "gcr_last_updated": gcr_last_updated.isoformat() if gcr_last_updated else None,
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
            
        next_read_dt = self.coordinator.data.get("next_meter_read_dt")
        
        if next_read_dt:
            try:
                localized_next = dt_util.as_local(next_read_dt)
                now = dt_util.now()
                remaining = (localized_next - now).days
                return max(0, remaining)
            except Exception as e:
                _LOGGER.debug("Error localized date in sensor: %s", e)
        
        # Fallback to start_date + 30 days
        start_date_str = self.coordinator.data.get(ATTR_BILLING_PERIOD_START)
        if start_date_str:
            try:
                start_date = dt_util.parse_datetime(start_date_str)
                if start_date:
                    localized_start = dt_util.as_local(start_date)
                    target_date = localized_start + timedelta(days=30)
                    remaining = (target_date - dt_util.now()).days
                    return max(0, remaining)
            except Exception:
                pass
                
        return None


class AtmosEnergyPredictedUsageSensor(AtmosEnergyBaseSensor):
    """Sensor that predicts gas usage for the next 7 days based on weather forecast."""

    _attr_device_class = SensorDeviceClass.GAS
    _attr_state_class = SensorStateClass.TOTAL
    _attr_native_unit_of_measurement = "CCF"
    _attr_name = "Predicted Gas Usage (Next 7 Days)"
    _attr_suggested_object_id = f"{DOMAIN}_predicted_usage_7d"
    _attr_icon = "mdi:chart-bell-curve"

    def __init__(self, coordinator, entry: ConfigEntry, account_id: str):
        """Initialize the sensor."""
        super().__init__(coordinator, entry, account_id)
        self._attr_unique_id = f"{DOMAIN}_{account_id}_predicted_usage_7d"

    @property
    def native_value(self):
        """Return the state of the sensor from pre-calculated coordinator data."""
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get("predicted_usage_7d")

    @property
    def extra_state_attributes(self):
        """Return extra state attributes."""
        return {
            "base_load": self.coordinator.base_load,
            "heating_coefficient": self.coordinator.heating_coeff,
            "balance_temperature": self.coordinator.balance_temp,
            "r_squared": self.coordinator.r_squared,
        }


class AtmosEnergyPredictedCostSensor(AtmosEnergyBaseSensor):
    """Sensor that predicts gas cost for the next 7 days."""

    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_state_class = SensorStateClass.TOTAL
    _attr_native_unit_of_measurement = "USD"
    _attr_name = "Predicted Gas Cost (Next 7 Days)"
    _attr_suggested_object_id = f"{DOMAIN}_predicted_cost_7d"
    _attr_icon = "mdi:currency-usd"

    def __init__(self, coordinator, entry: ConfigEntry, account_id: str):
        """Initialize the sensor."""
        super().__init__(coordinator, entry, account_id)
        self._attr_unique_id = f"{DOMAIN}_{account_id}_predicted_cost_7d"

    @property
    def native_value(self):
        """Return the estimated cost."""
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get("predicted_cost_7d")

    @property
    def extra_state_attributes(self):
        """Return extra state attributes with pro-rated breakdown."""
        if not self.coordinator.data:
            return {}
            
        b = self.coordinator.data.get("predicted_cost_7d_breakdown", {})
        if not b:
            return {}
            
        r = b.get("rates", {})
        
        return {
            "model_usage_forecast": f"{self.coordinator.data.get('predicted_usage_7d', 0)} CCF",
            "pro_rated_fixed_charge": f"${b.get('fixed_charge'):.2f} (7 days)",
            "predicted_consumption_charge": f"${b.get('consumption_charge'):.2f} (@ ${r.get('consumption'):.5f})",
            "predicted_gcr_charge": f"${b.get('gcr_charge'):.2f} (@ ${r.get('gcr'):.5f})",
            "predicted_wna_charge": f"${b.get('wna_charge'):.2f} (@ ${r.get('wna', 0):.6f}/CCF)",
            "predicted_uri_surcharge": f"${b.get('uri_charge'):.2f} (@ ${r.get('uri'):.5f})",
            "predicted_taxes": f"${b.get('tax_amount'):.2f}",
            "note": "Fixed costs are pro-rated to 7 days for accuracy."
        }


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
