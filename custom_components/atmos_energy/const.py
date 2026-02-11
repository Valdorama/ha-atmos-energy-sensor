"""Constants for Atmos Energy."""
from datetime import timedelta

DOMAIN = "atmos_energy"
TIMEOUT = 60
SCAN_INTERVAL = timedelta(hours=24)  # Initial interval; smart scheduling adjusts to 7 AM daily

CONF_FIXED_COST = "fixed_cost"
CONF_USAGE_RATE = "usage_rate"
CONF_TAX_PERCENT = "tax_percent"
CONF_WEATHER_ENTITY = "weather_entity"
CONF_DAILY_USAGE = "daily_usage"

ATTR_USAGE = "usage"
ATTR_AMOUNT_DUE = "amount_due"
ATTR_DUE_DATE = "due_date"
ATTR_BILL_DATE = "bill_date"
ATTR_BILLING_PERIOD_START = "billing_period_start"
ATTR_METER_READ_DATE = "meter_read_date"
ATTR_AVG_TEMP = "avg_temp"
ATTR_BILLING_MONTH = "billing_month"

# Regression defaults
DEFAULT_BASE_LOAD = 1.23
DEFAULT_HEATING_COEFF = 0.097
DEFAULT_BALANCE_TEMP = 65.0
STORAGE_KEY = f"{DOMAIN}_history"
STORAGE_VERSION = 1