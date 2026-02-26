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
@pytest.mark.asyncio
async def test_import_daily_statistics_continuity(hass, mock_coordinator):
    """Test that statistics pick up from previous cumulative sum."""
    data = {
        "history": [
            {"date": "2026-02-12", "usage": 1.5},
        ]
    }
    
    # Mock the database returning a previous sum of 10.0
    mock_stats = {
        f"{DOMAIN}:usage_test_entry_id": [
            MagicMock(get=lambda k, d=None: 10.0 if k == "sum" else d)
        ]
    }
    
    with patch("custom_components.atmos_energy.coordinator.statistics_during_period", return_value=mock_stats), \
         patch("custom_components.atmos_energy.coordinator.async_add_external_statistics") as mock_add_stats:
        
        await mock_coordinator._async_import_daily_statistics(data)
        
        stats = mock_add_stats.call_args[0][2]
        # Previous sum 10.0 + new usage 1.5 = 11.5
        assert stats[0].sum == 11.5

@pytest.mark.asyncio
async def test_import_daily_statistics_float_handling(hass, mock_coordinator):
    """Test that statistics handle float timestamps from the database."""
    # Batch starts today
    now = dt_util.now()
    batch_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    
    data = {
        "history": [
            {"date": batch_start.strftime("%Y-%m-%d"), "usage": 1.5},
        ]
    }
    
    # Mock the database returning a float Unix timestamp for "yesterday"
    yesterday_ts = (batch_start - timedelta(days=1)).timestamp()
    mock_stats = {
        f"{DOMAIN}:usage_test_entry_id": [
            MagicMock(
                get=lambda k, d=None: 10.0 if k == "sum" else (yesterday_ts if k == "start" else d)
            )
        ]
    }
    
    with patch("custom_components.atmos_energy.coordinator.statistics_during_period", return_value=mock_stats), \
         patch("custom_components.atmos_energy.coordinator.async_add_external_statistics") as mock_add_stats:
        
        await mock_coordinator._async_import_daily_statistics(data)
        
        stats = mock_add_stats.call_args[0][2]
        # Should successfully compare float to datetime and find predecessor
        assert stats[0].sum == 11.5

@pytest.mark.asyncio
async def test_import_daily_statistics_strict_filtering(hass, mock_coordinator):
    """Test that statistics strictly ignore records from the current batch."""
    # Batch starts today
    now = dt_util.now()
    batch_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    batch_start_utc = dt_util.as_utc(batch_start)
    
    data = {
        "history": [
            {"date": batch_start.strftime("%Y-%m-%d"), "usage": 1.5},
        ]
    }
    
    # Mock the database returning a record that is AT the batch start
    # (Simulates a previous failed/partial import for the same day)
    mock_stats = {
        f"{DOMAIN}:usage_test_entry_id": [
            MagicMock(
                get=lambda k, d=None: 500.0 if k == "sum" else (batch_start_utc if k == "start" else d)
            )
        ]
    }
    
    with patch("custom_components.atmos_energy.coordinator.statistics_during_period", return_value=mock_stats), \
         patch("custom_components.atmos_energy.coordinator.async_add_external_statistics") as mock_add_stats:
        
        await mock_coordinator._async_import_daily_statistics(data)
        
        stats = mock_add_stats.call_args[0][2]
        # Should IGNORE the 500.0 sum because it's not strictly BEFORE the batch
        # So it starts fresh at 0.0 + 1.5 = 1.5
        assert stats[0].sum == 1.5
