# Agent Guidance for Atmos Energy Integration

This file provides persisted guidance and "rules of engagement" for AI agents working on this codebase.

## 🛠 Operation Guidelines

### 1. Development Process (TDD)
- **Test-First**: Always attempt to reproduce a bug or define a new feature with a test case in `tests/test_reproduction.py` or `tests/test_api.py` before modifying core logic.
- **Verification**: Ensure all tests pass (`python -m unittest discover tests`) after any non-trivial change.

### 2. Versioning & Releases
- **Semantic Versioning**: Use the `v0.x.x` format.
- **Manifest Sync**: Ensure the `version` in `custom_components/atmos_energy/manifest.json` is bumped and synchronized with git tags.
- **Release Summaries**: When creating a new release (tagping and pushing), always provide a **Release Summary in Markdown format** as the final step. This summary should be easy to copy/paste and highlight:
    - New Features & Enhancements
    - Fixes & Stability Improvements
    - Internal Cleanup
    - Breaking Changes or Update Instructions

### 3. Core Logic Patterns
- **Centralized Verification**: Use the `_verify_response` method in `AtmosEnergyApiClient` to handle all URL redirects, HTML detection, and portal error messages.
- **Session Lifecycle**: Remember that Atmos Energy requires a multi-hop login flow (Form -> POST -> Landing Page) to properly initialize the session for file downloads.
- **Data Granularity**: Support both daily (`dailyUsageDownload.html`) and monthly (`monthlyUsageDownload.html`) data sources. Check the `daily_usage` config option before selecting the API method.
- **Robust Parsing**: Always use `content.strip()` before parsing XLS data to handle leading whitespace bugs. Maintain the HTML table fallback for mislabeled files.
- **Measurement vs Total**: Sensors using `state_class: measurement` (like the monthly usage sensor) should have `device_class: None` to avoid Home Assistant validation errors with gas units.

### 4. Home Assistant Integration Standards
- **Device Support**: The integration should be classified as a `hub` or `device`. Entities should be associated with the device using `has_entity_name = True`.
- **Naming**: Prefix entity names with `atmos_energy_` to ensure unique and clear identification in the HA registry.
- **Energy Dashboard**: Set `state_class: total` and `device_class: monetary` for the Estimated Cost sensor to ensure compatibility with cost tracking.

## 🔍 Troubleshooting
- Use `scripts/diagnose_atmos.py` for live login and download testing.
- Refer to `walkthrough.md` for historical context on recent fixes.
