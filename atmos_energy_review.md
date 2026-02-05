# Atmos Energy Home Assistant Integration - Complete Code Review

## Executive Summary

Your integration is **functional and well-structured** with good separation of concerns. However, there are several **critical issues** that could cause problems in production, particularly around session management, error handling, and data validation.

**Overall Grade: B-** (Functional but needs hardening)

---

## Critical Issues (Must Fix)

### 1. ❌ **Session Leak in `__init__.py`**
**Severity: HIGH - Memory Leak**

**Location:** `__init__.py:24-30`

```python
# CURRENT CODE - PROBLEMATIC
client = AtmosEnergyApiClient(username, password)
coordinator = AtmosEnergyDataUpdateCoordinator(hass, client)
await coordinator.async_config_entry_first_refresh()  # If this fails, session never closed
```

**Problem:** If `async_config_entry_first_refresh()` fails, the aiohttp session is never closed, causing a resource leak.

**Fix:**
```python
async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Atmos Energy from a config entry."""
    
    username = entry.data[CONF_USERNAME]
    password = entry.data[CONF_PASSWORD]

    client = AtmosEnergyApiClient(username, password)
    
    try:
        coordinator = AtmosEnergyDataUpdateCoordinator(hass, client)
        await coordinator.async_config_entry_first_refresh()
    except Exception:
        await client.close()
        raise

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    return True
```

---

### 2. ❌ **Session Leak in `config_flow.py`**
**Severity: HIGH - Memory Leak**

**Location:** `config_flow.py:33-40`

```python
# CURRENT CODE - PROBLEMATIC
client = AtmosEnergyApiClient(username, password)
try:
    await client.login()
    await client.close()
except Exception:
    errors["base"] = "auth_error"  # Session never closed on error path!
```

**Fix:**
```python
client = AtmosEnergyApiClient(username, password)

try:
    await client.login()
except AuthenticationError:
    errors["base"] = "invalid_auth"
except aiohttp.ClientError:
    errors["base"] = "cannot_connect"
except Exception as err:
    _LOGGER.exception("Unexpected error during authentication")
    errors["base"] = "unknown"
finally:
    await client.close()  # ALWAYS close

if not errors:
    return self.async_create_entry(
        title=user_input[CONF_USERNAME], data=user_input
    )
```

---

### 3. ❌ **Generic Exception Handling Masks Real Errors**
**Severity: HIGH - Poor Debugging**

**Multiple Locations:** `api.py`, `coordinator.py`, `config_flow.py`

**Problem:** Using `except Exception` catches programming errors (like `AttributeError`, `KeyError`) that should crash to reveal bugs.

**Fix in `api.py`:**
```python
# Create exceptions.py
class AtmosEnergyException(Exception):
    """Base exception."""

class AuthenticationError(AtmosEnergyException):
    """Authentication failed."""

class APIError(AtmosEnergyException):
    """API request failed."""

class DataParseError(AtmosEnergyException):
    """Failed to parse data."""

# Then in api.py:
async def login(self):
    """Login to Atmos Energy."""
    try:
        # ... login code ...
    except aiohttp.ClientError as err:
        raise APIError(f"Network error during login: {err}") from err
    
    if "Invalid username or password" in text:
        raise AuthenticationError("Invalid username or password")
```

**Fix in `coordinator.py`:**
```python
async def _async_update_data(self):
    """Fetch data from API."""
    try:
        data = await self.client.get_account_data()
        return data
    except AuthenticationError as err:
        raise UpdateFailed(f"Authentication failed: {err}") from err
    except APIError as err:
        raise UpdateFailed(f"API error: {err}") from err
    except aiohttp.ClientError as err:
        raise UpdateFailed(f"Connection error: {err}") from err
    # Let other exceptions propagate to reveal bugs
```

---

### 4. ❌ **No Session Timeout Configuration**
**Severity: MEDIUM - Hangs**

**Location:** `api.py:16-20`

```python
# CURRENT CODE
async def _get_session(self):
    if self._session is None:
        self._session = aiohttp.ClientSession()
    return self._session
```

**Problem:** No timeout means requests can hang indefinitely.

**Fix:**
```python
async def _get_session(self):
    """Get or create the aiohttp session."""
    if self._session is None:
        timeout = aiohttp.ClientTimeout(total=TIMEOUT, connect=10)
        self._session = aiohttp.ClientSession(timeout=timeout)
    return self._session
```

---

### 5. ❌ **XLS Parsing Returns Dict on Error**
**Severity: MEDIUM - Silent Failures**

**Location:** `api.py:102-106`

