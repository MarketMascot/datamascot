"""Run tracker — manages scraper_runs and scraper_run_items rows.

A "run" is one invocation of a scraper script. The tracker:
  - Inserts a 'running' row in scraper_runs at start
  - Records each processed external_id in scraper_run_items
  - Updates the row to 'success'/'failed'/'partial' on completion
  - Provides --resume support by querying the last failed run
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Literal
from uuid import UUID

if TYPE_CHECKING:
    from supabase import Client

logger = logging.getLogger(__name__)

RunStatus = Literal["running", "success", "failed", "partial"]
TriggeredBy = Literal["cron", "manual", "retry", "bootstrap"]


class RunTracker:
    """Tracks a single scraper run end-to-end."""

    def __init__(
        self,
        client: Client,
        source: str,
        mode: str | None = None,
        triggered_by: TriggeredBy = "manual",
    ) -> None:
        self._client = client
        self._source = source
        self._mode = mode
        self._triggered_by = triggered_by
        self._run_id: UUID | None = None
        self._records_ok = 0
        self._records_failed = 0
        self._last_item_id: str | None = None

    @property
    def run_id(self) -> UUID:
        if self._run_id is None:
            raise RuntimeError("RunTracker not started — call start() first")
        return self._run_id

    def start(self) -> UUID:
        """Insert a 'running' row and return the run UUID."""
        resp = (
            self._client.table("scraper_runs")
            .insert(
                {
                    "source": self._source,
                    "mode": self._mode,
                    "status": "running",
                    "triggered_by": self._triggered_by,
                }
            )
            .execute()
        )
        self._run_id = UUID(resp.data[0]["id"])
        logger.info(
            f"Started run {self._run_id} for {self._source}"
            + (f"/{self._mode}" if self._mode else "")
        )
        return self._run_id

    def mark_item_ok(self, external_id: str) -> None:
        """Record a successfully processed item."""
        self._records_ok += 1
        self._last_item_id = external_id
        self._insert_item(external_id, "ok")

    def mark_item_failed(self, external_id: str) -> None:
        """Record a failed item (dead-lettered)."""
        self._records_failed += 1
        self._insert_item(external_id, "failed")

    def _insert_item(self, external_id: str, status: Literal["ok", "failed"]) -> None:
        if self._run_id is None:
            return
        try:
            self._client.table("scraper_run_items").upsert(
                {
                    "run_id": str(self._run_id),
                    "external_id": external_id,
                    "status": status,
                },
                on_conflict="run_id,external_id",
            ).execute()
        except Exception as e:
            logger.warning(f"Failed to record run item {external_id}: {e}")

    def finish(
        self,
        status: RunStatus,
        error_summary: str | None = None,
    ) -> None:
        """Update the scraper_runs row to its final state."""
        if self._run_id is None:
            return
        update = {
            "status": status,
            "finished_at": datetime.now(UTC).isoformat(),
            "records_ok": self._records_ok,
            "records_failed": self._records_failed,
            "last_item_id": self._last_item_id,
        }
        if error_summary:
            update["error_summary"] = error_summary[:2000]  # cap to a reasonable size
        try:
            self._client.table("scraper_runs").update(update).eq(
                "id", str(self._run_id)
            ).execute()
        except Exception as e:
            logger.exception(f"Failed to finalize run {self._run_id}: {e}")
        logger.info(
            f"Finished run {self._run_id}: status={status} "
            f"ok={self._records_ok} failed={self._records_failed}"
        )

    # ─── Resume helpers ──────────────────────────────────────────────────────

    def already_processed_ids(self, within_hours: int = 24) -> set[str]:
        """Return external_ids successfully processed in the most recent failed run.

        Used by --resume to skip work that's already done.
        """
        since = (datetime.now(UTC) - timedelta(hours=within_hours)).isoformat()
        # Find the most recent failed run for this source+mode
        q = (
            self._client.table("scraper_runs")
            .select("id")
            .eq("source", self._source)
            .eq("status", "failed")
            .gte("started_at", since)
        )
        if self._mode:
            q = q.eq("mode", self._mode)
        runs = q.order("started_at", desc=True).limit(1).execute()
        if not runs.data:
            return set()
        last_run_id = runs.data[0]["id"]

        items = (
            self._client.table("scraper_run_items")
            .select("external_id")
            .eq("run_id", last_run_id)
            .eq("status", "ok")
            .execute()
        )
        ids = {row["external_id"] for row in items.data}
        logger.info(
            f"Resume: skipping {len(ids)} items already processed in run {last_run_id}"
        )
        return ids

    def dead_letter_ids(self, within_hours: int = 24) -> list[dict[str, str]]:
        """Return unresolved scraper_errors rows for this source+mode.

        Used by --retry-errors. Returns list of {id_type, id_value, raw_payload}.
        """
        since = (datetime.now(UTC) - timedelta(hours=within_hours)).isoformat()
        resp = (
            self._client.table("scraper_errors")
            .select("id, id_type, id_value, raw_payload")
            .eq("source", self._source)
            .eq("resolved", False)
            .gte("created_at", since)
            .execute()
        )
        return resp.data or []
