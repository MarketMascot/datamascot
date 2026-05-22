"""Pydantic models for Scan360 sector/industry API response.

Scan360 GET /api/industries returns a dict keyed by industry name,
each value containing a list of stocks with symbol, name, and financials.

Response shape:
  {
    "Information Technology": {
      "stocks": [
        {"symbol": "TCS", "name": "Tata Consultancy Services", "marketCap": ..., ...}
      ]
    },
    ...
  }
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, field_validator


class Scan360Stock(BaseModel):
    """One stock entry inside a Scan360 industry bucket."""

    model_config = ConfigDict(extra="ignore")

    symbol: str
    name: str | None = None
    market_cap: float | None = None

    @field_validator("symbol", mode="before")
    @classmethod
    def _strip_symbol(cls, v: object) -> str:
        return str(v).strip().upper()

    @field_validator("name", mode="before")
    @classmethod
    def _strip_name(cls, v: object) -> str | None:
        if not v:
            return None
        s = str(v).strip()
        return s if s else None


class Scan360IndustryRecord(BaseModel):
    """Flattened record: one stock → its industry assignment."""

    model_config = ConfigDict(extra="ignore")

    nse_symbol: str
    industry: str
