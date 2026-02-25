"""Tests for Atmos Energy statistics import logic."""
import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime
from custom_components.atmos_energy.coordinator import AtmosEnergyDataUpdateCoordinator
from custom_components.atmos_energy.const import DOMAIN

@pytest.fixture
def mock_coordinator(hass):
    """Fixture for coordinator."""
    client = MagicMock()
    entry = MagicMock()
    entry.entry_id = "test_entry_id"
    entry.data = {}
    entry.options = {}
    
    coordinator = AtmosEnergyDataUpdateCoordinator(hass, client, entry)
    return coordinator

@pytest.mark.asyncio
async def test_import_daily_statistics_success(hass, mock_coordinator):
    """Test successful import of daily statistics with cumulative sum."""
    data = {
        "history": [
            {"date": "2026-02-11", "usage": 1.0},
            {"date": "2026-02-12", "usage": 1.5},
            {"date": "02/13/2026", "usage": 2.0}, # US format
        ]
    }
    
    with patch("custom_components.atmos_energy.coordinator.async_add_external_statistics") as mock_add_stats:
        await mock_coordinator._async_import_daily_statistics(data)
        
        # Check if async_add_external_statistics was called (without await!)
        mock_add_stats.assert_called_once()
        
        # Verify metadata
        metadata = mock_add_stats.call_args[0][1]
        assert metadata.statistic_id == f"{DOMAIN}:usage_test_entry_id"
        assert metadata.has_sum is True
        assert metadata.mean_type is None
        
        # Verify statistics records
        stats = mock_add_stats.call_args[0][2]
        assert len(stats) == 3
        
        # Check cumulative sums: 1.0, 2.5, 4.5
        assert stats[0].state == 1.0
        assert stats[0].sum == 1.0
        assert stats[1].state == 1.5
        assert stats[1].sum == 2.5
        assert stats[2].state == 2.0
        assert stats[2].sum == 4.5
        
        # Check timestamps (start of day)
        assert stats[0].start.hour == 0
        assert stats[0].start.minute == 0

@pytest.mark.asyncio
async def test_import_daily_statistics_sorting(hass, mock_coordinator):
    """Test that statistics are sorted by date before calculating sums."""
    data = {
        "history": [
            {"date": "2026-02-13", "usage": 2.0},
            {"date": "2026-02-11", "usage": 1.0},
            {"date": "2026-02-12", "usage": 1.5},
        ]
    }
    
    with patch("custom_components.atmos_energy.coordinator.async_add_external_statistics") as mock_add_stats:
        await mock_coordinator._async_import_daily_statistics(data)
        
        stats = mock_add_stats.call_args[0][2]
        # Should be sorted: Feb 11 (1.0), Feb 12 (2.5), Feb 13 (4.5)
        assert stats[0].sum == 1.0
        assert stats[1].sum == 2.5
        assert stats[2].sum == 4.5