```python
# CURRENT CODE
except Exception as e:
    _LOGGER.error(f"Failed to open/parse XLS: {e}")
    return {"error": str(e)}  # Inconsistent error handling!
```

**Problem:** Returns a dict instead of raising exception, leading to confusing errors downstream.

**Fix:**
```python
except xlrd.XLRDError as e:
    _LOGGER.error(f"XLS format error: {e}")
    raise DataParseError(f"Invalid XLS format: {e}") from e
except Exception as e:
    _LOGGER.error(f"Unexpected error parsing XLS: {e}")
    raise DataParseError(f"Failed to parse usage data: {e}") from e
```

And update `api.py:158-161`:
```python
async def get_account_data(self):
    """Fetch account data including usage."""
    usage_data = await self.get_daily_usage()
    # Remove the error check since we now raise exceptions
    
    return {
        "bill_date": usage_data.get("latest_date"),
        "due_date": "Unknown",
        "amount_due": None,
        "usage": usage_data.get("total_usage", 0.0)
    }
```

---

### 6. ❌ **Missing Rate Limiting**
**Severity: MEDIUM - Could Get Blocked**

**Location:** `api.py` - entire class

**Problem:** No rate limiting means the integration could hammer Atmos Energy's servers and get your IP blocked.

**Fix:**
```python
from datetime import datetime, timedelta

class AtmosEnergyApiClient:
    def __init__(self, username, password, session=None):
        # ... existing code ...
        self._last_request = None
        self._min_request_interval = timedelta(minutes=5)  # Conservative
    
    async def _rate_limit(self):
        """Enforce rate limiting."""
        if self._last_request:
            elapsed = datetime.now() - self._last_request
            if elapsed < self._min_request_interval:
                wait_time = (self._min_request_interval - elapsed).total_seconds()
                _LOGGER.debug(f"Rate limiting: waiting {wait_time:.1f}s")
                await asyncio.sleep(wait_time)
        self._last_request = datetime.now()
    
    async def get_daily_usage(self):
        """Fetch and parse daily usage data."""
        await self._rate_limit()  # Add this
        await self.login()
        # ... rest of method
```

---

## Important Issues (Should Fix)

### 7. ⚠️ **Hardcoded xlrd Library**
**Severity: MEDIUM - Future Breakage**

**Location:** `api.py:98`

**Problem:** `xlrd` only supports old `.xls` format. If Atmos switches to `.xlsx`, this breaks.

**Fix:** Use pandas for better format support:
```python
async def _parse_xls_data(self, content):
    """Parse the binary XLS content."""
    def _parse_impl():
        import pandas as pd
        from io import BytesIO
        
        try:
            # Try xlsx first (newer format)
            df = pd.read_excel(BytesIO(content), engine='openpyxl')
        except Exception:
            # Fallback to xls (older format)
            try:
                df = pd.read_excel(BytesIO(content), engine='xlrd')
            except Exception as e:
                raise DataParseError(f"Could not read Excel file: {e}") from e
        
        # Normalize column names
        df.columns = df.columns.str.lower().str.strip()
        
        if 'consumption' not in df.columns:
            raise DataParseError(f"Missing 'consumption' column. Found: {list(df.columns)}")
        
        # Find date column
        date_col = next((col for col in df.columns if 'date' in col), None)
        
        # Filter out non-numeric consumption values
        df['consumption'] = pd.to_numeric(df['consumption'], errors='coerce')
        df = df.dropna(subset=['consumption'])
        
        total_usage = float(df['consumption'].sum())
        latest_date = df[date_col].iloc[-1] if date_col and len(df) > 0 else None
        
        return {
            "total_usage": total_usage,
            "latest_date": str(latest_date) if latest_date else None,
            "period": "Current"
        }
    
    import asyncio
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _parse_impl)
```

Update `manifest.json` requirements:
```json
"requirements": ["aiohttp>=3.8.0", "beautifulsoup4>=4.11.0", "pandas>=1.5.0", "openpyxl>=3.0.0"]
```

---

### 8. ⚠️ **Weak Authentication Detection**
**Severity: MEDIUM - Auth Failures**

**Location:** `api.py:77-78`

```python
if "Invalid username or password" in text:
    raise Exception("Invalid username or password")
```

**Problem:** Fragile string matching. What if they change the error message?

