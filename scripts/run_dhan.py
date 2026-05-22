"""Daily EOD price refresh from Dhan.

Iterates over stocks where dhan_security_id is set, fetches latest OHLCV
from Dhan's historical endpoint, upserts to daily_prices.

Usage:
  uv run python scripts/run_dhan.py                    # today's EOD
  uv run python scripts/run_dhan.py --date 2026-05-21  # specific date
  uv run python scripts/run_dhan.py --resume           # skip already-processed
  uv run python scripts/run_dhan.py --retry-errors     # only failed rows
  uv run python scripts/run_dhan.py --only-symbol RELIANCE  # debug single stock
  uv run python scripts/run_dhan.py --rps 1.0          # override rate limit
  uv run python scripts/run_dhan.py --limit 50         # only first N stocks
  uv run python scripts/run_dhan.py --exchange NSE     # restrict to NSE (default: all)

Requires DHAN_ACCESS_TOKEN and DHAN_CLIENT_ID in .env.
Requires bootstrap_identity.py to have populated stocks.dhan_security_id first.
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import date, datetime
from typing import Any
from uuid import UUID

import click

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from dastock.config import get_settings  # noqa: E402
from dastock.db.client import get_supabase  # noqa: E402
from dastock.db.repositories.stocks import StockRepository  # noqa: E402
from dastock.pipeline.dead_letter import DeadLetterLogger  # noqa: E402
from dastock.pipeline.run_tracker import RunTracker  # noqa: E402
from dastock.scrapers.dhan import DhanScraper  # noqa: E402
from dastock.scrapers.exceptions import EmptyRunError, TokenExpiredError  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("dastock.scripts.run_dhan")


@click.command()
@click.option("--date", "target_date", type=str, default=None, help="YYYY-MM-DD, default today")
@click.option("--resume", is_flag=True)
@click.option("--retry-errors", is_flag=True)
@click.option("--only-symbol", type=str, default=None, help="NSE/BSE ticker symbol")
@click.option("--rps", type=float, default=None)
@click.option("--limit", type=int, default=None)
@click.option(
    "--exchange",
    type=click.Choice(["NSE", "BSE", "ALL"], case_sensitive=False),
    default="ALL",
)
def main(
    target_date: str | None,
    resume: bool,
    retry_errors: bool,
    only_symbol: str | None,
    rps: float | None,
    limit: int | None,
    exchange: str,
) -> None:
    settings = get_settings()
    if rps is not None:
        settings.dhan_rate_limit_rps = rps
        logger.info(f"Rate limit overridden to {rps} RPS")

    trade_date = (
        datetime.strptime(target_date, "%Y-%m-%d").date()
        if target_date
        else date.today()
    )
    logger.info(f"Target trade_date: {trade_date}")

    client = get_supabase()
    repo = StockRepository(client)
    tracker = RunTracker(
        client, source="dhan", mode="eod", triggered_by=settings.triggered_by
    )
    tracker.start()
    dead_letter = DeadLetterLogger(client, run_id=tracker.run_id)

    # ─── Determine target set ────────────────────────────────────────────────
    if retry_errors:
        errs = tracker.dead_letter_ids(within_hours=24)
        targets = []
        for e in errs:
            sec_id = e.get("id_value")
            if sec_id:
                resolved = _lookup_stock_by_dhan_id(client, sec_id)
                if resolved:
                    targets.append(resolved)
        logger.info(f"Retry mode: {len(targets)} unresolved errors")
    elif only_symbol:
        resolved = _lookup_stock_by_symbol(client, only_symbol)
        if not resolved:
            logger.error(f"No stocks row found for symbol {only_symbol!r}")
            tracker.finish("failed", error_summary=f"unknown symbol {only_symbol}")
            sys.exit(1)
        targets = [resolved]
        logger.info(f"Single-symbol mode: {only_symbol} → {resolved['dhan_security_id']}")
    else:
        targets = repo.list_dhan_securities()
        # Exchange filter: a stock has nse_symbol set if listed on NSE,
        # bse_code set if listed on BSE (or both for dual-listed)
        if exchange.upper() == "NSE":
            targets = [t for t in targets if t.get("nse_symbol")]
        elif exchange.upper() == "BSE":
            targets = [t for t in targets if t.get("bse_code")]
        if limit:
            targets = targets[:limit]
        logger.info(
            f"Standard mode: {len(targets)} stocks with dhan_security_id (exchange={exchange})"
        )

    if resume and not retry_errors and not only_symbol:
        skip_ids = tracker.already_processed_ids()
        if skip_ids:
            before = len(targets)
            targets = [t for t in targets if str(t["dhan_security_id"]) not in skip_ids]
            logger.info(f"Resume: skipped {before - len(targets)} already-processed")

    # ─── Iterate and upsert ──────────────────────────────────────────────────
    try:
        with DhanScraper(settings=settings) as scraper:
            for target in targets:
                sec_id = str(target["dhan_security_id"])
                stock_uuid = UUID(target["id"])
                # Determine which exchange's symbol to request
                ex = "NSE" if target.get("nse_symbol") else "BSE"

                try:
                    candle = scraper.fetch_eod_candle(
                        security_id=sec_id,
                        exchange=ex,
                        trade_date=trade_date,
                    )
                except TokenExpiredError:
                    # 401/403 — token is dead, abort the whole run
                    logger.error("Dhan token expired or invalid — aborting run")
                    tracker.finish("failed", error_summary="Dhan token expired")
                    sys.exit(2)
                except Exception as e:
                    logger.warning(f"Fetch failed for {sec_id} ({ex}): {e}")
                    dead_letter.record(
                        source="dhan",
                        error_type="http_error",
                        error_msg=str(e)[:1000],
                        id_type="dhan_security_id",
                        id_value=sec_id,
                    )
                    tracker.mark_item_failed(sec_id)
                    continue

                if candle is None:
                    # DH-905 or empty window — illiquid/suspended stock, skip silently
                    # Do NOT count as failure — these are expected for many BSE stocks
                    continue

                try:
                    repo.upsert_daily_price(
                        stock_id=stock_uuid,
                        trade_date=candle.trade_date,
                        open_=candle.open,
                        high=candle.high,
                        low=candle.low,
                        close=candle.close,
                        volume=candle.volume,
                        source="dhan",
                    )
                    tracker.mark_item_ok(sec_id)
                except Exception as e:
                    logger.warning(f"Upsert failed for {sec_id}: {e}")
                    dead_letter.record(
                        source="dhan",
                        error_type="handler_error",
                        error_msg=str(e)[:1000],
                        id_type="dhan_security_id",
                        id_value=sec_id,
                    )
                    tracker.mark_item_failed(sec_id)

    except Exception as e:
        tracker.finish("failed", error_summary=f"{type(e).__name__}: {e}")
        raise

    status = "partial" if tracker._records_failed > 0 else "success"
    tracker.finish(status)

    if not only_symbol and limit is None and tracker._records_ok == 0:
        raise EmptyRunError("dhan EOD run produced 0 valid candles")

    logger.info(f"Done: {tracker._records_ok} ok, {tracker._records_failed} failed")


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _lookup_stock_by_symbol(client: Any, symbol: str) -> dict[str, Any] | None:
    """Find a stocks row by NSE or BSE symbol."""
    for col in ("nse_symbol", "bse_code"):
        resp = (
            client.table("stocks")
            .select("id, dhan_security_id, nse_symbol, bse_code")
            .eq(col, symbol)
            .not_.is_("dhan_security_id", "null")
            .limit(1)
            .execute()
        )
        if resp.data:
            return resp.data[0]
    return None


def _lookup_stock_by_dhan_id(client: Any, sec_id: str) -> dict[str, Any] | None:
    resp = (
        client.table("stocks")
        .select("id, dhan_security_id, nse_symbol, bse_code")
        .eq("dhan_security_id", sec_id)
        .limit(1)
        .execute()
    )
    return resp.data[0] if resp.data else None


if __name__ == "__main__":
    main()
