"""Daily NAV refresh from mfapi.in.

Usage:
    uv run python scripts/run_mfapi.py                # default daily run
    uv run python scripts/run_mfapi.py --resume       # skip already-processed
    uv run python scripts/run_mfapi.py --retry-errors # only failed rows from last 24h
    uv run python scripts/run_mfapi.py --only-symbol 125497  # single scheme debug
    uv run python scripts/run_mfapi.py --rps 1.0      # override rate limit
    uv run python scripts/run_mfapi.py --limit 50     # only first 50 funds (test mode)

The bootstrap mode (populate mutual_funds from /mf list) is invoked by
scripts/bootstrap_identity.py — not by this script. This script assumes
mutual_funds already has rows with amfi_code populated.
"""

from __future__ import annotations

import logging
import os
import sys
from decimal import Decimal
from typing import Any

import click

# Ensure src/ is on the path when run as a script
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from datetime import UTC

from dastock.config import get_settings  # noqa: E402
from dastock.db.client import get_supabase  # noqa: E402
from dastock.db.repositories.mutual_funds import MutualFundRepository  # noqa: E402
from dastock.pipeline.dead_letter import DeadLetterLogger  # noqa: E402
from dastock.pipeline.run_tracker import RunTracker  # noqa: E402
from dastock.scrapers.exceptions import EmptyRunError  # noqa: E402
from dastock.scrapers.mfapi import MfapiScraper  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("dastock.scripts.run_mfapi")


# A NAV change greater than this ratio vs the previous NAV is flagged
# "suspicious" but still stored. Most legitimate moves stay well under 5×.
SUSPICIOUS_NAV_RATIO = Decimal("5.0")


