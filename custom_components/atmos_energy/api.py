"""API Client for Atmos Energy."""
import logging
import asyncio
from datetime import datetime, timedelta
from typing import Any

import aiohttp
from bs4 import BeautifulSoup

from .const import TIMEOUT
from .exceptions import AuthenticationError, APIError, DataParseError

_LOGGER = logging.getLogger(__name__)

class AtmosEnergyApiClient:
    """API Client for Atmos Energy."""

    def __init__(self, username, password, session=None):
        """Initialize the API client."""
        self._username = username
        self._password = password
        self._session = session
        self._base_url = "https://www.atmosenergy.com"
        self._last_request: datetime | None = None
        self._min_request_interval = timedelta(minutes=5)

    async def _get_session(self):
        """Get or create the aiohttp session."""
        if self._session is None:
            timeout = aiohttp.ClientTimeout(total=TIMEOUT, connect=10)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    async def _rate_limit(self):
        """Enforce rate limiting to avoid getting blocked."""
        now = datetime.now()
        if self._last_request:
            elapsed = now - self._last_request
            if elapsed < self._min_request_interval:
                wait_time = (self._min_request_interval - elapsed).total_seconds()
                _LOGGER.debug("Rate limiting: waiting %.1f seconds", wait_time)
                await asyncio.sleep(wait_time)
        self._last_request = now

    async def _request_with_retry(self, method_name: str, url: str, max_retries: int = 3, **kwargs) -> aiohttp.ClientResponse:
        """Make HTTP request with exponential backoff retry."""
        session = await self._get_session()
        method = getattr(session, method_name)
        
        for attempt in range(max_retries):
            try:
                # We don't use 'async with' here because we want to return the response object 
                # (Caller's responsibility to read and close if they don't use 'async with' context)
                response = await method(url, **kwargs)
                
                if response.status in (200, 302):
                    return response
                
                # Server side errors - likely transient
                if response.status in (500, 502, 503, 504):
                    if attempt == max_retries - 1:
                        raise APIError(f"Server error {response.status} after {max_retries} attempts")
                else:
                    # Client side errors - unlikely to resolve by retrying
                    raise APIError(f"Request failed with status {response.status}")
                    
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                if attempt == max_retries - 1:
                    raise APIError(f"Request failed after {max_retries} attempts: {e}") from e
                
                wait_time = 2 ** attempt  # 1s, 2s, 4s
                _LOGGER.warning("Request failed (attempt %d/%d), retrying in %ds: %s", 
                               attempt + 1, max_retries, wait_time, e)
                await asyncio.sleep(wait_time)
        
        raise APIError(f"Request to {url} failed after {max_retries} retries")

    async def check_session(self):
        """Check if current session is valid by hitting a protected endpoint."""
        if self._session is None:
            return False
            
        url = f"{self._base_url}/accountcenter/usagehistory/UsageHistoryLanding.html"
        try:
            # allow_redirects=False lets us catch the 302 to login
            async with self._session.get(url, timeout=10, allow_redirects=False) as response:
                if response.status == 302:
                    location = response.headers.get("Location", "")
                    if "login" in location.lower():
                        return False
                return response.status == 200
        except Exception:
            return False

    async def login(self):
        """Login to Atmos Energy."""
        if await self.check_session():
            _LOGGER.debug("Session is still valid, skipping login.")
            return

        # 1. Get the login page to grab CSRF/Form tokens
        login_page_url = f"{self._base_url}/accountcenter/logon/login.html"
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
        }

        async with await self._request_with_retry('get', login_page_url, headers=headers) as response:
            text = await response.text()
            
        soup = BeautifulSoup(text, 'html.parser')
        form_id_input = soup.find('input', {'name': 'formId'})
        form_id = form_id_input.get('value') if form_id_input else ""

        # 2. Perform Login POST
        login_action_url = f"{self._base_url}/accountcenter/logon/authenticate.html"
        payload = {
            "formId": form_id,
            "username": self._username,
            "password": self._password,
            "button.Login": "Login"
        }
        
        headers['Content-Type'] = 'application/x-www-form-urlencoded'
        headers['Referer'] = login_page_url

        _LOGGER.debug("Attempting login for user: %s***", self._username[:3])
        async with await self._request_with_retry('post', login_action_url, data=payload, headers=headers, allow_redirects=False) as response:
            # Check for redirect to login (auth failure)
            if response.status == 302:
                location = response.headers.get("Location", "")
                if "login" in location.lower():
                    raise AuthenticationError("Invalid username or password (redirected to login)")
            
            # Check for error messages in 200 OK response
            if response.status == 200:
                text = await response.text()
                error_phrases = ["invalid username", "invalid password", "authentication failed", "login failed"]
                if any(phrase in text.lower() for phrase in error_phrases):
                    raise AuthenticationError("Invalid username or password")

        _LOGGER.debug("Login successful")

    async def get_daily_usage(self):
        """Fetch and parse daily usage data."""
        await self._rate_limit()
        await self.login()
        
        url = f"{self._base_url}/accountcenter/usagehistory/dailyUsageDownload.html"
        params = {"billingPeriod": "Current"}
        headers = {
            'Referer': f"{self._base_url}/accountcenter/usagehistory/dailyUsage.html",
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        }
        
        async with await self._request_with_retry('get', url, params=params, headers=headers) as response:
            content = await response.read()
            
        return await self._parse_xls_data(content)
        
    async def _parse_xls_data(self, content):
        """Parse the binary XLS content using pandas for better stability."""
        def _parse_impl():
            import pandas as pd
            from io import BytesIO
            
            try:
                # pandas handles both .xls (xlrd) and .xlsx (openpyxl) seamlessly if installed
                # We try without specifying engine first, but we can fallback
                try:
                    df = pd.read_excel(BytesIO(content))
                except Exception:
                    # Explicit fallback for .xls if engine detection fails
                    df = pd.read_excel(BytesIO(content), engine='xlrd')
            except Exception as e:
                _LOGGER.error("Failed to parse XLS: %s", e)
                raise DataParseError(f"Could not read Excel file: {e}") from e
                
            # Normalize column names to lowercase/stripped
            df.columns = [str(c).lower().strip() for c in df.columns]
            
            if 'consumption' not in df.columns:
                raise DataParseError(f"Missing 'consumption' column. Found: {list(df.columns)}")
            
            # Find date column (could be 'weather date', 'reading date', etc.)
            date_col = next((col for col in df.columns if 'date' in col), None)
            
            # Clean consumption data (convert to numeric, drop NaNs)
            df['consumption'] = pd.to_numeric(df['consumption'], errors='coerce')
            df = df.dropna(subset=['consumption'])
            
            total_usage = float(df['consumption'].sum())
            latest_usage = float(df['consumption'].iloc[-1]) if not df.empty else 0.0
            latest_date = None
            if date_col and not df.empty:
                # Convert date to string format for HA
                latest_date_raw = df[date_col].iloc[-1]
                latest_date = str(latest_date_raw)
                first_date_raw = df[date_col].iloc[0]
                first_date = str(first_date_raw)
            else:
                first_date = None
                    
            return {
                "total_usage": total_usage,
                "latest_usage": latest_usage,
                "latest_date": latest_date,
                "billing_period_start": first_date,
                "period": "Current"
            }

        import asyncio
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _parse_impl)

    async def get_account_data(self) -> dict[str, Any]:
        """Fetch account data including usage."""
        usage_data = await self.get_daily_usage()
        
        return {
            "bill_date": usage_data.get("latest_date"),
            "due_date": "Unknown",
            "amount_due": None,
            "usage": usage_data.get("total_usage", 0.0),
            "daily_usage": usage_data.get("latest_usage", 0.0)
        }
        
    async def close(self):
        """Close the session."""
        if self._session:
            await self._session.close()
            self._session = None

