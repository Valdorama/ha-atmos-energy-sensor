import unittest
from unittest.mock import MagicMock, patch
import os
import sys
import asyncio

# Mock Home Assistant modules
from unittest.mock import MagicMock
sys.modules["homeassistant"] = MagicMock()
sys.modules["homeassistant.core"] = MagicMock()
sys.modules["homeassistant.config_entries"] = MagicMock()
sys.modules["homeassistant.helpers"] = MagicMock()
sys.modules["homeassistant.helpers.entity_platform"] = MagicMock()
sys.modules["homeassistant.helpers.update_coordinator"] = MagicMock()
sys.modules["homeassistant.const"] = MagicMock()
sys.modules["homeassistant.components"] = MagicMock()
sys.modules["homeassistant.components.sensor"] = MagicMock()

# Add the project root to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from custom_components.atmos_energy.api import AtmosEnergyApiClient
from custom_components.atmos_energy.exceptions import DataParseError, AuthenticationError

class TestIssueReproduction(unittest.TestCase):
    def setUp(self):
        self.session = MagicMock()
        self.api = AtmosEnergyApiClient("test@example.com", "password", self.session)

    async def _mock_response(self, content, url, status=200):
        from unittest.mock import AsyncMock
        mock_resp = MagicMock()
        mock_resp.read = AsyncMock(return_value=content)
        mock_resp.text = AsyncMock(return_value=content.decode('utf-8', errors='replace'))
        mock_resp.url = url
        mock_resp.status = status
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=None)
        return mock_resp

    def test_reproduce_html_as_xls_error(self):
        """
        Reproduce the issue where receiving a login/error HTML page causes a parsing error.
        We now expect it to enter the HTML block and raise DataParseError because no tables are found.
        """
        html_content = b'<!DOCTYPE html> <html lang="en-US"> <head> <meta http-equiv="Content-Type" content="text/html; charset=utf-8" /> ...'
        
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        try:
            with self.assertRaises(DataParseError) as cm:
                loop.run_until_complete(self.api._parse_xls_data(html_content))
            
            error_msg = str(cm.exception)
            # It should say "no tables found" if it correctly identified it as HTML
            self.assertIn("no tables found", error_msg.lower())
            print(f"\nCaught HTML but failed to find tables: {error_msg}")
            
        finally:
            loop.close()

    def test_detect_login_page_in_html(self):
        """
        Test that we can detect a login page within the HTML and raise AuthenticationError.
        """
        html_content = b'<!DOCTYPE html> <html> <body> <h1>Login</h1> <form action="authenticate.html"> </form> </body> </html>'
        
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        try:
            # TDD: We now expect AuthenticationError
            with self.assertRaises(AuthenticationError):
                loop.run_until_complete(self.api._parse_xls_data(html_content))
        finally:
            loop.close()

    @patch('custom_components.atmos_energy.api.AtmosEnergyApiClient._request_with_retry')
    def test_get_daily_usage_redirect_detection(self, mock_request):
        """
        Test that get_daily_usage can detect when a request is redirected to a login page.
        """
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        try:
            # Mock redirect to login.html
            mock_resp = loop.run_until_complete(self._mock_response(
                b"<html>login page</html>", 
                "https://www.atmosenergy.com/accountcenter/logon/login.html"
            ))
            mock_request.return_value = mock_resp
            
            # Mock login to succeed so we can test the usage call
            with patch.object(self.api, 'login', return_value=None):
                # TDD: We now expect AuthenticationError
                with self.assertRaises(AuthenticationError):
                    loop.run_until_complete(self.api.get_daily_usage())
        finally:
            loop.close()

if __name__ == '__main__':
    unittest.main()
