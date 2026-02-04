"""Constants for the Atmos Energy integration."""
from datetime import timedelta

DOMAIN = "atmos_energy"

# Configuration Keys
CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_FIXED_COST = "fixed_cost"
CONF_USAGE_RATE = "usage_rate" # $/CCF
CONF_TAX_PERCENT = "tax_percent"

# Defaults
DEFAULT_NAME = "Atmos Energy"
SCAN_INTERVAL = timedelta(hours=24)
TIMEOUT = 60
DEFAULT_FIXED_COST = 25.03
DEFAULT_USAGE_RATE = 2.40 # Approximate from user data
DEFAULT_TAX_PERCENT = 8.0

# Attributes
ATTR_BILL_DATE = "bill_date"
ATTR_DUE_DATE = "due_date"
ATTR_AMOUNT_DUE = "amount_due"
ATTR_USAGE = "usage"
