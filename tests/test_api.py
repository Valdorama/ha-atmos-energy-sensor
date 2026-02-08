import unittest
from unittest.mock import MagicMock, patch
import os
import sys

# Mock Home Assistant modules before they are imported
from unittest.mock import MagicMock
import sys

# Create a mock homeassistant module
mock_ha = MagicMock()
sys.modules["homeassistant"] = mock_ha
sys.modules["homeassistant.core"] = MagicMock()
sys.modules["homeassistant.config_entries"] = MagicMock()
sys.modules["homeassistant.helpers"] = MagicMock()
sys.modules["homeassistant.helpers.entity_platform"] = MagicMock()
sys.modules["homeassistant.helpers.update_coordinator"] = MagicMock()
sys.modules["homeassistant.const"] = MagicMock()
sys.modules["homeassistant.components"] = MagicMock()
sys.modules["homeassistant.components.sensor"] = MagicMock()

# Add the project root to sys.path to allow imports from custom_components
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from custom_components.atmos_energy.api import AtmosEnergyApiClient
from custom_components.atmos_energy.exceptions import DataParseError, AuthenticationError

class TestAtmosEnergyApi(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.session = MagicMock()
        self.api = AtmosEnergyApiClient("test@example.com", "password", self.session)

    async def test_parse_xls_real_file(self):
        """Test parsing the real usage.xls file provided by the user."""
        file_path = os.path.join(os.path.dirname(__file__), 'data', 'usage.xls')
        with open(file_path, 'rb') as f:
            content = f.read()
        
        # but we can test the internal method directly since it's the logic we care about
        result = await self.api._parse_xls_data(content)
        
        self.assertIn("total_usage", result)
        self.assertIn("latest_usage", result)
        self.assertGreater(result["total_usage"], 0)

    async def test_parse_xls_with_whitespace(self):
        """Test parsing XLS content that has leading whitespace (the reported bug)."""
        file_path = os.path.join(os.path.dirname(__file__), 'data', 'usage.xls')
        with open(file_path, 'rb') as f:
            original_content = f.read()
        
        # Add leading newlines (the error report mentioned b'\r\n\r\n\r\n\r\n')
        corrupt_content = b"\r\n\r\n\r\n\r\n" + original_content
        
        result = await self.api._parse_xls_data(corrupt_content)
        
        self.assertIn("total_usage", result)
        self.assertGreater(result["total_usage"], 0)

    async def test_parse_xls_invalid_content(self):
        """Test that truly invalid content still raises DataParseError."""
        with self.assertRaises(DataParseError):
            await self.api._parse_xls_data(b"not an excel file")


    async def test_verify_content_with_leading_whitespace(self):
        """Test that _verify_content correctly identifies HTML with leading whitespace and inactivity markers."""
        file_path = os.path.join(os.path.dirname(__file__), 'data', 'atmos body.txt')
        with open(file_path, 'rb') as f:
            content = f.read()
        
        with self.assertRaises(AuthenticationError) as cm:
            await self.api._verify_content(content)
        
        # Should be caught by one of the indicators
        self.assertTrue(any(msg in str(cm.exception).lower() for msg in ["session-ended", "login page"]))

    async def test_check_session_with_real_sample(self):
        """Test that check_session returns False when the portal returns the inactivity message."""
        file_path = os.path.join(os.path.dirname(__file__), 'data', 'atmos body.txt')
        with open(file_path, 'rb') as f:
            content = f.read()
        
        # Mock _request_with_retry to return the sample content
        from unittest.mock import AsyncMock
        self.api._request_with_retry = AsyncMock(return_value=(200, "http://example.com", content))
        
        result = await self.api.check_session()
        self.assertFalse(result)

    async def test_verify_content_success_page(self):
        """Test that _verify_content does NOT raise error for a valid success page header/menu."""
        # Success page snippet that previously triggered false positive
        success_content = """
  <header class="noauth">
    <div class="line logoBar">
      <div class="margin">
        <ul class="actions">
           <li class="chgcctBtn"><a href="/accountcenter/logon/login.html?request_locale=es_MX" class="ftHeavy" title="Spanish">Español</a></li>
        </ul>
      </div>
    </div>
  </header>
  <li><a href="/accountcenter/changecustomerinfo/logoninformationtab.html">update username/password</a></li>
""".encode('utf-8')
        # This SHOULD NOT raise AuthenticationError
        await self.api._verify_content(success_content)


if __name__ == '__main__':
    unittest.main()
