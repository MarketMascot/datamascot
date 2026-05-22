"""Rupeevest scraper — MF metadata (weekly) + stock holdings (monthly).

Two modes:
  - metadata: POST /functionalities/asset_class_section → mf_metrics table
              Also bridges schemecode → mutual_funds.rupeevest_schemecode via name match
  - holdings: GET /functionalities/portfolio_holdings?schemecode=X → mf_stock_holdings
  - fincode:  GET /mf_stock_portfolio/get_search_data_stock → stocks.rupeevest_fincode

Note: Rupeevest has no ISIN on the fund metadata endpoint.
Bridge strategy: match Rupeevest s_name against mutual_funds.scheme_name
(populated from mfapi.in during bootstrap). Fuzzy match using pg_trgm or
simple exact-after-normalise.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from pydantic import BaseModel

from dastock.models.rupeevest import RupeevFincodeEntry, RupeevFundMetadata, RupeevHolding
from dastock.scrapers.base import BaseScraper

_METADATA_URL = "https://www.rupeevest.com/functionalities/asset_class_section"
_HOLDINGS_URL = "https://www.rupeevest.com/functionalities/portfolio_holdings"
_FINCODE_URL = "https://www.rupeevest.com/mf_stock_portfolio/get_search_data_stock"

# All scheme type IDs to include (from original code — covers equity, debt, hybrid, etc.)
_ALL_SCHEME_IDS = list(range(6, 50)) + [5]
_POST_DATA = (
    "es%5B%5D=5&"
    + "&".join(f"selected_schemes%5B%5D={i}" for i in range(6, 50))
    + "&selected_rating%5B%5D=1&selected_rating%5B%5D=2&selected_rating%5B%5D=3"
    "&selected_rating%5B%5D=4&selected_rating%5B%5D=5&selected_rating%5B%5D=Unrated"
    "&selected_amc%5B%5D=all&selected_manager%5B%5D=all&selected_index%5B%5D=all"
    "&selected_fund_type%5B%5D=1&selected_from_date=0&selected_to_date=0&condn_type=asset"
)
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "X-Requested-With": "XMLHttpRequest",
    "Origin": "https://www.rupeevest.com",
    "Referer": "https://www.rupeevest.com/mutual-fund-screener",
}


class RupeevScraper(BaseScraper):
    """Scraper for Rupeevest MF data (unofficial API)."""

    SOURCE_NAME = "rupeevest"

    # ─── Mode: metadata (fund list) ──────────────────────────────────────────

    def fetch_fund_list(self) -> list[dict[str, Any]]:
        """Fetch all funds from the screener endpoint. Returns raw schemedata[]."""
        resp = self._post(_METADATA_URL, content=_POST_DATA, headers=_HEADERS)
        data = resp.json()
        # Response key varies — try schemedata first, then fallbacks
        for key in ("schemedata", "data", "funds", "results"):
            if key in data and isinstance(data[key], list):
                return data[key]
        return []

    def parse_fund_list(self, raw: list[dict[str, Any]]) -> Iterator[dict[str, Any]]:
        yield from raw

    def transform_metadata(self, record: dict[str, Any]) -> RupeevFundMetadata:
        return RupeevFundMetadata.model_validate(record)

    # ─── Mode: holdings (per-fund stock breakdown) ───────────────────────────

    def fetch_holdings(self, schemecode: int | str) -> list[dict[str, Any]]:
        """Fetch portfolio holdings for one fund. Returns [] if not available."""
        resp = self._get(
            _HOLDINGS_URL,
            params={"schemecode": str(schemecode)},
            headers={k: v for k, v in _HEADERS.items() if k != "Content-Type"},
        )
        data = resp.json()
        if isinstance(data, dict):
            return data.get("portfolio_holdings") or []
        if isinstance(data, list):
            return data
        return []

    def parse_holdings(self, raw: list[dict[str, Any]]) -> Iterator[dict[str, Any]]:
        yield from raw

    def transform_holding(self, record: dict[str, Any]) -> RupeevHolding:
        return RupeevHolding.model_validate(record)

    # ─── Mode: fincode map (stock identity bridge) ───────────────────────────

    def fetch_fincode_map(self) -> list[dict[str, Any]]:
        """Fetch the full stock fincode list. Returns stock_data_search[]."""
        resp = self._get(
            _FINCODE_URL,
            headers={k: v for k, v in _HEADERS.items() if k != "Content-Type"},
        )
        data = resp.json()
        if isinstance(data, dict):
            return data.get("stock_data_search") or []
        return []

    def transform_fincode(self, record: dict[str, Any]) -> RupeevFincodeEntry:
        return RupeevFincodeEntry.model_validate(record)

    # ─── Abstract methods (default = fund metadata mode) ─────────────────────

    def fetch_raw(self) -> Any:
        return self.fetch_fund_list()

    def parse(self, raw: Any) -> Iterator[dict[str, Any]]:
        yield from self.parse_fund_list(raw)

    def transform(self, record: dict[str, Any]) -> BaseModel:
        return self.transform_metadata(record)

    def external_id_of(self, record: dict[str, Any]) -> str:
        return str(record.get("schemecode", ""))
