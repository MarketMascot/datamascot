"""Repository for mutual_funds and mf_nav_history tables.

A repository wraps Supabase table operations behind an interface the rest of
the codebase calls. This keeps SQL/Supabase-specific knowledge in one place.
"""

from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import UUID

if TYPE_CHECKING:
    from supabase import Client

logger = logging.getLogger(__name__)


class MutualFundRepository:
    """Read/write operations on mutual_funds + mf_nav_history."""

    def __init__(self, client: Client) -> None:
        self._client = client

    def upsert_nav(
        self,
        *,
        fund_id: UUID,
        nav_date: date,
        nav: Decimal,
        source: str = "mfapi",
        suspicious: bool = False,
    ) -> UUID:
        """Insert or update a NAV history row. Idempotent on (fund_id, nav_date)."""
        resp = (
            self._client.table("mf_nav_history")
            .upsert(
                {
                    "fund_id": str(fund_id),
                    "nav_date": nav_date.isoformat(),
                    "nav": str(nav),  # Decimal → str preserves precision through JSON
                    "source": source,
                    "suspicious": suspicious,
                },
                on_conflict="fund_id,nav_date",
            )
            .execute()
        )
        return UUID(resp.data[0]["id"])

    def previous_nav(self, fund_id: UUID, before: date) -> Decimal | None:
        """Return the most recent NAV strictly before `before`. Used for sanity checks."""
        resp = (
            self._client.table("mf_nav_history")
            .select("nav")
            .eq("fund_id", str(fund_id))
            .lt("nav_date", before.isoformat())
            .order("nav_date", desc=True)
            .limit(1)
            .execute()
        )
        if not resp.data:
            return None
        return Decimal(resp.data[0]["nav"])

    def list_amfi_codes(self) -> list[dict[str, Any]]:
        """Return all (id, amfi_code) pairs from mutual_funds with non-null amfi_code."""
        resp = (
            self._client.table("mutual_funds")
            .select("id, amfi_code")
            .not_.is_("amfi_code", "null")
            .eq("listing_status", "active")
            .execute()
        )
        return resp.data or []

    def list_rupeevest_schemecodes(self) -> list[dict[str, Any]]:
        """Return all (id, rupeevest_schemecode) pairs for active funds with schemecode set."""
        resp = (
            self._client.table("mutual_funds")
            .select("id, rupeevest_schemecode, scheme_name")
            .not_.is_("rupeevest_schemecode", "null")
            .eq("listing_status", "active")
            .execute()
        )
        return resp.data or []

    def find_by_name(self, scheme_name: str) -> dict[str, Any] | None:
        """Look up a mutual_funds row by exact scheme_name match.

        Used by Rupeevest schemecode bridge: s_name → find fund UUID.
        """
        resp = (
            self._client.table("mutual_funds")
            .select("id, rupeevest_schemecode, scheme_name")
            .eq("scheme_name", scheme_name)
            .limit(1)
            .execute()
        )
        return resp.data[0] if resp.data else None

    def set_rupeevest_schemecode(self, fund_id: UUID, schemecode: int) -> None:
        """Set rupeevest_schemecode on an existing mutual_funds row."""
        self._client.table("mutual_funds").update(
            {"rupeevest_schemecode": schemecode}
        ).eq("id", str(fund_id)).execute()

    def upsert_metrics(
        self,
        *,
        fund_id: UUID,
        as_of_date: date,
        metrics: dict[str, Any],
        source: str = "rupeevest",
    ) -> UUID:
        """Upsert fund metrics (returns, AUM, expense ratio, etc.). Idempotent."""
        row = {"fund_id": str(fund_id), "as_of_date": as_of_date.isoformat(), "source": source}
        # Decimal → str for JSON precision
        for k, v in metrics.items():
            if isinstance(v, Decimal):
                row[k] = str(v)
            elif v is not None:
                row[k] = v
        resp = (
            self._client.table("mf_metrics")
            .upsert(row, on_conflict="fund_id,as_of_date")
            .execute()
        )
        return UUID(resp.data[0]["id"])

    def upsert_holding(
        self,
        *,
        fund_id: UUID,
        stock_id: UUID,
        as_of_date: date,
        holding_pct: Decimal | None = None,
        holding_value_cr: Decimal | None = None,
        no_of_shares: int | None = None,
        source: str = "rupeevest",
        raw_payload: dict[str, Any] | None = None,
    ) -> UUID:
        """Upsert one MF→stock holding. Idempotent on (fund_id, stock_id, as_of_date)."""
        row: dict[str, Any] = {
            "fund_id": str(fund_id),
            "stock_id": str(stock_id),
            "as_of_date": as_of_date.isoformat(),
            "source": source,
        }
        if holding_pct is not None:
            row["holding_pct"] = str(holding_pct)
        if holding_value_cr is not None:
            row["holding_value_cr"] = str(holding_value_cr)
        if no_of_shares is not None:
            row["no_of_shares"] = no_of_shares
        if raw_payload is not None:
            row["raw_payload"] = raw_payload
        resp = (
            self._client.table("mf_stock_holdings")
            .upsert(row, on_conflict="fund_id,stock_id,as_of_date")
            .execute()
        )
        return UUID(resp.data[0]["id"])
