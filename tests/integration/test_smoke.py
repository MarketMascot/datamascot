"""Smoke test — verifies live Supabase connection works end-to-end.

Skipped unless DASTOCK_RUN_INTEGRATION=1 in the environment.
Uses the real Supabase project configured in .env.

This test:
  1. Creates a scraper_runs row (status='running')
  2. Inserts a test stock via IdentityResolver.upsert_stock
  3. Resolves the stock by NSE symbol → confirms UUID matches
  4. Inserts a scraper_errors row via DeadLetterLogger
  5. Cleans up all test rows

If this passes, the foundation is wired correctly to live Supabase.
"""

from __future__ import annotations

import os
import uuid

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("DASTOCK_RUN_INTEGRATION") != "1",
    reason="Live integration test; set DASTOCK_RUN_INTEGRATION=1 to enable",
)


def test_supabase_roundtrip_create_resolve_dead_letter() -> None:
    from dastock.db.client import get_supabase
    from dastock.identity.resolver import IdentityResolver
    from dastock.pipeline.dead_letter import DeadLetterLogger
    from dastock.pipeline.run_tracker import RunTracker

    client = get_supabase()

    # A unique marker so we can clean up our test rows precisely
    marker = f"TEST_{uuid.uuid4().hex[:8].upper()}"

    # ─── 1. Run tracker ──────────────────────────────────────────────────────
    tracker = RunTracker(client, source="dhan", mode="smoke", triggered_by="manual")
    run_id = tracker.start()
    assert isinstance(run_id, uuid.UUID)

    # ─── 2. Resolver upsert + lookup ─────────────────────────────────────────
    resolver = IdentityResolver(client)
    stock_uuid = resolver.upsert_stock(
        canonical_name=f"{marker} Industries Ltd.",
        isin=f"INE{marker}01",
        nse_symbol=marker,
        bse_code=f"9{marker[-5:]}",
        dhan_security_id=marker,
    )
    assert isinstance(stock_uuid, uuid.UUID)

    # Cache invalidation should let resolve() hit the DB freshly
    found = resolver.resolve_stock("nse_symbol", marker)
    assert found == stock_uuid

    found_by_isin = resolver.resolve_stock("isin", f"INE{marker}01")
    assert found_by_isin == stock_uuid

    # ─── 3. Dead letter ──────────────────────────────────────────────────────
    dl = DeadLetterLogger(client, run_id=run_id)
    dl.record(
        source="dhan",
        error_type="validation_error",
        error_msg=f"smoke test {marker}",
        id_value=marker,
        raw_payload={"test": True, "marker": marker},
    )

    # Verify dead letter row landed
    errs = (
        client.table("scraper_errors")
        .select("id, error_msg")
        .eq("source", "dhan")
        .like("error_msg", f"%{marker}%")
        .execute()
    )
    assert len(errs.data) == 1

    # ─── 4. Finish run ───────────────────────────────────────────────────────
    tracker.mark_item_ok(marker)
    tracker.finish("success")

    runs = (
        client.table("scraper_runs").select("status, records_ok").eq("id", str(run_id)).execute()
    )
    assert runs.data[0]["status"] == "success"
    assert runs.data[0]["records_ok"] == 1

    # ─── 5. Cleanup ──────────────────────────────────────────────────────────
    client.table("scraper_errors").delete().eq("run_id", str(run_id)).execute()
    client.table("scraper_run_items").delete().eq("run_id", str(run_id)).execute()
    client.table("scraper_runs").delete().eq("id", str(run_id)).execute()
    client.table("stocks").delete().eq("id", str(stock_uuid)).execute()
