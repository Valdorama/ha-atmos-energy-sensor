"""WNA in the daily cost statistics (Mid-Tex advanced mode)."""
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.atmos_energy.const import DOMAIN
from custom_components.atmos_energy.coordinator import AtmosEnergyDataUpdateCoordinator

OPTIONS = {
    "region": "mid_tex", "weather_station": "dallas", "fixed_cost": 24.40, "usage_rate": 1.0,
    "auto_fetch_gcr": False, "gcr_rate": 1.0, "uri_surcharge": 0.0, "tax_percent": 0.0,
}


@pytest.fixture
async def coordinator(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"username": "atmos_user", "password": "pw", "operation_mode": "daily_advanced"},
        options=OPTIONS,
    )
    entry.add_to_hass(hass)
    return AtmosEnergyDataUpdateCoordinator(hass, MagicMock(), entry)


async def test_wna_applies_to_days_in_its_billing_period(coordinator):
    coordinator._wna_info = {"wnaf_rate_raw": 0.05, "billing_period_start": "2026-10-06"}

    in_period = coordinator.calculate_total_cost(10, include_fixed=False, when=datetime(2026, 10, 10))
    assert in_period["breakdown"]["wna_charge"] == pytest.approx(0.50)
    assert in_period["total"] == pytest.approx(10 * 1.0 + 10 * 1.0 + 0.50)

    before = coordinator.calculate_total_cost(10, include_fixed=False, when=datetime(2026, 10, 5))
    assert before["breakdown"]["wna_charge"] == 0.0


async def test_wna_without_a_date_uses_current_factor(coordinator):
    coordinator._wna_info = {"wnaf_rate_raw": -0.02, "billing_period_start": "2026-10-06"}
    cost = coordinator.calculate_total_cost(10, include_fixed=False)
    assert cost["breakdown"]["wna_charge"] == pytest.approx(-0.20)


async def test_wna_falls_back_to_restored_data(coordinator):
    coordinator.data = {"wna_calculated": {"wnaf_rate_raw": 0.05, "billing_period_start": "2026-10-06"}}
    assert coordinator._wna_rate(datetime(2026, 10, 7)) == pytest.approx(0.05)


async def test_wna_without_period_start_is_not_applied_to_dated_days(coordinator):
    # Data stored before the period start was recorded alongside the factor
    coordinator.data = {"wna_calculated": {"wnaf_rate_raw": 0.05}}
    assert coordinator._wna_rate(datetime(2026, 10, 7)) == 0.0


@pytest.mark.parametrize("start", ["2026-10-06 00:00:00", "10/06/2026"])
async def test_advanced_metrics_record_the_period(coordinator, start):
    data = {"usage": 20.0, "billing_period_start": start}
    with patch.object(coordinator, "_recalculate_model"), \
         patch.object(coordinator, "_calculate_projected_add", AsyncMock(return_value=300)), \
         patch.object(coordinator, "_calculate_predicted_usage_7d", AsyncMock(return_value=None)):
        await coordinator._update_advanced_metrics(data)

    info = data["wna_calculated"]
    assert info["billing_month"] == 10
    assert info["billing_period_start"] == "2026-10-06"
    assert coordinator._wna_info is info


async def test_no_wna_outside_mid_tex(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"username": "atmos_user", "password": "pw", "operation_mode": "daily_advanced"},
        options={**OPTIONS, "region": "kansas"},
    )
    entry.add_to_hass(hass)
    coordinator = AtmosEnergyDataUpdateCoordinator(hass, MagicMock(), entry)
    data = {"usage": 20.0, "billing_period_start": "2026-12-06"}  # a WNA month in Mid-Tex
    projected = AsyncMock(return_value=300)
    with patch.object(coordinator, "_recalculate_model"), \
         patch.object(coordinator, "_calculate_projected_add", projected), \
         patch.object(coordinator, "_calculate_predicted_usage_7d", AsyncMock(return_value=None)):
        await coordinator._update_advanced_metrics(data)

    info = data["wna_calculated"]
    assert info["wnaf_rate_raw"] == 0.0
    assert info["wna_charge"] == 0.0
    assert info["weather_station"] is None
    projected.assert_not_awaited()
