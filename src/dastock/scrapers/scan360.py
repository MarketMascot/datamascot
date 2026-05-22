"""Scan360 scraper — sector/industry classification for NSE stocks.

Fetches GET /api/industries (single call, returns full market data).
Maps each stock symbol → industry name, then updates stocks.industry
in Supabase via IdentityResolver.

A stock can appear under multiple industries in the raw response;
we store the first occurrence (the primary industry bucket).
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from pydantic import BaseModel

from dastock.models.scan360 import Scan360IndustryRecord, Scan360Stock
from dastock.scrapers.base import BaseScraper

_API_URL = "https://scan360.in/api/industries"
_HEADERS = {
    "Accept": "application/json",
    "Referer": "https://scan360.in/",
}


class Scan360Scraper(BaseScraper):
    """Scraper for Scan360 sector/industry data."""

    SOURCE_NAME = "scan360"

    # ─── Fetch ───────────────────────────────────────────────────────────────

    def fetch_raw(self) -> dict[str, Any]:
        """Fetch all industries. Returns the full JSON response dict."""
        resp = self._get(_API_URL, headers=_HEADERS)
        data = resp.json()
        if not isinstance(data, dict):
            return {}
        return data

    # ─── Parse ───────────────────────────────────────────────────────────────

    def parse(self, raw: dict[str, Any]) -> Iterator[dict[str, Any]]:
        """Yield {nse_symbol, industry} dicts — one per unique stock (first industry wins)."""
        seen: set[str] = set()
        for industry_name, bucket in raw.items():
            if not isinstance(bucket, dict):
                continue
            stocks = bucket.get("stocks") or []
            if not isinstance(stocks, list):
                continue
            for stock_raw in stocks:
                if not isinstance(stock_raw, dict):
                    continue
                symbol = str(stock_raw.get("symbol", "")).strip().upper()
                if not symbol or symbol in seen:
                    continue
                seen.add(symbol)
                yield {
                    "nse_symbol": symbol,
                    "industry": industry_name.strip(),
                }

    # ─── Transform ───────────────────────────────────────────────────────────

    def transform(self, record: dict[str, Any]) -> BaseModel:
        return Scan360IndustryRecord.model_validate(record)

    def external_id_of(self, record: dict[str, Any]) -> str:
        return str(record.get("nse_symbol", ""))

    # ─── Convenience: parse into a symbol → industry lookup dict ─────────────

    def build_industry_map(self, raw: dict[str, Any]) -> dict[str, str]:
        """Return {nse_symbol: industry} from the raw API response."""
        return {r["nse_symbol"]: r["industry"] for r in self.parse(raw)}
