"""Sensor platform for Atmos Energy."""
from homeassistant.components.sensor import SensorEntity, SensorDeviceClass, SensorStateClass
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, ATTR_USAGE, ATTR_AMOUNT_DUE, ATTR_DUE_DATE

async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
):
    """Set up the Atmos Energy sensor platform."""
    coordinator = hass.data[DOMAIN][entry.entry_id]

    async_add_entities([
        AtmosEnergyUsageSensor(coordinator),
        AtmosEnergyCostSensor(coordinator, entry),
    ])

class AtmosEnergyUsageSensor(CoordinatorEntity, SensorEntity):
    """Representation of an Atmos Energy Usage Sensor."""

    _attr_device_class = SensorDeviceClass.GAS
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = "CCF" # or "therms", can be configurable
    _attr_name = "Atmos Energy Gas Usage"

    def __init__(self, coordinator):
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{DOMAIN}_usage"

    @property
    def native_value(self):
        """Return the state of the sensor."""
        return self.coordinator.data.get(ATTR_USAGE)

class AtmosEnergyCostSensor(CoordinatorEntity, SensorEntity):
    """Representation of an Atmos Energy Cost Sensor."""

    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_state_class = SensorStateClass.TOTAL
    _attr_native_unit_of_measurement = "USD"
    _attr_name = "Atmos Energy Estimated Cost"

    def __init__(self, coordinator, entry: ConfigEntry):
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{DOMAIN}_estimated_cost"
        self._entry = entry

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
    def extra_state_attributes(self):
        """Return the state attributes."""
        return {
            "due_date": self.coordinator.data.get(ATTR_DUE_DATE),
            "formula": f"({self._entry.options.get('fixed_cost',25.03)} + (usage * {self._entry.options.get('usage_rate',2.40)})) * {1 + self._entry.options.get('tax_percent',8.0)/100}"
        }
