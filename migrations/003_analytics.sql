-- ────────────────────────────────────────────────────────────────────────────
-- Migration 003 — Analytics Tables (Trendlyne, Scan360-driven sector data)
--
-- Date-keyed UNIQUE constraints preserve history. The original schema
-- single-keyed on stock_id, which destroyed point-in-time analysis data.
-- ────────────────────────────────────────────────────────────────────────────

-- ─── TRENDLYNE QVT (Quality / Valuation / Technical scores) ─────────────────
CREATE TABLE IF NOT EXISTS trendlyne_qvt (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    stock_id            UUID NOT NULL REFERENCES stocks(id) ON DELETE CASCADE,
    quality_score       SMALLINT CHECK (quality_score BETWEEN 0 AND 100),
    quality_insight     TEXT,
    valuation_score     SMALLINT CHECK (valuation_score BETWEEN 0 AND 100),
    valuation_insight   TEXT,
    technical_score     SMALLINT CHECK (technical_score BETWEEN 0 AND 100),
    technical_insight   TEXT,
    analysis_date       DATE NOT NULL DEFAULT CURRENT_DATE,
    scraped_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    source              TEXT NOT NULL DEFAULT 'trendlyne',
    raw_payload         JSONB,
    UNIQUE (stock_id, analysis_date)
);
CREATE INDEX IF NOT EXISTS idx_qvt_date ON trendlyne_qvt (analysis_date DESC);

-- ─── TRENDLYNE SWOT ─────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS trendlyne_swot (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    stock_id                UUID NOT NULL REFERENCES stocks(id) ON DELETE CASCADE,
    strengths_count         SMALLINT,
    strengths_text          TEXT,
    weakness_count          SMALLINT,
    weakness_text           TEXT,
    opportunities_count     SMALLINT,
    opportunities_text      TEXT,
    threats_count           SMALLINT,
    threats_text            TEXT,
    analysis_date           DATE NOT NULL DEFAULT CURRENT_DATE,
    scraped_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    source                  TEXT NOT NULL DEFAULT 'trendlyne',
    raw_payload             JSONB,
    UNIQUE (stock_id, analysis_date)
);
CREATE INDEX IF NOT EXISTS idx_swot_date ON trendlyne_swot (analysis_date DESC);
