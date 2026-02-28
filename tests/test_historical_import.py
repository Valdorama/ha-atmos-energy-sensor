"""Tests for Atmos Energy historical XLS import and cost calculations."""
import os
import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timedelta

from homeassistant.util import dt as dt_util
from custom_components.atmos_energy.coordinator import AtmosEnergyDataUpdateCoordinator
from custom_components.atmos_energy.const import DOMAIN

# Path to the tests directory
TESTS_DIR = os.path.dirname(__file__)
DATA_DIR = os.path.join(TESTS_DIR, "data")
TEST_XLS_FILE = os.path.join(DATA_DIR, "jan daily usage.xls")

@pytest.fixture
def mock_coordinator(hass):
    """Fixture for coordinator."""
    client = MagicMock()
    entry = MagicMock()
    entry.entry_id = "test_entry_id"
    entry.data = {}
    
    # Provide options to simulate real cost calculations
    entry.options = {
        "fixed_cost": 25.03,
        "usage_rate": 0.78,
        "gcr_rate": 1.17,
        "uri_surcharge": 0.018431,
        "tax_percent": 8.0
    }
    
    coordinator = AtmosEnergyDataUpdateCoordinator(hass, client, entry)
    coordinator.current_gcr_rate = 1.17
    
    # Mock WNA calculation
    coordinator.data = {"wna_calculated": {"wna_charge": 0.0, "wna_factor": 0.0}}
    
    return coordinator

@pytest.mark.asyncio
async def test_historical_import_xls_generates_cost_and_usage(hass, mock_coordinator):
    """Test that importing a real XLS file generates both usage and cost statistics."""
    
    if not os.path.exists(TEST_XLS_FILE):
        pytest.skip(f"Test data file not found: {TEST_XLS_FILE}")

    # Mock the underlying statistics database calls
    mock_stats = {}
    
    with patch("custom_components.atmos_energy.coordinator.statistics_during_period", return_value=mock_stats), \
         patch("custom_components.atmos_energy.coordinator.get_last_statistics", return_value=mock_stats), \
         patch("custom_components.atmos_energy.coordinator.async_add_external_statistics") as mock_add_stats:
        
        # We need to ensure the config_dir mock is valid for os.path operations inside the coordinator
        hass.config.config_dir = TESTS_DIR
        
        # Use an absolute path so the coordinator doesn't try to prepend config_dir
        await mock_coordinator.async_import_historical_xls(TEST_XLS_FILE)
        
        # Should be called twice: once for USD (costs) and once for usage
        assert mock_add_stats.call_count == 2
        
        # Verify the USAGE statistics (First call)
        usage_args = mock_add_stats.call_args_list[0][0] # (hass, metadata, stats)
        usage_metadata = usage_args[1]
        usage_stats = usage_args[2]
        
        assert usage_metadata.statistic_id == f"{DOMAIN}:usage_test_entry_id"
        assert len(usage_stats) > 0 # Should have parsed some days
        assert usage_stats[-1].sum > 0 # Cumulative sum should be > 0
        
        # Verify the COST statistics (Second call)
        cost_args = mock_add_stats.call_args_list[1][0]
        cost_metadata = cost_args[1]
        cost_stats = cost_args[2]
        
        assert cost_metadata.statistic_id == f"{DOMAIN}:cost_test_entry_id"
        assert cost_metadata.unit_of_measurement == "USD"
        assert len(cost_stats) == len(usage_stats) # Must have 1 cost record per usage record
        assert cost_stats[-1].sum > 0 # Cumulative cost sum should be > 0
        
        # Verify the daily cost ratio (it shouldn't be zero)
        for i in range(len(usage_stats)):
            usage = usage_stats[i].state
            cost = cost_stats[i].state
            
            # If usage is > 0, cost should be > 0
            if usage > 0:
                assert cost > 0
                
                # Let's verify our math works: Fixed cost pro-rated (25.03/30 = 0.83) + variable (~2.0 / CCF) + tax
                # It should be roughly between $1.50 and $2.50 per CCF depending on usage amount
                assert cost > (usage * 1.50)
