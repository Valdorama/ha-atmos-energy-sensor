"""Rate regions: the setup and options steps, and month-aware pricing in the coordinator."""
from datetime import datetime
from unittest.mock import MagicMock

import pytest
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.atmos_energy.const import DOMAIN
from custom_components.atmos_energy.coordinator import AtmosEnergyDataUpdateCoordinator

DAILY = {"username": "atmos_user", "password": "pw", "operation_mode": "daily"}
ADVANCED = {**DAILY, "operation_mode": "daily_advanced"}


def defaults(result) -> dict:
    """The form's default values, by field name."""
    return {str(key): key.default() for key in result["data_schema"].schema if callable(key.default)}


async def start(hass, creds):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    return await hass.config_entries.flow.async_configure(result["flow_id"], creds)


async def test_daily_mode_asks_region_then_its_rates(hass, flow_client, no_setup):
    flow_client.holder["accounts"] = []
    result = await start(hass, DAILY)
    assert result["step_id"] == "region"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"region": "kansas"})
    assert result["step_id"] == "cost"
    form = defaults(result)
    assert form["fixed_cost"] == 23.00
    assert form["usage_rate"] == 0.24290
    assert "fixed_cost_summer" not in form

    result = await hass.config_entries.flow.async_configure(result["flow_id"], form)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["options"]["region"] == "kansas"
    assert result["data"]["operation_mode"] == "daily"


async def test_seasonal_region_asks_summer_charge(hass, flow_client, no_setup):
    flow_client.holder["accounts"] = []
    result = await start(hass, DAILY)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"region": "tennessee"})
    form = defaults(result)
    assert form["fixed_cost"] == 27.20
    assert form["fixed_cost_summer"] == 25.20


async def test_advanced_mode_outside_mid_tex_skips_weather_station(hass, flow_client, no_setup):
    flow_client.holder["accounts"] = []
    result = await start(hass, ADVANCED)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"region": "louisiana"})
    assert result["step_id"] == "cost"  # the WNA weather station is Mid-Tex only

    result = await hass.config_entries.flow.async_configure(result["flow_id"], defaults(result))
    assert result["step_id"] == "weather_forecast"  # forecasts work in every region
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["operation_mode"] == "daily_advanced"
    assert "weather_station" not in result["options"]


async def test_advanced_mode_in_mid_tex_keeps_weather_steps(hass, flow_client, no_setup):
    flow_client.holder["accounts"] = []
    result = await start(hass, ADVANCED)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"region": "mid_tex"})
    assert result["step_id"] == "weather_station"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"weather_station": "dallas"})
    assert result["step_id"] == "cost"
    assert defaults(result)["fixed_cost"] == 24.40
    result = await hass.config_entries.flow.async_configure(result["flow_id"], defaults(result))
    assert result["step_id"] == "weather_forecast"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["operation_mode"] == "daily_advanced"
    assert result["options"]["region"] == "mid_tex"


async def test_options_region_change_offers_new_defaults(hass, flow_client, no_setup):
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id="atmos_user", data=DAILY,
        options={"region": "kansas", "fixed_cost": 30.0, "usage_rate": 0.3, "tax_percent": 2.0},
    )
    entry.add_to_hass(hass)
    flow_client.holder["accounts"] = []

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], DAILY)
    assert result["step_id"] == "region"
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"region": "virginia"})
    form = defaults(result)
    assert form["fixed_cost"] == 13.24
    assert form["tax_percent"] == 0.0

    result = await hass.config_entries.options.async_configure(result["flow_id"], form)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["region"] == "virginia"


async def test_options_same_region_keeps_user_rates(hass, flow_client, no_setup):
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id="atmos_user", data=DAILY,
        options={"region": "kansas", "fixed_cost": 30.0},
    )
    entry.add_to_hass(hass)
    flow_client.holder["accounts"] = []

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], DAILY)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"region": "kansas"})
    assert defaults(result)["fixed_cost"] == 30.0


async def test_entries_without_region_are_mid_tex(hass):
    entry = MockConfigEntry(domain=DOMAIN, data=ADVANCED, options={})
    entry.add_to_hass(hass)
    coordinator = AtmosEnergyDataUpdateCoordinator(hass, MagicMock(), entry)
    assert coordinator.region == "mid_tex"
    assert coordinator.gcr_fetcher.source.document_prefix == "mid-tex-gcr-rates"


@pytest.fixture
async def tennessee(hass):
    entry = MockConfigEntry(
        domain=DOMAIN, data=DAILY,
        options={
            "region": "tennessee", "fixed_cost": 27.20, "fixed_cost_summer": 25.20, "usage_rate": 0.20,
            "auto_fetch_gcr": True, "gcr_rate": 0.99, "uri_surcharge": 0.0, "tax_percent": 0.0,
        },
    )
    entry.add_to_hass(hass)
    coordinator = AtmosEnergyDataUpdateCoordinator(hass, MagicMock(), entry)
    coordinator.gcr_fetcher._cache = {"2026-07": 0.50, "2026-10": 0.60}
    coordinator.current_gcr_rate = 0.60
    return coordinator


