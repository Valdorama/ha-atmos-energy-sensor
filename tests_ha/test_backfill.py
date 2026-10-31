"""Backfill of past billing periods from the daily usage download."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.atmos_energy.api import AtmosEnergyApiClient
from custom_components.atmos_energy.const import DOMAIN
from custom_components.atmos_energy.coordinator import AtmosEnergyDataUpdateCoordinator
from custom_components.atmos_energy.exceptions import APIError, DataParseError

# The Daily Usage page's period picker, trimmed. Values are "<bill month>,<year>".
DAILY_USAGE_PAGE = b"""
<form action="/accountcenter/usagehistory/dailyUsage.html">
  <select name="billingPeriod">
    <option value="Current" selected>Current</option>
    <option value="October,2026">October,2026</option>
    <option value="September,2026">September,2026</option>
    <option value="August,2026">August,2026</option>
  </select>
</form>
"""


def days(month: int, first: int, last: int, usage: float = 1.0) -> list[dict]:
    """Daily records shaped like _parse_xls_data's history, dated MM/DD/2026."""
    return [{"date": f"{month:02d}/{d:02d}/2026", "usage": usage} for d in range(first, last + 1)]


# What each period's download holds, as the live portal splits them
PERIODS = {
    "August,2026": days(7, 6, 31) + days(8, 1, 5),
    "September,2026": days(8, 6, 31) + days(9, 1, 3),
    "October,2026": days(9, 4, 30) + days(10, 1, 5),
}
CURRENT = days(10, 6, 28)


def test_parse_billing_periods_skips_current():
    assert AtmosEnergyApiClient._parse_billing_periods(DAILY_USAGE_PAGE) == [
        "October,2026", "September,2026", "August,2026",
    ]


def test_parse_billing_periods_without_picker():
    assert AtmosEnergyApiClient._parse_billing_periods(b"<html><body>No picker</body></html>") == []


async def test_daily_history_logs_in_once_and_skips_unparseable_period():
    client = AtmosEnergyApiClient("user", "pw", session=MagicMock())
    client.login = AsyncMock()

    async def download(period):
        if period == "September,2026":
            raise DataParseError("Missing 'consumption' column")
        return {"history": PERIODS[period]}

    client._download_daily_usage = AsyncMock(side_effect=download)
    history = await client.get_daily_history(["August,2026", "September,2026", "October,2026"])

    client.login.assert_awaited_once()
    assert [c.args[0] for c in client._download_daily_usage.await_args_list] == [
        "August,2026", "September,2026", "October,2026",
    ]
    assert history == PERIODS["August,2026"] + PERIODS["October,2026"]


async def test_download_sends_billing_period():
    client = AtmosEnergyApiClient("user", "pw", session=MagicMock())
    client._request_with_retry = AsyncMock(return_value=(200, "https://www.atmosenergy.com/x", b""))
    client._parse_xls_data = AsyncMock(return_value={"history": []})

    await client._download_daily_usage("September,2026")

    kwargs = client._request_with_retry.await_args.kwargs
    assert kwargs["params"] == {"billingPeriod": "September,2026"}


@pytest.fixture
def coordinator(hass):
    entry = MockConfigEntry(
        domain=DOMAIN, entry_id="backfill01", unique_id="atmos_user",
        data={"username": "atmos_user", "password": "pw", "operation_mode": "daily"},
        options={"backfill_history": True},
    )
    entry.add_to_hass(hass)
    client = MagicMock()
    client.get_billing_periods = AsyncMock(return_value=list(PERIODS)[::-1])  # newest first

    async def history(periods):
        return [record for p in periods for record in PERIODS[p]]

    client.get_daily_history = AsyncMock(side_effect=history)
    return AtmosEnergyDataUpdateCoordinator(hass, client, entry)


async def test_first_run_backfills_every_period_oldest_first(coordinator):
    history = await coordinator._async_add_past_periods(CURRENT)

    coordinator.client.get_daily_history.assert_awaited_once_with(
        ["August,2026", "September,2026", "October,2026"]
    )
    assert coordinator._backfill_done
    dates = [r["date"] for r in history]
    assert dates[0] == "07/06/2026" and dates[-1] == "10/28/2026"
    # Periods meet end to end: one record per day from 7/6 through 10/28
    assert len(dates) == len(set(dates)) == 26 + 31 + 30 + 28
    assert dates == sorted(dates, key=lambda d: (d[6:], d[:5]))


async def test_backfill_off_fetches_latest_period_only(coordinator):
    coordinator.hass.config_entries.async_update_entry(coordinator.config_entry, options={"backfill_history": False})
    history = await coordinator._async_add_past_periods(CURRENT)

    coordinator.client.get_daily_history.assert_awaited_once_with(["October,2026"])
    assert not coordinator._backfill_done
    assert history[0]["date"] == "09/04/2026"


