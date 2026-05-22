"""Repository for stocks and daily_prices tables."""

from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import UUID

if TYPE_CHECKING:
    from supabase import Client

logger = logging.getLogger(__name__)


class StockRepository:
    """Read/write operations on stocks + daily_prices."""

    def __init__(self, client: Client) -> None:
        self._client = client

    def upsert_daily_price(
        self,
        *,
        stock_id: UUID,
        trade_date: date,
        open_: Decimal,
        high: Decimal,
        low: Decimal,
        close: Decimal,
        volume: int,
        source: str = "dhan",
        raw_payload: dict[str, Any] | None = None,
    ) -> UUID:
        """Insert or update one day's OHLCV. Idempotent on (stock_id, trade_date)."""
        row: dict[str, Any] = {
            "stock_id": str(stock_id),
            "trade_date": trade_date.isoformat(),
            "open": str(open_),
            "high": str(high),
            "low": str(low),
            "close": str(close),
            "volume": volume,
            "source": source,
        }
        if raw_payload is not None:
            row["raw_payload"] = raw_payload
        resp = (
            self._client.table("daily_prices")
            .upsert(row, on_conflict="stock_id,trade_date")
            .execute()
        )
        return UUID(resp.data[0]["id"])

    def list_dhan_securities(self) -> list[dict[str, Any]]:
        """Return all (id, dhan_security_id, nse_symbol|bse_code) from active stocks.

        Used by the EOD scraper to know which securities to fetch from Dhan.
        Filters to rows where dhan_security_id is set (i.e., bootstrapped).
        """
        resp = (
            self._client.table("stocks")
            .select("id, dhan_security_id, nse_symbol, bse_code, canonical_name")
            .not_.is_("dhan_security_id", "null")
            .eq("listing_status", "active")
            .execute()
        )
        return resp.data or []
