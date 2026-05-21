-- ────────────────────────────────────────────────────────────────────────────
-- Migration 001 — Bible Tables (stocks & mutual_funds)
--
-- These two tables ARE the identity mapper. Every external ID from every
-- data source lives as a dedicated column. No separate lookup table — one
-- row = one complete identity picture for a stock or fund.
-- ────────────────────────────────────────────────────────────────────────────

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";

-- ─── STOCKS BIBLE ───────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS stocks (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Identity columns (the mapper). NULL = "not yet mapped to that source".
    isin                    TEXT UNIQUE,
    nse_symbol              TEXT UNIQUE,
    bse_code                TEXT UNIQUE,
    dhan_security_id        TEXT UNIQUE,
    rupeevest_fincode       INTEGER UNIQUE,
    trendlyne_symbol        TEXT UNIQUE,

    -- Descriptive
    canonical_name          TEXT NOT NULL,
    listing_status          TEXT NOT NULL DEFAULT 'active'
                                CHECK (listing_status IN ('active','suspended','delisted')),
    sector                  TEXT,
    industry                TEXT,
    market_cap_cat          TEXT CHECK (market_cap_cat IN ('large','mid','small','micro') OR market_cap_cat IS NULL),

    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_stocks_nse_symbol        ON stocks (nse_symbol);
CREATE INDEX IF NOT EXISTS idx_stocks_bse_code          ON stocks (bse_code);
CREATE INDEX IF NOT EXISTS idx_stocks_dhan_security_id  ON stocks (dhan_security_id);
CREATE INDEX IF NOT EXISTS idx_stocks_rupeevest_fincode ON stocks (rupeevest_fincode);
CREATE INDEX IF NOT EXISTS idx_stocks_listing_status    ON stocks (listing_status);

-- ─── MUTUAL FUNDS BIBLE ─────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS mutual_funds (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Identity columns. ISIN bridges mfapi.in <-> Rupeevest.
    amfi_code               TEXT UNIQUE,
    rupeevest_schemecode    INTEGER UNIQUE,
    isin_growth             TEXT UNIQUE,
    isin_dividend           TEXT UNIQUE,

    -- Descriptive
    scheme_name             TEXT NOT NULL,
    fund_house              TEXT,
    classification          TEXT,
    scheme_type             TEXT,
    inception_date          DATE,
    listing_status          TEXT NOT NULL DEFAULT 'active'
                                CHECK (listing_status IN ('active','wound_up')),

    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_mf_amfi          ON mutual_funds (amfi_code);
CREATE INDEX IF NOT EXISTS idx_mf_rupeevest     ON mutual_funds (rupeevest_schemecode);
CREATE INDEX IF NOT EXISTS idx_mf_isin_growth   ON mutual_funds (isin_growth);
CREATE INDEX IF NOT EXISTS idx_mf_isin_dividend ON mutual_funds (isin_dividend);

-- ─── updated_at triggers ────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_stocks_updated_at ON stocks;
CREATE TRIGGER trg_stocks_updated_at
    BEFORE UPDATE ON stocks
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

DROP TRIGGER IF EXISTS trg_mf_updated_at ON mutual_funds;
CREATE TRIGGER trg_mf_updated_at
    BEFORE UPDATE ON mutual_funds
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
