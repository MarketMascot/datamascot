"""Pydantic models for Trendlyne QVT and SWOT widget responses.

Trendlyne exposes HTML widgets (not JSON endpoints), so these models
represent the parsed output after BeautifulSoup extraction, not raw API JSON.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, field_validator


def _parse_score(v: object) -> int | None:
    """Parse widget score text → int [0, 100], or None."""
    if v is None:
        return None
    s = str(v).strip().replace("%", "").replace(",", "")
    if s.lower() in ("", "n/a", "na", "--", "-", "null", "none"):
        return None
    try:
        score = int(float(s))
        return max(0, min(100, score))
    except (ValueError, TypeError):
        return None


class TrendlyneQvt(BaseModel):
    """Parsed QVT (Quality / Valuation / Technical) scores for one stock."""

    model_config = ConfigDict(extra="ignore")

    nse_symbol: str

    quality_score: int | None = None
    quality_insight: str | None = None

    valuation_score: int | None = None
    valuation_insight: str | None = None

    technical_score: int | None = None
    technical_insight: str | None = None

    @field_validator("quality_score", "valuation_score", "technical_score", mode="before")
    @classmethod
    def _coerce_score(cls, v: object) -> int | None:
        return _parse_score(v)

    @property
    def has_data(self) -> bool:
        return any(
            s is not None
            for s in (self.quality_score, self.valuation_score, self.technical_score)
        )


class TrendlyneSwot(BaseModel):
    """Parsed SWOT analysis for one stock."""

    model_config = ConfigDict(extra="ignore")

    nse_symbol: str

    strengths_count: int | None = None
    strengths_text: str | None = None

    weakness_count: int | None = None
    weakness_text: str | None = None

    opportunities_count: int | None = None
    opportunities_text: str | None = None

    threats_count: int | None = None
    threats_text: str | None = None

    @field_validator(
        "strengths_count", "weakness_count", "opportunities_count", "threats_count",
        mode="before",
    )
    @classmethod
    def _coerce_count(cls, v: object) -> int | None:
        return _parse_score(v)

    @property
    def has_data(self) -> bool:
        return any(
            c is not None
            for c in (
                self.strengths_count,
                self.weakness_count,
                self.opportunities_count,
                self.threats_count,
            )
        )
