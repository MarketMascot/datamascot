"""Scan360 sector/industry scraper.

Single API call fetches all industries. Updates stocks.industry (and
stocks.sector where it can be inferred) for each matched NSE symbol.

Usage:
  uv run python scripts/run_scan360.py
  uv run python scripts/run_scan360.py --limit 100
  uv run python scripts/run_scan360.py --only-symbol RELIANCE
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Any

import click

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from dastock.config import get_settings  # noqa: E402
from dastock.db.client import get_supabase  # noqa: E402
from dastock.pipeline.dead_letter import DeadLetterLogger  # noqa: E402
from dastock.pipeline.run_tracker import RunTracker  # noqa: E402
from dastock.scrapers.scan360 import Scan360Scraper  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("dastock.scripts.run_scan360")


@click.command()
@click.option("--only-symbol", type=str, default=None, help="Single NSE symbol (debug)")
@click.option("--limit", type=int, default=None)
def main(only_symbol: str | None, limit: int | None) -> None:
    settings = get_settings()
    client = get_supabase()

    tracker = RunTracker(
        client, source="scan360", mode="sectors", triggered_by=settings.triggered_by
    )
    tracker.start()
    dead_letter = DeadLetterLogger(client, run_id=tracker.run_id)

    try:
        with Scan360Scraper(settings=settings) as scraper:
            logger.info("Fetching Scan360 industry data...")
            raw = scraper.fetch_raw()

            if not raw:
                raise RuntimeError("Scan360: empty response — API may be down or changed")

            industry_map = scraper.build_industry_map(raw)
            logger.info(f"Scan360: {len(industry_map)} unique stocks in industry map")

            if only_symbol:
                industry_map = {
                    k: v for k, v in industry_map.items()
                    if k == only_symbol.upper()
                }

            if limit:
                industry_map = dict(list(industry_map.items())[:limit])

            updated = 0
            skipped = 0

            for symbol, industry in industry_map.items():
                try:
                    resp = (
                        client.table("stocks")
                        .update({"industry": industry})
                        .eq("nse_symbol", symbol)
                        .eq("listing_status", "active")
                        .execute()
                    )
                    if resp.data:
                        updated += 1
                        tracker.mark_item_ok(symbol)
                    else:
                        # Symbol not in our stocks table — log as unresolved
                        dead_letter.record(
                            source="scan360",
                            error_type="unresolved_id",
                            error_msg=f"no active stocks row for nse_symbol={symbol}",
                            id_type="nse_symbol",
                            id_value=symbol,
                        )
                        skipped += 1
                except Exception as e:
                    dead_letter.record(
                        source="scan360",
                        error_type="handler_error",
                        error_msg=str(e)[:500],
                        id_type="nse_symbol",
                        id_value=symbol,
                    )
                    tracker.mark_item_failed(symbol)

    except Exception as e:
        tracker.finish("failed", error_summary=f"{type(e).__name__}: {e}")
        raise

    status = "partial" if tracker._records_failed > 0 else "success"
    tracker.finish(status)
    logger.info(
        f"Scan360 done: {updated} updated, {skipped} unresolved, "
        f"{tracker._records_failed} errors"
    )


if __name__ == "__main__":
    main()