async def test_cost_uses_that_months_gas_cost_and_summer_charge(tennessee):
    july = tennessee.calculate_total_cost(10, pro_rate_days=30, when=datetime(2026, 7, 15))
    assert july["breakdown"]["fixed_charge"] == 25.20
    assert july["breakdown"]["rates"]["gcr"] == 0.50
    assert july["total"] == pytest.approx(25.20 + 10 * 0.20 + 10 * 0.50)

    october = tennessee.calculate_total_cost(10, pro_rate_days=30, when=datetime(2026, 10, 15))
    assert october["breakdown"]["fixed_charge"] == 27.20
    assert october["breakdown"]["rates"]["gcr"] == 0.60


async def test_month_before_the_cache_uses_current_rate(tennessee):
    old = tennessee.calculate_total_cost(10, when=datetime(2025, 1, 15))
    assert old["breakdown"]["rates"]["gcr"] == 0.60


async def test_manual_gas_cost_when_auto_fetch_off(hass, tennessee):
    hass.config_entries.async_update_entry(
        tennessee.config_entry, options={**tennessee.config_entry.options, "auto_fetch_gcr": False}
    )
    tennessee.current_gcr_rate = None
    cost = tennessee.calculate_total_cost(10, when=datetime(2026, 7, 15))
    assert cost["breakdown"]["rates"]["gcr"] == 0.99


async def test_published_rates_replace_saved_charges_when_enabled(hass, tennessee):
    tennessee.published_rates.doc = {
        "regions": {"tennessee": {"rates": [
            {"effective": "2026-10-01", "fixed_cost": 27.20, "fixed_cost_summer": 25.20, "usage_rate": 0.1970},
            {"effective": "2027-10-01", "fixed_cost": 29.00, "fixed_cost_summer": 26.00, "usage_rate": 0.2100},
        ]}},
    }
    options = dict(tennessee.config_entry.options)

    # Off (or missing, for entries made before the option): the saved values
    cost = tennessee.calculate_total_cost(10, pro_rate_days=30, when=datetime(2027, 11, 1))
    assert cost["breakdown"]["fixed_charge"] == 27.20
    assert cost["breakdown"]["rates"]["consumption"] == 0.20

    hass.config_entries.async_update_entry(tennessee.config_entry, options={**options, "use_published_rates": True})
    cost = tennessee.calculate_total_cost(10, pro_rate_days=30, when=datetime(2027, 11, 1))
    assert cost["breakdown"]["fixed_charge"] == 29.00
    assert cost["breakdown"]["rates"]["consumption"] == 0.2100
    # A day before the new rates took effect keeps the old ones
    july = tennessee.calculate_total_cost(10, pro_rate_days=30, when=datetime(2027, 7, 1))
    assert july["breakdown"]["fixed_charge"] == 25.20
    assert july["breakdown"]["rates"]["consumption"] == 0.1970


async def test_published_rates_missing_region_falls_back_to_saved(hass, tennessee):
    tennessee.published_rates.doc = {"regions": {}}
    hass.config_entries.async_update_entry(
        tennessee.config_entry, options={**tennessee.config_entry.options, "use_published_rates": True}
    )
    cost = tennessee.calculate_total_cost(10, pro_rate_days=30, when=datetime(2026, 11, 1))
    assert cost["breakdown"]["fixed_charge"] == 27.20


async def test_published_wna_parameters_are_applied(hass):
    entry = MockConfigEntry(domain=DOMAIN, data=ADVANCED, options={"region": "mid_tex", "weather_station": "dallas"})
    entry.add_to_hass(hass)
    coordinator = AtmosEnergyDataUpdateCoordinator(hass, MagicMock(), entry)
    coordinator.published_rates.doc = {"wna": {"mid_tex": {"commodity_rate": 110.0, "stations": {
        "dallas": {"base_load": 13.0, "heat_factor": 0.21, "ndd": {"1": 700, "12": 600}},
    }}}}
    coordinator._apply_published_wna()

    calc = coordinator.wna_calculator
    assert (calc.base_load, calc.heat_factor, calc.commodity_rate) == (13.0, 0.21, 110.0)
    assert calc.get_ndd_for_month(1) == 700
    assert calc.get_ndd_for_month(11) == 0  # not in the published season


async def test_cost_form_offers_published_rates(hass, flow_client, no_setup):
    flow_client.holder["accounts"] = []
    result = await start(hass, DAILY)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"region": "kansas"})
    assert defaults(result)["use_published_rates"] is True
