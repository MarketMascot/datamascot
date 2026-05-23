"""Tests for pipeline layer: DeadLetterLogger, RunTracker, run_pipeline."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any
from unittest.mock import MagicMock, call

import pytest
from pydantic import BaseModel, ValidationError

from dastock.pipeline.dead_letter import DeadLetterLogger, MAX_PAYLOAD_BYTES
from dastock.pipeline.run_tracker import RunTracker
from dastock.pipeline.runner import _SkipRecord, run_pipeline, skip_record
from dastock.scrapers.exceptions import EmptyRunError


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _make_client(run_uuid: str | None = None) -> MagicMock:
    """Mock Supabase client whose .execute() returns sensible defaults."""
    _id = run_uuid or str(uuid.uuid4())
    client = MagicMock(name="client")
    table = MagicMock(name="table")
    for method in ("select", "insert", "upsert", "update", "eq", "gte",
                   "order", "limit", "not_", "delete"):
        getattr(table, method).return_value = table
    table.execute.return_value = MagicMock(data=[{"id": _id, "status": "running"}])
    client.table.return_value = table
    return client


def _make_scraper(records: list[dict], fail_transform: bool = False) -> MagicMock:
    """A minimal scraper mock that yields the given records."""
    scraper = MagicMock()
    scraper.SOURCE_NAME = "test_source"
    scraper.fetch_raw.return_value = records
    scraper.parse.return_value = iter(records)

    class _M(BaseModel):
        value: str

    def _transform(record: dict) -> _M:
        if fail_transform:
            raise ValidationError.from_exception_data(
                title="bad", input_type="python",
                line_errors=[{
                    "type": "missing", "loc": ("value",),
                    "msg": "Field required", "input": record,
                    "url": "",
                }],
            )
        return _M(value=record.get("id", "x"))

    scraper.transform.side_effect = _transform
    scraper.external_id_of.side_effect = lambda r: str(r.get("id", ""))
    return scraper


# ─── DeadLetterLogger ─────────────────────────────────────────────────────────


class TestDeadLetterLogger:
    def test_record_inserts_row(self) -> None:
        client = _make_client()
        dl = DeadLetterLogger(client, run_id=uuid.uuid4())
        dl.record(source="dhan", error_type="validation_error", error_msg="bad row")
        client.table.assert_called_with("scraper_errors")
        client.table().insert.assert_called_once()

    def test_record_never_raises_on_client_error(self) -> None:
        client = _make_client()
        client.table.side_effect = RuntimeError("DB is down")
        dl = DeadLetterLogger(client)
        dl.record(source="dhan", error_type="validation_error")  # must not raise

    def test_truncate_payload_small_passes_through(self) -> None:
        payload = {"key": "value"}
        result = DeadLetterLogger._truncate_payload(payload)
        assert result == payload

    def test_truncate_payload_large_marks_truncated(self) -> None:
        big = {"text": "x" * (MAX_PAYLOAD_BYTES + 1)}
        result = DeadLetterLogger._truncate_payload(big)
        assert result["_truncated"] is True

    def test_record_no_run_id_still_inserts(self) -> None:
        client = _make_client()
        dl = DeadLetterLogger(client)  # no run_id
        dl.record(source="mfapi", error_type="http_error")
        client.table().insert.assert_called_once()
        inserted_row = client.table().insert.call_args[0][0]
        assert inserted_row["run_id"] is None


# ─── RunTracker ───────────────────────────────────────────────────────────────


class TestRunTracker:
    def test_start_returns_uuid(self) -> None:
        run_id = str(uuid.uuid4())
        client = _make_client(run_id)
        tracker = RunTracker(client, source="dhan", mode="eod")
        result = tracker.start()
        assert result == uuid.UUID(run_id)

    def test_run_id_raises_before_start(self) -> None:
        client = _make_client()
        tracker = RunTracker(client, source="dhan")
        with pytest.raises(RuntimeError, match="not started"):
            _ = tracker.run_id

    def test_mark_item_ok_increments_counter(self) -> None:
        client = _make_client()
        tracker = RunTracker(client, source="dhan")
        tracker.start()
        tracker.mark_item_ok("RELIANCE")
        tracker.mark_item_ok("TCS")
        assert tracker._records_ok == 2
        assert tracker._records_failed == 0
        assert tracker._last_item_id == "TCS"

    def test_mark_item_failed_increments_counter(self) -> None:
        client = _make_client()
        tracker = RunTracker(client, source="dhan")
        tracker.start()
        tracker.mark_item_failed("BAD_STOCK")
        assert tracker._records_failed == 1

    def test_finish_calls_update(self) -> None:
        client = _make_client()
        tracker = RunTracker(client, source="dhan", mode="eod")
        tracker.start()
        tracker.mark_item_ok("X")
        tracker.finish("success")
        client.table().update.assert_called()
        update_kwargs = client.table().update.call_args[0][0]
        assert update_kwargs["status"] == "success"
        assert update_kwargs["records_ok"] == 1

    def test_finish_with_error_summary(self) -> None:
        client = _make_client()
        tracker = RunTracker(client, source="dhan")
        tracker.start()
        tracker.finish("failed", error_summary="SomeError: details")
        update_row = client.table().update.call_args[0][0]
        assert update_row["error_summary"] == "SomeError: details"

    def test_finish_does_nothing_without_start(self) -> None:
        client = _make_client()
        tracker = RunTracker(client, source="dhan")
        tracker.finish("success")  # must not raise; no update called
        client.table().update.assert_not_called()

    def test_already_processed_ids_queries_last_failed_run(self) -> None:
        client = _make_client()
        last_run_id = str(uuid.uuid4())
        # First call returns the failed run; second returns the items
        client.table().execute.side_effect = [
            MagicMock(data=[{"id": str(uuid.uuid4())}]),          # start() insert
            MagicMock(data=[{"id": last_run_id}]),                  # failed run query
            MagicMock(data=[{"external_id": "RELIANCE"}, {"external_id": "TCS"}]),  # items
        ]
        tracker = RunTracker(client, source="dhan", mode="eod")
        tracker.start()
        ids = tracker.already_processed_ids()
        assert "RELIANCE" in ids
        assert "TCS" in ids

    def test_already_processed_ids_empty_when_no_prior_run(self) -> None:
        client = _make_client()
        client.table().execute.side_effect = [
            MagicMock(data=[{"id": str(uuid.uuid4())}]),  # start()
            MagicMock(data=[]),                             # no failed runs found
        ]
        tracker = RunTracker(client, source="dhan", mode="eod")
        tracker.start()
        ids = tracker.already_processed_ids()
        assert ids == set()


# ─── run_pipeline ─────────────────────────────────────────────────────────────


class TestRunPipeline:
    def _run_id(self) -> str:
        return str(uuid.uuid4())

    def test_happy_path_marks_items_ok(self) -> None:
        run_id = self._run_id()
        client = _make_client(run_id)
        records = [{"id": "A"}, {"id": "B"}, {"id": "C"}]
        scraper = _make_scraper(records)
        handler = MagicMock(return_value=uuid.uuid4())

        tracker = run_pipeline(scraper=scraper, client=client, handler=handler)

        assert tracker._records_ok == 3
        assert tracker._records_failed == 0

    def test_validation_failure_dead_letters_and_continues(self) -> None:
        client = _make_client()
        scraper = _make_scraper([{"id": "GOOD"}, {"id": "BAD"}], fail_transform=False)

        def _transform(record: dict) -> BaseModel:
            if record["id"] == "BAD":
                raise ValueError("broken")
            class _M(BaseModel):
                value: str
            return _M(value=record["id"])

        scraper.transform.side_effect = _transform
        handler = MagicMock(return_value=uuid.uuid4())

        tracker = run_pipeline(scraper=scraper, client=client, handler=handler)
        assert tracker._records_ok == 1
        assert tracker._records_failed == 1

    def test_handler_skip_record_dead_letters(self) -> None:
        client = _make_client()
        records = [{"id": "X"}]
        scraper = _make_scraper(records)

        def _handler(model: BaseModel) -> None:
            skip_record("unresolved_id", "no match", id_type="nse_symbol", id_value="X")

        tracker = run_pipeline(scraper=scraper, client=client, handler=_handler)
        assert tracker._records_ok == 0
        assert tracker._records_failed == 1

    def test_only_external_id_filters_records(self) -> None:
        client = _make_client()
        records = [{"id": "A"}, {"id": "B"}, {"id": "C"}]
        scraper = _make_scraper(records)
        handler = MagicMock(return_value=uuid.uuid4())

        tracker = run_pipeline(
            scraper=scraper, client=client, handler=handler, only_external_id="B"
        )
        assert tracker._records_ok == 1
        handler_calls = [call_args[0][0].value for call_args in handler.call_args_list]
        assert handler_calls == ["B"]

    def test_empty_run_error_when_require_min_records(self) -> None:
        client = _make_client()
        scraper = _make_scraper([])  # no records
        handler = MagicMock(return_value=uuid.uuid4())

        with pytest.raises(EmptyRunError):
            run_pipeline(
                scraper=scraper, client=client, handler=handler, require_min_records=1
            )

    def test_hard_failure_marks_run_failed_and_reraises(self) -> None:
        client = _make_client()
        scraper = MagicMock()
        scraper.SOURCE_NAME = "test_source"
        scraper.fetch_raw.side_effect = RuntimeError("source exploded")

        with pytest.raises(RuntimeError, match="source exploded"):
            run_pipeline(scraper=scraper, client=client, handler=MagicMock())

    def test_resume_skips_already_processed(self) -> None:
        last_run_id = str(uuid.uuid4())
        client = _make_client()
        # Simulate: start() → already_processed_ids queries → items for B
        client.table().execute.side_effect = [
            MagicMock(data=[{"id": str(uuid.uuid4())}]),         # start()
            MagicMock(data=[{"id": last_run_id}]),                # failed run
            MagicMock(data=[{"external_id": "B"}]),               # already done items
            # remaining calls for mark_item_ok upserts
            MagicMock(data=[]),
            MagicMock(data=[]),
        ]
        records = [{"id": "A"}, {"id": "B"}, {"id": "C"}]
        scraper = _make_scraper(records)
        handler = MagicMock(return_value=uuid.uuid4())

        run_pipeline(scraper=scraper, client=client, handler=handler, resume=True)

        # Only A and C should have been handled (B was skipped)
        handled_ids = [call_args[0][0].value for call_args in handler.call_args_list]
        assert "B" not in handled_ids
        assert "A" in handled_ids
        assert "C" in handled_ids

    def test_handler_returns_none_marks_failed(self) -> None:
        client = _make_client()
        records = [{"id": "X"}]
        scraper = _make_scraper(records)
        handler = MagicMock(return_value=None)  # handler returns None = skip

        tracker = run_pipeline(scraper=scraper, client=client, handler=handler)
        assert tracker._records_ok == 0
        assert tracker._records_failed == 1


# ─── _SkipRecord / skip_record ───────────────────────────────────────────────


def test_skip_record_raises_sentinel() -> None:
    with pytest.raises(_SkipRecord) as exc_info:
        skip_record("unresolved_id", "no match", id_type="nse_symbol", id_value="ABC")
    assert exc_info.value.error_type == "unresolved_id"
    assert exc_info.value.id_type == "nse_symbol"
    assert exc_info.value.id_value == "ABC"
