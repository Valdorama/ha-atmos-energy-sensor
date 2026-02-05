"""
Verify Login Script for Atmos Energy Integration.
Run this script to test the authentication flow without restarting Home Assistant.

WARNING: This script handles credentials in plain text (via input). 
Do not share screen captures of this script running if they show your password.
Delete this script after verification if you are on a shared system.

Usage:
    python verify_login.py
"""
import sys
import os
import asyncio
import logging
from getpass import getpass

# Add the current directory to sys.path so we can import custom_components
sys.path.append(os.getcwd())

# MOCK homeassistant module to avoid ImportError when __init__.py is executed
from unittest.mock import MagicMock
sys.modules["homeassistant"] = MagicMock()
sys.modules["homeassistant.core"] = MagicMock()
sys.modules["homeassistant.config_entries"] = MagicMock()
sys.modules["homeassistant.const"] = MagicMock()
sys.modules["homeassistant.helpers"] = MagicMock()
sys.modules["homeassistant.helpers.update_coordinator"] = MagicMock()
sys.modules["homeassistant.helpers.entity_platform"] = MagicMock()

from custom_components.atmos_energy.api import AtmosEnergyApiClient

# Configure logging to see what's happening
logging.basicConfig(level=logging.DEBUG)
_LOGGER = logging.getLogger(__name__)

async def main():
    print("Atmos Energy Login Verification")
    print("--------------------------------")
    
    username = input("Username: ")
    password = getpass("Password: ")
    
    print("\nAttempting login...")
    
    client = AtmosEnergyApiClient(username, password)
    
    try:
        await client.login()
        print("\n✅ Login successful!")
        
        print("Fetching and Parsing Account Data...")
        data = await client.get_account_data()
        
        print("\n✅ Result:")
        print(f"  Total Usage (Current Period): {data.get('usage')}")
        print(f"  Latest Reading Date: {data.get('bill_date')}")
        print(f"  Amount Due: {data.get('amount_due')} (Not yet implemented)")
        
    except Exception as e:
        print(f"\n❌ Failed: {e}")
        
    finally:
        await client.close()

if __name__ == "__main__":
    if os.name == 'nt':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
