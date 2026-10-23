"""API client: account parsing, selection, and error-page classification."""
from unittest.mock import AsyncMock

import pytest

from custom_components.atmos_energy.api import AtmosEnergyApiClient
from custom_components.atmos_energy.exceptions import AccountSelectionError, APIError, AuthenticationError

# Same structure as the portal's post-login "Summary of Accounts" page; fictional data
SUMMARY = b"""<!DOCTYPE html><html><head><title>Atmos Energy Account Center</title></head><body>
<form action="/accountcenter/successerror/inactivity.html" method="post"></form>
<div class="grpBody"><div class="grpBodyCell"><div class="line"><div class="margin">
 <div class="s-12 m-12 l-6 form_styled no_form narrow">
  <p><span class="emphasisLarge">Pat\xc2\xa0Example<br/>1111111111</span><br/>100 MAIN ST<br/>ANYTOWN, TX 75001</p>
  <div class="noInput hide-s hide-m"><a class="btnCommon btnCompact" href="/accountcenter/landing/singleAccount.html?accountNumber=1111111111">View Account</a></div>
 </div>
 <div class="noInput topPad hide-l"><a href="/accountcenter/landing/singleAccount.html?accountNumber=1111111111">View Account</a></div>
</div></div></div></div>
<div class="grpBody"><div class="grpBodyCell"><div class="line"><div class="margin">
 <div class="s-12 m-12 l-6 form_styled no_form narrow">
  <p><span class="emphasisLarge">Pat\xc2\xa0Example<br/>2222222222</span><br/>200 ELM DR, APT/SUITE 3<br/>ANYTOWN, TX 75002-1234</p>
  <div class="noInput"><a href="/accountcenter/landing/singleAccount.html?accountNumber=2222222222">View Account</a></div>
 </div>
</div></div></div></div>
</body></html>"""

SINGLE_LANDING = b"""<!DOCTYPE html><html><body><span>Next meter read date:</span><span class="value">Wed Nov 18</span></body></html>"""

OUTAGE = b"""<!DOCTYPE html><html><body><h2>Error</h2><p>Atmos Energy\xe2\x80\x99s systems are temporarily unavailable.
We are working to quickly restore service.</p></body></html>"""


def test_parse_accounts():
    assert AtmosEnergyApiClient._parse_accounts(SUMMARY) == [
        {"account_number": "1111111111", "address": "100 MAIN ST, ANYTOWN, TX 75001"},
        {"account_number": "2222222222", "address": "200 ELM DR, APT/SUITE 3, ANYTOWN, TX 75002-1234"},
    ]


def test_parse_accounts_single_account_page():
    assert AtmosEnergyApiClient._parse_accounts(SINGLE_LANDING) == []


async def test_outage_page_is_api_error_not_auth():
    client = AtmosEnergyApiClient("u", "p")
    with pytest.raises(APIError) as err:
        await client._verify_content(OUTAGE)
    assert not isinstance(err.value, AuthenticationError)


def client_with(accounts, account_number=None):
    client = AtmosEnergyApiClient("u", "p", account_number=account_number)
    client.accounts = accounts
    client._request_with_retry = AsyncMock(return_value=(200, "https://x/singleAccount.html", SINGLE_LANDING))
    return client


TWO = AtmosEnergyApiClient._parse_accounts(SUMMARY)


async def test_select_none_needed_for_single_account():
    client = client_with([])
    await client._select_account()
    client._request_with_retry.assert_not_awaited()


async def test_select_required_for_multi_account():
    with pytest.raises(AccountSelectionError, match="has 2 accounts"):
        await client_with(TWO)._select_account()


async def test_select_unknown_account():
    with pytest.raises(AccountSelectionError, match="not on this login"):
        await client_with(TWO, "9999999999")._select_account()


async def test_select_visits_single_account_page():
    client = client_with(TWO, "2222222222")
    await client._select_account()
    args, kwargs = client._request_with_retry.await_args
    assert args[1].endswith("/accountcenter/landing/singleAccount.html")
    assert kwargs["params"] == {"accountNumber": "2222222222"}


async def test_login_order():
    """authenticate -> select -> usage landing, and get_accounts never selects."""
    client = AtmosEnergyApiClient("u", "p", account_number="2222222222")
    calls = []
    client.check_session = AsyncMock(return_value=False)
    client._authenticate = AsyncMock(side_effect=lambda: calls.append("auth") or setattr(client, "accounts", TWO))
    client._select_account = AsyncMock(side_effect=lambda: calls.append("select"))
    client._request_with_retry = AsyncMock(side_effect=lambda *a, **k: calls.append(a[1].rsplit("/", 1)[-1]) or (200, a[1], SINGLE_LANDING))
    await client.login()
    assert calls == ["auth", "select", "UsageHistoryLanding.html"]

    calls.clear()
    assert await client.get_accounts() == TWO
    assert calls == ["auth"]