**Fix:**
```python
# Check for redirect to login (more reliable)
if response.status == 302:
    location = response.headers.get("Location", "")
    if "login" in location.lower():
        raise AuthenticationError("Login failed - redirected to login page")

# Check response content for multiple error phrases
if response.status == 200:
    text_lower = text.lower()
    error_phrases = [
        "invalid username",
        "invalid password",
        "incorrect username",
        "incorrect password",
        "authentication failed",
        "login failed",
    ]
    if any(phrase in text_lower for phrase in error_phrases):
        raise AuthenticationError("Invalid username or password")
```

---

### 9. ⚠️ **No Retry Logic**
**Severity: MEDIUM - Unreliable**

**Problem:** Transient network failures cause immediate failure. Should retry.

**Fix:**
```python
from asyncio import sleep

async def _request_with_retry(self, method, url, max_retries=3, **kwargs):
    """Make HTTP request with exponential backoff retry."""
    for attempt in range(max_retries):
        try:
            async with method(url, **kwargs) as response:
                if response.status in (200, 302):
                    return response
                elif response.status in (500, 502, 503, 504):
                    # Server error - retry
                    if attempt == max_retries - 1:
                        raise APIError(f"Server error {response.status} after {max_retries} attempts")
                else:
                    # Client error - don't retry
                    raise APIError(f"Request failed with status {response.status}")
                    
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            if attempt == max_retries - 1:
                raise APIError(f"Request failed after {max_retries} attempts: {e}") from e
            
            wait_time = 2 ** attempt  # Exponential backoff: 1s, 2s, 4s
            _LOGGER.warning(f"Request failed (attempt {attempt + 1}/{max_retries}), retrying in {wait_time}s: {e}")
            await sleep(wait_time)
```

---

### 10. ⚠️ **No Input Validation**
**Severity: MEDIUM - Poor UX**

**Location:** `config_flow.py:79-91`

**Problem:** Users could enter negative costs or absurd tax rates.

**Fix:**
```python
from homeassistant.helpers import config_validation as cv

data_schema=vol.Schema({
    vol.Required(
        "fixed_cost",
        default=self._config_entry.options.get("fixed_cost", 25.03)
    ): vol.All(vol.Coerce(float), vol.Range(min=0, max=1000)),
    vol.Required(
        "usage_rate", 
        default=self._config_entry.options.get("usage_rate", 2.40)
    ): vol.All(vol.Coerce(float), vol.Range(min=0, max=100)),
    vol.Required(
        "tax_percent", 
        default=self._config_entry.options.get("tax_percent", 8.0)
    ): vol.All(vol.Coerce(float), vol.Range(min=0, max=100)),
})
```

---

### 11. ⚠️ **Sensor: State Class Incorrect**
**Severity: MEDIUM - Energy Dashboard Issues**

**Location:** `sensor.py:77`

```python
class AtmosEnergyCostSensor(AtmosEnergyBaseSensor):
    _attr_state_class = SensorStateClass.TOTAL  # WRONG
```

**Problem:** Cost should be `TOTAL_INCREASING` for accumulating costs, or `MEASUREMENT` for current bill estimate.

**Fix:**
```python
class AtmosEnergyCostSensor(AtmosEnergyBaseSensor):
    """Representation of an Atmos Energy Cost Sensor."""

    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_state_class = SensorStateClass.MEASUREMENT  # It's an estimate, not accumulating
    _attr_native_unit_of_measurement = "USD"
    _attr_name = "Estimated Cost"
    _attr_icon = "mdi:currency-usd"
```

---

### 12. ⚠️ **No Data Validation in Coordinator**
**Severity: MEDIUM - Bad Data Propagates**

**Location:** `coordinator.py:28`

**Fix:**
```python
async def _async_update_data(self):
    """Fetch data from API."""
    try:
        data = await self.client.get_account_data()
        
        # Validate data structure
        if not isinstance(data, dict):
            raise UpdateFailed("Invalid data format received")
        
        # Validate usage is reasonable
        usage = data.get("usage")
        if usage is None:
            raise UpdateFailed("Missing usage data")
        
        if usage < 0:
            _LOGGER.warning(f"Negative usage value: {usage}, setting to 0")
            data["usage"] = 0
        elif usage > 10000:  # Sanity check - 10k CCF is ~1M cubic feet
            _LOGGER.warning(f"Unusually high usage value: {usage} CCF")
        
        return data
        
    except AuthenticationError as err:
        raise UpdateFailed(f"Authentication failed: {err}") from err
    except APIError as err:
        raise UpdateFailed(f"API error: {err}") from err
    except aiohttp.ClientError as err:
        raise UpdateFailed(f"Connection error: {err}") from err
```

---

## Minor Issues (Nice to Have)

### 13. ℹ️ **const.py: Duplicate Constants**

