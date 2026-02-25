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
- **GCR PDF Discovery**: The `GCRRateFetcher` uses Gatsby `page-data.json` discovery to find the dynamic monthly static PDF link without needing a browser.
- **Conditional Grid Search**: Full ML optimization (21 iterations) only runs when 10+ new data points are added. Otherwise, a quick update using existing balance temperature is performed.
- **Smart Scheduling**: Updates scheduled for 7 AM local time daily (aligned with Atmos's ~6 AM data refresh) instead of fixed intervals.
- **Incremental Storage**: Only new/modified history records are written to storage, not the entire dict.
- **Statistics API Integration (v0.7.1+)**:
    - **Concept**: Utility data is often delayed (24h+). Standard sensors (`state_class: total_increasing`) cause Home Assistant to attribute delayed data to the wrong day.
    - **Pattern**: Disable `state_class` on the usage sensor (`None`) to make it display-only. Use `async_add_external_statistics` to push historical data into the DB with the correct timestamps.
    - **Logic**: Statistics must include both `state` (daily value) and `sum` (cumulative total across all history) to be valid for the Energy Dashboard.
    - **Metadata**: Specify `unit_class="volume"` and `mean_type=None` (be aware of DB IntegrityErrors when specifying `mean_type` as `None` if the column is NOT NULL but empty).

### 4. Home Assistant Integration Standards
- **Device Support**: The integration is classified as a `device`. Entities are associated with the device using `has_entity_name = True`.
- **Naming**: Prefix entity names with `atmos_energy_` to ensure unique identification in the HA registry.
- **UI Wizards**: The setup and options flows use a multi-step "wizard" pattern to reduce complexity and provide context about regional limitations (e.g., advanced prediction is Mid-Tex only).

## 🔍 Troubleshooting
- Use `scripts/diagnose_atmos.py` for live login and download testing.
- Refer to `walkthrough.md` for historical context on recent fixes.
- **GCR Issues**: Check the `atmos_energy_gcr_cache` storage file to verify cached rates.
