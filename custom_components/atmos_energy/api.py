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
        self._common_headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
        }

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

    async def _verify_response(self, response: aiohttp.ClientResponse | None, content: bytes | None = None):
        """Verify the response for sanity, redirects, and portal errors."""
        # 1. URL and Redirect Checks (requires response object)
        if response:
            final_url = str(response.url).lower()
            if "login.html" in final_url or "authenticate.html" in final_url:
                _LOGGER.warning("Detected redirect to login page: %s", final_url)
                raise AuthenticationError("Session expired or redirected to login")

            if "successerrormessage.html" in final_url:
                _LOGGER.warning("Detected redirect to portal error page: %s", final_url)
                raise APIError("Atmos Energy portal returned an error page (successErrorMessage.html)")

        # 2. Content Checks (HTML detection and error strings)
        if content and (content.startswith(b"<!DOCTYP") or content.startswith(b"<html")):
            html_text = content[:2000].decode('utf-8', errors='replace').lower()
            
            # Login Indicators
            if any(ind in html_text for ind in ["login", "username", "password", "authenticate", "logon"]):
                _LOGGER.warning("Received HTML appears to be a login page.")
                raise AuthenticationError("Portal returned a login page instead of the expected data")
            
            # Error Indicators
            errors = ["access denied", "session expired", "not authorized", "temporary problem"]
            found_error = next((err for err in errors if err in html_text), None)
            if found_error:
                _LOGGER.warning("Received HTML appears to be an error page: %s", html_text[:200])
                raise APIError(f"Atmos Energy portal returned an error: {found_error}")

    async def _get_form_tokens(self, url: str) -> dict[str, str]:
        """Scrape form tokens (like formId) from a page."""
        async with await self._request_with_retry('get', url, headers=self._common_headers) as resp:
            text = await resp.text()
            
        soup = BeautifulSoup(text, 'html.parser')
        tokens = {}
        for input_tag in soup.find_all('input', {'name': True, 'value': True}):
            tokens[input_tag['name']] = input_tag['value']
        return tokens

    async def login(self):
        """Login to Atmos Energy and initialize the usage session."""
        if await self.check_session():
            _LOGGER.debug("Session is still valid, skipping login.")
            return

        # 1. Get login form tokens
        login_url = f"{self._base_url}/accountcenter/logon/login.html"
        tokens = await self._get_form_tokens(login_url)
        
        # 2. Perform Authentication POST
        auth_url = f"{self._base_url}/accountcenter/logon/authenticate.html"
        payload = {
            "formId": tokens.get("formId", ""),
            "username": self._username,
            "password": self._password,
            "button.Login": "Login"
        }
        
        headers = {**self._common_headers, 'Referer': login_url, 'Content-Type': 'application/x-www-form-urlencoded'}
        _LOGGER.debug("Attempting login for user: %s***", self._username[:3])
        
        async with await self._request_with_retry('post', auth_url, data=payload, headers=headers, allow_redirects=True) as resp:
            await self._verify_response(resp)

        # 3. LANDING: Visit the Usage Landing page to "activate" the session for downloads
        _LOGGER.debug("Initializing usage session...")
        landing_url = f"{self._base_url}/accountcenter/usagehistory/UsageHistoryLanding.html"
        headers['Referer'] = auth_url
        async with await self._request_with_retry('get', landing_url, headers=headers) as resp:
            await self._verify_response(resp)
            
        _LOGGER.debug("Login and session initialization successful")

    async def get_daily_usage(self):
        """Fetch and parse daily usage data."""
        await self._rate_limit()
        await self.login()
        
        url = f"{self._base_url}/accountcenter/usagehistory/dailyUsageDownload.html"
        headers = {
            **self._common_headers,
            'Referer': f"{self._base_url}/accountcenter/usagehistory/dailyUsage.html",
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        }
        
        async with await self._request_with_retry('get', url, params={"billingPeriod": "Current"}, headers=headers) as resp:
            await self._verify_response(resp)
            content = await resp.read()
            # Double check content for hidden errors (e.g. HTML inside binary)
            await self._verify_response(resp, content=content)
            
        return await self._parse_xls_data(content)
        
    async def _parse_xls_data(self, content: bytes) -> dict[str, Any]:
        """Parse the binary XLS content using pandas."""
        # Safety check: Verify content for errors/login pages
        await self._verify_response(None, content=content)

        def _parse_impl():
            import pandas as pd
            from io import BytesIO
            
            stripped = content.strip()
            
            # Robust Format Detection
            is_html = stripped.startswith((b"<!DOCTYP", b"<html"))
            
            try:
                if is_html:
                    # Attempt HTML table parsing
                    dfs = pd.read_html(BytesIO(stripped))
                    if not dfs:
                        raise DataParseError("HTML received but no tables found")
                    df = dfs[0]
                else:
                    # Standard Excel parsing (with fallback to xlrd for old formats)
                    try:
                        df = pd.read_excel(BytesIO(stripped))
                    except Exception:
                        df = pd.read_excel(BytesIO(stripped), engine='xlrd')
            except Exception as e:
                _LOGGER.error("Failed to parse data (first 50 bytes: %s): %s", stripped[:50].hex(' '), e)
                raise DataParseError(f"Format error: {e}") from e
                
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

