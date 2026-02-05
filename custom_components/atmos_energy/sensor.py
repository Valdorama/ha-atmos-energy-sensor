from typing import Any
from homeassistant.components.sensor import SensorEntity, SensorDeviceClass, SensorStateClass
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.const import CONF_USERNAME

from .const import DOMAIN, ATTR_USAGE, ATTR_AMOUNT_DUE, ATTR_DUE_DATE, ATTR_BILL_DATE

async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the Atmos Energy sensor platform."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    account_id = entry.data.get(CONF_USERNAME, "unknown")

    async_add_entities([
        AtmosEnergyUsageSensor(coordinator, entry, account_id),
        AtmosEnergyCostSensor(coordinator, entry, account_id),
    ])


class AtmosEnergyBaseSensor(CoordinatorEntity, SensorEntity):
    """Base class for Atmos Energy sensors."""

    def __init__(self, coordinator, entry: ConfigEntry, account_id: str):
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._entry = entry
        self._account_id = account_id

    @property
    def device_info(self):
        """Return device information."""
        return {
            "identifiers": {(DOMAIN, self._account_id)},
            "name": f"Atmos Energy ({self._account_id})",
            "manufacturer": "Atmos Energy",
            "model": "Gas Meter",
            "entry_type": "service",
        }


class AtmosEnergyUsageSensor(AtmosEnergyBaseSensor):
    """Representation of an Atmos Energy Usage Sensor."""

    _attr_device_class = SensorDeviceClass.GAS
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = "CCF"
    _attr_name = "Gas Usage"
    _attr_icon = "mdi:gas-burner"

    def __init__(self, coordinator, entry: ConfigEntry, account_id: str):
        """Initialize the sensor."""
        super().__init__(coordinator, entry, account_id)
        self._attr_unique_id = f"{DOMAIN}_{account_id}_usage"

    @property
    def native_value(self):
        """Return the state of the sensor."""
        return self.coordinator.data.get(ATTR_USAGE)

    @property
    def extra_state_attributes(self):
        """Return extra state attributes."""
        return {
            "account_id": self._account_id,
            "last_reading_date": self.coordinator.data.get(ATTR_BILL_DATE),
        }


class AtmosEnergyCostSensor(AtmosEnergyBaseSensor):
    """Representation of an Atmos Energy Cost Sensor."""

    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "USD"
    _attr_name = "Estimated Cost"
    _attr_icon = "mdi:currency-usd"

    def __init__(self, coordinator, entry: ConfigEntry, account_id: str):
        """Initialize the sensor."""
        super().__init__(coordinator, entry, account_id)
        self._attr_unique_id = f"{DOMAIN}_{account_id}_estimated_cost"

    @property
    def native_value(self):
        """Return the estimated cost."""
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
        return {
            "account_id": self._account_id,
            "due_date": self.coordinator.data.get(ATTR_DUE_DATE),
            "formula": f"({self._entry.options.get('fixed_cost',25.03)} + (usage * {self._entry.options.get('usage_rate',2.40)})) * {1 + self._entry.options.get('tax_percent',8.0)/100}"
        }

