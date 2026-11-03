# Atmos Energy Home Assistant Integration

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![Latest Release](https://img.shields.io/github/v/release/Valdorama/ha-atmos-energy-sensor?color=blue)](https://github.com/Valdorama/ha-atmos-energy-sensor/releases)
[![License](https://img.shields.io/github/license/Valdorama/ha-atmos-energy-sensor)](https://github.com/Valdorama/ha-atmos-energy-sensor/blob/master/LICENSE)
[![Buy Me A Coffee](https://img.shields.io/badge/buy%20me%20a%20coffee-donate-yellow.svg)](https://buymeacoffee.com/valdorama)

A custom component for Home Assistant to retrieve usage data from [Atmos Energy](https://www.atmosenergy.com/).

**Disclaimer**: This is an unofficial integration and is not affiliated with Atmos Energy. It scrapes the website to retrieve data, so changes to the Atmos Energy website may break this integration.

## ✨ Features
- **Usage Tracking**: Monitor your gas consumption for the current billing period (Daily accounts) or previous cycle (Monthly accounts).
- **Daily Cost in Every Atmos Region**: Choose your rate region (Texas, Louisiana, Mississippi, Kansas, Kentucky, Virginia, Colorado or Tennessee) and the daily cost statistics use its tariff rates and its automatically fetched monthly gas cost.
- **Cost Estimate & Prediction**: Bill estimates and forecasts in every region, plus the Weather Normalization Adjustment (WNA) for Mid-Tex customers.
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
*   **Energy Dashboard**: Uses the **Statistics API** for accurate historical tracking, including daily cost from your region's rates.

### 3. Daily Data + Cost Estimate & Prediction
*   **Data Source**: Daily usage + local weather + your region's rates (plus WNA in Mid-Tex).
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

As of v0.8.0, the integration generates both **Daily Usage (CCF)** and **Daily Cost (USD)** statistics dynamically. The daily cost pro-rates your fixed monthly charge and applies the consumption charge, gas cost, other per-CCF charges and tax for that specific day (plus WNA in Mid-Tex prediction mode). Billed days are priced like the bill they're on: the gas cost published for the bill's month (Atmos applies one month's rate to the whole bill period) and the customer charge spread over the bill period's actual days, so each billed period's daily costs add up to the bill. Days of the open current period use their own month and 30 days until they're billed.

### Historical Data
In daily modes, the first update after setup (or after upgrading) imports every past billing period the Atmos portal offers, about two years of daily usage, before the current period. This takes a minute or two, since the portal is asked for one period at a time. After that, each daily update fetches the current period plus the most recent past one, so days at the end of a billing cycle are still imported if Home Assistant was offline at the meter read.

To go back further than the portal's two years, download older "daily usage" `.xls` files from the Atmos website and import them:
1. Place the files somewhere accessible to Home Assistant (e.g., your `www` folder or `config` directory).
2. Go to **Developer Tools > Actions**.
3. Run the `atmos_energy.populate_past_usage` action, typing the absolute or config-relative path to the file (e.g., `www/january_usage.xls`) into the `file_path` field. Run it for your files in **chronological order** (oldest first, newest last). If you track more than one account, also pick the account in the `config_entry` field.
4. Run the `atmos_energy.refresh_current_usage` action to re-import the portal's two years on top.

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

**Note**: The cost estimate and 7-day predictions work in every region. The WNA estimate is Mid-Tex only, because each region calculates it differently and publishes its parameters in different places.

---

## 🗺️ Rate Regions

Atmos Energy sets rates separately in each region. In the daily modes, setup asks for your region:

| Region | Monthly gas cost fetched | Notes |
| :--- | :--- | :--- |
| Texas: Mid-Tex | GCR, Residential | WNA estimated in the prediction mode |
| Texas: West Texas | GCA | The GCA includes the Uri (CRR) charge. Default rates are for Amarillo, Lubbock and unincorporated areas; other cities differ slightly |
| Louisiana | PGA | |
| Mississippi | PGA, Combined Year Round | |
| Kansas | PGA, Firm | |
| Kentucky | GCA, G-1 | The tariff bills per Mcf; enter rates per CCF (divide by 10) |
| Virginia | PGA, Firm total | |
| Colorado: North / Southeast / Southwest | GCA, Firm, per area | Each area has its own gas cost |
| Tennessee (except Union City) / Union City | PGA, Firm total | Customer charge is lower May to September |

The region fills the rates form with that region's residential tariff values (customer charge and consumption charge), which you should check against your bill. Taxes, franchise fees and riders vary by city, so add those yourself: per-CCF riders go in **Other per-CCF charges**, taxes and fees in the percentage.

Outside Mid-Tex, cost estimates have no weather normalization adjustment. Most regions apply one on winter bills, so winter estimates can differ from the bill by a few cents per CCF.

Entries set up before regions existed are treated as Mid-Tex. Choose a different region in the entry's **Configure** dialog.

### Keeping Rates Up to Date
Rates change on two schedules, and neither needs a new version of the integration:
* **Gas cost** changes monthly. It is fetched from Atmos Energy's website.
* **Customer charge and consumption charge** change about once a year, when a region's tariff changes. The integration checks a published rates file in this repository once a week. With **Keep the customer charge and consumption charge up to date automatically** ticked (the default for new setups), it uses those rates, including the dates they took effect, instead of the values saved in the form. The saved values are still used if the published file can't be reached or doesn't cover your region. The Mid-Tex weather normalization parameters are updated the same way.

Entries set up before this option existed keep using their saved rates until you open **Configure** and save with the box ticked.

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
3. **Operation Mode**: Choose monthly, daily, or daily with cost estimate & prediction.

### Configuration Wizard
In the daily modes, you will be guided through:
*   **Rate Region**: Select the Atmos region on your bill (see [Rate Regions](#️-rate-regions)).
*   **Weather Station** (prediction mode, Mid-Tex only): Select the station Atmos uses for your billing area (e.g., Dallas, Austin).
*   **Rates**: Check your customer charge, consumption charge, other charges and tax (defaults come from your region's tariff).
*   **Gas Cost Auto-fetch**: Enable this to automatically track the monthly gas cost (GCR, GCA or PGA).
*   **Weather Entity** (prediction mode): Select a local weather source (e.g., `weather.home`) for 7-day forecasting.

### Multiple Accounts (Service Addresses)
If your login has more than one Atmos Energy account, setup asks which service address to track. Each config entry tracks one account, with its own sensors, rates, history and Energy Dashboard statistics. To track another address, add the integration again with the same login; addresses already set up are not offered again.

An entry created before multi-account support, on a login with several accounts, fails with "This Atmos Energy login has N accounts and none is selected". Open the entry's **Configure** dialog and pick the account; its existing sensors are kept.

With more than one entry, `atmos_energy.populate_past_usage` needs the `config_entry` field to say which account the spreadsheet belongs to, and `atmos_energy.refresh_current_usage` refreshes every account.

## 🔍 Troubleshooting
- **Meter readings in daily data**: The portal's daily download occasionally puts the meter reading (thousands of CCF) in a day's consumption. A day above both 100 CCF and 20× the bill period's typical day is treated as one of these: it gets an equal share of what the period's billed usage (from the monthly download) leaves after its other days, and a warning is logged. The period is re-imported on the next update, which also corrects a spike already in the Energy dashboard.
- **Frequency**: Data is fetched daily at 8 AM (aligned with Atmos's data refresh) to minimize portal load.
- **Login Issues**: Ensure you can log in to [Atmos Energy](https://www.atmosenergy.com/) directly and have accepted any new Terms of Service.
- **Gas Cost Fetching**: If the integration cannot fetch the latest rate PDF, it uses the most recent month it has, or else your manually entered gas cost, and logs a warning. It checks again once a day until the new month is published.
- **New Operation Modes (v0.7.2)**:
    - **Monthly**: Lightweight monthly billing data ONLY.
    - **Daily (Basic)**: Daily data and stats API, but skips heavy modeling and cost prediction.
    - **Daily (Cost Estimate & Prediction)**: Full predictive modeling and cost estimates, with WNA for Mid-Tex customers.
