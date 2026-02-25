import logging
from typing import Any
from datetime import datetime
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
    CONF_OPERATION_MODE,
    ATTR_METER_READ_DATE,
    ATTR_AVG_TEMP,
    ATTR_BILLING_MONTH,
    MODE_MONTHLY,
    MODE_DAILY,
    MODE_DAILY_ADVANCED
)

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the Atmos Energy sensor platform."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    account_id = entry.data.get(CONF_USERNAME, "unknown")
    mode = entry.data.get(CONF_OPERATION_MODE, MODE_DAILY_ADVANCED)
    
    if mode == MODE_MONTHLY:
        entities = [
            AtmosEnergyMonthlyUsageSensor(coordinator, entry, account_id),
        ]
    elif mode == MODE_DAILY:
        entities = [
            AtmosEnergyUsageSensor(coordinator, entry, account_id),
            AtmosEnergyDaysRemainingSensor(coordinator, entry, account_id),
        ]
    else:  # MODE_DAILY_ADVANCED
        entities = [
            AtmosEnergyUsageSensor(coordinator, entry, account_id),
            AtmosEnergyCostSensor(coordinator, entry, account_id),
            AtmosEnergyDaysRemainingSensor(coordinator, entry, account_id),
        ]

        weather_entity = entry.options.get(CONF_WEATHER_ENTITY)
        if weather_entity:
            entities.append(AtmosEnergyPredictedUsageSensor(coordinator, entry, account_id))
            entities.append(AtmosEnergyPredictedCostSensor(coordinator, entry, account_id))

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
    """Representation of an Atmos Energy Usage Sensor (Display Only).
    
    This sensor shows the current billing period total for display purposes.
    It does NOT have state_class to prevent Energy Dashboard from tracking it.
    
    Historical daily usage is imported via Statistics API in the coordinator,
    which ensures the Energy Dashboard shows usage on the correct dates.
    """

    _attr_device_class = SensorDeviceClass.GAS
    _attr_state_class = None  # No state class - display only!
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
        """Return extra state attributes including data freshness."""
        if not self.coordinator.data:
            return {"account_id": self._account_id}
        
        attrs = {
            "account_id": self._account_id,
            "last_reading_date": self.coordinator.data.get(ATTR_BILL_DATE),
            "billing_period_start": self.coordinator.data.get("billing_period_start"),
        }
        
        # Add data freshness indicator
        latest_date = self.coordinator.data.get("latest_date")
        if latest_date:
            try:
                latest = dt_util.parse_datetime(latest_date)
                if latest:
                    now = dt_util.now()
                    lag_days = (now - dt_util.as_local(latest)).days
                    
                    if lag_days == 0:
                        attrs["data_freshness"] = "current"
                    elif lag_days == 1:
                        attrs["data_freshness"] = "1 day old"
                    else:
                        attrs["data_freshness"] = f"{lag_days} days old"
                    
                    # Add note about what the total represents
                    attrs["note"] = f"Total through {latest.strftime('%b %d')}"
                    
            except Exception as e:
                _LOGGER.debug("Could not calculate data freshness: %s", e)
        
        return attrs


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
        
        usage = self.coordinator.data.get(ATTR_USAGE)
        if usage is None:
            return {"account_id": self._account_id}
        
        # Get WNA info and cost breakdown
        wna_info = self.coordinator.data.get("wna_calculated", {})
        wna_charge = wna_info.get("wna_charge", 0.0)
        
        cost_data = self.coordinator.calculate_total_cost(
            float(usage), 
            include_fixed=True, 
            wna_charge=wna_charge
        )
        breakdown = cost_data["breakdown"]
        
        # Get GCR info
        use_auto_gcr = self._entry.options.get("auto_fetch_gcr", True)
        if use_auto_gcr and self.coordinator.current_gcr_rate:
            gcr_source = "auto-fetched"
            gcr_last_updated = self.coordinator.gcr_fetcher._last_fetch
        else:
            gcr_source = "manual"
            gcr_last_updated = None
        
        return {
            "account_id": self._account_id,
            "formula": (
                f"({breakdown['fixed_charge']} fixed + "
                f"{breakdown['consumption_charge']} consumption + "
                f"{breakdown['wna_charge']} WNA + "
                f"{breakdown['gcr_charge']} GCR + "
                f"{breakdown['uri_charge']} URI) + "
                f"{breakdown['tax_amount']} tax = ${cost_data['total']}"
            ),
            "breakdown": {
                "fixed_charge": f"${breakdown['fixed_charge']:.2f}",
                "consumption_charge": f"${breakdown['consumption_charge']:.2f}",
                "wna_charge": f"${breakdown['wna_charge']:.2f}",
                "gcr_charge": f"${breakdown['gcr_charge']:.2f}",
                "uri_charge": f"${breakdown['uri_charge']:.2f}",
                "tax": f"${breakdown['tax_amount']:.2f}",
                "subtotal": f"${breakdown['subtotal']:.2f}",
            },
            "rates": {
                "consumption": f"${breakdown['rates']['consumption']:.2f}/CCF",
                "gcr": f"${breakdown['rates']['gcr']:.4f}/CCF",
                "wna": wna_info.get("wnaf_rate", "$0.00/CCF"),
                "uri": f"${breakdown['rates']['uri']:.4f}/CCF",
                "tax": f"{breakdown['rates']['tax']}%",
            },
            "wna_details": {
                "wnaf_rate": wna_info.get("wnaf_rate", "N/A"),
                "wna_charge": f"${wna_charge:.2f}",
                "ndd": wna_info.get("ndd", 0),
                "add_projected": wna_info.get("add_projected", 0),
                "billing_month": wna_info.get("billing_month", datetime.now().month),
                "weather_station": wna_info.get("weather_station", "unknown"),
            },
            "gcr_details": {
                "rate": f"${breakdown['rates']['gcr']:.4f}/CCF",
                "charge": f"${breakdown['gcr_charge']:.2f}",
                "source": gcr_source,
                "last_updated": gcr_last_updated.isoformat() if gcr_last_updated else None,
            }
        }


