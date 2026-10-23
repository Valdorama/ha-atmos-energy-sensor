"""Entry setup: entity/device identity, per-entry storage and migration, services."""
from unittest.mock import patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr, entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.atmos_energy.const import DOMAIN

from conftest import ACCOUNTS, make_client

CREDS = {"username": "atmos_user", "password": "pw", "operation_mode": "monthly"}
MAIN, ELM = ACCOUNTS[0]["account_number"], ACCOUNTS[1]["account_number"]

LEGACY_HISTORY = {
    "version": 1,
    "minor_version": 1,
    "key": "atmos_energy_history",
    "data": {"history": {"2026-10-01": {"usage": 3.0}}, "last_update": "2026-10-01T08:00:00-05:00"},
}


@pytest.fixture
def setup_client():
    factory = make_client(ACCOUNTS)
    with patch("custom_components.atmos_energy.AtmosEnergyApiClient", side_effect=factory):
        yield factory


def legacy_entry():
    return MockConfigEntry(domain=DOMAIN, unique_id="atmos_user", data=CREDS, entry_id="legacy01")


def multi_entry(number, address, entry_id):
    return MockConfigEntry(
        domain=DOMAIN, unique_id=number, entry_id=entry_id,
        data={**CREDS, "account_number": number, "service_address": address},
    )


async def setup(hass, *entries):
    for e in entries:
        e.add_to_hass(hass)
    for e in entries:
        # Setting up the integration's first entry also sets up the others already added
        if e.state is ConfigEntryState.NOT_LOADED:
            assert await hass.config_entries.async_setup(e.entry_id)
    await hass.async_block_till_done()
    assert all(e.state is ConfigEntryState.LOADED for e in entries)


async def test_client_gets_account_number(hass, setup_client):
    await setup(hass, legacy_entry(), multi_entry(ELM, ACCOUNTS[1]["address"], "elm01"))
    by_number = {c.account_number for c in setup_client.instances}
    assert by_number == {None, ELM}


async def test_entity_and_device_identity(hass, setup_client):
    await setup(hass, legacy_entry(), multi_entry(ELM, ACCOUNTS[1]["address"], "elm01"))
    ent_reg, dev_reg = er.async_get(hass), dr.async_get(hass)

    legacy_ids = {e.unique_id for e in er.async_entries_for_config_entry(ent_reg, "legacy01")}
    elm_ids = {e.unique_id for e in er.async_entries_for_config_entry(ent_reg, "elm01")}
    assert legacy_ids == {"atmos_energy_atmos_user_usage"}  # unchanged from before
    assert elm_ids == {f"atmos_energy_{ELM}_usage"}

    names = {e: [d.name for d in dr.async_entries_for_config_entry(dev_reg, e)] for e in ("legacy01", "elm01")}
    assert names == {"legacy01": ["Atmos Energy"], "elm01": ["Atmos Energy 200 Elm Dr"]}

    # Distinct entity_ids, no _2 suffixes
    ids = sorted(e.entity_id for e in ent_reg.entities.values() if e.platform == DOMAIN)
    assert len(ids) == 2 and not any(i.endswith("_2") for i in ids), ids


async def test_legacy_entry_fixed_in_options_keeps_entity_ids(hass, setup_client):
    """After a legacy entry picks an account, its entities keep their username-based IDs."""
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id="atmos_user", entry_id="legacy01",
        data={**CREDS, "account_number": MAIN, "service_address": ACCOUNTS[0]["address"]},
    )
    await setup(hass, entry)
    ids = {e.unique_id for e in er.async_entries_for_config_entry(er.async_get(hass), "legacy01")}
    assert ids == {"atmos_energy_atmos_user_usage"}


async def test_legacy_history_moves_to_legacy_entry_only(hass, hass_storage, setup_client):
    hass_storage["atmos_energy_history"] = LEGACY_HISTORY
    # Set up the new multi-account entry first: it must not claim the old file
    await setup(hass, multi_entry(ELM, ACCOUNTS[1]["address"], "elm01"))
    assert "atmos_energy_history" in hass_storage
    await setup(hass, legacy_entry())

    assert "atmos_energy_history" not in hass_storage
    moved = hass_storage["atmos_energy_history_legacy01"]["data"]
    assert moved["history"] == {"2026-10-01": {"usage": 3.0}}
    elm = hass_storage.get("atmos_energy_history_elm01", {}).get("data", {})
    assert "2026-10-01" not in elm.get("history", {})


