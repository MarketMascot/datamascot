"""mfapi.in scraper — free public API for Indian mutual fund NAVs.

Two modes:
  - bootstrap: fetch full /mf scheme list. Used to seed mutual_funds bible.
  - nav: fetch /mf/{code}/latest for each active fund. Daily NAV refresh.

API characteristics:
  - No auth required
  - No documented rate limit; we self-impose 5 RPS by default
  - List endpoint returns camelCase; per-scheme endpoint returns snake_case
  - Dates in DD-MM-YYYY format
  - Updates 6× daily per their docs
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from pydantic import BaseModel

from dastock.models.mfapi import (
    MfapiSchemeLatest,
    MfapiSchemeListEntry,
)
from dastock.scrapers.base import BaseScraper


class MfapiScraper(BaseScraper):
    """Scraper for https://api.mfapi.in/mf endpoints."""

    SOURCE_NAME = "mfapi"
    BASE_URL = "https://api.mfapi.in/mf"

    # ─── Mode 1: bootstrap (full scheme master list) ────────────────────────

    def fetch_scheme_list(self) -> list[dict[str, Any]]:
        """Fetch the complete list of all schemes (camelCase JSON array)."""
        resp = self._get(self.BASE_URL)
        return resp.json()

    def parse_scheme_list(
        self, raw: list[dict[str, Any]]
    ) -> Iterator[dict[str, Any]]:
        """Yield one dict per scheme. The runner streams these to transform()."""
        yield from raw

    def transform_scheme(self, record: dict[str, Any]) -> MfapiSchemeListEntry:
        """Validate one scheme list entry."""
        return MfapiSchemeListEntry.model_validate(record)

    # ─── Mode 2: nav (per-scheme latest NAV) ────────────────────────────────

    def fetch_latest_for(self, amfi_code: str | int) -> dict[str, Any]:
        """Fetch /mf/{code}/latest. Returns the raw JSON dict."""
        resp = self._get(f"{self.BASE_URL}/{amfi_code}/latest")
        return resp.json()

    def transform_latest(self, record: dict[str, Any]) -> MfapiSchemeLatest:
        """Validate the per-scheme latest response."""
        return MfapiSchemeLatest.model_validate(record)

    # ─── Abstract methods (default mode = scheme list bootstrap) ────────────
    # The pipeline runner uses these for generic orchestration. We default to
    # the scheme list path. The NAV mode uses fetch_latest_for / transform_latest
    # directly inside scripts/run_mfapi.py because it loops over our own
    # mutual_funds list, not over a single source-returned blob.

    def fetch_raw(self) -> Any:
        return self.fetch_scheme_list()

    def parse(self, raw: Any) -> Iterator[dict[str, Any]]:
        yield from self.parse_scheme_list(raw)

    def transform(self, record: dict[str, Any]) -> BaseModel:
        return self.transform_scheme(record)

    def external_id_of(self, record: dict[str, Any]) -> str:
        return str(record.get("schemeCode") or record.get("scheme_code", ""))
