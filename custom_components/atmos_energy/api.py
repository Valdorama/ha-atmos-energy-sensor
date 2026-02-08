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

    def __init__(
        self, 
        username: str, 
        password: str, 
        session: aiohttp.ClientSession | None = None
    ) -> None:
        """Initialize the API client."""
        self._username = username
        self._password = password
        self._session = session
        self._base_url = "https://www.atmosenergy.com"
        self._last_request: datetime | None = None
        self._min_request_interval = timedelta(seconds=2)
        self._common_headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
        }

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create the aiohttp session."""
        if self._session is None:
            timeout = aiohttp.ClientTimeout(total=TIMEOUT, connect=10)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    async def _rate_limit(self) -> None:
        """Enforce rate limiting to avoid getting blocked."""
        now = datetime.now()
        if self._last_request:
            elapsed = now - self._last_request
            if elapsed < self._min_request_interval:
                wait_time = (self._min_request_interval - elapsed).total_seconds()
                _LOGGER.debug("Rate limiting: waiting %.1f seconds", wait_time)
                await asyncio.sleep(wait_time)
        self._last_request = now

    async def _request_with_retry(
        self, 
        method_name: str, 
        url: str, 
        max_retries: int = 3, 
        **kwargs
    ) -> tuple[int, str, bytes]:
        """Make HTTP request with exponential backoff retry.
        
        Returns:
            tuple: (status, effective_url, content)
        """
        session = await self._get_session()
        method = getattr(session, method_name)
        
        for attempt in range(max_retries):
            try:
                await self._rate_limit()
                async with method(url, **kwargs) as response:
                    status = response.status
                    effective_url = str(response.url)
                    content = await response.read()
                    
                    if status in (200, 302):
                        return status, effective_url, content
                    
                    # Server side errors - likely transient
                    if status in (500, 502, 503, 504):
                        if attempt == max_retries - 1:
                            raise APIError(f"Server error {status} after {max_retries} attempts")
                    else:
                        # Client side errors - unlikely to resolve by retrying
                        raise APIError(f"Request failed with status {status}")
                        
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                if attempt == max_retries - 1:
                    raise APIError(f"Request failed after {max_retries} attempts: {e}") from e
                
                wait_time = 2 ** attempt  # 1s, 2s, 4s
                _LOGGER.warning("Request failed (attempt %d/%d), retrying in %ds: %s", 
                               attempt + 1, max_retries, wait_time, e)
                await asyncio.sleep(wait_time)
        
        raise APIError(f"Request to {url} failed after {max_retries} retries")

    async def check_session(self) -> bool:
        """Check if current session is valid by hitting a protected endpoint."""
        url = f"{self._base_url}/accountcenter/usagehistory/UsageHistoryLanding.html"
        try:
            # Use small timeout for check
            async with asyncio.timeout(10):
                status, effective_url, content = await self._request_with_retry(
                    'get', 
                    url, 
                    headers=self._common_headers,
                    allow_redirects=True # Allow following internal redirects if any
                )
                await self._verify_response_headers(status, effective_url)
                await self._verify_content(content)
                return True
        except (AuthenticationError, APIError):
            return False
        except Exception as e:
            _LOGGER.debug("Session check failed: %s", e)
            return False

    async def _verify_response_headers(self, status: int, url: str, allow_login: bool = False) -> None:
        """Check response URL for authentication/error redirects."""
        lower_url = url.lower()
        
        if not allow_login and ("login.html" in lower_url or "authenticate.html" in lower_url):
            _LOGGER.warning("Detected redirect to login page: %s", url)
            raise AuthenticationError("Session expired or redirected to login")

        if "successerrormessage.html" in lower_url:
            _LOGGER.warning("Detected redirect to portal error page: %s", url)
            raise APIError("Atmos Energy portal returned an error page")

    async def _verify_content(self, content: bytes) -> None:
        """Check content for unexpected HTML or error messages."""
        if not content:
            return
            
        # Strip leading whitespace for initial check
        stripped_content = content.lstrip()
        if stripped_content.startswith((b"<!DOCTYP", b"<html", b"<HTML")):
            try:
                html_text = stripped_content[:10000].decode('utf-8', errors='replace').lower()
            except Exception:
                return

            # Login Indicators - focus on actual form elements and very specific login text
            indicators = [
                'type="password"', 
                'name="username"', 
                'name="password"',
                'name="j_username"',
                'name="j_password"',
                "sign in to your account",
                "sign in to the account center",
                "<h1>login</h1>"
            ]
            for ind in indicators:
                if ind in html_text:
                    _LOGGER.warning("Found login indicator '%s' in HTML response", ind)
                    raise AuthenticationError(f"Portal returned a login page instead of expected data (matched: {ind})")
            
            # Error and Session Indicators - these describe failures or state changes
            errors = [
                "access denied", 
                "session expired", 
                "not authorized", 
                "temporary problem",
                "session ended",
                "extended inactivity",
                "security measure",
                "invalid username",
                "invalid password",
                "login failed",
                "authentication failed"
            ]
            for err in errors:
                if err in html_text:
                    _LOGGER.warning("Found session error indicator '%s' in HTML response", err)
                    raise AuthenticationError(f"Atmos Energy portal returned an error or session-ended: {err}")

    async def _get_form_tokens(self, content: bytes) -> dict[str, str]:
        """Extract hidden form tokens from HTML."""
        soup = BeautifulSoup(content, 'html.parser')
        tokens = {}
        for input_tag in soup.find_all('input', {'name': True, 'value': True}):
            tokens[input_tag['name']] = input_tag['value']
        return tokens

    async def login(self) -> None:
        """Login to Atmos Energy and initialize the usage session."""
        if await self.check_session():
            _LOGGER.debug("Session is still valid, skipping login.")
            return

        _LOGGER.debug("Logging in user: %s***", self._username[:3])
        
        # 1. Get LogOn page and tokens
        login_url = f"{self._base_url}/accountcenter/logon/login.html"
        status, effective_url, content = await self._request_with_retry('get', login_url, headers=self._common_headers)
        await self._verify_response_headers(status, effective_url, allow_login=True)
        
        tokens = await self._get_form_tokens(content)
        tokens.update({
            'username': self._username,
            'password': self._password,
            'button.Login': 'Login'
        })

        # 2. Submit Login
        auth_url = f"{self._base_url}/accountcenter/logon/authenticate.html"
        _LOGGER.debug("Attempting authentication...")
        status, effective_url, content = await self._request_with_retry(
            'post', 
            auth_url, 
            data=tokens, 
            headers={**self._common_headers, 'Referer': login_url}
        )
        await self._verify_response_headers(status, effective_url, allow_login=True)
        await self._verify_content(content)

        # 3. Visit Landing Page to activate session
        landing_url = f"{self._base_url}/accountcenter/usagehistory/UsageHistoryLanding.html"
        _LOGGER.debug("Initializing usage session...")
        status, effective_url, content = await self._request_with_retry('get', landing_url, headers=self._common_headers)
        await self._verify_response_headers(status, effective_url)
        await self._verify_content(content)

        _LOGGER.debug("Login and session initialization successful")

    async def get_daily_usage(self) -> dict[str, Any]:
        """Fetch and parse daily usage data."""
        await self.login()
        
        url = f"{self._base_url}/accountcenter/usagehistory/dailyUsageDownload.html"
        _LOGGER.debug("Fetching usage data from %s", url)
        
        status, effective_url, content = await self._request_with_retry(
            'get', 
            url, 
            params={"billingPeriod": "Current"},
            headers={**self._common_headers, 'Referer': f"{self._base_url}/accountcenter/usagehistory/dailyUsage.html"}
        )
        await self._verify_response_headers(status, effective_url)
        await self._verify_content(content)
            
        return await self._parse_xls_data(content)
        
    async def _parse_xls_data(self, content: bytes) -> dict[str, Any]:
        """Parse the binary XLS content using pandas."""
        await self._verify_content(content)

        def _parse_impl():
            import pandas as pd
            from io import BytesIO
            
            stripped = content.strip()
            
            try:
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
            
            # Find date column
            date_col = next((col for col in df.columns if 'date' in col), None)
            
            # Clean consumption data
            df['consumption'] = pd.to_numeric(df['consumption'], errors='coerce')
            df = df.dropna(subset=['consumption'])
            
            total_usage = float(df['consumption'].sum())
            latest_usage = float(df['consumption'].iloc[-1]) if not df.empty else 0.0
            latest_date = None
            first_date = None
            if date_col and not df.empty:
                latest_date = str(df[date_col].iloc[-1])
                first_date = str(df[date_col].iloc[0])
                    
            return {
                "total_usage": total_usage,
                "latest_usage": latest_usage,
                "latest_date": latest_date,
                "billing_period_start": first_date,
            }

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _parse_impl)

    async def get_account_data(self) -> dict[str, Any]:
        """Fetch account data including usage."""
        usage_data = await self.get_daily_usage()
        
        return {
            "bill_date": usage_data.get("latest_date"),
            "billing_period_start": usage_data.get("billing_period_start"),
            "due_date": "Unknown",
            "amount_due": None,
            "usage": usage_data.get("total_usage", 0.0),
        }
        
    async def close(self) -> None:
        """Close the session."""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

