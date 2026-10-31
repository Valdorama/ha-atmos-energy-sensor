"""The setup wizard asks whether to import past billing periods (daily modes only, last step)."""
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.atmos_energy.const import DOMAIN

DAILY = {"username": "atmos_user", "password": "pw", "operation_mode": "daily"}
ADVANCED = {**DAILY, "operation_mode": "daily_advanced"}


def defaults(result) -> dict:
    """The form's default values, by field name."""
    return {str(key): key.default() for key in result["data_schema"].schema if callable(key.default)}


async def start(hass, creds):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    return await hass.config_entries.flow.async_configure(result["flow_id"], creds)


async def to_backfill(hass, creds):
    """Walk a daily-mode setup (Kansas, so no weather station) up to the backfill step."""
    result = await start(hass, creds)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"region": "kansas"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], defaults(result))
    if result["step_id"] == "weather_forecast":
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    return result


async def test_daily_mode_asks_about_backfill_last(hass, flow_client, no_setup):
    flow_client.holder["accounts"] = []
    result = await to_backfill(hass, DAILY)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "backfill"
    assert defaults(result)["backfill_history"] is True  # on for new setups

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"backfill_history": True})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["options"]["backfill_history"] is True
    assert result["options"]["region"] == "kansas"


async def test_daily_mode_can_decline_backfill(hass, flow_client, no_setup):
    flow_client.holder["accounts"] = []
    result = await to_backfill(hass, DAILY)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"backfill_history": False})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["options"]["backfill_history"] is False


async def test_advanced_mode_asks_after_forecast(hass, flow_client, no_setup):
    flow_client.holder["accounts"] = []
    result = await to_backfill(hass, ADVANCED)
    assert result["step_id"] == "backfill"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"backfill_history": True})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["options"]["backfill_history"] is True
    assert result["data"]["operation_mode"] == "daily_advanced"


async def test_monthly_mode_has_no_backfill_step(hass, flow_client, no_setup):
    flow_client.holder["accounts"] = []
    result = await start(hass, {**DAILY, "operation_mode": "monthly"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert "backfill_history" not in result["options"]


async def test_options_can_turn_backfill_on_for_existing_entry(hass, flow_client, no_setup):
    entry = MockConfigEntry(domain=DOMAIN, unique_id="atmos_user", data=DAILY, options={"region": "kansas"})
    entry.add_to_hass(hass)
    flow_client.holder["accounts"] = []

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], DAILY)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"region": "kansas"})
    result = await hass.config_entries.options.async_configure(result["flow_id"], defaults(result))
    assert result["step_id"] == "backfill"
    assert defaults(result)["backfill_history"] is False  # missing means off

    result = await hass.config_entries.options.async_configure(result["flow_id"], {"backfill_history": True})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["backfill_history"] is True
    assert entry.options["region"] == "kansas"
