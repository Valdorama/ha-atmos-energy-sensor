import logging
import asyncio
from datetime import datetime, timedelta
from typing import Optional
from io import BytesIO
import re
import json

import aiohttp

from homeassistant.helpers.storage import Store

_LOGGER = logging.getLogger(__name__)


class GCRRateFetcher:
    """Fetches and caches GCR rates from Atmos PDFs."""
    
    BASE_URL = "https://www.atmosenergy.com/document/mid-tex-gcr-rates-{month}-{year}"
    PAGE_DATA_URL = "https://www.atmosenergy.com/page-data/document/mid-tex-gcr-rates-{month}-{year}/page-data.json"
    
    MONTH_NAMES = {
        1: "january", 2: "february", 3: "march", 4: "april",
        5: "may", 6: "june", 7: "july", 8: "august",
        9: "september", 10: "october", 11: "november", 12: "december",
    }
    
    # Common headers to avoid being blocked
    HEADERS = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/json',
    }
    
    def __init__(self, hass):
        """Initialize the fetcher."""
        self.hass = hass
        self._cache = {}
        self._last_fetch = None
        self._fetch_interval = timedelta(days=7)  # Check weekly
        self._store = Store(hass, 1, "atmos_energy_gcr_cache")
    
    async def async_load(self):
        """Load cache from storage."""
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
                
                _LOGGER.debug("Loaded GCR cache: %d rates", len(self._cache))
        except Exception as e:
            _LOGGER.warning("Failed to load GCR cache: %s", e)
    
    async def get_current_rate(self) -> Optional[float]:
        """Get the current GCR rate.
        
        Returns cached value if fresh, otherwise fetches new.
        """
        now = datetime.now()
        cache_key = f"{now.year}-{now.month:02d}"
        
        # Check cache first
        if cache_key in self._cache:
            _LOGGER.debug("Using cached GCR rate for %s: $%.4f", cache_key, self._cache[cache_key])
            return self._cache[cache_key]
        
        # Determine if we should fetch
        should_fetch = (
            self._last_fetch is None or 
            now - self._last_fetch > self._fetch_interval
        )
        
        if should_fetch:
            rate = await self._fetch_rate(now.year, now.month)
            
            if rate is None:
                # Try previous month (current might not be published yet)
                prev_month_dt = now.replace(day=1) - timedelta(days=1)
                rate = await self._fetch_rate(prev_month_dt.year, prev_month_dt.month)
                if rate:
                    _LOGGER.info("Current month GCR not available, using previous month")
            
            if rate is not None:
                self._cache[cache_key] = rate
                self._last_fetch = now
                await self._save_cache()
                return rate
        
        # Return cached value or None
        return self._cache.get(cache_key)
    
    async def _fetch_rate(self, year: int, month: int) -> Optional[float]:
        """Fetch GCR rate from Atmos PDF."""
        month_name = self.MONTH_NAMES.get(month, "").lower()
        url = self.BASE_URL.format(month=month_name, year=year)
        
        _LOGGER.info("Fetching GCR rate from: %s", url)
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url, 
                    headers=self.HEADERS,
                    timeout=aiohttp.ClientTimeout(total=30),
                    allow_redirects=True
                ) as response:
                    
                    if response.status != 200:
                        _LOGGER.warning("Failed to fetch GCR: HTTP %d", response.status)
                        return None
                    
                    content = await response.read()
                    
                    # 1. Check if it's already a PDF (Direct redirect fallback)
                    if content.startswith(b'%PDF'):
                        return await self._parse_gcr_pdf(content)
                    
                    # 2. If it's HTML, it's a Gatsby landing page. 
                    # Fetch page-data.json to get the real dynamic link.
                    _LOGGER.debug("Received HTML landing page, searching page-data.json for PDF link...")
                    
                    data_url = self.PAGE_DATA_URL.format(month=month_name, year=year)
                    async with session.get(
                        data_url,
                        headers=self.HEADERS,
                        timeout=aiohttp.ClientTimeout(total=30)
                    ) as data_response:
                        if data_response.status == 200:
                            json_data = await data_response.json()
                            
                            # Extract path from Gatsby structure: result.pageContext.redirect
                            pdf_path = None
                            try:
                                pdf_path = json_data.get("result", {}).get("pageContext", {}).get("redirect")
                            except Exception:
                                pass
                            
                            if not pdf_path:
                                # Fallback regex search in the JSON string
                                data_str = json.dumps(json_data)
                                match = re.search(r'"redirect"\s*:\s*"([^"]+\.pdf)"', data_str)
                                if match:
                                    pdf_path = match.group(1)
                            
                            if pdf_path:
                                # Construct full URL
                                if pdf_path.startswith('/'):
                                    pdf_url = f"https://www.atmosenergy.com{pdf_path}"
                                else:
                                    pdf_url = pdf_path
                                
                                # aiohttp handles spaces in URLs automatically, but let's be safe
                                pdf_url = pdf_url.replace(" ", "%20")
                                
                                _LOGGER.info("Found dynamic PDF link: %s", pdf_url)
                                async with session.get(
                                    pdf_url,
                                    headers=self.HEADERS,
                                    timeout=aiohttp.ClientTimeout(total=30),
                                    allow_redirects=True
                                ) as pdf_response:
                                    if pdf_response.status == 200:
                                        pdf_content = await pdf_response.read()
                                        if pdf_content.startswith(b'%PDF'):
                                            return await self._parse_gcr_pdf(pdf_content)
                                        else:
                                            _LOGGER.error("Link found in JSON did not lead to a PDF file")
                            else:
                                _LOGGER.warning("Could not find dynamic PDF link in page-data.json")
                        else:
                            _LOGGER.warning("Failed to fetch Gatsby page-data: HTTP %d", data_response.status)
                    
                    return None
                    
        except asyncio.TimeoutError:
            _LOGGER.error("Timeout fetching GCR from %s", url)
            return None
        except aiohttp.ClientError as e:
            _LOGGER.error("Network error fetching GCR: %s", e)
            return None
        except Exception as e:
            _LOGGER.exception("Unexpected error fetching GCR rate: %s", e)
            return None

    async def _parse_gcr_pdf(self, content: bytes) -> Optional[float]:
        """Parse GCR rate from PDF content."""
        
        def _parse():
            try:
                import pdfplumber
                with pdfplumber.open(BytesIO(content)) as pdf:
                    if len(pdf.pages) == 0:
                        return None
                    
                    page = pdf.pages[0]
                    
                    # Try table extraction first
                    tables = page.extract_tables()
                    if tables:
                        for table in tables:
                            rate = self._extract_rate_from_table(table)
                            if rate:
                                return rate
                    
                    # Fall back to text parsing
                    text = page.extract_text()
                    if text:
                        return self._extract_rate_from_text(text)
                    
                    return None
                    
            except Exception as e:
                _LOGGER.error("Error parsing GCR PDF: %s", e)
                return None
        
        # Run in executor (pdfplumber is synchronous)
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _parse)
    
    def _extract_rate_from_table(self, table) -> Optional[float]:
        """Extract residential rate from PDF table."""
        for row in table:
            if not row:
                continue
            
            # Filter out None and normalize
            clean_row = [str(cell).lower().strip() if cell else "" for cell in row]
            
            # Check if row indicates residential
            if any(keyword in clean_row[0] for keyword in ['residential', 'r-1', 'rate r', 'class r']):
                # Look for the rate in subsequent columns
                for cell in clean_row[1:]:
                    if not cell:
                        continue
                        
                    # Find number in rate string (e.g. "$1.1234")
                    match = re.search(r'(\d+\.\d{2,})', cell)
                    if match:
                        try:
                            rate = float(match.group(1))
                            # Sanity check (GCR should be between $0.20 and $5.00/CCF)
                            if 0.2 < rate < 5.0:
                                return rate
                        except ValueError:
                            continue
        
        return None
    
    def _extract_rate_from_text(self, text: str) -> Optional[float]:
        """Extract residential rate from PDF text."""
        lines = text.split('\n')
        
        for i, line in enumerate(lines):
            line_lower = line.lower()
            
            # Look for residential rate indicators
            if any(keyword in line_lower for keyword in ['residential', 'r-1', 'rate r']):
                # Look for number in this line or next few lines
                search_text = "\n".join(lines[i:i+3])
                
                # Find numbers that look like rates (at least 2 decimal places)
                matches = re.findall(r'(\d+\.\d{2,})', search_text)
                
                for match in matches:
                    try:
                        rate = float(match)
                        # Sanity check
                        if 0.2 < rate < 5.0:
                            return rate
                    except ValueError:
                        continue
        
        return None
    
    async def _save_cache(self):
        """Save cache to file."""
        try:
            await self._store.async_save({
                "rates": self._cache,
                "last_fetch": self._last_fetch.isoformat() if self._last_fetch else None,
            })
        except Exception as e:
            _LOGGER.warning("Failed to save GCR cache: %s", e)
