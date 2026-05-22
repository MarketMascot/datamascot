"""Rupeevest MF data scraper.

Modes:
  metadata  — weekly: fetch fund metrics (returns, AUM, expense ratio).
              Also bridges Rupeevest schemecode → mutual_funds rows via name match.
  holdings  — monthly: fetch per-fund stock holdings → mf_stock_holdings.
  fincode   — one-time/periodic: update stocks.rupeevest_fincode via BSE/NSE match.

Usage:
  uv run python scripts/run_rupeevest.py --mode metadata
  uv run python scripts/run_rupeevest.py --mode holdings
  uv run python scripts/run_rupeevest.py --mode fincode
  uv run python scripts/run_rupeevest.py --mode metadata --limit 50
  uv run python scripts/run_rupeevest.py --mode holdings --only-symbol 125497
  uv run python scripts/run_rupeevest.py --mode metadata --resume
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import date
from typing import Any
from uuid import UUID

import click

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from dastock.config import get_settings  # noqa: E402
from dastock.db.client import get_supabase  # noqa: E402
from dastock.db.repositories.mutual_funds import MutualFundRepository  # noqa: E402
from dastock.identity.resolver import IdentityResolver  # noqa: E402
from dastock.pipeline.dead_letter import DeadLetterLogger  # noqa: E402
from dastock.pipeline.run_tracker import RunTracker  # noqa: E402
from dastock.scrapers.rupeevest import RupeevScraper  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("dastock.scripts.run_rupeevest")


@click.command()
@click.option(
    "--mode",
    type=click.Choice(["metadata", "holdings", "fincode"]),
    default="metadata",
    show_default=True,
)
@click.option("--resume", is_flag=True)
@click.option("--retry-errors", is_flag=True)
@click.option("--only-symbol", type=str, default=None, help="Single schemecode (metadata/holdings)")
@click.option("--rps", type=float, default=None)
@click.option("--limit", type=int, default=None)
def main(
    mode: str,
    resume: bool,
    retry_errors: bool,
    only_symbol: str | None,
    rps: float | None,
    limit: int | None,
) -> None:
    settings = get_settings()
    if rps is not None:
        settings.rupeevest_rate_limit_rps = rps

    client = get_supabase()
    repo = MutualFundRepository(client)
    resolver = IdentityResolver(client)

    if mode == "metadata":
        _run_metadata(client, repo, resolver, settings, resume, retry_errors, only_symbol, limit)
    elif mode == "holdings":
        _run_holdings(client, repo, resolver, settings, resume, only_symbol, limit)
    elif mode == "fincode":
        _run_fincode(client, resolver, settings, limit)


# ─── Metadata mode ───────────────────────────────────────────────────────────


def _run_metadata(
    client: Any,
    repo: MutualFundRepository,
    resolver: IdentityResolver,
    settings: Any,
    resume: bool,
    retry_errors: bool,
    only_symbol: str | None,
    limit: int | None,
) -> None:
    tracker = RunTracker(
        client, source="rupeevest", mode="metadata", triggered_by=settings.triggered_by
    )
    tracker.start()
    dead_letter = DeadLetterLogger(client, run_id=tracker.run_id)
    today = date.today()

    skip_ids: set[str] = tracker.already_processed_ids() if resume else set()

    try:
        with RupeevScraper(settings=settings) as scraper:
            if only_symbol:
                raw_list = [r for r in scraper.fetch_fund_list() if str(r.get("schemecode")) == only_symbol]
            else:
                raw_list = scraper.fetch_fund_list()

            if limit:
                raw_list = raw_list[:limit]

            logger.info(f"Metadata mode: {len(raw_list)} funds to process")

            for record in raw_list:
                schemecode = str(record.get("schemecode", ""))
                if schemecode in skip_ids:
                    continue

                try:
                    model = scraper.transform_metadata(record)
                except Exception as e:
                    dead_letter.record(
                        source="rupeevest",
                        error_type="validation_error",
                        error_msg=str(e)[:1000],
                        id_value=schemecode,
                        raw_payload=record,
                    )
                    tracker.mark_item_failed(schemecode)
                    continue

                # Resolve or create a mutual_funds row for this Rupeevest schemecode.
                # Rupeevest schemecodes are NOT AMFI codes, so we can't deduplicate
                # with mfapi rows by code. We upsert_fund using rupeevest_schemecode
                # as the conflict key — creates a new row if needed.
                fund_id = resolver.upsert_fund(
                    scheme_name=model.s_name,
                    rupeevest_schemecode=model.schemecode,
                    fund_house=model.fund_house,
                    classification=model.classification,
                )

                # Upsert metrics
                metrics = {
                    "nav": model.navrs,
                    "aum_cr": model.aumtotal,
                    "expense_ratio": model.expenceratio,
                    "return_1m": model.returns_1month,
                    "return_3m": model.returns_3month,
                    "return_6m": model.returns_6month,
                    "return_1y": model.returns_1year,
                    "return_3y": model.returns_3year,
                    "return_5y": model.returns_5year,
                    "return_10y": model.returns_10year,
                    "pe_ratio": model.pe_ratio,
                    "pb_ratio": model.pb_ratio,
                    "no_of_stocks": model.no_of_stocks,
                }
                metrics = {k: v for k, v in metrics.items() if v is not None}

                repo.upsert_metrics(fund_id=fund_id, as_of_date=today, metrics=metrics)
                tracker.mark_item_ok(schemecode)

    except Exception as e:
        tracker.finish("failed", error_summary=f"{type(e).__name__}: {e}")
        raise

    status = "partial" if tracker._records_failed > 0 else "success"
    tracker.finish(status)
    logger.info(f"Metadata done: {tracker._records_ok} ok, {tracker._records_failed} failed")




# ─── Holdings mode ────────────────────────────────────────────────────────────


def _run_holdings(
    client: Any,
    repo: MutualFundRepository,
    resolver: IdentityResolver,
    settings: Any,
    resume: bool,
    only_symbol: str | None,
    limit: int | None,
) -> None:
    tracker = RunTracker(
        client, source="rupeevest", mode="holdings", triggered_by=settings.triggered_by
    )
    tracker.start()
    dead_letter = DeadLetterLogger(client, run_id=tracker.run_id)
    today = date.today()

    if only_symbol:
        targets = [{"id": None, "rupeevest_schemecode": int(only_symbol)}]
    else:
        targets = repo.list_rupeevest_schemecodes()
        if limit:
            targets = targets[:limit]

    skip_ids: set[str] = tracker.already_processed_ids() if resume else set()
    logger.info(f"Holdings mode: {len(targets)} funds")

    try:
        with RupeevScraper(settings=settings) as scraper:
            for target in targets:
                schemecode = target["rupeevest_schemecode"]
                ext_id = str(schemecode)

                if ext_id in skip_ids:
                    continue

                fund_id = (
                    UUID(target["id"]) if target.get("id")
                    else resolver.resolve_fund("rupeevest_schemecode", schemecode)
                )
                if fund_id is None:
                    tracker.mark_item_failed(ext_id)
                    continue

                try:
                    raw_holdings = scraper.fetch_holdings(schemecode)
                except Exception as e:
                    logger.warning(f"Holdings fetch failed for {schemecode}: {e}")
                    dead_letter.record(
                        source="rupeevest",
                        error_type="http_error",
                        error_msg=str(e)[:1000],
                        id_type="rupeevest_schemecode",
                        id_value=ext_id,
                    )
                    tracker.mark_item_failed(ext_id)
                    continue

                if not raw_holdings:
                    # Fund has no published holdings (common for debt/liquid funds)
                    continue

                holdings_ok = 0
                for h_raw in raw_holdings:
                    try:
                        holding = scraper.transform_holding(h_raw)
                    except Exception:
                        continue

                    # Resolve stock: try fincode first, then company name
                    stock_id = None
                    if holding.fincode is not None:
                        stock_id = resolver.resolve_stock("rupeevest_fincode", holding.fincode)

                    if stock_id is None and holding.display_name:
                        stock_id = _resolve_stock_by_name(client, holding.display_name)

                    if stock_id is None:
                        dead_letter.record(
                            source="rupeevest",
                            error_type="unresolved_id",
                            error_msg=f"no stocks row for '{holding.display_name}' / fincode={holding.fincode}",
                            id_type="canonical_name",
                            id_value=holding.display_name,
                            raw_payload=h_raw,
                        )
                        continue

                    repo.upsert_holding(
                        fund_id=fund_id,
                        stock_id=stock_id,
                        as_of_date=today,
                        holding_pct=holding.holding_pct,
                        holding_value_cr=holding.market_value,
                        no_of_shares=holding.no_of_shares,
                        raw_payload=h_raw,
                    )
                    holdings_ok += 1

                if holdings_ok > 0:
                    tracker.mark_item_ok(ext_id)
                else:
                    tracker.mark_item_failed(ext_id)

    except Exception as e:
        tracker.finish("failed", error_summary=f"{type(e).__name__}: {e}")
        raise

    status = "partial" if tracker._records_failed > 0 else "success"
    tracker.finish(status)
    logger.info(f"Holdings done: {tracker._records_ok} ok, {tracker._records_failed} failed")


def _resolve_stock_by_name(client: Any, name: str) -> UUID | None:
    """Resolve a stock by company name using prefix ilike. Best-effort."""
    from uuid import UUID

    # Try first 20 chars to handle truncated names
    prefix = name[:20].strip()
    if len(prefix) < 5:
        return None
    resp = (
        client.table("stocks")
        .select("id")
        .ilike("canonical_name", prefix + "%")
        .limit(1)
        .execute()
    )
    return UUID(resp.data[0]["id"]) if resp.data else None


# ─── Fincode mode ─────────────────────────────────────────────────────────────


def _run_fincode(
    client: Any,
    resolver: IdentityResolver,
    settings: Any,
    limit: int | None,
) -> None:
    """Update stocks.rupeevest_fincode by matching BSE/NSE codes from Rupeevest fincode list."""
    tracker = RunTracker(
        client, source="rupeevest", mode="fincode", triggered_by=settings.triggered_by
    )
    tracker.start()
    dead_letter = DeadLetterLogger(client, run_id=tracker.run_id)
    logger.info("Fincode mode: fetching stock fincode map")

    try:
        with RupeevScraper(settings=settings) as scraper:
            raw_list = scraper.fetch_fincode_map()
            if limit:
                raw_list = raw_list[:limit]
            logger.info(f"Got {len(raw_list)} fincode entries")

            for record in raw_list:
                ext_id = str(record.get("fincode", ""))
                try:
                    entry = scraper.transform_fincode(record)
                except Exception as e:
                    dead_letter.record(
                        source="rupeevest", error_type="validation_error",
                        error_msg=str(e)[:500], id_value=ext_id, raw_payload=record,
                    )
                    tracker.mark_item_failed(ext_id)
                    continue

                # Try BSE code first, then NSE symbol
                stock_id = None
                bse = entry.bse_code()
                nse = entry.nse_symbol()

                if bse:
                    stock_id = resolver.resolve_stock("bse_code", bse)
                if stock_id is None and nse:
                    stock_id = resolver.resolve_stock("nse_symbol", nse)

                if stock_id is None:
                    dead_letter.record(
                        source="rupeevest",
                        error_type="unresolved_id",
                        error_msg=f"no stocks row for BSE={bse} NSE={nse}",
                        id_type="rupeevest_fincode",
                        id_value=ext_id,
                    )
                    tracker.mark_item_failed(ext_id)
                    continue

                # Update the stocks row with this fincode
                try:
                    client.table("stocks").update(
                        {"rupeevest_fincode": entry.fincode}
                    ).eq("id", str(stock_id)).execute()
                    resolver._stock_cache.pop(("rupeevest_fincode", str(entry.fincode)), None)
                    tracker.mark_item_ok(ext_id)
                except Exception as e:
                    # UNIQUE conflict — another stock already has this fincode
                    dead_letter.record(
                        source="rupeevest",
                        error_type="handler_error",
                        error_msg=str(e)[:500],
                        id_type="rupeevest_fincode",
                        id_value=ext_id,
                    )
                    tracker.mark_item_failed(ext_id)

    except Exception as e:
        tracker.finish("failed", error_summary=f"{type(e).__name__}: {e}")
        raise

    status = "partial" if tracker._records_failed > 0 else "success"
    tracker.finish(status)
    logger.info(f"Fincode done: {tracker._records_ok} ok, {tracker._records_failed} failed")


if __name__ == "__main__":
    main()