@click.command()
@click.option("--resume", is_flag=True, help="Skip items processed in last failed run")
@click.option("--retry-errors", is_flag=True, help="Only retry rows in scraper_errors")
@click.option("--only-symbol", type=str, default=None, help="Run for a single AMFI code")
@click.option(
    "--rps",
    type=float,
    default=None,
    help="Override MFAPI_RATE_LIMIT_RPS for this run",
)
@click.option(
    "--limit",
    type=int,
    default=None,
    help="Process at most N funds (for testing/debugging)",
)
def main(
    resume: bool,
    retry_errors: bool,
    only_symbol: str | None,
    rps: float | None,
    limit: int | None,
) -> None:
    settings = get_settings()
    if rps is not None:
        # Override at the Settings level so BaseScraper picks it up
        settings.mfapi_rate_limit_rps = rps
        logger.info(f"Rate limit overridden to {rps} RPS")

    client = get_supabase()
    repo = MutualFundRepository(client)
    tracker = RunTracker(
        client,
        source="mfapi",
        mode="nav",
        triggered_by=settings.triggered_by,
    )
    tracker.start()
    dead_letter = DeadLetterLogger(client, run_id=tracker.run_id)

    # ─── Determine target set of funds ───────────────────────────────────────
    if retry_errors:
        targets = _targets_from_dead_letter(tracker)
        logger.info(f"Retry mode: {len(targets)} unresolved errors from last 24h")
    elif only_symbol:
        targets = [{"id": None, "amfi_code": only_symbol}]
        logger.info(f"Single-symbol mode: {only_symbol}")
    else:
        targets = repo.list_amfi_codes()
        if limit:
            targets = targets[:limit]
        logger.info(f"Standard mode: {len(targets)} funds with amfi_code")

    if resume and not retry_errors and not only_symbol:
        skip_ids = tracker.already_processed_ids()
        if skip_ids:
            before = len(targets)
            targets = [t for t in targets if str(t["amfi_code"]) not in skip_ids]
            logger.info(f"Resume: skipped {before - len(targets)} already-processed")

    # ─── Iterate and upsert ──────────────────────────────────────────────────
    try:
        with MfapiScraper(settings=settings) as scraper:
            for target in targets:
                amfi_code = str(target["amfi_code"])
                fund_id = target.get("id")  # None if --only-symbol or --retry-errors

                try:
                    raw = scraper.fetch_latest_for(amfi_code)
                except Exception as e:
                    logger.warning(f"Fetch failed for {amfi_code}: {e}")
                    dead_letter.record(
                        source="mfapi",
                        error_type="http_error",
                        error_msg=str(e)[:1000],
                        id_type="amfi_code",
                        id_value=amfi_code,
                    )
                    tracker.mark_item_failed(amfi_code)
                    continue

                try:
                    model = scraper.transform_latest(raw)
                except Exception as e:
                    logger.warning(f"Validation failed for {amfi_code}: {e}")
                    dead_letter.record(
                        source="mfapi",
                        error_type="validation_error",
                        error_msg=str(e)[:1000],
                        id_type="amfi_code",
                        id_value=amfi_code,
                        raw_payload=raw,
                    )
                    tracker.mark_item_failed(amfi_code)
                    continue

                # If we don't have a fund_id (single-symbol or retry mode),
                # resolve via repo.list_amfi_codes — small enough to cache once
                if fund_id is None:
                    fund_id = _resolve_fund_id(client, amfi_code)
                    if fund_id is None:
                        dead_letter.record(
                            source="mfapi",
                            error_type="unresolved_id",
                            error_msg=f"no mutual_funds row for amfi_code {amfi_code}",
                            id_type="amfi_code",
                            id_value=amfi_code,
                        )
                        tracker.mark_item_failed(amfi_code)
                        continue

                latest = model.latest_nav_point
                # Sanity check: huge jump → flag but still store
                prev = repo.previous_nav(fund_id, before=latest.date)
                suspicious = False
                if prev is not None and prev > 0:
                    ratio = latest.nav / prev
                    if ratio > SUSPICIOUS_NAV_RATIO or ratio < (1 / SUSPICIOUS_NAV_RATIO):
                        suspicious = True
                        logger.warning(
                            f"Suspicious NAV jump for {amfi_code}: {prev} → {latest.nav}"
                        )

                repo.upsert_nav(
                    fund_id=fund_id,
                    nav_date=latest.date,
                    nav=latest.nav,
                    source="mfapi",
                    suspicious=suspicious,
                )
                tracker.mark_item_ok(amfi_code)

    except Exception as e:
        tracker.finish("failed", error_summary=f"{type(e).__name__}: {e}")
        raise

    # Mark unresolved errors as retried if this was a retry run
    if retry_errors:
        _mark_retried(client, [str(t["amfi_code"]) for t in targets])

    status = "partial" if tracker._records_failed > 0 else "success"
    tracker.finish(status)

    # In a non-debug run, require at least 1 valid record so we catch the
    # "source returned empty list" silent failure
    if not only_symbol and limit is None and tracker._records_ok == 0:
        raise EmptyRunError("mfapi run produced 0 valid NAV records")

    logger.info(
        f"Done: {tracker._records_ok} ok, {tracker._records_failed} failed"
    )


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _targets_from_dead_letter(tracker: RunTracker) -> list[dict[str, Any]]:
    """Build target list from unresolved scraper_errors rows."""
    errs = tracker.dead_letter_ids(within_hours=24)
    return [{"id": None, "amfi_code": e["id_value"]} for e in errs if e.get("id_value")]


def _resolve_fund_id(client: Any, amfi_code: str) -> Any:
    """One-off lookup for fund_id by amfi_code. Used by single-symbol / retry modes."""
    from uuid import UUID

    resp = (
        client.table("mutual_funds")
        .select("id")
        .eq("amfi_code", amfi_code)
        .limit(1)
        .execute()
    )
    return UUID(resp.data[0]["id"]) if resp.data else None


def _mark_retried(client: Any, amfi_codes: list[str]) -> None:
    """Mark scraper_errors rows as resolved=True for a retry run."""
    from datetime import datetime

    if not amfi_codes:
        return
    client.table("scraper_errors").update(
        {"resolved": True, "retried_at": datetime.now(UTC).isoformat()}
    ).eq("source", "mfapi").in_("id_value", amfi_codes).execute()


if __name__ == "__main__":
    main()
