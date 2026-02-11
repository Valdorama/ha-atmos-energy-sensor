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

### 3a. Performance Optimizations (v0.6.1+)
- **Conditional Grid Search**: Full optimization (21 iterations) only runs when 10+ new data points are added. Otherwise, a quick update using existing balance temperature is performed (~95% faster).
- **Smart Scheduling**: Updates scheduled for 7 AM local time daily (aligned with Atmos's ~6 AM data refresh) instead of fixed intervals.
- **Incremental Storage**: Only new/modified history records are written to storage, not the entire dict.
- **Periodic Weather Updates**: Prediction sensors update every 6 hours instead of on every weather state change (~96% fewer events).
- **Race Condition Protection**: All dict iterations use `dict(self._history).items()` to prevent modification during iteration.
- **Balance Temp Validation**: Learned balance temperatures are validated (50-80°F range) with warnings for unusual values.

### 4. Home Assistant Integration Standards
- **Device Support**: The integration should be classified as a `hub` or `device`. Entities should be associated with the device using `has_entity_name = True`.
- **Naming**: Prefix entity names with `atmos_energy_` to ensure unique and clear identification in the HA registry.
- **Energy Dashboard**: Set `state_class: total` and `device_class: monetary` for the Estimated Cost sensor to ensure compatibility with cost tracking.

## 🔍 Troubleshooting
- Use `scripts/diagnose_atmos.py` for live login and download testing.
- Refer to `walkthrough.md` for historical context on recent fixes.
