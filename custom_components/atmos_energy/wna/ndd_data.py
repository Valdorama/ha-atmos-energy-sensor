"""Historical Normal Degree Days (NDD) for Mid-Tex weather stations.

Data source: Atmos Energy WNA Report (June 2026)
Based on: 10-year rolling average (2016-2025)
Update frequency: Annually (June)
"""

# NDD values by weather station and month
# Format: {station_id: {month: ndd}}
MID_TEX_NDD = {
    "austin": {
        11: 208,  # November
        12: 429,  # December
        1: 498,   # January
        2: 318,   # February
        3: 181,   # March
        4: 44,    # April
        # May-Oct: No WNA season
    },
    "dallas": {
        11: 314,
        12: 601,
        1: 706,
        2: 489,
        3: 292,
        4: 85,
    },
    "waco": {
        11: 223,
        12: 452,
        1: 536,
        2: 359,
        3: 196,
        4: 49,
    },
    "abilene": {
        11: 292,
        12: 585,
        1: 616,
        2: 443,
        3: 247,
        4: 80,
    },
    "wichita_falls": {
        11: 362,
        12: 678,
        1: 697,
        2: 520,
        3: 299,
        4: 107,
    },
}

def get_ndd(station_id: str, month: int) -> int:
    """Get NDD for a specific station and month.
    
    Args:
        station_id: Weather station ID (austin, dallas, etc.)
        month: Month number (1-12)
    
    Returns:
        NDD value, or 0 if outside WNA season
    """
    station_data = MID_TEX_NDD.get(station_id, {})
    return station_data.get(month, 0)


# Weather Station Configuration
# Source: Mid-Tex Tariff (October 1, 2025)
WEATHER_STATION_CONFIG = {
    "austin": {
        "name": "Austin",
        "base_load_residential": 8.19,      # CCF
        "heat_factor_residential": 0.1394,  # CCF/HDD
        "commodity_rate": 78.025,           # cents/CCF
    },
    "dallas": {
        "name": "Dallas",
        "base_load_residential": 12.74,
        "heat_factor_residential": 0.2017,
        "commodity_rate": 78.025,
    },
    "waco": {
        "name": "Waco",
        "base_load_residential": 9.23,
        "heat_factor_residential": 0.1277,
        "commodity_rate": 78.025,
    },
    "abilene": {
        "name": "Abilene",
        "base_load_residential": 9.61,
        "heat_factor_residential": 0.1476,
        "commodity_rate": 78.025,
    },
    "wichita_falls": {
        "name": "Wichita Falls",
        "base_load_residential": 10.43,
        "heat_factor_residential": 0.1387,
        "commodity_rate": 78.025,
    },
}


# Update instructions for maintainers
UPDATE_INSTRUCTIONS = """
NDD Data Update Process
=======================

When: Annually in June (when Atmos publishes WNA report)
Source: https://www.atmosenergy.com/MTXtariffs

Steps:
1. Download "Mid-Tex_WNA_Report_Data_{year}.xlsx"
2. Open sheet "Monthly WNAF - ATM" (Austin) or corresponding sheet
3. Find each weather station section
4. Extract NDD values from column (usually column 4)
5. Update MID_TEX_NDD dictionary above
6. Bump integration version
7. Create release notes mentioning NDD update
8. Submit PR

Note: NDD typically changes <5% year-over-year due to rolling 10-year average
"""
