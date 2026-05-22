"""Pydantic models for mfapi.in responses.

The list endpoint /mf returns camelCase, the per-scheme /mf/{code}/latest
endpoint returns snake_case. Aliases handle both.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# ─── Master list (/mf) ───────────────────────────────────────────────────────


class MfapiSchemeListEntry(BaseModel):
    """One entry in the /mf master list (camelCase response)."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    scheme_code: int = Field(alias="schemeCode")
    scheme_name: str = Field(alias="schemeName")
    isin_growth: str | None = Field(default=None, alias="isinGrowth")
    isin_div_reinvestment: str | None = Field(default=None, alias="isinDivReinvestment")


# ─── Per-scheme latest NAV (/mf/{code}/latest) ───────────────────────────────


class MfapiMeta(BaseModel):
    """The 'meta' block in the per-scheme response (snake_case)."""

    model_config = ConfigDict(extra="ignore")

    scheme_code: int
    scheme_name: str
    fund_house: str | None = None
    scheme_type: str | None = None
    scheme_category: str | None = None
    isin_growth: str | None = None
    isin_div_reinvestment: str | None = None


class MfapiNavPoint(BaseModel):
    """One NAV data point (date + value) in the response 'data' array."""

    model_config = ConfigDict(extra="ignore")

    date: date
    nav: Decimal

    @field_validator("date", mode="before")
    @classmethod
    def parse_dd_mm_yyyy(cls, v: str | date) -> date:
        """mfapi returns dates as DD-MM-YYYY strings."""
        if isinstance(v, date):
            return v
        return datetime.strptime(v, "%d-%m-%Y").date()

    @field_validator("nav", mode="before")
    @classmethod
    def parse_nav(cls, v: str | Decimal | float) -> Decimal:
        """Coerce string nav to Decimal; reject obviously bad values."""
        if isinstance(v, str):
            v = v.strip()
        d = Decimal(str(v))
        if d < 0:
            raise ValueError(f"NAV must be non-negative, got {d}")
        return d


class MfapiSchemeLatest(BaseModel):
    """Top-level response of /mf/{code}/latest."""

    model_config = ConfigDict(extra="ignore")

    meta: MfapiMeta
    data: list[MfapiNavPoint]
    status: str

    @model_validator(mode="after")
    def must_have_data(self) -> MfapiSchemeLatest:
        if self.status.upper() != "SUCCESS":
            raise ValueError(f"mfapi returned status={self.status}")
        if not self.data:
            raise ValueError("mfapi returned empty data array")
        return self

    @property
    def latest_nav_point(self) -> MfapiNavPoint:
        """The most recent NAV (data is sorted newest-first by the API)."""
        return self.data[0]
