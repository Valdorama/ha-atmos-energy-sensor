"""Saving options in Monthly mode."""
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.atmos_energy.const import DOMAIN

MONTHLY = {"username": "atmos_user", "password": "pw", "operation_mode": "monthly"}
SAVED = {"region": "louisiana", "fixed_cost": 31.25, "usage_rate": 0.75187, "tax_percent": 4.0}


async def test_saving_in_monthly_mode_keeps_stored_options(hass, flow_client, no_setup):
    """Switching to Monthly and back shouldn't lose the region and rates."""
    flow_client.holder["accounts"] = []
    entry = MockConfigEntry(domain=DOMAIN, unique_id="atmos_user", data=MONTHLY, options=SAVED)
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], MONTHLY)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == SAVED