**Location:** `const.py:7-8`

```python
CONF_USERNAME = "username"
CONF_PASSWORD = "password"
```

**Problem:** These already exist in `homeassistant.const`. Use those instead.

**Fix:**
```python
# Remove these lines, import from homeassistant.const instead
from homeassistant.const import CONF_USERNAME, CONF_PASSWORD
```

---

### 14. ℹ️ **Add Reauthentication Flow**

**Problem:** If password changes, users have to delete and re-add the integration.

**Fix:** Add to `config_flow.py`:
```python
async def async_step_reauth(self, user_input=None):
    """Handle reauth flow."""
    errors = {}
    
    if user_input is not None:
        username = self._get_reauth_entry().data[CONF_USERNAME]
        password = user_input[CONF_PASSWORD]
        
        client = AtmosEnergyApiClient(username, password)
        try:
            await client.login()
        except AuthenticationError:
            errors["base"] = "invalid_auth"
        except Exception as err:
            _LOGGER.exception("Unexpected error during reauth")
            errors["base"] = "unknown"
        finally:
            await client.close()
        
        if not errors:
            self.hass.config_entries.async_update_entry(
                self._get_reauth_entry(),
                data={CONF_USERNAME: username, CONF_PASSWORD: password}
            )
            await self.hass.config_entries.async_reload(self._get_reauth_entry().entry_id)
            return self.async_abort(reason="reauth_successful")
    
    return self.async_show_form(
        step_id="reauth",
        data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}),
        errors=errors,
        description_placeholders={
            "username": self._get_reauth_entry().data[CONF_USERNAME]
        },
    )
```

And trigger it from coordinator when auth fails:
```python
except AuthenticationError:
    self.hass.config_entries.async_schedule_entry_reload(self.config_entry)
    raise UpdateFailed("Authentication failed - please reauthenticate")
```

---

### 15. ℹ️ **Add Diagnostics**

Create `diagnostics.py`:
```python
"""Diagnostics support for Atmos Energy."""
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN

async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict:
    """Return diagnostics for a config entry."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    
    return {
        "entry": {
            "title": entry.title,
            "version": entry.version,
        },
        "data": {
            "username": entry.data.get("username", "")[:3] + "***",
            "last_update_success": coordinator.last_update_success_time.isoformat() 
                if coordinator.last_update_success_time else None,
            "update_interval": str(coordinator.update_interval),
        },
        "coordinator_data": coordinator.data if coordinator.data else {},
        "options": dict(entry.options),
    }
```

---

### 16. ℹ️ **Update SCAN_INTERVAL**

**Location:** `const.py:15`

```python
SCAN_INTERVAL = timedelta(hours=24)
```

**Recommendation:** This is good! Daily updates are appropriate for gas usage. Consider adding a note:

```python
# Update once per day - Atmos updates usage data daily
SCAN_INTERVAL = timedelta(hours=24)
```

---

### 17. ℹ️ **Add manifest.json Validation**

Ensure your `manifest.json` has:
```json
{
  "domain": "atmos_energy",
  "name": "Atmos Energy",
  "codeowners": ["@Valdorama"],
  "config_flow": true,
  "documentation": "https://github.com/Valdorama/ha-atmos-energy-sensor",
  "issue_tracker": "https://github.com/Valdorama/ha-atmos-energy-sensor/issues",
  "requirements": [
    "aiohttp>=3.8.0",
    "beautifulsoup4>=4.11.0",
    "pandas>=1.5.0",
    "openpyxl>=3.0.0"
  ],
  "version": "0.2.1",
  "iot_class": "cloud_polling"
}
```

---

### 18. ℹ️ **Add strings.json**

Create `strings.json` for better i18n:
```json
{
  "config": {
    "step": {
      "user": {
        "title": "Atmos Energy",
        "description": "Enter your Atmos Energy account credentials",
        "data": {
          "username": "Username",
          "password": "Password"
        }
      },
      "reauth": {
        "title": "Reauthenticate Atmos Energy",
        "description": "The credentials for {username} are no longer valid.",
        "data": {
          "password": "Password"
        }
      }
    },
    "error": {
      "invalid_auth": "Invalid username or password",
      "cannot_connect": "Cannot connect to Atmos Energy",
      "unknown": "Unexpected error occurred"
    },
    "abort": {
      "already_configured": "Account already configured",
      "reauth_successful": "Reauthentication successful"
    }
  },
  "options": {
    "step": {
      "init": {
        "title": "Atmos Energy Options",
        "description": "Configure rate calculation",
        "data": {
          "fixed_cost": "Fixed Monthly Cost ($)",
          "usage_rate": "Usage Rate ($/CCF)",
          "tax_percent": "Tax Percentage (%)"
        }
      }
    }
  }
}
```

