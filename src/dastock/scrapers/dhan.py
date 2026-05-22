"""Dhan API scraper — stock master (public CSV) + EOD prices (SDK).

Two distinct modes:
  - master: fetch api-scrip-master-detailed.csv. Used by bootstrap_identity.
  - eod: per-security historical daily OHLCV via dhanhq SDK.

Uses the official dhanhq Python SDK for authenticated calls.
CSV scrape is unauthenticated (public file).
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterator
from datetime import date, timedelta
from typing import Any

from pydantic import BaseModel

from dastock.config import Settings
from dastock.models.dhan import (
    DhanCandle,
    DhanHistoricalResponse,
    DhanSecurityMasterRow,
)
from dastock.scrapers.base import BaseScraper
from dastock.scrapers.exceptions import TokenExpiredError

# Lazy import of dhanhq SDK so that unit tests that don't need it
# don't require a real token to be present
_dhanhq_available = True
try:
    from dhanhq import DhanContext  # noqa: PLC0415
    from dhanhq import dhanhq as DhanHQ  # noqa: N812,PLC0415
except ImportError:
    _dhanhq_available = False


class DhanScraper(BaseScraper):
    """Scraper for Dhan broker APIs."""

    SOURCE_NAME = "dhan"
    SECURITY_MASTER_URL = (
        "https://images.dhan.co/api-data/api-scrip-master-detailed.csv"
    )

    def __init__(self, settings: Settings | None = None) -> None:
        super().__init__(settings=settings)
        self._sdk: Any = None  # lazily initialised on first EOD call

    def _get_sdk(self) -> Any:
        """Return initialised dhanhq SDK client. Fail fast if no token."""
        if self._sdk is not None:
            return self._sdk
        if not _dhanhq_available:
            raise ImportError("dhanhq package not installed. Run: uv add dhanhq")
        token = self._settings.dhan_access_token
        client_id = self._settings.dhan_client_id
        if not token or not client_id:
            raise TokenExpiredError(
                "DHAN_ACCESS_TOKEN and DHAN_CLIENT_ID must be set in .env"
            )
        ctx = DhanContext(client_id, token.get_secret_value())
        self._sdk = DhanHQ(ctx)
        return self._sdk

    # ─── Mode 1: security master CSV (public, no auth) ──────────────────────

    def fetch_security_master(self) -> str:
        """Download the detailed scrip master CSV. Returns raw text."""
        resp = self._get(self.SECURITY_MASTER_URL)
        return resp.text

    def parse_security_master(self, csv_text: str) -> Iterator[dict[str, Any]]:
        """Stream genuine equity stock rows.

        Filters:
          - INSTRUMENT == "EQUITY"
          - INSTRUMENT_TYPE == "ES" (Equity Stock, excludes bonds/ETFs/MFs)
          - ISIN present and not "NA"
        """
        reader = csv.DictReader(io.StringIO(csv_text))
        for row in reader:
            if row.get("INSTRUMENT") != "EQUITY":
                continue
            if (row.get("INSTRUMENT_TYPE") or "").strip().upper() != "ES":
                continue
            isin = (row.get("ISIN") or "").strip().upper()
            if not isin or isin == "NA":
                continue
            yield row

    def transform_security(self, record: dict[str, Any]) -> DhanSecurityMasterRow:
        return DhanSecurityMasterRow.model_validate(record)

    # ─── Mode 2: EOD historical OHLCV (SDK) ─────────────────────────────────

    def fetch_historical(
        self,
        *,
        security_id: str,
        exchange: str,
        from_date: date,
        to_date: date,
    ) -> dict[str, Any] | None:
        """Fetch daily OHLCV via dhanhq SDK.

        Returns None when Dhan has no data for this security/date range.
        The SDK raises on auth failures; those propagate as TokenExpiredError
        from _get_sdk().
        """
        dhan = self._get_sdk()
        # SDK constants: dhan.NSE = "NSE_EQ", dhan.BSE = "BSE_EQ"
        segment = dhan.NSE if exchange.upper() == "NSE" else dhan.BSE

        result = dhan.historical_daily_data(
            security_id=str(security_id),
            exchange_segment=segment,
            instrument_type="EQUITY",
            from_date=from_date.isoformat(),
            to_date=to_date.isoformat(),
        )

        # SDK returns dict with 'status' key; 'failure' means no data
        if isinstance(result, dict) and result.get("status") == "failure":
            return None
        # SDK returns DataFrame or list of records; normalise to parallel-array dict
        return self._normalise_sdk_response(result)

    @staticmethod
    def _normalise_sdk_response(result: Any) -> dict[str, Any] | None:
        """Convert SDK response to the same parallel-array dict shape our
        DhanHistoricalResponse model expects."""
        try:
            import pandas as pd  # dhanhq pulls in pandas
            if isinstance(result, pd.DataFrame):
                if result.empty:
                    return None
                return {
                    "open": result["open"].tolist(),
                    "high": result["high"].tolist(),
                    "low": result["low"].tolist(),
                    "close": result["close"].tolist(),
                    "volume": result["volume"].tolist(),
                    "timestamp": result["start_Time"].tolist()
                    if "start_Time" in result.columns
                    else result.index.astype("int64").tolist(),
                }
        except Exception:
            pass

        if isinstance(result, dict):
            # Already parallel arrays (direct REST shape or SDK passthrough)
            if all(k in result for k in ("open", "close", "timestamp")):
                return result
            # SDK may wrap in {'data': {...}}
            inner = result.get("data", result)
            if isinstance(inner, dict) and "close" in inner:
                return inner

        return None

    def transform_historical(self, raw: dict[str, Any] | None) -> DhanHistoricalResponse | None:
        if raw is None:
            return None
        return DhanHistoricalResponse.model_validate(raw)

    def fetch_eod_candle(
        self,
        *,
        security_id: str,
        exchange: str,
        trade_date: date | None = None,
    ) -> DhanCandle | None:
        """Fetch a single day's OHLCV. Returns the most recent candle <= trade_date."""
        target = trade_date or date.today()
        raw = self.fetch_historical(
            security_id=security_id,
            exchange=exchange,
            from_date=target - timedelta(days=5),
            to_date=target,
        )
        model = self.transform_historical(raw)
        if model is None:
            return None
        candles = model.iter_candles()
        if not candles:
            return None
        candidates = [c for c in candles if c.trade_date <= target]
        return candidates[-1] if candidates else None

    # ─── Abstract methods (default = security master bootstrap flow) ─────────

    def fetch_raw(self) -> str:
        return self.fetch_security_master()

    def parse(self, raw: str) -> Iterator[dict[str, Any]]:
        yield from self.parse_security_master(raw)

    def transform(self, record: dict[str, Any]) -> BaseModel:
        return self.transform_security(record)

    def external_id_of(self, record: dict[str, Any]) -> str:
        return str(record.get("SECURITY_ID", "")).strip()
