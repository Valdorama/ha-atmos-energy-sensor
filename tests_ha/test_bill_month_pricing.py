"""Daily costs priced like the bill: the bill month's gas cost, the bill period's length."""
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.atmos_energy.const import DOMAIN
from custom_components.atmos_energy.coordinator import AtmosEnergyDataUpdateCoordinator

DAILY = {"username": "atmos_user", "password": "pw", "operation_mode": "daily"}
# An Atmos Louisiana (Lafayette) residential bill: customer charge, consumption
# charge plus the two Energy Efficiency riders, no other per-CCF charge, 4% franchise fee
LAFAYETTE = {
    "region": "louisiana",
    "fixed_cost": 31.25,
    "usage_rate": 0.72769 + 0.01656 + 0.00762,
    "uri_surcharge": 0.0,
    "tax_percent": 4.0,
    "auto_fetch_gcr": True,
}
# From "Louisiana PGA Rates - October 2026"
PGA = {"2026-07": 0.52407, "2026-08": 0.46844, "2026-09": 0.48771, "2026-10": 0.50254}

# Service period, billing month as in the daily download, CCF, current charges on the bill
BILLS = [
    (date(2026, 7, 7), date(2026, 8, 5), "Aug 26", 37, 79.44),  # before a $0.18 interim refund
    (date(2026, 8, 6), date(2026, 9, 3), "Sep 26", 35, 77.63),
    (date(2026, 9, 4), date(2026, 10, 5), "Oct 26", 41, 85.99),
]


def records(start: date, end: date, month: str, ccf: float) -> list[dict]:
    days = (end - start).days + 1
    return [
        {"date": (start + timedelta(days=i)).strftime("%m/%d/%Y"), "usage": ccf / days, "billing_month": month}
        for i in range(days)
    ]


@pytest.fixture
def coordinator(hass):
    entry = MockConfigEntry(domain=DOMAIN, data=DAILY, options=LAFAYETTE)
    entry.add_to_hass(hass)
    coordinator = AtmosEnergyDataUpdateCoordinator(hass, MagicMock(), entry)
    coordinator.gcr_fetcher._cache = dict(PGA)
    return coordinator


async def cost_total(coordinator, history: list[dict]) -> float:
    with patch("custom_components.atmos_energy.coordinator.statistics_during_period", return_value={}), \
         patch("custom_components.atmos_energy.coordinator.get_last_statistics", return_value={}), \
         patch("custom_components.atmos_energy.coordinator.async_add_external_statistics") as add:
        await coordinator._async_import_statistics_list(history)
    cost = next(c.args[2] for c in add.call_args_list if c.args[1]["statistic_id"].startswith(f"{DOMAIN}:cost_"))
    return cost[-1]["sum"]


@pytest.mark.parametrize("start,end,month,ccf,charges", BILLS)
async def test_billed_days_add_up_to_the_bill(coordinator, start, end, month, ccf, charges):
    # Bills round each line (Aug: the $3.0556 franchise fee is printed as $3.05), so allow 2 cents
    assert await cost_total(coordinator, records(start, end, month, ccf)) == pytest.approx(charges, abs=0.02)


async def test_pricing_by_calendar_month_and_30_days_misses_the_bill(coordinator):
    """The October bill (9/4-10/5, 32 days) charged October's 0.50254 on all 41 CCF.

    Priced the old way (each day's own month, 1/30 of the customer charge a day)
    the same days come to $87.62 instead of $85.99.
    """
    period = records(date(2026, 9, 4), date(2026, 10, 5), "Oct 26", 41)
    assert await cost_total(coordinator, period) == pytest.approx(85.99, abs=0.01)
    old_way = [{k: v for k, v in r.items() if k != "billing_month"} for r in period]
    assert await cost_total(coordinator, old_way) - 85.99 > 1.0


async def test_unbilled_days_fall_back_to_their_own_month_and_30_days(coordinator):
    """Days of the open current period have no billing month yet."""
    cost = coordinator._calculate_daily_cost_for_date(2.0, coordinator._parse_date("10/06/2026"))
    assert cost == pytest.approx((31.25 / 30 + 2.0 * (LAFAYETTE["usage_rate"] + 0.50254)) * 1.04)
