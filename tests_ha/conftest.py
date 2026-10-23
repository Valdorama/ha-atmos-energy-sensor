"""Fixtures for the multi-account tests.

These run against a real Home Assistant from pytest-homeassistant-custom-component,
so they live apart from tests/: the modules there replace homeassistant.* in
sys.modules with mocks at import time, which breaks a real HA in the same session.

    pytest tests_ha
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

ACCOUNTS = [
    {"account_number": "1111111111", "address": "100 MAIN ST, ANYTOWN, TX 75001"},
    {"account_number": "2222222222", "address": "200 ELM DR, APT/SUITE 3, ANYTOWN, TX 75002-1234"},
]

# Shape of get_account_data(daily_usage=False), as returned by the live portal
MONTHLY = {
    "bill_date": "2026-10-21 00:00:00",
    "meter_read_date": "10/20/2026",
    "avg_temp": 70.0,
    "billing_month": "Oct 26",
    "usage": 99.0,
    "next_meter_read_date": "Wed Nov 18 00:00:00 CST 2026",
}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


def make_client(accounts):
    """A stand-in AtmosEnergyApiClient class whose instances report `accounts`."""
    instances = []

    def factory(username, password, session=None, source="api", account_number=None):
        client = MagicMock()
        client.username = username
        client.account_number = account_number
        client.accounts = list(accounts)
        client.get_accounts = AsyncMock(return_value=list(accounts))
        client.login = AsyncMock()
        client.close = AsyncMock()
        client.get_account_data = AsyncMock(return_value=dict(MONTHLY))
        instances.append(client)
        return client

    factory.instances = instances
    return factory


@pytest.fixture
def flow_client():
    """Patch the client used by the config/options flows. Set .accounts before use."""
    holder = {"accounts": [], "error": None}

    def factory(*args, **kwargs):
        client = make_client(holder["accounts"])(*args, **kwargs)
        if holder["error"]:
            client.get_accounts.side_effect = holder["error"]
        return client

    with patch("custom_components.atmos_energy.config_flow.AtmosEnergyApiClient", side_effect=factory) as m:
        m.holder = holder
        yield m


@pytest.fixture
async def no_setup(hass):
    """Keep entry setup from running after a flow creates or reloads an entry."""
    with patch("custom_components.atmos_energy.async_setup_entry", return_value=True), \
         patch("custom_components.atmos_energy.async_unload_entry", return_value=True):
        yield
        # Unload while still patched; hass teardown would otherwise call the real unload
        for entry in hass.config_entries.async_entries("atmos_energy"):
            await hass.config_entries.async_unload(entry.entry_id)
