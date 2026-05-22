"""IdentityResolver — maps any external identifier to the internal UUID.

This is the single class that solves the identifier-chaos bug from the
original codebase. Every scraper must call resolve() before upserting.

The bible tables (stocks, mutual_funds) store every external ID as a
dedicated column. Resolution = a single WHERE clause, no joins.

Caching: an in-memory LRU avoids round-tripping to Supabase for hot symbols
during a single scraper run. Cleared between runs.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Literal
from uuid import UUID

if TYPE_CHECKING:
    from supabase import Client

logger = logging.getLogger(__name__)


# Source/id_type pairs that are valid. Adding a new pair requires updating
# the bible table schema first (a new column on stocks or mutual_funds).
StockIdType = Literal[
    "isin",
    "nse_symbol",
    "bse_code",
    "dhan_security_id",
    "rupeevest_fincode",
    "trendlyne_symbol",
]
FundIdType = Literal[
    "amfi_code",
    "rupeevest_schemecode",
    "isin_growth",
    "isin_dividend",
]


class IdentityResolver:
    """Resolves external identifiers to internal UUIDs via bible tables.

    Usage:
        resolver = IdentityResolver(supabase_client)
        stock_uuid = resolver.resolve_stock("nse_symbol", "RELIANCE")
        fund_uuid = resolver.resolve_fund("amfi_code", "125497")
    """

    def __init__(self, client: Client) -> None:
        self._client = client
        # Two separate caches keyed by (id_type, id_value)
        self._stock_cache: dict[tuple[str, str], UUID | None] = {}
        self._fund_cache: dict[tuple[str, str], UUID | None] = {}

    # ─── Stocks ──────────────────────────────────────────────────────────────

    def resolve_stock(self, id_type: StockIdType, id_value: str | int) -> UUID | None:
        """Resolve a stock external ID to its internal UUID.

        Returns None if the stock is not in the bible table. The caller
        decides what to do (log to scraper_errors, skip, etc.).
        """
        value = str(id_value).strip()
        if not value:
            return None

        cache_key = (id_type, value)
        if cache_key in self._stock_cache:
            return self._stock_cache[cache_key]

        resp = (
            self._client.table("stocks")
            .select("id")
            .eq(id_type, value)
            .limit(1)
            .execute()
        )
        result: UUID | None = UUID(resp.data[0]["id"]) if resp.data else None
        self._stock_cache[cache_key] = result
        return result

    def upsert_stock(
        self,
        *,
        canonical_name: str,
        isin: str | None = None,
        nse_symbol: str | None = None,
        bse_code: str | None = None,
        dhan_security_id: str | None = None,
        rupeevest_fincode: int | None = None,
        trendlyne_symbol: str | None = None,
    ) -> UUID:
        """Insert or update a stock by its strongest available identifier.

        Used by bootstrap_identity to seed the bible table. Resolution
        priority: ISIN > NSE symbol > BSE code > Dhan ID.
        """
        row: dict[str, str | int | None] = {"canonical_name": canonical_name}
        for k, v in {
            "isin": isin,
            "nse_symbol": nse_symbol,
            "bse_code": bse_code,
            "dhan_security_id": dhan_security_id,
            "rupeevest_fincode": rupeevest_fincode,
            "trendlyne_symbol": trendlyne_symbol,
        }.items():
            if v is not None:
                row[k] = v

        # Determine the conflict column (strongest identifier present)
        if isin:
            on_conflict = "isin"
        elif nse_symbol:
            on_conflict = "nse_symbol"
        elif bse_code:
            on_conflict = "bse_code"
        elif dhan_security_id:
            on_conflict = "dhan_security_id"
        else:
            raise ValueError("Need at least one identifier to upsert a stock")

        resp = (
            self._client.table("stocks")
            .upsert(row, on_conflict=on_conflict)
            .execute()
        )
        stock_id = UUID(resp.data[0]["id"])
        # Invalidate cache entries for this stock (all identifiers we set)
        for k, v in row.items():
            if k != "canonical_name" and v is not None:
                self._stock_cache.pop((k, str(v)), None)
        return stock_id

    # ─── Mutual funds ────────────────────────────────────────────────────────

    def resolve_fund(self, id_type: FundIdType, id_value: str | int) -> UUID | None:
        """Resolve a mutual fund external ID to its internal UUID."""
        value = str(id_value).strip()
        if not value:
            return None

        cache_key = (id_type, value)
        if cache_key in self._fund_cache:
            return self._fund_cache[cache_key]

        resp = (
            self._client.table("mutual_funds")
            .select("id")
            .eq(id_type, value)
            .limit(1)
            .execute()
        )
        result: UUID | None = UUID(resp.data[0]["id"]) if resp.data else None
        self._fund_cache[cache_key] = result
        return result

    def upsert_fund(
        self,
        *,
        scheme_name: str,
        amfi_code: str | None = None,
        rupeevest_schemecode: int | None = None,
        isin_growth: str | None = None,
        isin_dividend: str | None = None,
        fund_house: str | None = None,
        classification: str | None = None,
        scheme_type: str | None = None,
    ) -> UUID:
        """Insert or update a mutual fund. AMFI code or ISIN preferred as conflict key."""
        row: dict[str, str | int | None] = {"scheme_name": scheme_name}
        for k, v in {
            "amfi_code": amfi_code,
            "rupeevest_schemecode": rupeevest_schemecode,
            "isin_growth": isin_growth,
            "isin_dividend": isin_dividend,
            "fund_house": fund_house,
            "classification": classification,
            "scheme_type": scheme_type,
        }.items():
            if v is not None:
                row[k] = v

        if amfi_code:
            on_conflict = "amfi_code"
        elif isin_growth:
            on_conflict = "isin_growth"
        elif rupeevest_schemecode:
            on_conflict = "rupeevest_schemecode"
        else:
            raise ValueError("Need at least one identifier to upsert a mutual fund")

        resp = (
            self._client.table("mutual_funds")
            .upsert(row, on_conflict=on_conflict)
            .execute()
        )
        fund_id = UUID(resp.data[0]["id"])
        for k, v in row.items():
            if k != "scheme_name" and v is not None:
                self._fund_cache.pop((k, str(v)), None)
        return fund_id

    # ─── Cache management ───────────────────────────────────────────────────

    def invalidate_all(self) -> None:
        """Clear both caches. Useful between scraper runs in long-lived processes."""
        self._stock_cache.clear()
        self._fund_cache.clear()
