-- ────────────────────────────────────────────────────────────────────────────
-- Migration 004 — Operational Tables (run tracking, dead letter queue)
--
-- These tables make scrapers safely re-runnable and observable.
-- ────────────────────────────────────────────────────────────────────────────

-- ─── SCRAPER RUNS — one row per script invocation ───────────────────────────
CREATE TABLE IF NOT EXISTS scraper_runs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source          TEXT NOT NULL,           -- 'dhan' | 'mfapi' | 'rupeevest' | 'trendlyne' | 'scan360'
    mode            TEXT,                    -- 'eod' | 'nav' | 'metadata' | 'holdings' | 'qvt' | 'swot' | NULL
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at     TIMESTAMPTZ,
    records_ok      INTEGER NOT NULL DEFAULT 0,
    records_failed  INTEGER NOT NULL DEFAULT 0,
    last_item_id    TEXT,
    status          TEXT NOT NULL DEFAULT 'running'
                        CHECK (status IN ('running','success','failed','partial')),
    error_summary   TEXT,
    triggered_by    TEXT NOT NULL DEFAULT 'cron'
                        CHECK (triggered_by IN ('cron','manual','retry','bootstrap'))
);
CREATE INDEX IF NOT EXISTS idx_scraper_runs_lookup
    ON scraper_runs (source, mode, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_scraper_runs_status
    ON scraper_runs (status, started_at DESC) WHERE status = 'failed';

-- ─── SCRAPER RUN ITEMS — per-record progress within a run ───────────────────
-- Used by --resume to skip already-processed items after a failure.
CREATE TABLE IF NOT EXISTS scraper_run_items (
    run_id      UUID NOT NULL REFERENCES scraper_runs(id) ON DELETE CASCADE,
    external_id TEXT NOT NULL,
    status      TEXT NOT NULL CHECK (status IN ('ok','failed')),
    PRIMARY KEY (run_id, external_id)
);

-- ─── SCRAPER ERRORS — dead letter queue ─────────────────────────────────────
CREATE TABLE IF NOT EXISTS scraper_errors (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id      UUID REFERENCES scraper_runs(id) ON DELETE SET NULL,
    source      TEXT NOT NULL,
    id_type     TEXT,
    id_value    TEXT,
    error_type  TEXT NOT NULL,    -- 'validation_error' | 'unresolved_id' | 'http_error' | 'parse_error' | 'manual_review' | 'no_nse_listing' | 'unknown_symbol'
    error_msg   TEXT,
    raw_payload JSONB,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    retried_at  TIMESTAMPTZ,
    resolved    BOOLEAN NOT NULL DEFAULT false
);
CREATE INDEX IF NOT EXISTS idx_scraper_errors_unresolved
    ON scraper_errors (source, created_at DESC) WHERE resolved = false;
CREATE INDEX IF NOT EXISTS idx_scraper_errors_type
    ON scraper_errors (error_type, created_at DESC);
