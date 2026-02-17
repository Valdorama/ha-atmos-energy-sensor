"""Historical Normal Degree Days (NDD) for Mid-Tex weather stations.

Data source: Atmos Energy WNA Report (June 2025)
Based on: 10-year rolling average (2015-2024)
Update frequency: Annually (June)
"""

# NDD values by weather station and month
# Format: {station_id: {month: ndd}}
MID_TEX_NDD = {
    "austin": {
        11: 206,  # November
        12: 425,  # December
        1: 493,   # January
        2: 315,   # February
        3: 179,   # March
        4: 44,    # April
        # May-Oct: No WNA season
    },
    "dallas": {
        11: 311,
        12: 595,
        1: 699,
        2: 484,
        3: 289,
        4: 84,
    },
    "waco": {
        11: 221,
        12: 448,
        1: 531,
        2: 355,
        3: 194,
        4: 49,
    },
    "abilene": {
        11: 289,
        12: 579,
        1: 610,
        2: 439,
        3: 245,
        4: 79,
    },
    "wichita_falls": {
        11: 358,
        12: 671,
        1: 690,
        2: 515,
        3: 296,
        4: 106,
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
