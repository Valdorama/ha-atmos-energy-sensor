"""Fetch and cache a region's monthly gas cost (GCR / GCA / PGA) from atmosenergy.com."""
from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timedelta
from urllib.parse import quote

import aiohttp

from homeassistant.helpers.storage import Store

from .gas_cost_parser import parse_gas_cost_pdf
from .regions import DEFAULT_REGION, GasCostSource

_LOGGER = logging.getLogger(__name__)

SITE = "https://www.atmosenergy.com"
MONTH_NAMES = [
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
]
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8,application/json",
}
TIMEOUT = aiohttp.ClientTimeout(total=30)
RETRY_INTERVAL = timedelta(days=1)  # How often to look for a month that isn't published yet


def cache_storage_key(region: str) -> str:
    """Mid-Tex keeps the file name it had before regions existed."""
    if region == DEFAULT_REGION:
        return "atmos_energy_gcr_cache"
    return f"atmos_energy_gas_cost_{region}"


def _month_key(year: int, month: int) -> str:
    return f"{year}-{month:02d}"


class GasCostFetcher:
    """Monthly residential gas cost for one region, cached by month ("YYYY-MM": $/Ccf).

    Each PDF holds 13-25 months of history, so one download also prices past months.
    """

    def __init__(self, hass, region: str, source: GasCostSource):
        self.hass = hass
        self.region = region
        self.source = source
        self._cache: dict[str, float] = {}
        self._last_fetch: datetime | None = None
        self._store = Store(hass, 1, cache_storage_key(region))

    async def async_load(self):
        """Load the cache from storage."""
        try:
            data = await self._store.async_load()
            if data:
                self._cache = data.get("rates", {})
                last_fetch = data.get("last_fetch")
                if last_fetch:
                    try:
                        self._last_fetch = datetime.fromisoformat(last_fetch)
                    except ValueError:
                        self._last_fetch = None
                _LOGGER.debug("Loaded %s gas cost cache: %d months", self.region, len(self._cache))
        except Exception as e:
            _LOGGER.warning("Failed to load %s gas cost cache: %s", self.region, e)

    def rate_for(self, year: int, month: int) -> float | None:
        """Rate for a month: that month's, else the latest earlier one cached, else None."""
        key = _month_key(year, month)
        earlier = [k for k in self._cache if k <= key]
        return self._cache[max(earlier)] if earlier else None

    async def get_current_rate(self) -> float | None:
        """This month's rate, fetching when it isn't cached yet (at most once a day)."""
        now = datetime.now()
        key = _month_key(now.year, now.month)
        if key in self._cache:
            return self._cache[key]

        if self._last_fetch is None or now - self._last_fetch > RETRY_INTERVAL:
            rates = await self._fetch_rates(now.year, now.month)
            self._last_fetch = now
            if rates:
                self._cache.update({_month_key(*ym): rate for ym, rate in rates.items()})
                _LOGGER.info(
                    "Fetched %s gas cost for %d months (latest %s)", self.region, len(rates), max(rates)
                )
            await self._save_cache()

        # The current month may not be published yet: use the latest one before it
        return self.rate_for(now.year, now.month)

    async def _fetch_rates(self, year: int, month: int) -> dict[tuple[int, int], float]:
        """Download and parse the newest gas-cost PDF; {} on any failure."""
        try:
            async with aiohttp.ClientSession(headers=HEADERS, timeout=TIMEOUT) as session:
                slugs = await self._find_document_slugs(session)
                if not slugs:
                    # Tariff page unavailable or changed: guess this and last month's slugs
                    prev = datetime(year, month, 1) - timedelta(days=1)
                    slugs = [
                        f"{self.source.document_prefix}-{MONTH_NAMES[m - 1]}-{y}"
                        for y, m in ((year, month), (prev.year, prev.month))
                    ]
                for slug in slugs:
                    content = await self._download_document(session, slug)
                    if content is None:
                        continue
                    rates = await self.hass.async_add_executor_job(
                        parse_gas_cost_pdf, content, self.source.column, self.source.page, self.source.occurrence
                    )
                    if rates:
                        return rates
                    _LOGGER.warning("No %s residential gas cost found in %s", self.region, slug)
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            _LOGGER.warning("Network error fetching %s gas cost: %s", self.region, e)
        except Exception:
            _LOGGER.exception("Unexpected error fetching %s gas cost", self.region)
        return {}

    async def _find_document_slugs(self, session: aiohttp.ClientSession) -> list[str]:
        """Gas-cost document slugs linked from the region's tariff page, newest first."""
        data = await self._get_json(session, f"{SITE}/page-data/{self.source.tariff_page}/page-data.json")
        if data is None:
            return []
        pattern = re.compile(
            rf"/document/({re.escape(self.source.document_prefix)}-([a-z]+)-(\d{{4}})(?:-\d+)?)\b"
        )
        # Links sit inside embedded HTML, where slashes are often escaped as "\/"
        text = json.dumps(data).replace("\\\\/", "/").replace("\\/", "/")
        found = {}
        for match in pattern.finditer(text):
            slug, month_name, year = match.groups()
            if month_name in MONTH_NAMES:
                found[slug] = (int(year), MONTH_NAMES.index(month_name) + 1)
        return sorted(found, key=found.get, reverse=True)

    async def _download_document(self, session: aiohttp.ClientSession, slug: str) -> bytes | None:
        """A document page's PDF: the page-data "redirect" field points at the static file."""
        data = await self._get_json(session, f"{SITE}/page-data/document/{slug}/page-data.json")
        if data is None:
            return None
        pdf_path = (data.get("result") or {}).get("pageContext", {}).get("redirect")
        if not pdf_path:
            match = re.search(r'"redirect"\s*:\s*"([^"]+\.pdf)"', json.dumps(data))
            pdf_path = match.group(1) if match else None
        if not pdf_path:
            _LOGGER.debug("No PDF link in page-data for %s", slug)
            return None

        url = f"{SITE}{quote(pdf_path)}" if pdf_path.startswith("/") else pdf_path
        async with session.get(url, allow_redirects=True) as response:
            if response.status != 200:
                _LOGGER.debug("HTTP %d for %s", response.status, url)
                return None
            content = await response.read()
        if not content.startswith(b"%PDF"):
            _LOGGER.debug("%s is not a PDF", url)
            return None
        return content

    async def _get_json(self, session: aiohttp.ClientSession, url: str) -> dict | None:
        async with session.get(url) as response:
            if response.status != 200:
                _LOGGER.debug("HTTP %d for %s", response.status, url)
                return None
            return await response.json(content_type=None)

    async def _save_cache(self):
        try:
            await self._store.async_save({
                "rates": self._cache,
                "last_fetch": self._last_fetch.isoformat() if self._last_fetch else None,
            })
        except Exception as e:
            _LOGGER.warning("Failed to save %s gas cost cache: %s", self.region, e)