class AtmosEnergyDaysRemainingSensor(AtmosEnergyBaseSensor):
    """Representation of Days Remaining in Billing Period Sensor."""

    _attr_native_unit_of_measurement = "days"
    _attr_name = "Days remaining in billing period"
    _attr_suggested_object_id = f"{DOMAIN}_days_remaining"
    _attr_icon = "mdi:calendar-clock"

    def __init__(self, coordinator, entry: ConfigEntry, account_id: str):
        """Initialize the sensor."""
        super().__init__(coordinator, entry, account_id)
        self._attr_unique_id = f"{DOMAIN}_{account_id}_days_remaining"

    @property
    def native_value(self):
        """Return the days remaining."""
        if not self.coordinator.data:
            return None
        
        next_read_dt = self.coordinator.data.get("next_meter_read_dt")
        if not next_read_dt:
            return None
        
        # Ensure both datetimes are timezone-aware for comparison
        now = dt_util.now()
        if next_read_dt.tzinfo is None:
            next_read_dt = dt_util.as_local(next_read_dt)
        
        delta = next_read_dt - now
        return max(0, delta.days)

    @property
    def extra_state_attributes(self):
        """Return extra state attributes."""
        if not self.coordinator.data:
            return {"account_id": self._account_id}
        
        next_read_dt = self.coordinator.data.get("next_meter_read_dt")
        
        return {
            "account_id": self._account_id,
            "next_meter_read_date": next_read_dt.strftime("%Y-%m-%d") if next_read_dt else None,
            "billing_period_start": self.coordinator.data.get("billing_period_start"),
        }


