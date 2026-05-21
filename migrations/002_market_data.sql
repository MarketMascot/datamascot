-- ────────────────────────────────────────────────────────────────────────────
-- Migration 002 — Market & Fund Data
--
-- All data tables FK to stocks(id) or mutual_funds(id). UNIQUE constraints
-- on natural keys make every upsert idempotent.
-- ────────────────────────────────────────────────────────────────────────────

-- ─── DAILY OHLCV (from Dhan) ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS daily_prices (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    stock_id    UUID NOT NULL REFERENCES stocks(id) ON DELETE CASCADE,
    trade_date  DATE NOT NULL,
    open        NUMERIC(14,4),
    high        NUMERIC(14,4),
    low         NUMERIC(14,4),
    close       NUMERIC(14,4) NOT NULL CHECK (close > 0),
    volume      BIGINT CHECK (volume IS NULL OR volume >= 0),
    scraped_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    source      TEXT NOT NULL DEFAULT 'dhan',
    raw_payload JSONB,
    UNIQUE (stock_id, trade_date)
);
CREATE INDEX IF NOT EXISTS idx_daily_prices_date  ON daily_prices (trade_date DESC);
CREATE INDEX IF NOT EXISTS idx_daily_prices_stock ON daily_prices (stock_id, trade_date DESC);

-- ─── MF NAV HISTORY (from mfapi.in) ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS mf_nav_history (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    fund_id     UUID NOT NULL REFERENCES mutual_funds(id) ON DELETE CASCADE,
    nav_date    DATE NOT NULL,
    nav         NUMERIC(14,4) NOT NULL CHECK (nav >= 0),
    suspicious  BOOLEAN NOT NULL DEFAULT false,
    scraped_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    source      TEXT NOT NULL DEFAULT 'mfapi',
    UNIQUE (fund_id, nav_date)
);
CREATE INDEX IF NOT EXISTS idx_mf_nav_date  ON mf_nav_history (nav_date DESC);
CREATE INDEX IF NOT EXISTS idx_mf_nav_fund  ON mf_nav_history (fund_id, nav_date DESC);

-- ─── MF STOCK HOLDINGS (from Rupeevest, monthly) ────────────────────────────
-- This is the table that had orphan-row bugs in the original code.
-- Now uses UUID FKs resolved through the bible tables.
CREATE TABLE IF NOT EXISTS mf_stock_holdings (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    fund_id             UUID NOT NULL REFERENCES mutual_funds(id) ON DELETE CASCADE,
    stock_id            UUID NOT NULL REFERENCES stocks(id) ON DELETE CASCADE,
    as_of_date          DATE NOT NULL,
    holding_pct         NUMERIC(7,4),
    holding_value_cr    NUMERIC(18,4),
    no_of_shares        BIGINT,
    scraped_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    source              TEXT NOT NULL DEFAULT 'rupeevest',
    raw_payload         JSONB,
    UNIQUE (fund_id, stock_id, as_of_date)
);
CREATE INDEX IF NOT EXISTS idx_mfsh_stock ON mf_stock_holdings (stock_id, as_of_date DESC);
CREATE INDEX IF NOT EXISTS idx_mfsh_fund  ON mf_stock_holdings (fund_id, as_of_date DESC);

-- ─── MF METRICS (from Rupeevest, weekly) ────────────────────────────────────
CREATE TABLE IF NOT EXISTS mf_metrics (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    fund_id         UUID NOT NULL REFERENCES mutual_funds(id) ON DELETE CASCADE,
    as_of_date      DATE NOT NULL DEFAULT CURRENT_DATE,
    nav             NUMERIC(14,4),
    aum_cr          NUMERIC(18,4),
    expense_ratio   NUMERIC(6,4),
    return_1m       NUMERIC(10,4),
    return_3m       NUMERIC(10,4),
    return_6m       NUMERIC(10,4),
    return_1y       NUMERIC(10,4),
    return_3y       NUMERIC(10,4),
    return_5y       NUMERIC(10,4),
    return_10y      NUMERIC(10,4),
    pe_ratio        NUMERIC(10,4),
    pb_ratio        NUMERIC(10,4),
    no_of_stocks    INTEGER,
    scraped_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    source          TEXT NOT NULL DEFAULT 'rupeevest',
    raw_payload     JSONB,
    UNIQUE (fund_id, as_of_date)
);
CREATE INDEX IF NOT EXISTS idx_mf_metrics_date ON mf_metrics (as_of_date DESC);
