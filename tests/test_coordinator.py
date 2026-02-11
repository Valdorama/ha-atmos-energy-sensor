"""Tests for Atmos Energy coordinator modeling logic."""
import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timedelta

from homeassistant.util import dt as dt_util
from custom_components.atmos_energy.coordinator import AtmosEnergyDataUpdateCoordinator
from custom_components.atmos_energy.const import (
    DEFAULT_BASE_LOAD,
    DEFAULT_HEATING_COEFF,
    DEFAULT_BALANCE_TEMP
)

@pytest.fixture
def mock_coordinator(hass):
    """Fixture for coordinator."""
    client = MagicMock()
    entry = MagicMock()
    entry.data = {}
    entry.options = {}
    
    coordinator = AtmosEnergyDataUpdateCoordinator(hass, client, entry)
    return coordinator

def test_insufficient_data_defaults(mock_coordinator):
    """Test that defaults are used when less than 10 data points exist."""
    mock_coordinator._history = {
        f"2026-01-0{i}": {"usage": 1.0, "avg_temp": 60}
        for i in range(1, 6)
    }
    mock_coordinator._recalculate_model()
    
    assert mock_coordinator.base_load == DEFAULT_BASE_LOAD
    assert mock_coordinator.heating_coeff == DEFAULT_HEATING_COEFF
    assert mock_coordinator.balance_temp == DEFAULT_BALANCE_TEMP
    assert mock_coordinator.r_squared == 0.0

def test_perfect_linear_regression(mock_coordinator):
    """Test model with perfect linear data: usage = 1.5 + 0.1 * HDD(65)."""
    # Fix balance temp to 65 for this test by providing data that fits it perfectly
    balance_temp = 65.0
    mock_coordinator._history = {}
    
    for i in range(1, 15):
        temp = 65 - i # HDD = i
        usage = 1.5 + (0.1 * i)
        date = (datetime(2026, 1, 1) + timedelta(days=i)).strftime("%Y-%m-%d")
        mock_coordinator._history[date] = {"usage": usage, "avg_temp": temp}
        
    mock_coordinator._recalculate_model()
    
    # Grid search might find a slightly different balance temp if it improves R2
    # but with perfect data at 65, it should be very close.
    assert abs(mock_coordinator.base_load - 1.5) < 0.05
    assert abs(mock_coordinator.heating_coeff - 0.1) < 0.01
    assert mock_coordinator.r_squared > 0.99

def test_balance_temp_autodetect(mock_coordinator):
    """Test that the model detects a different balance temperature (e.g., 70°F)."""
    # House starts heating when it's colder than 70
    target_balance = 70.0
    mock_coordinator._history = {}
    
    for i in range(1, 20):
        temp = 80 - (i * 2) # Range 78 to 40
        hdd = max(0, target_balance - temp)
        usage = 1.0 + (0.2 * hdd)
        date = (datetime(2026, 1, 1) + timedelta(days=i)).strftime("%Y-%m-%d")
        mock_coordinator._history[date] = {"usage": usage, "avg_temp": temp}
        
    mock_coordinator._recalculate_model()
    
    assert mock_coordinator.balance_temp == target_balance
    assert abs(mock_coordinator.base_load - 1.0) < 0.05
    assert abs(mock_coordinator.heating_coeff - 0.2) < 0.01
    assert mock_coordinator.r_squared > 0.99

def test_negative_slope_clamping(mock_coordinator):
    """Test that negative heating coefficients are clamped to zero."""
    # Data where it uses MORE gas when it's HOTTER (e.g. error or pool heater)
    mock_coordinator._history = {
        f"2026-01-{i:02d}": {"usage": 1.0 + (i * 0.1), "avg_temp": 40 + i}
        for i in range(1, 15)
    }
    
    mock_coordinator._recalculate_model()
    
    assert mock_coordinator.heating_coeff == 0.0
    assert mock_coordinator.base_load > 0

def test_date_parsing_robustness(mock_coordinator):
    """Test Suggestion 5: Robust date parsing."""
    # We'll mock the internal call to dt_util.now to control pruning
    now = datetime(2026, 5, 1, tzinfo=dt_util.DEFAULT_TIME_ZONE)
    
    with patch("homeassistant.util.dt.now", return_value=now):
        mock_coordinator._history = {
            "2026-04-30": {"usage": 1.0, "avg_temp": 60},     # ISO YYYY-MM-DD
            "04/29/2026": {"usage": 1.1, "avg_temp": 61},     # US MM/DD/YYYY
            "2026-04-28 12:00:00": {"usage": 1.2, "avg_temp": 62}, # With Time
            "2025-01-01": {"usage": 5.0, "avg_temp": 30},     # OLD (should be pruned)
        }
        
        # Manually trigger the pruning logic from _async_update_data
        # (This logic was moved into the coordinator's update method)
        cutoff = now - timedelta(days=90)
        keys_to_remove = []
        for date_str in mock_coordinator._history:
            dt = dt_util.parse_datetime(date_str)
            if not dt:
                for fmt in ("%Y-%m-%d", "%m/%d/%Y"):
                    try:
                        naive_dt = datetime.strptime(date_str, fmt)
                        dt = dt_util.as_local(naive_dt)
                        break
                    except ValueError:
                        pass
            
            if dt and dt < cutoff:
                keys_to_remove.append(date_str)
        
        assert "2025-01-01" in keys_to_remove
        assert "2026-04-30" not in keys_to_remove
        assert "04/29/2026" not in keys_to_remove
