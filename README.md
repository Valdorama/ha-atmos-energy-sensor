# Atmos Energy Home Assistant Integration

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![Latest Release](https://img.shields.io/github/v/release/Valdorama/ha-atmos-energy-sensor?color=blue)](https://github.com/Valdorama/ha-atmos-energy-sensor/releases)
[![License](https://img.shields.io/github/license/Valdorama/ha-atmos-energy-sensor)](https://github.com/Valdorama/ha-atmos-energy-sensor/blob/master/LICENSE)
[![Buy Me A Coffee](https://img.shields.io/badge/buy%20me%20a%20coffee-donate-yellow.svg)](https://buymeacoffee.com/valdorama)

A custom component for Home Assistant to retrieve usage data from [Atmos Energy](https://www.atmosenergy.com/).

**Disclaimer**: This is an unofficial integration and is not affiliated with Atmos Energy. It scrapes the website to retrieve data, so changes to the Atmos Energy website may break this integration.

## ✨ Features
- **Usage Tracking**: Monitor your gas consumption for the current billing period (Daily accounts) or previous cycle (Monthly accounts).
- **Advanced Cost Prediction (Mid-Tex Only)**: High-accuracy bill forecasting using Weather Normalization Adjustment (WNA) and automated Gas Cost Recovery (GCR) rate fetching.
- **Smart Predictions**: 7-day gas usage and cost forecasts driven by local weather data and a personalized heating model.
- **Energy Dashboard Ready**: Fully compatible with the Home Assistant Energy Dashboard for long-term tracking.
- **Automated Modeling**: Automatically learns your home's heating efficiency by analyzing historical usage and temperature data.

## 📊 Available Sensors

This integration provides different sensors depending on your account type (Daily vs Monthly).

### Daily Usage Mode (Standard)
*Enabled by checking "Enable advanced cost prediction" during setup.*

| Sensor | Description | Class |
| :--- | :--- | :--- |
| **Gas usage (Current)** | Total usage (CCF) for the current billing period. | `total_increasing` |
| **Estimated cost** | Calculated cost for the current period (including WNA, GCR, tax, and fixed fees). | `total` |
| **Days remaining** | Precise days left in your billing cycle (parsed from the Atmos portal). | `measurement` |
| **Predicted Usage (7d)** | Estimated gas usage for the next 7 days based on weather forecast. | `total` |
| **Predicted Cost (7d)** | Estimated gas cost for the next 7 days based on weather forecast. | `total` |

### Monthly Usage Mode
*Used if your account does not provide granular daily data.*

| Sensor | Description | Class |
| :--- | :--- | :--- |
| **Gas Usage (Previous)** | Total usage (CCF) from the last completed billing cycle. | `measurement` |

---

## ❄️ Advanced Prediction (Mid-Tex Region)

Billing for natural gas is complex. In the **Mid-Tex region** (Austin, Dallas, Waco, etc.), Atmos Energy applies a **Weather Normalization Adjustment (WNA)** to level out the impact of unusually warm or cold weather.

### How v0.7.0 Improves Accuracy
This integration now delivers ~95% cost accuracy for Mid-Tex customers by:
1. **Automated GCR Rates**: Automatically fetches the latest monthly Gas Cost Recovery (GCR) rates from Atmos Energy's official filings.
2. **Real-time WNA Calculation**: Uses the official Atmos tariff formula combined with your local weather forecast to estimate your bill's weather adjustment before it arrives.
3. **Precise Cycle Tracking**: Parses your actual "Next Meter Read Date" from the portal to accurately project usage for the remainder of the month.

**Note**: Due to significant differences in billing formulas and regional tariffs, advanced prediction is currently only available for the **Mid-Tex region**. Customers in other regions will still see usage data and basic cost estimates.

---

## 🚀 Installation

### Option 1: HACS (Recommended)
1. Ensure [HACS](https://hacs.xyz/) is installed.
2. Go to **HACS > Integrations**.
3. Click the **three dots** in the top right corner and select **Custom repositories**.
4. Add the URL: `https://github.com/Valdorama/ha-atmos-energy-sensor`.
5. Select **Integration** as the category and click **Add**.
6. Install and restart Home Assistant.

### Option 2: Manual
1. Copy `custom_components/atmos_energy` to your `config/custom_components/` directory.
2. Restart Home Assistant.

---

## ⚙️ Configuration

1. Go to **Settings > Devices & Services** > **Add Integration** > **Atmos Energy**.
2. Enter your **Username** and **Password**.
3. **Enable Advanced Cost Prediction**: Select this if you are a Mid-Tex customer and want high-accuracy billing forecasts.

### Configuration Wizard
If advanced prediction is enabled, you will be guided through:
*   **Weather Station**: Select the station Atmos uses for your billing area (e.g., Dallas, Austin).
*   **Rates**: Enter your consumption rate and fixed fees (defaults provided).
*   **GCR Auto-fetch**: Enable this to automatically track monthly gas price fluctuations.
*   **Weather Entity**: Select a local weather source (e.g., `weather.home`) for 7-day forecasting.

## 🔍 Troubleshooting
- **Frequency**: Data is fetched daily at 7 AM (aligned with Atmos's data refresh) to minimize portal load.
- **Login Issues**: Ensure you can log in to [Atmos Energy](https://www.atmosenergy.com/) directly and have accepted any new Terms of Service.
- **GCR Fetching**: If the integration cannot fetch the latest rate PDF, it will fall back to your manually entered GCR rate and log a warning.
