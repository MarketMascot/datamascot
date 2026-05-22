"""Pydantic models for Rupeevest API responses.

Three data shapes:
  1. Fund metadata — POST /functionalities/asset_class_section
     Returns schemedata[] with returns, AUM, expense ratio, NAV, etc.
  2. Fund holdings — GET /functionalities/portfolio_holdings?schemecode=X
     Returns portfolio_holdings[] with stock name, weight, value.
  3. Stock fincode map — GET /mf_stock_portfolio/get_search_data_stock
     Returns stock_data_search[] with fincode + stock_search (BSE+NSE identifiers).
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, field_validator


def _to_decimal_or_none(v: object) -> Decimal | None:
    """Coerce various nullish representations to None, rest to Decimal."""
    if v is None:
        return None
    s = str(v).strip().replace(",", "").replace("%", "")
    if s in ("", "-", "n/a", "na", "null", "none", "--", "0") or s.lower() in ("na", "null"):
        return None
    try:
        return Decimal(s)
    except Exception:
        return None


# ─── Fund metadata ───────────────────────────────────────────────────────────


class RupeevFundMetadata(BaseModel):
    """One fund record from /functionalities/asset_class_section → schemedata[]."""

    model_config = ConfigDict(extra="ignore")

    schemecode: int
    s_name: str                      # short name (used for name-matching with mfapi)
    s_name1: str | None = None       # longer display name
    fund_house: str | None = None
    fund_manager: str | None = None
    classification: str | None = None
    type_code: int | None = None
    rupeevest_rating: str | None = None
    navrs: Decimal | None = None       # current NAV
    aumtotal: Decimal | None = None    # AUM in crores
    expenceratio: Decimal | None = None
    pe_ratio: Decimal | None = None
    pb_ratio: Decimal | None = None
    no_of_stocks: int | None = None
    highest_sector: str | None = None
    inception_date: date | None = None

    # Returns
    returns_1month: Decimal | None = None
    returns_3month: Decimal | None = None
    returns_6month: Decimal | None = None
    returns_1year: Decimal | None = None
    returns_2year: Decimal | None = None
    returns_3year: Decimal | None = None
    returns_5year: Decimal | None = None
    returns_10year: Decimal | None = None
    ytd_returns: Decimal | None = None

    # Risk metrics
    betax_returns: Decimal | None = None
    alphax_returns: Decimal | None = None
    sharpex_returns: Decimal | None = None
    sotinox_returns: Decimal | None = None

    @field_validator(
        "navrs", "aumtotal", "expenceratio", "pe_ratio", "pb_ratio",
        "returns_1month", "returns_3month", "returns_6month", "returns_1year",
        "returns_2year", "returns_3year", "returns_5year", "returns_10year",
        "ytd_returns", "betax_returns", "alphax_returns", "sharpex_returns",
        "sotinox_returns",
        mode="before",
    )
    @classmethod
    def _clean_numeric(cls, v: object) -> Decimal | None:
        return _to_decimal_or_none(v)

    @field_validator("no_of_stocks", mode="before")
    @classmethod
    def _clean_int(cls, v: object) -> int | None:
        if v is None:
            return None
        try:
            return int(v)
        except (ValueError, TypeError):
            return None

    @field_validator("inception_date", mode="before")
    @classmethod
    def _parse_date(cls, v: object) -> date | None:
        if not v:
            return None
        s = str(v).strip()
        if not s or s in ("-", "null", "none"):
            return None
        for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"):
            try:
                return datetime.strptime(s, fmt).date()
            except ValueError:
                continue
        return None


# ─── Fund holdings ───────────────────────────────────────────────────────────


class RupeevHolding(BaseModel):
    """One stock holding from portfolio_holdings[].

    Rupeevest uses different field names across their endpoints:
      - portfolio_holdings: {compname, holdpercentage}
      - older endpoints: {stock_name, corpus_per, fincode, market_value}
    We accept all variants.
    """

    model_config = ConfigDict(extra="ignore")

    # Company name — multiple field name variants
    compname: str | None = None        # portfolio_holdings endpoint
    stock_name: str | None = None      # older endpoint
    company_name: str | None = None    # alias

    # Holding percentage — multiple field name variants
    holdpercentage: Decimal | None = None   # portfolio_holdings endpoint
    corpus_per: Decimal | None = None       # older endpoint

    fincode: int | None = None         # Rupeevest internal stock ID (not always present)
    market_value: Decimal | None = None
    no_of_shares: int | None = None
    sector: str | None = None

    @field_validator("holdpercentage", "corpus_per", "market_value", mode="before")
    @classmethod
    def _clean_numeric(cls, v: object) -> Decimal | None:
        return _to_decimal_or_none(v)

    @field_validator("fincode", "no_of_shares", mode="before")
    @classmethod
    def _clean_int(cls, v: object) -> int | None:
        if v is None:
            return None
        try:
            return int(v)
        except (ValueError, TypeError):
            return None

    @property
    def display_name(self) -> str:
        return (self.compname or self.stock_name or self.company_name or "").strip()

    @property
    def holding_pct(self) -> Decimal | None:
        return self.holdpercentage or self.corpus_per


# ─── Fincode map ─────────────────────────────────────────────────────────────


class RupeevFincodeEntry(BaseModel):
    """One entry from /mf_stock_portfolio/get_search_data_stock → stock_data_search[]."""

    model_config = ConfigDict(extra="ignore")

    fincode: int
    compname: str
    s_name: str
    stock_search: str   # format: "Company Name | BSE_CODE | NSE_SYMBOL"

    def bse_code(self) -> str | None:
        """Parse BSE code from stock_search: 'Name | 500002 | SYMBOL'."""
        parts = [p.strip() for p in self.stock_search.split("|")]
        if len(parts) >= 2:
            code = parts[1].strip()
            return code if code else None
        return None

    def nse_symbol(self) -> str | None:
        """Parse NSE symbol from stock_search: 'Name | BSE_CODE | SYMBOL'."""
        parts = [p.strip() for p in self.stock_search.split("|")]
        if len(parts) >= 3:
            sym = parts[2].strip()
            return sym if sym else None
        return None
