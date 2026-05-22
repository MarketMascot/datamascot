"""Pipeline runner — orchestrates fetch → validate → resolve → upsert for a scraper.

This is the only place that knows the full lifecycle:
  1. Start a scraper_runs row (status='running')
  2. Optionally load resume set (already-processed external_ids)
  3. For each record: scraper.transform → resolve identifier → repo.upsert
  4. On per-row failure: dead-letter the row + record as failed in run_items
  5. On hard failure: mark run as 'failed' and re-raise
  6. On success: mark run as 'success' (or 'partial' if any rows failed)
  7. Guard: if 0 valid records, raise EmptyRunError — source format likely changed
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING
from uuid import UUID

from pydantic import BaseModel, ValidationError

from dastock.pipeline.dead_letter import DeadLetterLogger
from dastock.pipeline.run_tracker import RunTracker, TriggeredBy
from dastock.scrapers.exceptions import EmptyRunError

if TYPE_CHECKING:
    from supabase import Client

    from dastock.scrapers.base import BaseScraper

logger = logging.getLogger(__name__)


# A handler is a callable that takes a validated Pydantic model and writes
# it to Supabase. Each scraper provides its own — the runner stays generic.
RecordHandler = Callable[[BaseModel], UUID | None]


def run_pipeline(
    *,
    scraper: BaseScraper,
    client: Client,
    handler: RecordHandler,
    mode: str | None = None,
    triggered_by: TriggeredBy = "manual",
    resume: bool = False,
    only_external_id: str | None = None,
    require_min_records: int = 0,
) -> RunTracker:
    """Execute a full scraper run.

    Args:
        scraper: the source-specific scraper instance
        client: Supabase client
        handler: callable that persists a validated model. Returns the upserted
                 row's UUID (or None if it chose to skip). May raise to dead-letter the row.
        mode: optional sub-mode (e.g. 'qvt' vs 'swot' for Trendlyne)
        triggered_by: how this run was triggered
        resume: if True, skip external_ids already processed in last failed run
        only_external_id: debug helper — process only the matching record
        require_min_records: minimum valid records to consider the run non-empty.
                             Default 0 means even an empty source is OK; set to 1 (or higher)
                             when you expect data, to catch silent source format changes.

    Returns:
        The RunTracker after completion (caller can inspect record counts).

    Raises:
        EmptyRunError if `require_min_records` is positive and the run produced fewer.
        Any other exception → run marked 'failed', re-raised to caller.
    """
    tracker = RunTracker(client, scraper.SOURCE_NAME, mode=mode, triggered_by=triggered_by)
    tracker.start()
    dead_letter = DeadLetterLogger(client, run_id=tracker.run_id)

    skip_ids: set[str] = tracker.already_processed_ids() if resume else set()

    try:
        raw = scraper.fetch_raw()
        for record in scraper.parse(raw):
            ext_id = scraper.external_id_of(record)

            if only_external_id and ext_id != only_external_id:
                continue
            if ext_id in skip_ids:
                continue

            try:
                model = scraper.transform(record)
            except ValidationError as e:
                logger.warning(f"Validation failed for {scraper.SOURCE_NAME}/{ext_id}: {e}")
                dead_letter.record(
                    source=scraper.SOURCE_NAME,
                    error_type="validation_error",
                    error_msg=str(e)[:1000],
                    id_value=ext_id,
                    raw_payload=record,
                )
                tracker.mark_item_failed(ext_id)
                continue
            except Exception as e:
                logger.exception(f"Transform crashed for {scraper.SOURCE_NAME}/{ext_id}")
                dead_letter.record(
                    source=scraper.SOURCE_NAME,
                    error_type="parse_error",
                    error_msg=str(e)[:1000],
                    id_value=ext_id,
                    raw_payload=record,
                )
                tracker.mark_item_failed(ext_id)
                continue

            try:
                result = handler(model)
            except _SkipRecord as skip:
                # Handler chose to dead-letter this row (e.g., unresolved identifier)
                dead_letter.record(
                    source=scraper.SOURCE_NAME,
                    error_type=skip.error_type,
                    error_msg=skip.message,
                    id_type=skip.id_type,
                    id_value=skip.id_value or ext_id,
                    raw_payload=record,
                )
                tracker.mark_item_failed(ext_id)
                continue
            except Exception as e:
                logger.exception(f"Handler crashed for {scraper.SOURCE_NAME}/{ext_id}")
                dead_letter.record(
                    source=scraper.SOURCE_NAME,
                    error_type="handler_error",
                    error_msg=str(e)[:1000],
                    id_value=ext_id,
                    raw_payload=record,
                )
                tracker.mark_item_failed(ext_id)
                continue

            if result is None:
                tracker.mark_item_failed(ext_id)
            else:
                tracker.mark_item_ok(ext_id)

    except Exception as e:
        # Hard failure — let the tracker record it, then re-raise
        tracker.finish("failed", error_summary=f"{type(e).__name__}: {e}")
        raise

    # Guard against "0 valid records" silent format change
    if tracker._records_ok < require_min_records:
        msg = (
            f"{scraper.SOURCE_NAME} produced {tracker._records_ok} valid records "
            f"(required >= {require_min_records}); source format may have changed"
        )
        tracker.finish("failed", error_summary=msg)
        raise EmptyRunError(msg)

    status = "partial" if tracker._records_failed > 0 else "success"
    tracker.finish(status)
    return tracker


class _SkipRecord(Exception):  # noqa: N818  (intentional sentinel, not user-facing error)
    """Internal sentinel that a handler raises to dead-letter a record without crashing."""

    def __init__(
        self,
        error_type: str,
        message: str,
        id_type: str | None = None,
        id_value: str | None = None,
    ) -> None:
        super().__init__(message)
        self.error_type = error_type
        self.message = message
        self.id_type = id_type
        self.id_value = id_value


def skip_record(
    error_type: str,
    message: str,
    *,
    id_type: str | None = None,
    id_value: str | None = None,
) -> None:
    """Helper for handlers — raise to dead-letter the current record cleanly.

    Usage in a handler:
        if stock_id is None:
            skip_record("unresolved_id", "no stocks row for fincode", id_type="rupeevest_fincode", id_value=str(fincode))
    """
    raise _SkipRecord(error_type, message, id_type=id_type, id_value=id_value)