class AtmosEnergyPredictedUsageSensor(AtmosEnergyBaseSensor):
    """7-day predicted gas usage based on weather forecast."""

    _attr_device_class = SensorDeviceClass.GAS
    _attr_state_class = None  # Predictions are point-in-time, not cumulative
    _attr_native_unit_of_measurement = "CCF"
    _attr_name = "Predicted usage (7 days)"
    _attr_suggested_object_id = f"{DOMAIN}_predicted_usage_7d"
    _attr_icon = "mdi:chart-line"

    def __init__(self, coordinator, entry: ConfigEntry, account_id: str):
        """Initialize the sensor."""
        super().__init__(coordinator, entry, account_id)
        self._attr_unique_id = f"{DOMAIN}_{account_id}_predicted_usage_7d"

    @property
    def native_value(self):
        """Return predicted usage."""
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get("predicted_usage_7d")

    @property
    def extra_state_attributes(self):
        """Return extra state attributes."""
        if not self.coordinator.data:
            return {"account_id": self._account_id}
        
        return {
            "account_id": self._account_id,
            "model_base_load": self.coordinator.base_load,
            "model_heating_coefficient": self.coordinator.heating_coeff,
            "model_balance_temperature": self.coordinator.balance_temp,
            "model_r_squared": self.coordinator.r_squared,
            "forecast_days": 7,
        }


class AtmosEnergyPredictedCostSensor(AtmosEnergyBaseSensor):
    """7-day predicted cost based on weather forecast."""

    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_state_class = None  # Predictions are point-in-time, not cumulative
    _attr_native_unit_of_measurement = "USD"
    _attr_name = "Predicted cost (7 days)"
    _attr_suggested_object_id = f"{DOMAIN}_predicted_cost_7d"
    _attr_icon = "mdi:cash-clock"

    def __init__(self, coordinator, entry: ConfigEntry, account_id: str):
        """Initialize the sensor."""
        super().__init__(coordinator, entry, account_id)
        self._attr_unique_id = f"{DOMAIN}_{account_id}_predicted_cost_7d"

    @property
    def native_value(self):
        """Return predicted cost."""
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get("predicted_cost_7d")

    @property
    def extra_state_attributes(self):
        """Return extra state attributes."""
        if not self.coordinator.data:
            return {"account_id": self._account_id}
        
        breakdown = self.coordinator.data.get("predicted_cost_7d_breakdown", {})
        
        return {
            "account_id": self._account_id,
            "forecast_days": 7,
            "breakdown": {
                "fixed_charge": f"${breakdown.get('fixed_charge', 0):.2f}",
                "consumption_charge": f"${breakdown.get('consumption_charge', 0):.2f}",
                "wna_charge": f"${breakdown.get('wna_charge', 0):.2f}",
                "gcr_charge": f"${breakdown.get('gcr_charge', 0):.2f}",
                "uri_charge": f"${breakdown.get('uri_charge', 0):.2f}",
                "tax": f"${breakdown.get('tax_amount', 0):.2f}",
            } if breakdown else None,
        }


class AtmosEnergyMonthlyUsageSensor(AtmosEnergyBaseSensor):
    """Representation of an Atmos Energy Monthly Usage Sensor (no daily data)."""

    _attr_device_class = SensorDeviceClass.GAS
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = "CCF"
    _attr_name = "Gas usage"
    _attr_suggested_object_id = f"{DOMAIN}_usage"
    _attr_icon = "mdi:fire"

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
            "amount_due": self.coordinator.data.get("amount_due"),
            "due_date": self.coordinator.data.get(ATTR_DUE_DATE),
            "last_bill_date": self.coordinator.data.get(ATTR_BILL_DATE),
            "meter_read_date": self.coordinator.data.get(ATTR_METER_READ_DATE),
            "avg_temp": self.coordinator.data.get(ATTR_AVG_TEMP),
            "billing_month": self.coordinator.data.get(ATTR_BILLING_MONTH),
        }
