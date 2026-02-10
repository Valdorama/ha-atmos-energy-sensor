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
from custom_components.atmos_energy.exceptions import DataParseError, AuthenticationError, APIError

class TestIssueReproduction(unittest.TestCase):
    def setUp(self):
        self.session = MagicMock()
        self.api = AtmosEnergyApiClient("test@example.com", "password", self.session, source="test")

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
        Verification logic now handles this by raising AuthenticationError or APIError.
        """
        html_content = b'<!DOCTYPE html> <html lang="en-US"> <head> <meta http-equiv="Content-Type" content="text/html; charset=utf-8" /> ...'
        
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        try:
            # pd.read_excel will fail if _verify_content doesn't catch it first
            with self.assertRaises((DataParseError, APIError, AuthenticationError)):
                loop.run_until_complete(self.api._parse_xls_data(html_content))
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
            with self.assertRaises(AuthenticationError):
                loop.run_until_complete(self.api._parse_xls_data(html_content))
        finally:
            loop.close()

    @patch('custom_components.atmos_energy.api.AtmosEnergyApiClient._request_with_retry')
    def test_login_follow_redirects_and_landing_page(self, mock_request):
        """
        Test that login follows redirects and visits the usage landing page.
        """
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        try:
            # 1. Mock first GET to login page
            res1 = (200, "https://www.atmosenergy.com/accountcenter/logon/login.html", b'<html><input name="formId" value="token123"></html>')
            
            # 2. Mock POST redirect to account home
            res2 = (200, "https://www.atmosenergy.com/accountcenter/home/accountHome.html", b"<html>Account Home</html>")
            
            # 3. Mock GET landing page
            res3 = (200, "https://www.atmosenergy.com/accountcenter/usagehistory/UsageHistoryLanding.html", b"<html>Usage Landing</html>")
            
            mock_request.side_effect = [res1, res2, res3]
            
            # We must mock check_session to return False initially to trigger login
            with patch.object(self.api, 'check_session', return_value=False):
                loop.run_until_complete(self.api.login())
                
            # Verify 3 calls were made
            self.assertEqual(mock_request.call_count, 3)
            
        finally:
            loop.close()

    @patch('custom_components.atmos_energy.api.AtmosEnergyApiClient._request_with_retry')
    def test_get_daily_usage_error_landing_page(self, mock_request):
        """
        Test that get_daily_usage detects the success/error landing page.
        """
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        try:
            # Mock redirect to successErrorMessage.html
            res = (200, "https://www.atmosenergy.com/accountcenter/successerror/successErrorMessage.html", b"<html>error message</html>")
            mock_request.return_value = res
            
            # Mock login to succeed
            with patch.object(self.api, 'login', return_value=None):
                with self.assertRaises(APIError) as cm:
                    loop.run_until_complete(self.api.get_daily_usage())
                self.assertIn("error page", str(cm.exception))
        finally:
            loop.close()

    def test_parse_monthly_xls_data(self):
        """
        Test parsing the monthly usage XLS file.
        """
        file_path = os.path.join(os.path.dirname(__file__), 'data', 'monthly usage.xls')
        with open(file_path, 'rb') as f:
            content = f.read()

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        try:
            result = loop.run_until_complete(self.api._parse_monthly_xls_data(content))
            
            # Based on the Get-Content output we saw:
            # Charge Date: 01/12/2026
            # Consumption: 62.0
            # Avg Temp: 60
            # Billing Month: Jan 26
            
            self.assertIn('usage', result)
            self.assertEqual(result['usage'], 62.0)
            self.assertIn('charge_date', result)
            self.assertIn('2026-01-12', result['charge_date']) # pd.to_datetime might format it
            self.assertEqual(result['avg_temp'], 60.0)
            self.assertEqual(result['billing_month'], 'Jan 26')
            self.assertIn('meter_read_date', result)
            
        finally:
            loop.close()

if __name__ == '__main__':
    unittest.main()
