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
from custom_components.atmos_energy.exceptions import DataParseError

class TestAtmosEnergyApi(unittest.TestCase):
    def setUp(self):
        self.session = MagicMock()
        self.api = AtmosEnergyApiClient("test@example.com", "password", self.session)

    def test_parse_xls_real_file(self):
        """Test parsing the real usage.xls file provided by the user."""
        file_path = os.path.join(os.path.dirname(__file__), 'data', 'usage.xls')
        with open(file_path, 'rb') as f:
            content = f.read()
        
        # This will call the internal _parse_xls_data through an async wrapper 
        # but we can test the internal method directly since it's the logic we care about
        import asyncio
        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(self.api._parse_xls_data(content))
        
        self.assertIn("total_usage", result)
        self.assertIn("latest_usage", result)
        self.assertGreater(result["total_usage"], 0)

    def test_parse_xls_with_whitespace(self):
        """Test parsing XLS content that has leading whitespace (the reported bug)."""
        file_path = os.path.join(os.path.dirname(__file__), 'data', 'usage.xls')
        with open(file_path, 'rb') as f:
            original_content = f.read()
        
        # Add leading newlines (the error report mentioned b'\r\n\r\n\r\n\r\n')
        corrupt_content = b"\r\n\r\n\r\n\r\n" + original_content
        
        import asyncio
        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(self.api._parse_xls_data(corrupt_content))
        
        self.assertIn("total_usage", result)
        self.assertGreater(result["total_usage"], 0)

    def test_parse_xls_invalid_content(self):
        """Test that truly invalid content still raises DataParseError."""
        import asyncio
        loop = asyncio.get_event_loop()
        with self.assertRaises(DataParseError):
            loop.run_until_complete(self.api._parse_xls_data(b"not an excel file"))

if __name__ == '__main__':
    unittest.main()
