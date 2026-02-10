# Atmos Energy Home Assistant Integration

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![Latest Release](https://img.shields.io/github/v/release/Valdorama/ha-atmos-energy-sensor?color=blue)](https://github.com/Valdorama/ha-atmos-energy-sensor/releases)
[![License](https://img.shields.io/github/license/Valdorama/ha-atmos-energy-sensor)](https://github.com/Valdorama/ha-atmos-energy-sensor/blob/master/LICENSE)
[![Buy Me A Coffee](https://img.shields.io/badge/buy%20me%20a%20coffee-donate-yellow.svg)](https://buymeacoffee.com/valdorama)

A custom component for Home Assistant to retrieve usage data from [Atmos Energy](https://www.atmosenergy.com/).

**Disclaimer**: This is an unofficial integration and is not affiliated with Atmos Energy. It scrapes the website to retrieve data, so changes to the Atmos Energy website may break this integration.

## ✨ Features
- **Usage Tracking**: Monitor your gas consumption for the current billing period (Daily accounts) or previous cycle (Monthly accounts).
- **Cost Estimation**: Real-time cost tracking based on your specific utility rates, fixed fees, and local taxes.
- **Smart Predictions**: 7-day gas usage and cost forecasts driven by local weather data and a personalized heating model.
- **Energy Dashboard Ready**: Fully compatible with the Home Assistant Energy Dashboard for long-term tracking.
- **Automated Modeling**: Automatically learns your home's heating efficiency by analyzing historical usage and temperature data.

## 📊 Available Sensors

This integration provides different sensors depending on your account type (Daily vs Monthly).

### Daily Usage Mode (Standard)
*Enabled by checking "provides daily usage data" during setup.*

| Sensor | Description | Class |
| :--- | :--- | :--- |
| **Gas usage (Current)** | Total usage (CCF) for the current billing period. | `total_increasing` |
| **Estimated cost** | Calculated cost for the current period (including tax/fixed fees). | `total` |
| **Days remaining** | Estimated days left in the 30-day billing cycle. | `measurement` |
| **Predicted Usage (7d)** | Estimated gas usage for the next 7 days based on weather. | `total` |
| **Predicted Cost (7d)** | Estimated gas cost for the next 7 days based on weather. | `total` |

### Monthly Usage Mode
*Used if your account does not provide granular daily data.*

| Sensor | Description | Class |
| :--- | :--- | :--- |
| **Gas Usage (Previous)** | Total usage (CCF) from the last completed billing cycle. | `measurement` |

---

## 🔮 How Predictions Work

The integration uses a **Linear Regression** model to predict your future gas consumption based on local weather forecasts.

### 1. The Algorithm
Gas usage is modeled using the **Heating Degree Day (HDD)** method:
`Daily Usage = Base Load + (Heating Coefficient * HDD)`

*   **Heating Degree Day (HDD)**: Calculated as `max(0, 65°F - Average Daily Temperature)`. If the temperature is 40°F, the HDD is 25.
*   **Base Load**: Your baseline usage for non-heating appliances (water heater, stove).
*   **Heating Coefficient**: How many extra CCFs of gas you burn for every degree the temperature drops below 65°F.

### 2. Automatic Training
The integration automatically calculates your personalized `Base Load` and `Heating Coefficient` by analyzing the last **90 days** of your actual usage history from the Atmos portal.
*   It requires at least **10 days** of history to generate a custom model.
*   If insufficient history is available, it uses regional defaults until enough data is collected.

### 3. Requirements
To enable predictions, you must:
1. Enable **Daily Usage** during configuration.
2. Select a **Weather Entity** (e.g., `weather.home`) in the Integration Options.

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
3. **Daily Usage Check**: Only uncheck this if your Atmos portal does *not* show a "Daily Usage" chart.

### Fine-Tuning Accuracy (Options)
Click **Configure** on the Atmos Energy card to adjust:
*   **Fixed Cost**: The base monthly customer charge (e.g., $25.03).
*   **Usage Rate ($/CCF)**: The total of all per-unit charges (Distribution + Pipeline + Gas Cost).
*   **Tax Percent**: Your local sales tax (e.g., 8.25%).
*   **Weather Entity**: Required for the 7-day prediction features.

## 🔍 Troubleshooting
- **Frequency**: Data is fetched every **12 hours** to avoid overwhelming the portal.
- **Login Issues**: Ensure you can log in to [Atmos Energy](https://www.atmosenergy.com/) directly and have accepted any new Terms of Service.
- **Sensor Errors**: If sensors show `None`, check the logs for "Authentication Error" or "Data Parsing Error".