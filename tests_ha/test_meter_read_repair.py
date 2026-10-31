"""Days where the portal's daily download holds a meter reading instead of usage."""
from unittest.mock import AsyncMock, MagicMock

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.atmos_energy.const import DOMAIN
from custom_components.atmos_energy.coordinator import AtmosEnergyDataUpdateCoordinator
from custom_components.atmos_energy.exceptions import APIError


def period(month: str, usage: list[float], first_day: int = 1, mm: int = 9) -> list[dict]:
    return [
        {"date": f"{mm:02d}/{first_day + i:02d}/2026", "usage": u, "billing_month": month}
        for i, u in enumerate(usage)
    ]


# Shaped like the portal's "October,2026" period: 1-2 CCF a day, then two days
# carrying the meter reading. Billed for the period: 41 CCF.
OCTOBER = period("Oct 26", [1, 1, 2, 1, 1, 2, 1, 1, 1, 2, 4947, 5056, 0, 2, 2, 2, 1, 1])


@pytest.fixture
def coordinator(hass):
    entry = MockConfigEntry(domain=DOMAIN, data={"username": "u", "password": "p", "operation_mode": "daily"})
    entry.add_to_hass(hass)
    client = MagicMock()
    client.get_billed_usage = AsyncMock(return_value={"Oct 26": 41.0, "Sep 26": 35.0})
    return AtmosEnergyDataUpdateCoordinator(hass, client, entry)


async def test_meter_read_days_share_what_the_bill_leaves(coordinator):
    repaired = await coordinator._async_repair_meter_read_days(OCTOBER)

    good = sum(r["usage"] for r in OCTOBER if r["usage"] < 100)
    fill = (41.0 - good) / 2
    assert [r["usage"] for r in repaired[10:12]] == [fill, fill]
    assert repaired[10]["reported_usage"] == 4947
    assert sum(r["usage"] for r in repaired) == pytest.approx(41.0)
    # Every other day is untouched
    assert [r for i, r in enumerate(repaired) if i not in (10, 11)] == [
        r for i, r in enumerate(OCTOBER) if i not in (10, 11)
    ]


async def test_clean_data_is_left_alone_without_fetching_bills(coordinator):
    september = period("Sep 26", [1, 2, 1, 1, 2] * 6)
    assert await coordinator._async_repair_meter_read_days(september) == september
    coordinator.client.get_billed_usage.assert_not_awaited()


async def test_cold_snap_is_not_a_meter_reading(coordinator):
    # February 2025 ran up to ~15 CCF a day; well under 20x a 4 CCF typical day
    february = period("Feb 25", [3, 4, 4, 5, 15, 14, 9, 4, 3, 4], mm=2)
    assert await coordinator._async_repair_meter_read_days(february) == february


async def test_large_customers_are_not_flagged(coordinator):
    commercial = period("Oct 26", [450, 500, 520, 480, 610, 495])
    assert await coordinator._async_repair_meter_read_days(commercial) == commercial


async def test_without_billed_usage_the_typical_day_is_used(coordinator):
    coordinator.client.get_billed_usage.side_effect = APIError("temporarily unavailable")
    repaired = await coordinator._async_repair_meter_read_days(OCTOBER)
    assert [r["usage"] for r in repaired[10:12]] == [1, 1]


async def test_bills_are_fetched_once_for_several_bad_periods(coordinator):
    september = period("Sep 26", [1, 1, 5023, 1, 2], mm=8)
    await coordinator._async_repair_meter_read_days(september + OCTOBER)
    coordinator.client.get_billed_usage.assert_awaited_once()


async def test_repaired_day_replaces_stored_model_history(coordinator):
    coordinator._history = {"09/11/2026": {"date": "09/11/2026", "usage": 4947.0}}
    repaired = await coordinator._async_repair_meter_read_days(OCTOBER)
    await coordinator._sync_history({"history": repaired})
    assert coordinator._history["09/11/2026"]["usage"] == repaired[10]["usage"]
