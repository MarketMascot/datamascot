"""Repository for analytics tables: trendlyne_qvt, trendlyne_swot."""

from __future__ import annotations

import logging
from datetime import date
from typing import TYPE_CHECKING, Any
from uuid import UUID

if TYPE_CHECKING:
    from supabase import Client

logger = logging.getLogger(__name__)


class AnalyticsRepository:
    """Read/write operations on trendlyne_qvt and trendlyne_swot."""

    def __init__(self, client: Client) -> None:
        self._client = client

    def upsert_qvt(
        self,
        *,
        stock_id: UUID,
        analysis_date: date,
        quality_score: int | None = None,
        quality_insight: str | None = None,
        valuation_score: int | None = None,
        valuation_insight: str | None = None,
        technical_score: int | None = None,
        technical_insight: str | None = None,
        source: str = "trendlyne",
        raw_payload: dict[str, Any] | None = None,
    ) -> UUID:
        """Upsert QVT scores. Idempotent on (stock_id, analysis_date)."""
        row: dict[str, Any] = {
            "stock_id": str(stock_id),
            "analysis_date": analysis_date.isoformat(),
            "source": source,
        }
        for k, v in {
            "quality_score": quality_score,
            "quality_insight": quality_insight,
            "valuation_score": valuation_score,
            "valuation_insight": valuation_insight,
            "technical_score": technical_score,
            "technical_insight": technical_insight,
        }.items():
            if v is not None:
                row[k] = v
        if raw_payload is not None:
            # Cap at 256KB to avoid huge JSONB rows
            import json
            payload_str = json.dumps(raw_payload)
            if len(payload_str) > 256 * 1024:
                raw_payload = {"_truncated": True, "raw": payload_str[: 256 * 1024]}
            row["raw_payload"] = raw_payload

        resp = (
            self._client.table("trendlyne_qvt")
            .upsert(row, on_conflict="stock_id,analysis_date")
            .execute()
        )
        return UUID(resp.data[0]["id"])

    def upsert_swot(
        self,
        *,
        stock_id: UUID,
        analysis_date: date,
        strengths_count: int | None = None,
        strengths_text: str | None = None,
        weakness_count: int | None = None,
        weakness_text: str | None = None,
        opportunities_count: int | None = None,
        opportunities_text: str | None = None,
        threats_count: int | None = None,
        threats_text: str | None = None,
        source: str = "trendlyne",
        raw_payload: dict[str, Any] | None = None,
    ) -> UUID:
        """Upsert SWOT analysis. Idempotent on (stock_id, analysis_date)."""
        row: dict[str, Any] = {
            "stock_id": str(stock_id),
            "analysis_date": analysis_date.isoformat(),
            "source": source,
        }
        for k, v in {
            "strengths_count": strengths_count,
            "strengths_text": strengths_text,
            "weakness_count": weakness_count,
            "weakness_text": weakness_text,
            "opportunities_count": opportunities_count,
            "opportunities_text": opportunities_text,
            "threats_count": threats_count,
            "threats_text": threats_text,
        }.items():
            if v is not None:
                row[k] = v
        if raw_payload is not None:
            import json
            payload_str = json.dumps(raw_payload)
            if len(payload_str) > 256 * 1024:
                raw_payload = {"_truncated": True, "raw": payload_str[: 256 * 1024]}
            row["raw_payload"] = raw_payload

        resp = (
            self._client.table("trendlyne_swot")
            .upsert(row, on_conflict="stock_id,analysis_date")
            .execute()
        )
        return UUID(resp.data[0]["id"])

    def list_nse_symbols(self) -> list[dict[str, Any]]:
        """Return all (id, nse_symbol) pairs for active stocks with an NSE symbol."""
        resp = (
            self._client.table("stocks")
            .select("id, nse_symbol")
            .not_.is_("nse_symbol", "null")
            .eq("listing_status", "active")
            .execute()
        )
        return resp.data or []