---

## Code Quality Improvements

### 19. ℹ️ **Add Type Hints**

```python
from typing import Any

class AtmosEnergyApiClient:
    """API Client for Atmos Energy."""

    def __init__(
        self, 
        username: str, 
        password: str, 
        session: aiohttp.ClientSession | None = None
    ) -> None:
        """Initialize the API client."""
        # ...
    
    async def login(self) -> None:
        """Login to Atmos Energy."""
        # ...
    
    async def get_account_data(self) -> dict[str, Any]:
        """Fetch account data including usage."""
        # ...
```

---

### 20. ℹ️ **Add Docstring Details**

```python
async def get_daily_usage(self) -> dict[str, Any]:
    """Fetch and parse daily usage data.
    
    Returns:
        dict: Usage data containing:
            - total_usage (float): Total consumption in CCF
            - latest_date (str|None): Most recent reading date
            - period (str): Billing period (always "Current")
    
    Raises:
        AuthenticationError: If session is invalid
        APIError: If download fails
        DataParseError: If XLS parsing fails
    """
```

---

## Testing Recommendations

### 21. Add Unit Tests

Create `tests/test_api.py`:
```python
"""Tests for Atmos Energy API client."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from custom_components.atmos_energy.api import AtmosEnergyApiClient
from custom_components.atmos_energy.exceptions import AuthenticationError, APIError

@pytest.mark.asyncio
async def test_login_success():
    """Test successful login."""
    client = AtmosEnergyApiClient("test_user", "test_pass")
    
    with patch.object(client, '_get_session') as mock_session:
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.text = AsyncMock(return_value="<html>Success</html>")
        mock_session.return_value.get = AsyncMock(return_value=mock_response)
        mock_session.return_value.post = AsyncMock(return_value=mock_response)
        
        await client.login()
        
        assert mock_session.called

@pytest.mark.asyncio
async def test_login_invalid_credentials():
    """Test login with invalid credentials."""
    client = AtmosEnergyApiClient("bad_user", "bad_pass")
    
    with patch.object(client, '_get_session') as mock_session:
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.text = AsyncMock(return_value="Invalid username or password")
        mock_session.return_value.post = AsyncMock(return_value=mock_response)
        
        with pytest.raises(AuthenticationError):
            await client.login()
```

---

## Security Recommendations

### 22. ℹ️ **Credential Storage**

Good news: Home Assistant stores credentials securely in `config/.storage/`. Just make sure you're not logging them:

```python
# In api.py, make sure you never log credentials
_LOGGER.debug(f"Logging in user: {username[:3]}***")  # Good
# NOT: _LOGGER.debug(f"Logging in with: {username}/{password}")  # BAD!
```

---

## Summary of Priority Fixes

### Must Fix (Do First)
1. ✅ Fix session leaks in `__init__.py` and `config_flow.py`
2. ✅ Add custom exceptions and improve error handling
3. ✅ Add session timeout configuration
4. ✅ Fix XLS error handling to raise exceptions
5. ✅ Add rate limiting

### Should Fix (Do Next)
6. ✅ Switch from xlrd to pandas for Excel parsing
7. ✅ Improve authentication error detection
8. ✅ Add retry logic for network requests
9. ✅ Add input validation in options flow
10. ✅ Fix cost sensor state class
11. ✅ Add data validation in coordinator

### Nice to Have (Future)
12. ✅ Add reauthentication flow
13. ✅ Add diagnostics support
14. ✅ Add comprehensive tests
15. ✅ Improve type hints and documentation

---

## Positive Aspects

Your integration does several things well:
- ✅ Proper use of DataUpdateCoordinator
- ✅ Good separation of concerns (api, coordinator, sensor)
- ✅ Options flow for user customization
- ✅ Device info for proper entity organization
- ✅ Reasonable update interval (24 hours)
- ✅ Session validity checking before re-login
- ✅ Dynamic header parsing for XLS robustness
- ✅ HACS compatibility

---

## Final Recommendations

1. **Fix the critical session leaks first** - these will cause real problems
2. **Add proper exception handling** - will make debugging much easier
3. **Add retry logic and rate limiting** - will make it more reliable
4. **Switch to pandas** - future-proofs against format changes
5. **Add tests** - ensures reliability as you make changes

Would you like me to create a pull request with these fixes, or would you prefer to implement them yourself?
