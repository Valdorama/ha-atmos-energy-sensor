"""Diagnostics: the download works and leaks no login or address."""
from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.atmos_energy.const import DOMAIN
from custom_components.atmos_energy.diagnostics import async_get_config_entry_diagnostics

from conftest import ACCOUNTS, make_client

USERNAME = "jane.doe@example.com"
NUMBER, ADDRESS = ACCOUNTS[1]["account_number"], ACCOUNTS[1]["address"]


@pytest.fixture
async def entry(hass):
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id=NUMBER, entry_id="diag01",
        title=f"{USERNAME} ({ADDRESS.split(',')[0]})",
        data={
            "username": USERNAME, "password": "secret-pw", "operation_mode": "monthly",
            "account_number": NUMBER, "service_address": ADDRESS,
        },
    )
    entry.add_to_hass(hass)
    with patch("custom_components.atmos_energy.AtmosEnergyApiClient", side_effect=make_client(ACCOUNTS)):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


async def test_diagnostics_download_works(hass, entry):
    hass.data[DOMAIN][entry.entry_id]._history = {"2026-10-01": {"usage": 1.0}, "2026-10-02": {"usage": 2.0}}

    diag = await async_get_config_entry_diagnostics(hass, entry)

    assert diag["model_info"]["history_days"] == 2
    assert diag["model_info"]["model_trained"] is False


async def test_diagnostics_redact_login_address_and_account(hass, entry):
    diag = await async_get_config_entry_diagnostics(hass, entry)
    text = repr(diag)

    for secret in (USERNAME, "jane", "secret-pw", NUMBER, NUMBER[-4:], "ELM DR"):
        assert secret not in text, secret
    assert diag["entry"]["title"] == "**REDACTED**"
    assert diag["entry"]["unique_id"] == "**REDACTED**"
    assert diag["data_config"]["username"] == "**REDACTED**"
    assert diag["data_config"]["has_service_address"] is True
