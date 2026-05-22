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
        """Return all (id, amfi_code) pairs from mutual_funds with non-null amfi_code.

        Used by the daily NAV scraper to know which funds to fetch from mfapi.
        """
        resp = (
            self._client.table("mutual_funds")
            .select("id, amfi_code")
            .not_.is_("amfi_code", "null")
            .eq("listing_status", "active")
            .execute()
        )
        return resp.data or []
