# Atmos Energy Home Assistant Integration

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![Latest Release](https://img.shields.io/github/v/release/Valdorama/ha-atmos-energy-sensor?color=blue)](https://github.com/Valdorama/ha-atmos-energy-sensor/releases)
[![License](https://img.shields.io/github/license/Valdorama/ha-atmos-energy-sensor)](https://github.com/Valdorama/ha-atmos-energy-sensor/blob/master/LICENSE)
[![Buy Me A Coffee](https://img.shields.io/badge/buy%20me%20a%20coffee-donate-yellow.svg)](https://buymeacoffee.com/valdorama)

A custom component for Home Assistant to retrieve usage data from [Atmos Energy](https://www.atmosenergy.com/).

**Disclaimer**: This is an unofficial integration and is not affiliated with Atmos Energy. It scrapes the website to retrieve data, so changes to the Atmos Energy website may break this integration.

## Features
- **Gas usage (Current Billing Period)**: Retrieves total usage for the current billing period in CCF (for accounts with daily data).
- **Gas Usage (Previous Billing Period)**: Retrieves the usage from the most recent completed billing cycle (for accounts without daily data).
- **Estimated cost**: Calculates an estimated cost for the current billing period (daily accounts only).
- **Days remaining in billing period**: Tracks how many days are left in your current billing cycle (daily accounts only).
- **Predicted Gas Usage/Cost (Next 7 Days)**: Uses weather forecasts and a Heating Degree Day (HDD) algorithm to estimate your upcoming usage (daily accounts only).
- Reports the latest date for which usage data is available.
- (Planned) Bill amount and due date.

## Installation

### Option 1: HACS (Recommended)
1. Ensure [HACS](https://hacs.xyz/) is installed.
2. Go to **HACS > Integrations**.
3. Click the **three dots** in the top right corner and select **Custom repositories**.
4. Add the URL of this repository: `https://github.com/Valdorama/ha-atmos-energy-sensor`.
5. Select **Integration** as the category.
6. Click **Add** and then install the integration.
7. Restart Home Assistant.

### Option 2: Manual Check
1. Copy the `custom_components/atmos_energy` directory to your Home Assistant `config/custom_components/` directory.
2. Restart Home Assistant.

## Configuration
1. Go to **Settings > Devices & Services**.
2. Click **Add Integration**.
3. Search for **Atmos Energy**.
4. Enter your Atmos Energy **Username** and **Password**.
5. Select whether your account provides **daily usage data**. (Note: If your account only provides monthly data, uncheck this box to avoid configuration errors).
6. If daily usage is enabled, configure your **Rates and Weather Entity** on the next screen.

## Configuration Options
To update your credentials or improve the accuracy of the estimated cost sensor, you can adjust the integration options:
1. Go to **Settings > Devices & Services**.
2. Click on the **Atmos Energy** integration card.
3. Click **Configure**.
4. Adjust the following values:
    - **Username/Password**: Update your credentials if they change.
    - **Fixed Cost**: The base monthly charge/customer charge found on your bill.
    - **Usage Rate ($/CCF)**: The total cost per unit of gas. **Note:** This should be the **sum** of all individual per-unit costs (e.g., Distribution Charge + Pipeline Charge + Rider GCR).
    - **Tax Percent**: Your local tax rate.
    - **Weather Entity**: (Optional) Select a weather entity (like `weather.home`) to enable the 7-day predicted usage and cost sensors.

## Troubleshooting
If you have issues logging in, ensure you can log in to the [Atmos Energy website](https://www.atmosenergy.com/) directly.
