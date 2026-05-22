"""Dead letter logging — writes failed records to the scraper_errors table.

Single-row failures (validation error, unresolved identifier, transient HTTP
error after retries) get logged here with their raw_payload, instead of
killing the entire run. The run continues; manual --retry-errors re-processes
these after fixes.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any
from uuid import UUID

if TYPE_CHECKING:
    from supabase import Client

logger = logging.getLogger(__name__)


# Truncate JSONB payloads larger than this (Postgres can handle more, but
# query performance degrades and we don't actually need >256KB of context).
MAX_PAYLOAD_BYTES = 256 * 1024


class DeadLetterLogger:
    """Writes failure rows to the scraper_errors Supabase table."""

    def __init__(self, client: Client, run_id: UUID | None = None) -> None:
        self._client = client
        self._run_id = run_id

    def record(
        self,
        *,
        source: str,
        error_type: str,
        error_msg: str | None = None,
        id_type: str | None = None,
        id_value: str | None = None,
        raw_payload: dict[str, Any] | None = None,
    ) -> None:
        """Record a failed row. Never raises — logging failures shouldn't kill the run."""
        try:
            payload = self._truncate_payload(raw_payload) if raw_payload else None
            row = {
                "run_id": str(self._run_id) if self._run_id else None,
                "source": source,
                "id_type": id_type,
                "id_value": str(id_value) if id_value is not None else None,
                "error_type": error_type,
                "error_msg": error_msg,
                "raw_payload": payload,
            }
            self._client.table("scraper_errors").insert(row).execute()
        except Exception as e:
            # We can't recover from a dead-letter logging failure; just log + continue
            logger.exception(f"Failed to write to scraper_errors: {e}")

    @staticmethod
    def _truncate_payload(payload: dict[str, Any]) -> dict[str, Any]:
        """Truncate a payload dict if its JSON size exceeds MAX_PAYLOAD_BYTES."""
        encoded = json.dumps(payload, default=str)
        if len(encoded.encode("utf-8")) <= MAX_PAYLOAD_BYTES:
            return payload
        # Replace text fields with truncated versions; mark the row truncated
        truncated: dict[str, Any] = {"_truncated": True, "_original_size": len(encoded)}
        for k, v in payload.items():
            if isinstance(v, str) and len(v) > 1000:
                truncated[k] = v[:1000] + "...[truncated]"
            else:
                truncated[k] = v
        return truncated
