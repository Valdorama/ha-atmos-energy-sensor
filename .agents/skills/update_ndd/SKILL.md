---
name: Update NDD Data
description: Run this skill to annually update the Normal Degree Days (NDD) tables and WNA configuration from Atmos Energy.
---

# Update NDD Data Skill

This skill automates the annual task of updating the Normal Degree Days (NDD) baseline data used for Weather Normalization Adjustments (WNA).

## Instructions for the Agent

When the user requests to update the NDD data:

1. Look at `custom_components/atmos_energy/wna/ndd_data.py` to check the current year and the previous rolling average timeframe.
2. Search for the latest `Mid-Tex_WNA_Report_Data_{year}.xlsx` from the Atmos Energy Tariffs website (`https://www.atmosenergy.com/utility-operationsrates/tariffs-mid-tex/`).
3. If the file is available, use a python script to parse the "Monthly WNAF - ATM" (Austin) or corresponding sheets to extract the new NDD values for each station.
4. If the file is not yet available, notify the user, and if they approve, approximate the update by bumping the values by ~1% (following the typical <5% year-over-year change) as a simulation.
5. Update the `MID_TEX_NDD` dictionary in `ndd_data.py`.
6. Update the metadata strings in `ndd_data.py` (e.g. `Data source: Atmos Energy WNA Report (June 2026)`, `Based on: 10-year rolling average (2016-2025)`).
7. Bump the integration version in `custom_components/atmos_energy/manifest.json`.
8. Output a Release Summary in Markdown format detailing the NDD data update, strictly following the rules in `AGENTS.md`.

**Note:** Use Python scripts in a scratch directory to parse HTML or Excel files as necessary, since typical shell tools like `grep` might not be available on Windows or sufficient for fetching and parsing Excel data.
