# Agent Guidance for Atmos Energy Integration

This file provides persisted guidance and "rules of engagement" for AI agents working on this codebase.

> **This is a collaborative project.** You (the agent) and the user are working together
> on this codebase. As you learn new things about how the project works, discover bugs,
> understand design decisions, or form new ideas, **always come back to this file and
> update it**. Add new information, remove anything thats no longer accurate, and refine
> concepts and thoughts. This file is your persistent memory of the project — treat it
> as a living document that evolves with the work.

## 🛠 Operation Guidelines

### 1. Development Process (TDD)
- **Home Assistant Standards**: Follow the official [Home Assistant Developer Guidelines](https://developers.home-assistant.io/docs/creating_component_index/) for custom integrations.
- **AI Agent Best Practices**: Priority must be placed on **reliability, robustness, and performance**. Agents should proactively handle edge cases, network failures, and API inconsistencies.
- **Test-First**: Always attempt to reproduce a bug or define a new feature with a test case in `tests/test_reproduction.py` or `tests/test_api.py` before modifying core logic.
- **Verification**: Ensure all tests pass (`python -m unittest discover tests`) after any non-trivial change.

### 2. Versioning & Releases
- **Semantic Versioning**: Use the `v0.x.x` format.
- **Version Sync (CRITICAL)**: Before creating a git tag, the `version` in `custom_components/atmos_energy/manifest.json` **MUST** be updated to match the target tag version. This ensures the integration reflects the correct version within Home Assistant.
- **Release Summaries**: When creating a new release (tagging and pushing), always provide a **Release Summary in Markdown format** as the final step. This summary should be easy to copy/paste and highlight:
    - New Features & Enhancements
    - Fixes & Stability Improvements
    - Internal Cleanup
    - Breaking Changes or Update Instructions

### 3. Core Logic Patterns
- **Centralized Verification**: Use the `_verify_response` method in `AtmosEnergyApiClient` to handle all URL redirects, HTML detection, and portal error messages.
- **Session Lifecycle**: Remember that Atmos Energy requires a multi-hop login flow (Form -> POST -> Landing Page) to properly initialize the session for file downloads.
- **Data Granularity**: Support both daily (`dailyUsageDownload.html`) and monthly (`monthlyUsageDownload.html`) data sources. Check the `daily_usage` config option before selecting the API method.
- **Robust Parsing**: Always use `content.strip()` before parsing XLS data to handle leading whitespace bugs. Maintain the HTML table fallback for mislabeled files.
- **Date Handling**: The Atmos portal sometimes returns verbose Java-style dates (e.g., `Wed Mar 11 00:00:00 CDT 2026`). The parsing logic in `Coordinator._parse_next_read_date` is designed to strip timezones and attempt multiple formats for robustness.

### 3a. Performance Optimizations (v0.7.0+)
- **Centralized Heavy Lifting**: Weather service calls (`get_forecasts`), WNA math, and date parsing are performed **once** in the `Coordinator`. Sensors are "dumb" and only display pre-calculated values from `coordinator.data`.
- **Gas Cost PDF Discovery**: `GasCostFetcher` (`gas_cost.py`) reads the region's tariff page `page-data.json` for links to `/document/<prefix>-<month>-<year>[-N]`, takes the newest, then that document's `page-data.json` `redirect` field for the static PDF. Slugs sometimes carry a `-0` suffix, so don't build them from a template except as the fallback. Links in page-data escape slashes as `\/`.
- **Conditional Grid Search**: Full ML optimization (21 iterations) only runs when 10+ new data points are added. Otherwise, a quick update using existing balance temperature is performed.
- **Smart Scheduling**: Updates scheduled for 8 AM local time daily (aligned with Atmos's ~7 AM data refresh) instead of fixed intervals.
- **Incremental Storage**: Only new/modified history records are written to storage, not the entire dict.
- **Statistics API Integration (v0.8.0+)**:
    - **Concept**: Utility data is often delayed (24h+). Standard sensors (`state_class: total_increasing`) cause Home Assistant to attribute delayed data to the wrong day.
    - **Pattern**: Disable `state_class` on the usage sensor (`None`) to make it display-only. Use `async_add_external_statistics` to push historical data into the DB with the correct timestamps.
    - **Parallel Cost Statistics**: For Energy Dashboard cost tracking, rely on the `stat_cost` method. Create a parallel external statistic (`domain:cost_<id>`) that calculates the exact per-day cost including pro-rated fixed charges, WNA, and taxes, rather than trying to provide a dynamic price sensor.
    - **WNA in cost statistics**: `_wna_rate(day)` applies the current WNA factor (`wnaf_rate_raw`) only to days on or after its `billing_period_start`; earlier periods' factors aren't known, so their days get no WNA. `_update_advanced_metrics` runs before the statistics import so the factor is the current update's. (Up to v0.9.1 the lookup used a non-existent `wna_factor` key, so cost statistics never included WNA.)
    - **Logic**: Statistics must include both `state` (daily value) and `sum` (cumulative total across all history) to be valid for the Energy Dashboard. Predecessor sums must be retrieved dynamically to prevent negative spikes.
    - **Historical Import**: Use the `populate_past_usage` service to import backdated XLS files. This relies on the same statistics infrastructure.
    - **Backfill (opt-in)**: `dailyUsageDownload.html?billingPeriod=<value>` accepts any value from the `billingPeriod` picker on `dailyUsage.html`: `Current` plus about 24 past periods, newest first, like `October,2026`. A period is named for its bill month, so `October,2026` holds mostly September usage; always take dates from the file. Periods meet end to end with no overlap. The one-time bulk backfill is opt-in: the `backfill_history` option (the last step of the daily-mode setup wizard, default on for new setups; off for entries that don't have it, so upgrades never backfill unasked; also in the options flow). When enabled, the first daily update downloads every period (`client.get_billing_periods()` + `get_daily_history()`, one login). Every update, enabled or not, adds the latest past period so days that leave `Current` at a meter read aren't lost; errors there (including a login-looking page) never fail the update. `backfill_done` in the entry's store records completion and is never reset, so re-enabling the option after a completed backfill does nothing; until it completes, `async_setup_refresh` refreshes at startup. Backfilled days are priced at their bill month's gas cost where the region's cache has it, else the current one.
    - **Cost predecessor sum**: like the usage sum, the cost sum may only be seeded from a record strictly before the batch's first day. A backfill batch starts before any existing statistic, and seeding it from the latest total offset the whole cost series.
    - **One Import Call**: `async_add_external_statistics` is queued, not immediate, so a second import in the same update can't see the first one's sums and restarts at 0. Merge every period into one list (`_merge_history`) and import it with a single `_async_import_statistics_list` call.

### 3b. Multiple Accounts (Service Addresses)
- **Portal behavior**: A login with several accounts lands on a "Summary of Accounts" page after the auth POST. Until one is opened with `landing/singleAccount.html?accountNumber=N`, every usage-history page (and the XLS downloads) returns the generic "systems are temporarily unavailable" error page. The selection lasts for the session.
- **Pattern**: One config entry per account. `AtmosEnergyApiClient._authenticate()` parses the summary page into `client.accounts`; `login()` then calls `_select_account()`. Single-account logins land on the account directly, parse to `[]`, and skip selection entirely.
- **Identity**: Multi-account entries use the account number as `unique_id`; single-account and pre-existing entries keep the username. Sensors and devices key off `entry.unique_id`, so existing entity IDs never change. Don't switch an entry's account: history and statistics are per entry.
- **Storage**: History is per entry (`atmos_energy_history_<entry_id>`). The old shared `atmos_energy_history` file is moved once to the first pre-existing entry that loads.
- **Errors**: The "temporarily unavailable" page is an `APIError`, not an `AuthenticationError`, so it never starts reauth. A multi-account entry with no account raises `AccountSelectionError` and opens a Repairs issue.
- **Tests**: `tests_ha/` runs against a real HA (`pytest tests_ha`). New tests belong there; several modules in `tests/` reference XLS files that aren't in the repo or rely on mocks that no longer match, and fail on a clean checkout. It is separate from `tests/` because those modules mock `homeassistant.*` in `sys.modules` at import time.

### 3c. Rate Regions
- **Table**: `regions.py` lists every Atmos rate region (12 keys; Colorado and Tennessee are split by gas-cost area). Each has its gas-cost source and setup defaults from the residential tariff. It has no HA imports, like `gas_cost_parser.py`, so `tests/test_gas_cost_parser.py` loads both by path and runs without HA.
- **Option**: `options["region"]`; missing means `mid_tex`, so pre-region entries keep their behaviour. Asked in daily modes only (monthly mode computes no cost). Advanced mode (UI: "Cost Estimate & Prediction") works in every region; only its WNA part is Mid-Tex: the weather-station step is skipped elsewhere and `_update_advanced_metrics` reports a zero WNA factor (`weather_station: None`). Changing region in options drops the stored rates so the new region's defaults show.
- **Parser**: every region publishes a month-by-class table in $/Ccf. `gas_cost_parser.extract_rates` works from pdfplumber word positions (column header regex + occurrence, month labels in the nearest label column to its left, nearest number on each row). `extract_tables()` was tried and is unreliable on these PDFs. A region's `column`/`page`/`occurrence` is all that differs.
- **History**: each PDF has 13-25 months, all cached per region (`atmos_energy_gas_cost_<region>`; Mid-Tex keeps `atmos_energy_gcr_cache`). `calculate_total_cost(when=...)` prices a day at its month's gas cost and seasonal customer charge (`fixed_cost_summer`, May-Sep, Tennessee).
- **Bill-month pricing**: a bill charges one month's gas cost (its billing month) on the whole service period: the Louisiana October 2026 bill for 9/4-10/5 used Oct-26 for all 41 CCF. Daily records carry `billing_month` ("Oct 26", from the download's Billing Month column), so the statistics import passes `gas_cost_month` and `period_days` (days in that billing month's group) to `calculate_total_cost`, and uses the unrounded `total_exact`. Checked against three Louisiana bills (within the bills' own line rounding). Current-period days have no billing month and keep the calendar month and 30 days until the period is billed and re-imported with the latest past period.
- **Fixtures**: `tests/fixtures/gas_cost/<region>-YYYY-MM.pdf` (October 2026). When a region changes its PDF layout, add the new PDF as a fixture and adjust its `GasCostSource`. Defaults were read from the tariffs in Nov 2026; refresh them with the PDFs on each region's tariff page.
- **Published rates (`rates.json`)**: yearly values (each region's customer charge, summer charge and consumption rate with `effective` dates; Mid-Tex WNA parameters and NDD) live in `custom_components/atmos_energy/rates.json`. Every install ships a copy and `PublishedRates` (`published_rates.py`) fetches the `master` copy from raw.githubusercontent.com once a week, keeping whichever has the later `updated` date. Validation (`rates_data.validate`) rejects the whole file on any out-of-range value; failures keep the current copy. Entries use it when `options["use_published_rates"]` is true (form default on; missing means off, so pre-existing entries are unchanged until saved).
- **Updating rates (no release needed)**: when a tariff changes, *append* an entry with its `effective` date (don't overwrite: backfilled days use the entry in effect on their date), bump `updated`, and push to `master`. Installs pick it up within a week. Also update the matching setup defaults in `regions.py` (and `wna/ndd_data.py` for WNA); `tests/test_rates_data.py` fails if they drift from the latest entry. Changing the file's structure needs `SCHEMA` bumped, and older installs then ignore the new file, so prefer adding optional fields.
- **Not modelled outside Mid-Tex**: WNA (most regions have a rider; Tennessee's parameters are public in TPUC WNA audit dockets, the others are in commission filings), city taxes/franchise fees, fixed monthly riders (e.g. Kansas WESCR, Virginia IRRA).

### 4. Home Assistant Integration Standards
- **Device Support**: The integration is classified as a `device`. Entities are associated with the device using `has_entity_name = True`.
- **Naming**: Prefix entity names with `atmos_energy_` to ensure unique identification in the HA registry.
- **UI Wizards**: The setup and options flows use a multi-step "wizard" pattern to reduce complexity and provide context about regional limitations (e.g., WNA is estimated for Mid-Tex only).

## 🔍 Troubleshooting
- Use `scripts/diagnose_atmos.py` for live login and download testing.
- Refer to `walkthrough.md` for historical context on recent fixes.
- **GCR Issues**: Check the `atmos_energy_gcr_cache` storage file to verify cached rates.
