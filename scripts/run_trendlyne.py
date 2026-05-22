"""Trendlyne scraper — QVT scores and SWOT analysis.

Modes:
  qvt   — weekly: Quality / Valuation / Technical scores → trendlyne_qvt
  swot  — weekly: Strengths / Weakness / Opportunity / Threats → trendlyne_swot

Usage:
  uv run python scripts/run_trendlyne.py --mode qvt
  uv run python scripts/run_trendlyne.py --mode swot
  uv run python scripts/run_trendlyne.py --mode qvt --limit 50
  uv run python scripts/run_trendlyne.py --mode qvt --only-symbol RELIANCE
  uv run python scripts/run_trendlyne.py --mode qvt --resume
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
from dastock.db.repositories.analytics import AnalyticsRepository  # noqa: E402
from dastock.pipeline.dead_letter import DeadLetterLogger  # noqa: E402
from dastock.pipeline.run_tracker import RunTracker  # noqa: E402
from dastock.scrapers.exceptions import CircuitOpenError  # noqa: E402
from dastock.scrapers.trendlyne import TrendlyneScraper  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("dastock.scripts.run_trendlyne")


@click.command()
@click.option(
    "--mode",
    type=click.Choice(["qvt", "swot"]),
    default="qvt",
    show_default=True,
)
@click.option("--resume", is_flag=True)
@click.option("--only-symbol", type=str, default=None, help="Single NSE symbol (debug)")
@click.option("--rps", type=float, default=None)
@click.option("--limit", type=int, default=None)
def main(
    mode: str,
    resume: bool,
    only_symbol: str | None,
    rps: float | None,
    limit: int | None,
) -> None:
    settings = get_settings()
    if rps is not None:
        settings.trendlyne_rate_limit_rps = rps

    client = get_supabase()
    repo = AnalyticsRepository(client)

    if mode == "qvt":
        _run_qvt(client, repo, settings, resume, only_symbol, limit)
    else:
        _run_swot(client, repo, settings, resume, only_symbol, limit)


# ─── QVT mode ─────────────────────────────────────────────────────────────────


def _run_qvt(
    client: Any,
    repo: AnalyticsRepository,
    settings: Any,
    resume: bool,
    only_symbol: str | None,
    limit: int | None,
) -> None:
    tracker = RunTracker(
        client, source="trendlyne", mode="qvt", triggered_by=settings.triggered_by
    )
    tracker.start()
    dead_letter = DeadLetterLogger(client, run_id=tracker.run_id)
    today = date.today()

    targets = _build_targets(client, only_symbol, limit)
    skip_ids: set[str] = tracker.already_processed_ids() if resume else set()
    logger.info(f"QVT mode: {len(targets)} stocks")

    try:
        with TrendlyneScraper(settings=settings) as scraper:
            for target in targets:
                symbol = target["nse_symbol"]
                stock_id = UUID(target["id"])

                if symbol in skip_ids:
                    continue

                try:
                    html = scraper.fetch_qvt(symbol)
                except CircuitOpenError:
                    logger.error("Circuit open — aborting QVT run")
                    break
                except Exception as e:
                    logger.warning(f"QVT fetch failed for {symbol}: {e}")
                    dead_letter.record(
                        source="trendlyne",
                        error_type="http_error",
                        error_msg=str(e)[:1000],
                        id_type="nse_symbol",
                        id_value=symbol,
                    )
                    tracker.mark_item_failed(symbol)
                    continue

                model = scraper.parse_qvt(symbol, html)

                if not model.has_data:
                    # Empty-state page (newly listed or no data) — skip silently.
                    logger.debug(f"{symbol}: no QVT data available")
                    continue

                try:
                    repo.upsert_qvt(
                        stock_id=stock_id,
                        analysis_date=today,
                        quality_score=model.quality_score,
                        quality_insight=model.quality_insight,
                        valuation_score=model.valuation_score,
                        valuation_insight=model.valuation_insight,
                        technical_score=model.technical_score,
                        technical_insight=model.technical_insight,
                    )
                    tracker.mark_item_ok(symbol)
                except Exception as e:
                    dead_letter.record(
                        source="trendlyne",
                        error_type="handler_error",
                        error_msg=str(e)[:1000],
                        id_type="nse_symbol",
                        id_value=symbol,
                    )
                    tracker.mark_item_failed(symbol)

    except Exception as e:
        tracker.finish("failed", error_summary=f"{type(e).__name__}: {e}")
        raise

    status = "partial" if tracker._records_failed > 0 else "success"
    tracker.finish(status)
    logger.info(f"QVT done: {tracker._records_ok} ok, {tracker._records_failed} failed")


# ─── SWOT mode ────────────────────────────────────────────────────────────────


def _run_swot(
    client: Any,
    repo: AnalyticsRepository,
    settings: Any,
    resume: bool,
    only_symbol: str | None,
    limit: int | None,
) -> None:
    tracker = RunTracker(
        client, source="trendlyne", mode="swot", triggered_by=settings.triggered_by
    )
    tracker.start()
    dead_letter = DeadLetterLogger(client, run_id=tracker.run_id)
    today = date.today()

    targets = _build_targets(client, only_symbol, limit)
    skip_ids: set[str] = tracker.already_processed_ids() if resume else set()
    logger.info(f"SWOT mode: {len(targets)} stocks")

    try:
        with TrendlyneScraper(settings=settings) as scraper:
            for target in targets:
                symbol = target["nse_symbol"]
                stock_id = UUID(target["id"])

                if symbol in skip_ids:
                    continue

                try:
                    html = scraper.fetch_swot(symbol)
                except CircuitOpenError:
                    logger.error("Circuit open — aborting SWOT run")
                    break
                except Exception as e:
                    logger.warning(f"SWOT fetch failed for {symbol}: {e}")
                    dead_letter.record(
                        source="trendlyne",
                        error_type="http_error",
                        error_msg=str(e)[:1000],
                        id_type="nse_symbol",
                        id_value=symbol,
                    )
                    tracker.mark_item_failed(symbol)
                    continue

                model = scraper.parse_swot(symbol, html)

                if not model.has_data:
                    logger.debug(f"{symbol}: no SWOT data available")
                    continue

                try:
                    repo.upsert_swot(
                        stock_id=stock_id,
                        analysis_date=today,
                        strengths_count=model.strengths_count,
                        strengths_text=model.strengths_text,
                        weakness_count=model.weakness_count,
                        weakness_text=model.weakness_text,
                        opportunities_count=model.opportunities_count,
                        opportunities_text=model.opportunities_text,
                        threats_count=model.threats_count,
                        threats_text=model.threats_text,
                    )
                    tracker.mark_item_ok(symbol)
                except Exception as e:
                    dead_letter.record(
                        source="trendlyne",
                        error_type="handler_error",
                        error_msg=str(e)[:1000],
                        id_type="nse_symbol",
                        id_value=symbol,
                    )
                    tracker.mark_item_failed(symbol)

    except Exception as e:
        tracker.finish("failed", error_summary=f"{type(e).__name__}: {e}")
        raise

    status = "partial" if tracker._records_failed > 0 else "success"
    tracker.finish(status)
    logger.info(f"SWOT done: {tracker._records_ok} ok, {tracker._records_failed} failed")


# ─── helpers ─────────────────────────────────────────────────────────────────


def _build_targets(
    client: Any,
    only_symbol: str | None,
    limit: int | None,
) -> list[dict[str, Any]]:
    if only_symbol:
        resp = (
            client.table("stocks")
            .select("id, nse_symbol")
            .eq("nse_symbol", only_symbol)
            .limit(1)
            .execute()
        )
        return resp.data or []

    resp = (
        client.table("stocks")
        .select("id, nse_symbol")
        .not_.is_("nse_symbol", "null")
        .eq("listing_status", "active")
        .execute()
    )
    targets = resp.data or []
    if limit:
        targets = targets[:limit]
    return targets


if __name__ == "__main__":
    main()
