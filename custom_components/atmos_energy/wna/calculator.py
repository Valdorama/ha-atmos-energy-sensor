import logging
from typing import Optional

from .ndd_data import get_ndd, WEATHER_STATION_CONFIG

_LOGGER = logging.getLogger(__name__)


class WNACalculator:
    """Calculate Weather Normalization Adjustment using Atmos tariff formula."""
    
    def __init__(self, station_id: str = "austin"):
        """Initialize calculator for a specific weather station.
        
        Args:
            station_id: Weather station ID (austin, dallas, etc.)
        """
        self.station_id = station_id
        
        # Get station configuration
        config = WEATHER_STATION_CONFIG.get(station_id, {})
        
        self.base_load = config.get("base_load_residential", 8.19)
        self.heat_factor = config.get("heat_factor_residential", 0.1394)
        self.commodity_rate = config.get("commodity_rate", 78.025)
        
        _LOGGER.debug(
            "WNA Calculator initialized for %s: BL=%.2f, HSF=%.4f, R=%.3f",
            station_id, self.base_load, self.heat_factor, self.commodity_rate
        )
    
    def calculate_wnaf(self, month: int, projected_add: int) -> float:
        """Calculate WNA factor (WNAF) for a given month and projected ADD.
        
        Formula from Mid-Tex Tariff:
        WNAF = R × (HSF × (NDD - ADD)) / (BL + (HSF × ADD))
        
        Where:
        - R = Commodity rate (cents/CCF)
        - HSF = Heat Sensitive Factor (CCF/HDD)
        - NDD = Normal Degree Days (historical average)
        - ADD = Actual Degree Days (projected)
        - BL = Base Load (CCF)
        
        Args:
            month: Billing month (1-12)
            projected_add: Projected total actual degree days
        
        Returns:
            WNAF in cents per CCF (can be negative for credits)
        """
        # Get NDD for this month and station
        ndd = get_ndd(self.station_id, month)
        
        if ndd == 0:
            # Outside WNA season (May-Oct)
            return 0.0
        
        # Apply formula
        numerator = self.commodity_rate * self.heat_factor * (ndd - projected_add)
        denominator = self.base_load + (self.heat_factor * projected_add)
        
        if denominator == 0:
            _LOGGER.warning("WNA denominator is zero, returning 0")
            return 0.0
        
        wnaf = numerator / denominator
        
        _LOGGER.debug(
            "WNA Calculation: month=%d, NDD=%d, ADD=%d, WNAF=%.6f cents/CCF",
            month, ndd, projected_add, wnaf
        )
        
        return round(wnaf, 8)
    
    def calculate_wna_charge(
        self, 
        month: int, 
        projected_add: int, 
        usage_ccf: float,
        wnaf_cents: Optional[float] = None
    ) -> float:
        """Calculate WNA charge in dollars.
        
        Args:
            month: Billing month (1-12)
            projected_add: Projected actual degree days
            usage_ccf: Gas usage in CCF
            wnaf_cents: Optional pre-calculated WNAF in cents
        
        Returns:
            WNA charge in dollars (negative = credit to customer)
        """
        if wnaf_cents is None:
            wnaf_cents = self.calculate_wnaf(month, projected_add)
        
        # Convert cents to dollars
        wnaf_dollars = wnaf_cents / 100.0
        
        # Multiply by usage
        wna_charge = wnaf_dollars * usage_ccf
        
        return round(wna_charge, 2)
    
    def get_ndd_for_month(self, month: int) -> int:
        """Get NDD value for this station and month."""
        return get_ndd(self.station_id, month)
