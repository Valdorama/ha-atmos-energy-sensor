"""Constants for Atmos Energy."""
from datetime import timedelta

DOMAIN = "atmos_energy"
TIMEOUT = 60
SCAN_INTERVAL = timedelta(hours=24)  # Initial interval; smart scheduling adjusts to 7 AM daily

# Configuration keys
CONF_FIXED_COST = "fixed_cost"
CONF_USAGE_RATE = "usage_rate"
CONF_TAX_PERCENT = "tax_percent"
CONF_WEATHER_ENTITY = "weather_entity"
CONF_OPERATION_MODE = "operation_mode"
CONF_WEATHER_STATION = "weather_station"
CONF_ACCOUNT_NUMBER = "account_number"
CONF_SERVICE_ADDRESS = "service_address"

# Operation Modes
MODE_MONTHLY = "monthly"
MODE_DAILY = "daily"
MODE_DAILY_ADVANCED = "daily_advanced"
CONF_GCR_RATE = "gcr_rate"
CONF_URI_SURCHARGE = "uri_surcharge"
CONF_AUTO_FETCH_GCR = "auto_fetch_gcr"

# Attributes
ATTR_USAGE = "usage"
ATTR_AMOUNT_DUE = "amount_due"
ATTR_DUE_DATE = "due_date"
ATTR_BILL_DATE = "bill_date"
ATTR_BILLING_PERIOD_START = "billing_period_start"
ATTR_METER_READ_DATE = "meter_read_date"
ATTR_AVG_TEMP = "avg_temp"
ATTR_BILLING_MONTH = "billing_month"

# Regression defaults
DEFAULT_BASE_LOAD = 25.03
DEFAULT_HEATING_COEFF = 0.097
DEFAULT_BALANCE_TEMP = 65.0
STORAGE_KEY = f"{DOMAIN}_history"  # Pre-multi-account: one file shared by every entry
STORAGE_VERSION = 1


def history_storage_key(entry_id: str) -> str:
    """Per-entry history store key."""
    return f"{STORAGE_KEY}_{entry_id}"