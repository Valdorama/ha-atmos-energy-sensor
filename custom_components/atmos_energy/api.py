"""API Client for Atmos Energy."""
import logging
import aiohttp
from bs4 import BeautifulSoup
from .const import TIMEOUT

_LOGGER = logging.getLogger(__name__)

class AtmosEnergyApiClient:
    """API Client for Atmos Energy."""

    def __init__(self, username, password, session=None):
        """Initialize the API client."""
        self._username = username
        self._password = password
        self._session = session
        self._base_url = "https://www.atmosenergy.com"

    async def _get_session(self):
        """Get or create the aiohttp session."""
        if self._session is None:
            self._session = aiohttp.ClientSession()
        return self._session

    async def check_session(self):
        """Check if current session is valid by hitting a protected endpoint."""
        # Lightweight check: Usage Summary page
        if self._session is None:
            return False
            
        url = f"{self._base_url}/accountcenter/usagehistory/UsageHistoryLanding.html"
        try:
            async with self._session.get(url, timeout=10, allow_redirects=False) as response:
                # If redirected to login, session is invalid
                if response.status == 302 and "login" in response.headers.get("Location", ""):
                    return False
                return response.status == 200
        except Exception:
            return False

    async def login(self):
        """Login to Atmos Energy."""
        if await self.check_session():
            _LOGGER.debug("Session is still valid, skipping login.")
            return

        session = await self._get_session()
        
        # 1. Get the login page to grab CSRF tokens and the dynamic formId
        login_page_url = f"{self._base_url}/accountcenter/logon/login.html"
        _LOGGER.debug(f"Fetching login page: {login_page_url}")
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
            'Referer': 'https://www.atmosenergy.com/',
        }

        async with session.get(login_page_url, headers=headers, timeout=TIMEOUT) as response:
            if response.status != 200:
                raise Exception(f"Failed to fetch login page: {response.status}")
            text = await response.text()
            
        # Parse for the hidden 'formId'
        soup = BeautifulSoup(text, 'html.parser')
        form_id_input = soup.find('input', {'name': 'formId'})
        
        if not form_id_input:
            _LOGGER.warning("Could not find 'formId'. Attempting login without it.")
            form_id = ""
        else:
            form_id = form_id_input.get('value')

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

        _LOGGER.debug(f"Attempting login POST to {login_action_url}")
        async with session.post(login_action_url, data=payload, headers=headers, timeout=TIMEOUT) as response:
             if response.status not in (200, 302):
                 _LOGGER.error(f"Login failed with status: {response.status}")
                 raise Exception(f"Authentication failed with status code {response.status}")
             
             text = await response.text()
             if "Invalid username or password" in text:
                 raise Exception("Invalid username or password")
             
             _LOGGER.debug("Login POST completed.")

    async def get_daily_usage(self):
        """Fetch and parse daily usage data."""
        # Ensure login
        await self.login()
        
        session = await self._get_session()
        url = f"{self._base_url}/accountcenter/usagehistory/dailyUsageDownload.html"
        params = {"billingPeriod": "Current"}
        
        headers = {
            'Referer': f"{self._base_url}/accountcenter/usagehistory/dailyUsage.html",
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        }
        
        _LOGGER.debug(f"Downloading usage XLS from {url}")
        async with session.get(url, params=params, headers=headers, timeout=TIMEOUT) as response:
            if response.status != 200:
                raise Exception(f"Failed to fetch daily usage XLS: {response.status}")
            content = await response.read()
            
        return await self._parse_xls_data(content)
        
    async def _parse_xls_data(self, content):
        """Parse the binary XLS content."""
        def _parse_impl():
            import xlrd
            try:
                workbook = xlrd.open_workbook(file_contents=content)
                sheet = workbook.sheet_by_index(0)
            except Exception as e:
                _LOGGER.error(f"Failed to open/parse XLS: {e}")
                return {"error": str(e)}
            
            total_usage = 0.0
            latest_date = None
            
            if sheet.nrows < 2:
                return {} # No data
                
            # Dynamic Header Mapping
            headers = [h.lower() for h in sheet.row_values(0)]
            try:
                idx_consumption = headers.index("consumption")
                idx_date = -1
                for i, h in enumerate(headers):
                    if "date" in h: # 'weather date' or just 'date'
                        idx_date = i
                        break
            except ValueError:
                _LOGGER.error("Could not find required columns (Consumption, Date) in XLS headers")
                return {}

            for row_idx in range(1, sheet.nrows):
                row = sheet.row_values(row_idx)
                try:
                    val = row[idx_consumption]
                    consumption = float(val) if val not in (None, '') else 0.0
                    
                    if idx_date != -1:
                        latest_date = row[idx_date]
                    
                    total_usage += consumption
                except ValueError:
                    continue
                    
            return {
                "total_usage": total_usage,
                "latest_date": latest_date,
                "period": "Current"
            }

        import asyncio
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _parse_impl)

    async def get_account_data(self):
        """Fetch account data including usage."""
        # 1. Get Usage Data
        usage_data = await self.get_daily_usage()
        if "error" in usage_data:
             raise Exception(f"Error parsing usage data: {usage_data['error']}")
        
        return {
            "bill_date": usage_data.get("latest_date"),
            "due_date": "Unknown",
            "amount_due": None,
            "usage": usage_data.get("total_usage", 0.0)
        }
        
    async def close(self):
        """Close the session."""
        if self._session:
            await self._session.close()
