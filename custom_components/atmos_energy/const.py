"""Constants for the Atmos Energy integration."""
from datetime import timedelta
from homeassistant.const import CONF_USERNAME, CONF_PASSWORD

DOMAIN = "atmos_energy"

# Configuration Keys
CONF_FIXED_COST = "fixed_cost"
CONF_USAGE_RATE = "usage_rate" # $/CCF
CONF_TAX_PERCENT = "tax_percent"
CONF_WEATHER_ENTITY = "weather_entity"

# Defaults
DEFAULT_NAME = "Atmos Energy"
# Update once per day - Atmos updates usage data daily
SCAN_INTERVAL = timedelta(hours=24)
TIMEOUT = 60
DEFAULT_FIXED_COST = 25.03
DEFAULT_USAGE_RATE = 2.40 # Approximate from user data
DEFAULT_TAX_PERCENT = 8.0

# Attributes
ATTR_BILL_DATE = "bill_date"
ATTR_BILLING_PERIOD_START = "billing_period_start"
ATTR_DUE_DATE = "due_date"
ATTR_AMOUNT_DUE = "amount_due"
ATTR_USAGE = "usage"
ATTR_DAYS_REMAINING = "days_remaining"
