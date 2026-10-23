"""Polite SEC EDGAR HTTP client: declared User-Agent, rate limiting, disk cache, retries.

SEC fair-access policy (verify current text at sec.gov): identify yourself via User-Agent and
stay under ~10 requests/second. We throttle to ~8/s and cache aggressively.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

import httpx

from agentlens_tools.edgar_parse import accession_nodash, normalize_cik

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
DOC_URL = "https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc}/{doc}"

DAY = 86_400


class SecClient:
    def __init__(
        self,
        user_agent: str | None = None,
        cache_dir: str | Path | None = None,
        min_interval_s: float = 0.125,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        ua = user_agent or os.environ.get("SEC_USER_AGENT")
        if not ua:
            raise RuntimeError(
                "SEC_USER_AGENT is required (e.g. 'Your Name you@example.com'): SEC fair-access policy"
            )
        self._client = client or httpx.AsyncClient(headers={"User-Agent": ua}, timeout=30.0)
        self._client.headers["User-Agent"] = ua
        self._cache = Path(cache_dir or os.environ.get("EDGAR_CACHE_DIR", ".cache/edgar"))
        self._cache.mkdir(parents=True, exist_ok=True)
        self._min_interval = min_interval_s
        self._lock = asyncio.Lock()
        self._last = 0.0

    async def _throttle(self) -> None:
        async with self._lock:
            wait = self._last + self._min_interval - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._last = time.monotonic()

    async def fetch(self, url: str, ttl_s: int) -> bytes:
        path = self._cache / hashlib.sha256(url.encode()).hexdigest()
        if path.exists() and time.time() - path.stat().st_mtime < ttl_s:
            return path.read_bytes()
        last_exc: Exception | None = None
        for attempt in range(3):
            await self._throttle()
            try:
                resp = await self._client.get(url)
                if resp.status_code in (429, 500, 502, 503, 504):
                    raise httpx.HTTPStatusError("retryable", request=resp.request, response=resp)
                resp.raise_for_status()
                path.write_bytes(resp.content)
                return resp.content
            except (httpx.TransportError, httpx.HTTPStatusError) as e:
                last_exc = e
                await asyncio.sleep(0.5 * 2**attempt)
        raise RuntimeError(f"SEC request failed after retries: {url}: {last_exc}")

    async def get_json(self, url: str, ttl_s: int = DAY) -> Any:
        return json.loads(await self.fetch(url, ttl_s))

    # --- typed helpers -------------------------------------------------------------
    async def tickers(self) -> dict[str, Any]:
        return await self.get_json(TICKERS_URL, 7 * DAY)

    async def submissions(self, cik: str | int) -> dict[str, Any]:
        return await self.get_json(SUBMISSIONS_URL.format(cik=normalize_cik(cik)), DAY)

    async def company_facts(self, cik: str | int) -> dict[str, Any]:
        return await self.get_json(FACTS_URL.format(cik=normalize_cik(cik)), DAY)

    async def filing_html(self, cik: str | int, accession: str, primary_document: str) -> str:
        url = DOC_URL.format(
            cik_int=int(normalize_cik(cik)), acc=accession_nodash(accession), doc=primary_document
        )
        # Filed documents are immutable: cache for a year.
        return (await self.fetch(url, 365 * DAY)).decode("utf-8", errors="replace")

    async def aclose(self) -> None:
        await self._client.aclose()
