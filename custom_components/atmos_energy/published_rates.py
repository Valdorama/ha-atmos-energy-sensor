"""Weekly fetch of rates.json from the repository, with the bundled copy as fallback."""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta
from typing import Any

import aiohttp

from homeassistant.helpers.storage import Store

from .rates_data import InvalidRates, load_bundled, newer, validate

_LOGGER = logging.getLogger(__name__)

URL = (
    "https://raw.githubusercontent.com/Valdorama/ha-atmos-energy-sensor/"
    "master/custom_components/atmos_energy/rates.json"
)
FETCH_INTERVAL = timedelta(days=7)
TIMEOUT = aiohttp.ClientTimeout(total=30)
STORAGE_KEY = "atmos_energy_published_rates"


class PublishedRates:
    """The newest valid rates.json among: the last download, and the copy in this version."""

    def __init__(self, hass):
        self.hass = hass
        self.doc: dict[str, Any] | None = None
        self._downloaded: dict[str, Any] | None = None
        self._last_fetch: datetime | None = None
        self._store = Store(hass, 1, STORAGE_KEY)

    async def async_load(self):
        """Load the bundled copy and the last download."""
        bundled = None
        try:
            bundled = await self.hass.async_add_executor_job(load_bundled)
        except (OSError, ValueError) as e:
            _LOGGER.error("Bundled rates.json is unusable: %s", e)
        try:
            stored = await self._store.async_load() or {}
            if stored.get("last_fetch"):
                self._last_fetch = datetime.fromisoformat(stored["last_fetch"])
            if stored.get("doc"):
                self._downloaded = validate(stored["doc"])
        except (ValueError, TypeError) as e:
            _LOGGER.warning("Ignoring stored published rates: %s", e)
        # An upgrade can bring a bundled copy newer than the last download
        self.doc = newer(self._downloaded, bundled)

    async def async_refresh(self) -> bool:
        """Download rates.json if the last attempt was a week ago or more. True if it changed."""
        now = datetime.now()
        if self._last_fetch is not None and now - self._last_fetch < FETCH_INTERVAL:
            return False
        self._last_fetch = now
        changed = False
        try:
            async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
                async with session.get(URL) as response:
                    if response.status != 200:
                        raise InvalidRates(f"HTTP {response.status}")
                    doc = validate(json.loads(await response.text()))
            self._downloaded = doc
            latest = newer(self.doc, doc)
            changed = latest is not self.doc
            self.doc = latest
            _LOGGER.debug("Published rates checked (updated %s)", doc["updated"])
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as e:
            # Keep what we have; try again next week
            _LOGGER.warning("Could not fetch published Atmos rates: %s", e)
        await self._save()
        return changed

    async def _save(self):
        try:
            await self._store.async_save({
                "doc": self._downloaded,
                "last_fetch": self._last_fetch.isoformat() if self._last_fetch else None,
            })
        except Exception as e:
            _LOGGER.warning("Failed to save published rates: %s", e)
