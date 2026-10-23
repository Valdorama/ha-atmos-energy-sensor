"""Config, options and reauth flows with single- and multi-account logins."""
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.atmos_energy.const import DOMAIN
from custom_components.atmos_energy.exceptions import AuthenticationError

from conftest import ACCOUNTS

CREDS = {"username": "atmos_user", "password": "pw", "operation_mode": "monthly"}
MAIN, ELM = ACCOUNTS[0]["account_number"], ACCOUNTS[1]["account_number"]


async def start(hass):
    return await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})


async def test_single_account_unchanged(hass, flow_client, no_setup):
    flow_client.holder["accounts"] = []
    result = await start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], CREDS)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "atmos_user"
    assert result["data"] == CREDS  # no account fields for single-account logins
    assert result["result"].unique_id == "atmos_user"


async def test_single_account_duplicate_aborts(hass, flow_client, no_setup):
    MockConfigEntry(domain=DOMAIN, unique_id="atmos_user", data=CREDS).add_to_hass(hass)
    flow_client.holder["accounts"] = []
    result = await start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], CREDS)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_invalid_auth(hass, flow_client, no_setup):
    flow_client.holder["error"] = AuthenticationError("nope")
    result = await start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], CREDS)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_multi_account_picker(hass, flow_client, no_setup):
    flow_client.holder["accounts"] = ACCOUNTS
    result = await start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], CREDS)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "account"
    options = result["data_schema"].schema["account_number"].config["options"]
    assert [o["value"] for o in options] == [MAIN, ELM]
    assert options[0]["label"] == "100 MAIN ST, ANYTOWN, TX 75001 (1111111111)"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"account_number": MAIN})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "atmos_user (100 MAIN ST)"
    assert result["data"]["account_number"] == MAIN
    assert result["data"]["service_address"] == ACCOUNTS[0]["address"]
    assert result["result"].unique_id == MAIN


async def test_second_account_offers_only_the_rest(hass, flow_client, no_setup):
    MockConfigEntry(domain=DOMAIN, unique_id=MAIN, data={**CREDS, "account_number": MAIN}).add_to_hass(hass)
    flow_client.holder["accounts"] = ACCOUNTS

    result = await start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], CREDS)
    options = result["data_schema"].schema["account_number"].config["options"]
    assert [o["value"] for o in options] == [ELM]
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"account_number": ELM})
    assert result["type"] is FlowResultType.CREATE_ENTRY

    # Both taken now: a third attempt aborts
    result = await start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], CREDS)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_legacy_entry_does_not_block_multi_account_setup(hass, flow_client, no_setup):
    """A pre-upgrade entry has unique_id=username and no account; adding an account must still work."""
    MockConfigEntry(domain=DOMAIN, unique_id="atmos_user", data=CREDS).add_to_hass(hass)
    flow_client.holder["accounts"] = ACCOUNTS
    result = await start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], CREDS)
    assert result["step_id"] == "account"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"account_number": ELM})
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_options_legacy_entry_picks_account(hass, flow_client, no_setup):
    entry = MockConfigEntry(domain=DOMAIN, unique_id="atmos_user", data=CREDS)
    entry.add_to_hass(hass)
    flow_client.holder["accounts"] = ACCOUNTS

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], CREDS)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "account"
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"account_number": MAIN})
    assert result["type"] is FlowResultType.CREATE_ENTRY

    assert entry.data["account_number"] == MAIN
    assert entry.data["service_address"] == ACCOUNTS[0]["address"]
    assert entry.unique_id == "atmos_user"  # kept, so entity IDs don't change


async def test_options_entry_with_account_skips_picker(hass, flow_client, no_setup):
    entry = MockConfigEntry(domain=DOMAIN, unique_id=MAIN, data={**CREDS, "account_number": MAIN})
    entry.add_to_hass(hass)
    flow_client.holder["accounts"] = ACCOUNTS
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], CREDS)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.data["account_number"] == MAIN


async def test_options_account_gone(hass, flow_client, no_setup):
    entry = MockConfigEntry(domain=DOMAIN, unique_id="9999999999", data={**CREDS, "account_number": "9999999999"})
    entry.add_to_hass(hass)
    flow_client.holder["accounts"] = ACCOUNTS
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], CREDS)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "account_not_found"}


async def test_reauth_multi_account_entry(hass, flow_client, no_setup):
    """Reauth only checks credentials (get_accounts), so a multi-account entry can't trip
    on account selection. HA passes entry.data as the first input, and the pre-existing
    reauth step treats that as a submitted password, so it completes in one call."""
    entry = MockConfigEntry(domain=DOMAIN, unique_id=MAIN, data={**CREDS, "account_number": MAIN})
    entry.add_to_hass(hass)
    flow_client.holder["accounts"] = ACCOUNTS
    result = await entry.start_reauth_flow(hass)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    client = flow_client.call_args
    assert client.kwargs.get("source") == "reauth"
    assert entry.data["account_number"] == MAIN
