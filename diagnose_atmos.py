import asyncio
import logging
import getpass
import sys
import os
from unittest.mock import MagicMock

# Mock Home Assistant modules before ANY other imports
sys.modules["homeassistant"] = MagicMock()
sys.modules["homeassistant.core"] = MagicMock()
sys.modules["homeassistant.config_entries"] = MagicMock()
sys.modules["homeassistant.helpers"] = MagicMock()
sys.modules["homeassistant.helpers.entity_platform"] = MagicMock()
sys.modules["homeassistant.helpers.update_coordinator"] = MagicMock()
sys.modules["homeassistant.const"] = MagicMock()
sys.modules["homeassistant.components"] = MagicMock()
sys.modules["homeassistant.components.sensor"] = MagicMock()

# Import components from the integration
# We need to add the parent directory to sys.path to find custom_components
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), ".")))

from custom_components.atmos_energy.api import AtmosEnergyApiClient
from custom_components.atmos_energy.exceptions import AuthenticationError, APIError

# Setup logging to see what the API client is doing
logging.basicConfig(level=logging.DEBUG, format='%(levelname)s: %(message)s')
_LOGGER = logging.getLogger(__name__)

async def diagnose():
    print("\n=== Atmos Energy Integration Diagnostic Tool ===")
    print("This script will attempt a live login and file download to debug connection issues.")
    
    username = input("\nEnter Atmos Energy Username: ")
    password = getpass.getpass("Enter Atmos Energy Password: ")
    
    if not username or not password:
        print("Error: Username and password are required.")
        return
    
    # Initialize client
    api = AtmosEnergyApiClient(username, password)
    
    try:
        print("\n1. Attempting Login...")
        await api.login()
        print("✓ Login successful.")
        
        print("\n2. Checking Session Validation...")
        is_valid = await api.check_session()
        if is_valid:
            print("✓ Session validated successfully.")
        else:
            print("✗ Session validation failed (but login returned success).")
            
        print("\n3. Attempting Daily Usage Download...")
        # Since we want to SEE the raw headers and content, we will manually perform the request 
        # using the client's session to capture details.
        session = await api._get_session()
        url = f"{api._base_url}/accountcenter/usagehistory/dailyUsageDownload.html"
        params = {"billingPeriod": "Current"}
        headers = {
            'Referer': f"{api._base_url}/accountcenter/usagehistory/dailyUsage.html",
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        }
        
        print(f"Requesting: {url}")
        async with session.get(url, params=params, headers=headers) as response:
            print(f"Response Status: {response.status}")
            print(f"Final URL: {response.url}")
            print("\nResponse Headers:")
            for k, v in response.headers.items():
                print(f"  {k}: {v}")
                
            content = await response.read()
            print(f"\nResponse Body Length: {len(content)} bytes")
            
            # Check for binary vs text
            if content.startswith(b"<!DOCTYP") or content.startswith(b"<html"):
                print("\n!!! WARNING: RECEIVED HTML CONTENT INSTEAD OF BINARY XLS !!!")
                text = content.decode('utf-8', errors='replace')
                print("\nFull Content (first 2000 chars):")
                print("-" * 50)
                print(text[:2000])
                print("-" * 50)
            else:
                print("\n✓ Received binary content (likely XLS).")
                print(f"First 50 bytes (hex): {content[:50].hex(' ')}")

    except AuthenticationError as e:
        print(f"\n✗ Authentication Failed: {e}")
    except APIError as e:
        print(f"\n✗ API Error: {e}")
    except Exception as e:
        print(f"\n✗ Unexpected Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        await api.close()
        print("\nDiagnostic complete.")

if __name__ == "__main__":
    asyncio.run(diagnose())
