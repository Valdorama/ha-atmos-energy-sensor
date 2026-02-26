"""Tests for Atmos Energy state persistence and startup optimization."""
import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from datetime import datetime, timedelta
from homeassistant.util import dt as dt_util
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
async def test_persistence_save_load(hass, mock_coordinator):
    """Test that data and last_update are correctly persisted and restored."""
    # 1. Setup sample data
    now = dt_util.now()
    mock_coordinator.last_update = now
    mock_coordinator.data = {"usage": 15.5, "wna_calculated": {"wna_charge": 1.25}}
    mock_coordinator._history = {"2026-02-25": {"usage": 1.5}}
    mock_coordinator._unsaved_keys.add("2026-02-25")

    # Mock Store.async_save and Store.async_load
    saved_payload = {}
    async def mock_save(payload):
        nonlocal saved_payload
        saved_payload = payload

    with patch.object(mock_coordinator._store, "async_save", side_effect=mock_save), \
         patch.object(mock_coordinator._store, "async_load", return_value=None):
        
        # 2. Save
        await mock_coordinator._async_save_history()
        
        # Verify saved payload contents
        assert saved_payload["last_update"] == now.isoformat()
        assert saved_payload["data"]["usage"] == 15.5
        assert saved_payload["history"]["2026-02-25"]["usage"] == 1.5

        # 3. Load into a fresh coordinator
        new_coordinator = AtmosEnergyDataUpdateCoordinator(hass, mock_coordinator.client, mock_coordinator.config_entry)
        with patch.object(new_coordinator._store, "async_load", return_value=saved_payload):
            await new_coordinator._async_load_history()
            
            # Verify restoration
            assert new_coordinator.last_update == now
            assert new_coordinator.data["usage"] == 15.5
            assert new_coordinator._history["2026-02-25"]["usage"] == 1.5

@pytest.mark.asyncio
async def test_startup_refresh_skip(hass, mock_coordinator):
    """Test that startup refresh is skipped if data is recent."""
    # Set last update to 8 AM today (7 AM window passed)
    now = dt_util.now().replace(hour=8, minute=0, second=0, microsecond=0)
    mock_coordinator.last_update = now
    
    with patch("homeassistant.util.dt.now", return_value=now), \
         patch.object(mock_coordinator, "async_refresh", new_callable=AsyncMock) as mock_refresh:
        
        await mock_coordinator.async_setup_refresh()
        
        # Should NOT trigger refresh because 8 AM > 7 AM reference
        mock_refresh.assert_not_called()

@pytest.mark.asyncio
async def test_startup_refresh_trigger(hass, mock_coordinator):
    """Test that startup refresh triggers if data is old."""
    # Set last update to 6 AM today (7 AM window not reached yet, so reference is 7 AM yesterday)
    # But if we make it 6 AM yesterday, it's definitely old.
    now = dt_util.now().replace(hour=8, minute=0, second=0, microsecond=0)
    mock_coordinator.last_update = now - timedelta(days=2)
    
    with patch("homeassistant.util.dt.now", return_value=now), \
         patch.object(mock_coordinator, "async_refresh", new_callable=AsyncMock) as mock_refresh:
        
        await mock_coordinator.async_setup_refresh()
        
        # Should trigger refresh
        mock_refresh.assert_called_once()
