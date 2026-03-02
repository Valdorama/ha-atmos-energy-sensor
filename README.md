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

## 📊 Available Sensors & Modes

This integration supports three modes of operation to balance data granularity for different regions and use cases.

### 1. Monthly Data (Basic)
*   **Data Source**: Monthly bill downloads.
*   **Usage**: Displays the total from your last successful bill.
*   **Sensors**: `Gas usage` (Previous bill).
*   **Energy Dashboard**: Direct tracking of the sensor (`total_increasing`).

### 2. Daily Data (Basic)
*   **Data Source**: Daily usage downloads.
*   **Usage**: Historical daily data and "Days Remaining" in cycle.
*   **Sensors**: `Gas usage (Current)`, `Days remaining`.
*   **Energy Dashboard**: Uses the **Statistics API** for accurate historical tracking.

### 3. Daily Data + Advanced Prediction (Mid-Tex Only)
*   **Data Source**: Daily usage + regional weather + GCR rates.
*   **Usage**: Total high-accuracy bill forecasting.
*   **Sensors**: All of the above, plus `Estimated cost`, `Predicted usage (7d)`, and `Predicted cost (7d)`.
*   **Energy Dashboard**: Uses the **Statistics API**.

---

### Sensor Comparison

| Sensor | Monthly | Daily | Daily Advanced | `state_class` |
| :--- | :---: | :---: | :---: | :--- |
| **Gas usage (Current)** | | ✅ | ✅ | `None` (Use Statistics API) |
| **Days remaining** | | ✅ | ✅ | `None` |
| **Estimated cost** | | | ✅ | `total` |
| **Predicted Usage (7d)** | | | ✅ | `None` |
| **Predicted Cost (7d)** | | | ✅ | `None` |
| **Gas Usage (Monthly)** | ✅ | | | `total_increasing` |

---

## ⚡ Energy Dashboard Integration (Daily Mode)

Atmos Energy data is typically delayed by 24 hours. To ensure your gas usage and calculated costs appear on the **correct day** in Home Assistant, this integration uses the **Statistics API** instead of tracking the sensor state.

As of v0.8.0, the integration generates both **Daily Usage (CCF)** and **Daily Cost (USD)** statistics dynamically. The daily cost perfectly pro-rates your fixed monthly charge and applies all variable WNA, GCR, URI, and tax formulas for that specific day.

### Setting Up from Scratch (For Best Accuracy)
To get full historical accuracy extending back months or years before you installed the integration:
1. Log into your Atmos Energy account on the web and download your historical "daily usage" `.xls` files.
2. Place these files somewhere accessible to Home Assistant (e.g., your `www` folder or `config` directory).
3. Go to **Developer Tools > Services**.
4. Run the `atmos_energy.populate_past_usage` service, typing the absolute or config-relative path to the file (e.g., `www/january_usage.xls`) into the `file_path` field. Run this service for your files in **chronological order** (oldest first, newest last).
5. Once historical data is loaded, you can now run the `atmos_energy.refresh_current_usage` service to pull the latest days up to the present.

### How to configure the Energy Dashboard:
1. Go to **Settings > Dashboards > Energy**.
2. Under **Gas Consumption**, click **Add Gas Source**.
3. **Gas consumption**: Select **Atmos Energy Daily Usage** (look for the entry with the graph icon, as it is a statistic).
    > [!WARNING]
    > Do **not** select `sensor.atmos_energy_gas_usage` as the gas source. You must use the external statistic ID.
4. **Costs**: Select the second option: ☑ **Use an entity tracking total costs**.
5. **Entity**: Select **Atmos Energy Daily Cost** (look for the entry with the graph icon).
6. Click **Save**.

The dashboard will take about an hour or two to recalculate and display your fully accurate historical usage and daily-calculated bill costs.

---

## ❄️ Advanced Prediction (Mid-Tex Region)

Billing for natural gas is complex. In the **Mid-Tex region** (Austin, Dallas, Waco, etc.), Atmos Energy applies a **Weather Normalization Adjustment (WNA)** to level out the impact of unusually warm or cold weather.

### Precision & Accuracy (v0.7.x)
This integration delivers high cost accuracy for Mid-Tex customers by:
1. **Automated GCR Rates**: Automatically fetches the latest monthly Gas Cost Recovery (GCR) rates from Atmos Energy's official filings.
2. **Real-time WNA Calculation**: Uses the official Atmos tariff formula combined with your local weather forecast to estimate your bill's weather adjustment.
3. **Bill Alignment (v0.7.1)**: Sensor attributes and configuration labels are mapped 1:1 to the terminology on your actual Atmos bill (e.g., "Consump Chrg", "Rider GCR").
4. **Pro-rated Forecasts (v0.7.1)**: Fixed monthly fees are pro-rated across the 7-day prediction for a more realistic marginal cost forecast.
5. **Precise Cycle Tracking**: Parses your actual "Next Meter Read Date" from the portal.

**Note**: Due to significant differences in billing formulas and regional tariffs, advanced prediction is currently only available for the **Mid-Tex region**.

---

## 🔄 Upgrading to v0.8.0

Version 0.8.0 introduces a massive improvement to historical accuracy by switching costs directly to the Home Assistant Statistics API. Because this fundamentally changes how data is recorded, **a clean wipe of old data is highly recommended** to avoid duplicate entities or jagged dashboard graphs.

**Recommended Upgrade Path:**
1. Navigate to **Settings > Dashboards > Energy** and remove your Atmos Energy gas source.
2. Navigate to **Settings > Devices & Services** and delete the Atmos Energy integration.
3. Restart Home Assistant completely.
4. Navigate to **Developer Tools > Statistics**. Look for any remaining `atmos_energy...` or `sensor.atmos_energy...` entities and click the **Fix Issue** (or remove) button to purge old data from the database.
5. Install v0.8.0 via HACS or manually.
6. Run the setup configuration (entering your login and preferences).
7. Follow the **Setting Up from Scratch** instructions above to re-import your past `.xls` statements for a perfectly clean historical record.
8. Re-add the new Statistics entities back to your Energy Dashboard.

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
- **Frequency**: Data is fetched daily at 8 AM (aligned with Atmos's data refresh) to minimize portal load.
- **Login Issues**: Ensure you can log in to [Atmos Energy](https://www.atmosenergy.com/) directly and have accepted any new Terms of Service.
- **GCR Fetching**: If the integration cannot fetch the latest rate PDF, it will fall back to your manually entered GCR rate and log a warning.
- **New Operation Modes (v0.7.2)**:
    - **Monthly**: Lightweight monthly billing data ONLY.
    - **Daily (Basic)**: Daily data and stats API, but skips heavy modeling and cost prediction.
    - **Daily (Advanced)**: Full predictive modeling and WNA/GCR calculations for Mid-Tex customers.