async def test_two_legacy_entries_only_one_claims(hass, hass_storage, setup_client):
    hass_storage["atmos_energy_history"] = LEGACY_HISTORY
    a = MockConfigEntry(domain=DOMAIN, unique_id="user_a", data={**CREDS, "username": "user_a"}, entry_id="a")
    b = MockConfigEntry(domain=DOMAIN, unique_id="user_b", data={**CREDS, "username": "user_b"}, entry_id="b")
    await setup(hass, a, b)
    owners = [k for k in ("a", "b") if "2026-10-01" in
              hass_storage.get(f"atmos_energy_history_{k}", {}).get("data", {}).get("history", {})]
    assert len(owners) == 1


async def test_remove_entry_deletes_history(hass, hass_storage, setup_client):
    entry = multi_entry(ELM, ACCOUNTS[1]["address"], "elm01")
    await setup(hass, entry)
    assert "atmos_energy_history_elm01" in hass_storage
    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert "atmos_energy_history_elm01" not in hass_storage


async def test_refresh_service_refreshes_every_entry(hass, setup_client):
    await setup(hass, legacy_entry(), multi_entry(ELM, ACCOUNTS[1]["address"], "elm01"))
    before = [c.get_account_data.await_count for c in setup_client.instances]
    await hass.services.async_call(DOMAIN, "refresh_current_usage", {}, blocking=True)
    after = [c.get_account_data.await_count for c in setup_client.instances]
    assert all(a == b + 1 for a, b in zip(after, before)), (before, after)


async def test_populate_needs_entry_when_several(hass, setup_client):
    await setup(hass, legacy_entry(), multi_entry(ELM, ACCOUNTS[1]["address"], "elm01"))
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, "populate_past_usage", {"file_path": "/x.xls"}, blocking=True)

    with patch("custom_components.atmos_energy.coordinator.AtmosEnergyDataUpdateCoordinator.async_import_historical_xls") as imp:
        await hass.services.async_call(
            DOMAIN, "populate_past_usage", {"file_path": "/x.xls", "config_entry": "elm01"}, blocking=True
        )
    imp.assert_awaited_once_with("/x.xls")
    assert hass.data[DOMAIN]["elm01"] is not None


ISSUE = "account_selection_legacy01"


@pytest.fixture
def unselected_client():
    """Every fetch fails the way a multi-account login with no account does."""
    from custom_components.atmos_energy.exceptions import AccountSelectionError
    base = make_client(ACCOUNTS)

    def factory(*args, **kwargs):
        client = base(*args, **kwargs)
        client.get_account_data.side_effect = AccountSelectionError(
            "This Atmos Energy login has 2 accounts and none is selected."
        )
        return client

    factory.instances = base.instances
    with patch("custom_components.atmos_energy.AtmosEnergyApiClient", side_effect=factory):
        yield factory


async def test_repair_issue_raised_and_cleared(hass, unselected_client):
    from homeassistant.helpers import issue_registry as ir

    await setup(hass, legacy_entry())
    issue = ir.async_get(hass).async_get_issue(DOMAIN, ISSUE)
    assert issue is not None
    assert issue.severity is ir.IssueSeverity.ERROR
    assert issue.translation_key == "account_selection"
    assert issue.translation_placeholders["title"] == "Mock Title"
    assert "none is selected" in issue.translation_placeholders["error"]

    # Next fetch succeeds (account chosen): the issue goes away
    unselected_client.instances[-1].get_account_data.side_effect = None
    await hass.data[DOMAIN]["legacy01"].async_refresh()
    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE) is None


async def test_repair_issue_not_raised_for_other_errors(hass, setup_client):
    from homeassistant.helpers import issue_registry as ir
    from custom_components.atmos_energy.exceptions import APIError

    await setup(hass, legacy_entry())
    setup_client.instances[-1].get_account_data.side_effect = APIError("portal down")
    await hass.data[DOMAIN]["legacy01"].async_refresh()
    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE) is None


async def test_repair_issue_removed_with_entry(hass, unselected_client):
    from homeassistant.helpers import issue_registry as ir

    entry = legacy_entry()
    await setup(hass, entry)
    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE) is not None
    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE) is None
