"""Pydantic models for Dhan API responses.

Two distinct data shapes:
  1. Security master CSV — public, no auth. One row per instrument across NSE/BSE.
  2. Historical OHLCV — authenticated. Returns parallel arrays of open/high/low/close/volume/timestamp.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# ─── Security master (detailed CSV) ──────────────────────────────────────────


class DhanSecurityMasterRow(BaseModel):
    """One row from api-scrip-master-detailed.csv (EQUITY rows only).

    Column order in source CSV:
      EXCH_ID,SEGMENT,SECURITY_ID,ISIN,INSTRUMENT,UNDERLYING_SECURITY_ID,
      UNDERLYING_SYMBOL,SYMBOL_NAME,DISPLAY_NAME,INSTRUMENT_TYPE,SERIES,LOT_SIZE,...

    We only model the fields we use.
    """

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    exchange: str = Field(alias="EXCH_ID")
    security_id: str = Field(alias="SECURITY_ID")
    isin: str | None = Field(default=None, alias="ISIN")
    instrument: str = Field(alias="INSTRUMENT")  # "EQUITY" after coarse filter
    instrument_type: str | None = Field(default=None, alias="INSTRUMENT_TYPE")
    # "ES"=Equity Stock, "DEB"/"DBT"=bonds, "MF"=listed mutual fund, "ETF", "CB"=convertible, etc.
    trading_symbol: str = Field(alias="UNDERLYING_SYMBOL")  # e.g. "RELIANCE", "500325"
    symbol_name: str = Field(alias="SYMBOL_NAME")  # canonical full name
    display_name: str | None = Field(default=None, alias="DISPLAY_NAME")
    series: str | None = Field(default=None, alias="SERIES")

    @field_validator("isin", mode="before")
    @classmethod
    def _clean_isin(cls, v: str | None) -> str | None:
        """Dhan stores 'NA' or '' for missing ISINs; coerce to None."""
        if v is None:
            return None
        s = str(v).strip()
        if not s or s.upper() == "NA":
            return None
        return s

    @field_validator("exchange", mode="before")
    @classmethod
    def _normalize_exchange(cls, v: str) -> str:
        return str(v).strip().upper()

    @field_validator("security_id", "trading_symbol", mode="before")
    @classmethod
    def _strip(cls, v: str | int) -> str:
        return str(v).strip()


# ─── Historical OHLCV (authenticated) ────────────────────────────────────────


class DhanHistoricalResponse(BaseModel):
    """Response from /v2/charts/historical.

    Returns parallel arrays — index i across all arrays = one trading day.
    """

    model_config = ConfigDict(extra="ignore")

    open: list[Decimal]
    high: list[Decimal]
    low: list[Decimal]
    close: list[Decimal]
    volume: list[Decimal]
    timestamp: list[float]  # Unix epoch seconds

    @model_validator(mode="after")
    def _arrays_same_length(self) -> DhanHistoricalResponse:
        lens = {
            "open": len(self.open),
            "high": len(self.high),
            "low": len(self.low),
            "close": len(self.close),
            "volume": len(self.volume),
            "timestamp": len(self.timestamp),
        }
        if len(set(lens.values())) > 1:
            raise ValueError(f"Dhan response arrays have mismatched lengths: {lens}")
        return self

    def iter_candles(self) -> list[DhanCandle]:
        """Convert parallel arrays into a list of one-candle-per-day dicts."""
        return [
            DhanCandle(
                trade_date=datetime.fromtimestamp(self.timestamp[i], tz=UTC).date(),
                open=self.open[i],
                high=self.high[i],
                low=self.low[i],
                close=self.close[i],
                volume=int(self.volume[i]) if self.volume[i] >= 0 else 0,
            )
            for i in range(len(self.timestamp))
        ]


class DhanCandle(BaseModel):
    """One trading day's OHLCV. Derived from DhanHistoricalResponse.iter_candles()."""

    model_config = ConfigDict(extra="forbid")

    trade_date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int

    @field_validator("close", "open", "high", "low", mode="before")
    @classmethod
    def _positive_price(cls, v: Decimal | str | float) -> Decimal:
        d = Decimal(str(v))
        if d <= 0:
            raise ValueError(f"Price must be positive, got {d}")
        return d

    @model_validator(mode="after")
    def _ohlc_consistency(self) -> DhanCandle:
        """Validate that low <= open/close <= high."""
        if self.low > self.open or self.low > self.close:
            raise ValueError(
                f"OHLC inconsistency: low={self.low} > open={self.open} or close={self.close}"
            )
        if self.high < self.open or self.high < self.close:
            raise ValueError(
                f"OHLC inconsistency: high={self.high} < open={self.open} or close={self.close}"
            )
        return self