async def test_backfill_option_missing_defaults_to_off(coordinator):
    coordinator.hass.config_entries.async_update_entry(coordinator.config_entry, options={})
    await coordinator._async_add_past_periods(CURRENT)

    coordinator.client.get_daily_history.assert_awaited_once_with(["October,2026"])
    assert not coordinator._backfill_done


async def test_later_runs_fetch_only_latest_period(coordinator):
    coordinator._backfill_done = True
    history = await coordinator._async_add_past_periods(CURRENT)

    coordinator.client.get_daily_history.assert_awaited_once_with(["October,2026"])
    assert history[0]["date"] == "09/04/2026"
    assert history[-1]["date"] == "10/28/2026"


async def test_repeated_day_keeps_newest_period(coordinator):
    coordinator._backfill_done = True
    overlap = [{"date": "10/05/2026", "usage": 9.0}] + CURRENT
    history = await coordinator._async_add_past_periods(overlap)

    oct5 = [r for r in history if r["date"] == "10/05/2026"]
    assert oct5 == [{"date": "10/05/2026", "usage": 9.0}]


async def test_portal_error_keeps_current_and_retries_backfill(coordinator):
    coordinator.client.get_billing_periods.side_effect = APIError("temporarily unavailable")
    history = await coordinator._async_add_past_periods(CURRENT)

    assert history == CURRENT
    assert not coordinator._backfill_done


async def test_no_period_picker_leaves_backfill_pending(coordinator):
    coordinator.client.get_billing_periods.return_value = []
    history = await coordinator._async_add_past_periods(CURRENT)

    assert history == CURRENT
    assert not coordinator._backfill_done


async def test_update_imports_all_periods_in_one_statistics_call(hass, coordinator):
    coordinator.client.get_account_data = AsyncMock(return_value={
        "usage": float(len(CURRENT)),
        "history": CURRENT,
        "billing_period_start": "10/06/2026",
        "next_meter_read_date": None,
    })
    with patch("custom_components.atmos_energy.coordinator.statistics_during_period", return_value={}), \
         patch("custom_components.atmos_energy.coordinator.get_last_statistics", return_value={}), \
         patch("custom_components.atmos_energy.coordinator.async_add_external_statistics") as add_stats, \
         patch.object(coordinator, "_update_gcr_rate", AsyncMock()), \
         patch.object(coordinator.published_rates, "async_refresh", AsyncMock(return_value=False)), \
         patch.object(coordinator, "_async_save_history", AsyncMock()):
        data = await coordinator._async_update_data()

    usage_calls = [c for c in add_stats.call_args_list if c.args[1]["statistic_id"].startswith(f"{DOMAIN}:usage_")]
    assert len(usage_calls) == 1
    stats = usage_calls[0].args[2]
    assert len(stats) == 26 + 31 + 30 + 28
    assert stats[0]["start"] == dt_util.as_utc(dt_util.parse_datetime("2026-07-06T00:00:00"))
    assert stats[-1]["sum"] == pytest.approx(len(stats))
    # Sensors still see only the current period
    assert data["history"] == CURRENT


async def test_cost_sum_ignores_existing_record_inside_the_batch(hass, coordinator):
    """A cost record at or after the batch start gets rewritten, so it can't seed the sum."""
    coordinator.client.get_account_data = AsyncMock(return_value={
        "usage": float(len(CURRENT)),
        "history": CURRENT,
        "billing_period_start": "10/06/2026",
        "next_meter_read_date": None,
    })
    inside_batch = dt_util.as_utc(dt_util.parse_datetime("2026-10-01T00:00:00")).timestamp()

    def last_stats(hass_, count, statistic_id, *args):
        return {statistic_id: [{"start": inside_batch, "sum": 773.16}]}

    with patch("custom_components.atmos_energy.coordinator.statistics_during_period", return_value={}), \
         patch("custom_components.atmos_energy.coordinator.get_last_statistics", side_effect=last_stats), \
         patch("custom_components.atmos_energy.coordinator.async_add_external_statistics") as add_stats, \
         patch.object(coordinator, "_update_gcr_rate", AsyncMock()), \
         patch.object(coordinator.published_rates, "async_refresh", AsyncMock(return_value=False)), \
         patch.object(coordinator, "_async_save_history", AsyncMock()):
        await coordinator._async_update_data()

    cost_calls = [c for c in add_stats.call_args_list if c.args[1]["statistic_id"].startswith(f"{DOMAIN}:cost_")]
    first = cost_calls[0].args[2][0]
    assert first["sum"] == pytest.approx(first["state"])  # starts from 0, not 773.16
