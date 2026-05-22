"""One-time bootstrap of stocks and mutual_funds bible tables.

Run this:
  - Once after creating the Supabase project (first-time setup)
  - Periodically (monthly) to pick up new listings

Stocks bootstrap order:
  1. Fetch Dhan detailed scrip master (~22k equity rows)
  2. For each EQUITY row with valid ISIN: upsert stocks row keyed by ISIN
     Same ISIN on NSE and BSE → same internal UUID (the dedup point)
  3. NSE rows fill in nse_symbol; BSE rows fill in bse_code

Mutual funds bootstrap order:
  1. Fetch mfapi.in /mf master list (~37k schemes)
  2. For each scheme with isinGrowth: upsert mutual_funds row keyed by amfi_code
     Stores amfi_code, isin_growth, isin_dividend, scheme_name

After this script: bible tables have all identity columns populated.
Session 5 (Rupeevest) will fill in rupeevest_fincode / rupeevest_schemecode.
Session 6 (Trendlyne) and Session 7 (Scan360) read identifiers, never write.

Usage:
  uv run python scripts/bootstrap_identity.py           # both stocks + funds
  uv run python scripts/bootstrap_identity.py --skip-stocks
  uv run python scripts/bootstrap_identity.py --skip-funds
  uv run python scripts/bootstrap_identity.py --limit 100   # test mode
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
from dastock.identity.resolver import IdentityResolver  # noqa: E402
from dastock.pipeline.dead_letter import DeadLetterLogger  # noqa: E402
from dastock.pipeline.run_tracker import RunTracker  # noqa: E402
from dastock.scrapers.dhan import DhanScraper  # noqa: E402
from dastock.scrapers.mfapi import MfapiScraper  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("dastock.bootstrap")


@click.command()
@click.option("--skip-stocks", is_flag=True, help="Skip the stocks bible bootstrap")
@click.option("--skip-funds", is_flag=True, help="Skip the mutual_funds bible bootstrap")
@click.option(
    "--limit",
    type=int,
    default=None,
    help="Process at most N rows per source (testing)",
)
def main(skip_stocks: bool, skip_funds: bool, limit: int | None) -> None:
    settings = get_settings()
    client = get_supabase()
    resolver = IdentityResolver(client)

    if not skip_stocks:
        _bootstrap_stocks(client, resolver, settings, limit)
    else:
        logger.info("Skipping stocks bootstrap (--skip-stocks)")

    if not skip_funds:
        _bootstrap_funds(client, resolver, settings, limit)
    else:
        logger.info("Skipping mutual funds bootstrap (--skip-funds)")


# ─── Stocks bootstrap ────────────────────────────────────────────────────────


def _bootstrap_stocks(
    client: Any,
    resolver: IdentityResolver,
    settings: Any,
    limit: int | None,
) -> None:
    tracker = RunTracker(
        client, source="dhan", mode="bootstrap", triggered_by="bootstrap"
    )
    tracker.start()
    dead_letter = DeadLetterLogger(client, run_id=tracker.run_id)
    logger.info(f"Stocks bootstrap run {tracker.run_id} started")

    # Cache by ISIN so we resolve NSE+BSE listings of the same stock to one UUID
    isin_to_uuid: dict[str, str] = {}

    try:
        with DhanScraper(settings=settings) as scraper:
            csv_text = scraper.fetch_security_master()
            logger.info("Downloaded Dhan security master CSV")

            row_count = 0
            for record in scraper.parse_security_master(csv_text):
                if limit and row_count >= limit:
                    break
                row_count += 1

                try:
                    model = scraper.transform_security(record)
                except Exception as e:
                    dead_letter.record(
                        source="dhan",
                        error_type="validation_error",
                        error_msg=str(e)[:1000],
                        id_value=record.get("SECURITY_ID"),
                        raw_payload=record,
                    )
                    tracker.mark_item_failed(str(record.get("SECURITY_ID", "?")))
                    continue

                # We already filtered to INSTRUMENT=EQUITY in the CSV parser.
                # Don't further filter by SERIES — BSE has X/XT/SM/ST/MT groups
                # (X-group, SME tiers) which are legitimate equity listings.
                # Bonds/govt-secs come through with different INSTRUMENT codes,
                # not as EQUITY, so they're already excluded.

                # Upsert keyed by ISIN — same ISIN means same UUID
                # For NSE rows: trading_symbol is the human ticker (e.g. RELIANCE)
                # For BSE rows: security_id is the numeric BSE code (e.g. 500325).
                #   UNDERLYING_SYMBOL is Dhan-normalized to the NSE-equivalent
                #   ticker, NOT the BSE-native scrip name — so we store
                #   security_id as bse_code.
                kwargs: dict[str, Any] = {
                    "canonical_name": model.symbol_name,
                    "isin": model.isin,
                    "dhan_security_id": model.security_id,
                }
                if model.exchange == "NSE":
                    kwargs["nse_symbol"] = model.trading_symbol
                elif model.exchange == "BSE":
                    kwargs["bse_code"] = model.security_id

                try:
                    if model.isin and model.isin in isin_to_uuid:
                        # We already have a stocks row for this ISIN.
                        # Update with the other-exchange listing columns.
                        existing_uuid = isin_to_uuid[model.isin]
                        update: dict[str, Any] = {}
                        if model.exchange == "NSE":
                            update["nse_symbol"] = model.trading_symbol
                            # Prefer the NSE dhan_security_id (used for EOD fetches
                            # since we query NSE_EQ segment by default for dual-listed).
                            update["dhan_security_id"] = model.security_id
                        elif model.exchange == "BSE":
                            update["bse_code"] = model.security_id
                            # Only set dhan_security_id if not already set by NSE row
                        if update:
                            try:  # noqa: SIM105
                                client.table("stocks").update(update).eq(
                                    "id", existing_uuid
                                ).execute()
                            except Exception:
                                # dhan_security_id UNIQUE conflict is expected when
                                # we try to set it to the NSE ID (already taken by
                                # a different NSE-only stock). Skip silently.
                                pass
                        # Invalidate caches
                        for k, v in update.items():
                            resolver._stock_cache.pop((k, str(v)), None)
                        tracker.mark_item_ok(model.security_id)
                        continue

                    stock_uuid = resolver.upsert_stock(**kwargs)
                    if model.isin:
                        isin_to_uuid[model.isin] = str(stock_uuid)
                    tracker.mark_item_ok(model.security_id)
                except Exception as e:
                    logger.warning(f"Failed to upsert {model.security_id}: {e}")
                    dead_letter.record(
                        source="dhan",
                        error_type="handler_error",
                        error_msg=str(e)[:1000],
                        id_type="dhan_security_id",
                        id_value=model.security_id,
                        raw_payload=record,
                    )
                    tracker.mark_item_failed(model.security_id)

                if row_count % 1000 == 0:
                    logger.info(
                        f"  processed {row_count} rows "
                        f"(ok={tracker._records_ok}, failed={tracker._records_failed})"
                    )

    except Exception as e:
        tracker.finish("failed", error_summary=f"{type(e).__name__}: {e}")
        raise

    status = "partial" if tracker._records_failed > 0 else "success"
    tracker.finish(status)
    logger.info(
        f"Stocks bootstrap done: {tracker._records_ok} ok, {tracker._records_failed} failed, "
        f"{len(isin_to_uuid)} unique ISINs"
    )


# ─── Mutual funds bootstrap ──────────────────────────────────────────────────


def _bootstrap_funds(
    client: Any,
    resolver: IdentityResolver,
    settings: Any,
    limit: int | None,
) -> None:
    tracker = RunTracker(
        client, source="mfapi", mode="bootstrap", triggered_by="bootstrap"
    )
    tracker.start()
    dead_letter = DeadLetterLogger(client, run_id=tracker.run_id)
    logger.info(f"Mutual funds bootstrap run {tracker.run_id} started")

    try:
        with MfapiScraper(settings=settings) as scraper:
            schemes = scraper.fetch_scheme_list()
            logger.info(f"Downloaded {len(schemes)} schemes from mfapi.in")

            processed = 0
            for record in scraper.parse_scheme_list(schemes):
                if limit and processed >= limit:
                    break

                amfi_code = str(record.get("schemeCode", ""))
                try:
                    model = scraper.transform_scheme(record)
                except Exception as e:
                    dead_letter.record(
                        source="mfapi",
                        error_type="validation_error",
                        error_msg=str(e)[:1000],
                        id_value=amfi_code,
                        raw_payload=record,
                    )
                    tracker.mark_item_failed(amfi_code)
                    continue

                # Skip schemes without any ISIN — usually wound-up legacy schemes
                if not model.isin_growth and not model.isin_div_reinvestment:
                    continue

                try:
                    resolver.upsert_fund(
                        scheme_name=model.scheme_name,
                        amfi_code=str(model.scheme_code),
                        isin_growth=model.isin_growth,
                        isin_dividend=model.isin_div_reinvestment,
                    )
                    tracker.mark_item_ok(amfi_code)
                    processed += 1
                except Exception as e:
                    logger.warning(f"Failed to upsert scheme {amfi_code}: {e}")
                    dead_letter.record(
                        source="mfapi",
                        error_type="handler_error",
                        error_msg=str(e)[:1000],
                        id_type="amfi_code",
                        id_value=amfi_code,
                        raw_payload=record,
                    )
                    tracker.mark_item_failed(amfi_code)

                if processed % 1000 == 0 and processed > 0:
                    logger.info(
                        f"  processed {processed} schemes "
                        f"(ok={tracker._records_ok}, failed={tracker._records_failed})"
                    )

    except Exception as e:
        tracker.finish("failed", error_summary=f"{type(e).__name__}: {e}")
        raise

    status = "partial" if tracker._records_failed > 0 else "success"
    tracker.finish(status)
    logger.info(
        f"Funds bootstrap done: {tracker._records_ok} ok, {tracker._records_failed} failed"
    )


if __name__ == "__main__":
    main()
