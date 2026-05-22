"""Tests for IdentityResolver — covers cache, lookup, and upsert paths."""

from __future__ import annotations

from unittest.mock import MagicMock
from uuid import UUID

import pytest

from dastock.identity.resolver import IdentityResolver

# A fixed UUID for deterministic tests
SAMPLE_UUID = UUID("11111111-1111-1111-1111-111111111111")
SAMPLE_UUID_2 = UUID("22222222-2222-2222-2222-222222222222")


def _set_query_result(mock_client: MagicMock, rows: list[dict[str, str]]) -> None:
    """Configure the mock client's chained API to return `rows` from .execute()."""
    mock_client.table.return_value.execute.return_value = MagicMock(data=rows)


# ─── resolve_stock ───────────────────────────────────────────────────────────


def test_resolve_stock_returns_uuid_on_hit(mock_supabase: MagicMock) -> None:
    _set_query_result(mock_supabase, [{"id": str(SAMPLE_UUID)}])
    resolver = IdentityResolver(mock_supabase)

    result = resolver.resolve_stock("nse_symbol", "RELIANCE")

    assert result == SAMPLE_UUID
    mock_supabase.table.assert_called_with("stocks")
    mock_supabase.table.return_value.eq.assert_called_with("nse_symbol", "RELIANCE")


def test_resolve_stock_returns_none_when_not_found(mock_supabase: MagicMock) -> None:
    _set_query_result(mock_supabase, [])
    resolver = IdentityResolver(mock_supabase)

    assert resolver.resolve_stock("nse_symbol", "UNKNOWN") is None


def test_resolve_stock_caches_result(mock_supabase: MagicMock) -> None:
    _set_query_result(mock_supabase, [{"id": str(SAMPLE_UUID)}])
    resolver = IdentityResolver(mock_supabase)

    resolver.resolve_stock("nse_symbol", "RELIANCE")
    resolver.resolve_stock("nse_symbol", "RELIANCE")

    # Second call should hit cache — table() called only once
    assert mock_supabase.table.call_count == 1


def test_resolve_stock_strips_whitespace(mock_supabase: MagicMock) -> None:
    _set_query_result(mock_supabase, [{"id": str(SAMPLE_UUID)}])
    resolver = IdentityResolver(mock_supabase)

    resolver.resolve_stock("bse_code", "  500002  ")

    mock_supabase.table.return_value.eq.assert_called_with("bse_code", "500002")


def test_resolve_stock_handles_integer_input(mock_supabase: MagicMock) -> None:
    """Rupeevest fincode is numeric; resolver must coerce to string for the query."""
    _set_query_result(mock_supabase, [{"id": str(SAMPLE_UUID)}])
    resolver = IdentityResolver(mock_supabase)

    resolver.resolve_stock("rupeevest_fincode", 100002)

    mock_supabase.table.return_value.eq.assert_called_with("rupeevest_fincode", "100002")


def test_resolve_stock_empty_string_returns_none(mock_supabase: MagicMock) -> None:
    resolver = IdentityResolver(mock_supabase)
    assert resolver.resolve_stock("nse_symbol", "") is None
    assert resolver.resolve_stock("nse_symbol", "   ") is None
    # Should never query the DB for an empty value
    mock_supabase.table.assert_not_called()


# ─── upsert_stock ───────────────────────────────────────────────────────────


def test_upsert_stock_prefers_isin_as_conflict_key(mock_supabase: MagicMock) -> None:
    _set_query_result(mock_supabase, [{"id": str(SAMPLE_UUID)}])
    resolver = IdentityResolver(mock_supabase)

    resolver.upsert_stock(
        canonical_name="Reliance Industries Ltd.",
        isin="INE002A01018",
        nse_symbol="RELIANCE",
        bse_code="500325",
    )

    mock_supabase.table.return_value.upsert.assert_called_once()
    args, kwargs = mock_supabase.table.return_value.upsert.call_args
    assert kwargs["on_conflict"] == "isin"


def test_upsert_stock_falls_back_to_nse_symbol_without_isin(
    mock_supabase: MagicMock,
) -> None:
    _set_query_result(mock_supabase, [{"id": str(SAMPLE_UUID)}])
    resolver = IdentityResolver(mock_supabase)

    resolver.upsert_stock(canonical_name="X Ltd.", nse_symbol="XLTD")

    args, kwargs = mock_supabase.table.return_value.upsert.call_args
    assert kwargs["on_conflict"] == "nse_symbol"


def test_upsert_stock_raises_with_no_identifier(mock_supabase: MagicMock) -> None:
    resolver = IdentityResolver(mock_supabase)
    with pytest.raises(ValueError, match="at least one identifier"):
        resolver.upsert_stock(canonical_name="Anonymous Co")


def test_upsert_stock_invalidates_cache(mock_supabase: MagicMock) -> None:
    _set_query_result(mock_supabase, [{"id": str(SAMPLE_UUID)}])
    resolver = IdentityResolver(mock_supabase)

    # Prime the cache
    resolver.resolve_stock("nse_symbol", "RELIANCE")
    assert ("nse_symbol", "RELIANCE") in resolver._stock_cache

    # Upserting the same identifier must invalidate
    resolver.upsert_stock(canonical_name="RIL", isin="INE002A01018", nse_symbol="RELIANCE")
    assert ("nse_symbol", "RELIANCE") not in resolver._stock_cache


# ─── resolve_fund ───────────────────────────────────────────────────────────


def test_resolve_fund_returns_uuid(mock_supabase: MagicMock) -> None:
    _set_query_result(mock_supabase, [{"id": str(SAMPLE_UUID)}])
    resolver = IdentityResolver(mock_supabase)

    assert resolver.resolve_fund("amfi_code", "125497") == SAMPLE_UUID
    mock_supabase.table.assert_called_with("mutual_funds")


def test_resolve_fund_separate_cache_from_stocks(mock_supabase: MagicMock) -> None:
    """Stock and fund caches must not collide even with same id_value strings."""
    resolver = IdentityResolver(mock_supabase)
    resolver._stock_cache[("amfi_code", "12345")] = SAMPLE_UUID
    resolver._fund_cache[("amfi_code", "12345")] = SAMPLE_UUID_2

    # Note: id_type "amfi_code" isn't valid for stocks (typing only), but the
    # cache is keyed by tuple so this isolation test still holds.
    assert resolver._stock_cache[("amfi_code", "12345")] == SAMPLE_UUID
    assert resolver._fund_cache[("amfi_code", "12345")] == SAMPLE_UUID_2


# ─── invalidate_all ─────────────────────────────────────────────────────────


def test_invalidate_all_clears_both_caches(mock_supabase: MagicMock) -> None:
    resolver = IdentityResolver(mock_supabase)
    resolver._stock_cache[("nse_symbol", "RELIANCE")] = SAMPLE_UUID
    resolver._fund_cache[("amfi_code", "125497")] = SAMPLE_UUID

    resolver.invalidate_all()

    assert resolver._stock_cache == {}
    assert resolver._fund_cache == {}
