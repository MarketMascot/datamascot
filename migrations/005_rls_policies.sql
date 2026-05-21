-- ────────────────────────────────────────────────────────────────────────────
-- Migration 005 — Row Level Security Policies
--
-- All tables: public read (open-source market data should be readable),
-- write access only for the service_role (used by scrapers on GitHub Actions).
--
-- The anon key used by future Flutter/web clients gets read-only access.
-- ────────────────────────────────────────────────────────────────────────────

-- Enable RLS on every table
ALTER TABLE stocks              ENABLE ROW LEVEL SECURITY;
ALTER TABLE mutual_funds        ENABLE ROW LEVEL SECURITY;
ALTER TABLE daily_prices        ENABLE ROW LEVEL SECURITY;
ALTER TABLE mf_nav_history      ENABLE ROW LEVEL SECURITY;
ALTER TABLE mf_stock_holdings   ENABLE ROW LEVEL SECURITY;
ALTER TABLE mf_metrics          ENABLE ROW LEVEL SECURITY;
ALTER TABLE trendlyne_qvt       ENABLE ROW LEVEL SECURITY;
ALTER TABLE trendlyne_swot      ENABLE ROW LEVEL SECURITY;
ALTER TABLE scraper_runs        ENABLE ROW LEVEL SECURITY;
ALTER TABLE scraper_run_items   ENABLE ROW LEVEL SECURITY;
ALTER TABLE scraper_errors      ENABLE ROW LEVEL SECURITY;

-- ─── Public-read policies on data tables ────────────────────────────────────
DROP POLICY IF EXISTS public_read ON stocks;
CREATE POLICY public_read ON stocks            FOR SELECT USING (true);

DROP POLICY IF EXISTS public_read ON mutual_funds;
CREATE POLICY public_read ON mutual_funds      FOR SELECT USING (true);

DROP POLICY IF EXISTS public_read ON daily_prices;
CREATE POLICY public_read ON daily_prices      FOR SELECT USING (true);

DROP POLICY IF EXISTS public_read ON mf_nav_history;
CREATE POLICY public_read ON mf_nav_history    FOR SELECT USING (true);

DROP POLICY IF EXISTS public_read ON mf_stock_holdings;
CREATE POLICY public_read ON mf_stock_holdings FOR SELECT USING (true);

DROP POLICY IF EXISTS public_read ON mf_metrics;
CREATE POLICY public_read ON mf_metrics        FOR SELECT USING (true);

DROP POLICY IF EXISTS public_read ON trendlyne_qvt;
CREATE POLICY public_read ON trendlyne_qvt     FOR SELECT USING (true);

DROP POLICY IF EXISTS public_read ON trendlyne_swot;
CREATE POLICY public_read ON trendlyne_swot    FOR SELECT USING (true);

-- ─── Operational tables: NO public access (only service role) ───────────────
-- scraper_runs / scraper_run_items / scraper_errors contain operational
-- metadata that contributors shouldn't see by default. No SELECT policy
-- for anon means anon gets nothing. Service role bypasses RLS automatically.

-- ─── Service role write policies ────────────────────────────────────────────
-- Service role bypasses RLS by default in Supabase, but we add explicit
-- policies for clarity and to document the intent.
-- (These are mostly a no-op since service_role bypasses RLS, but make
-- the security model self-documenting.)

DO $$
DECLARE
    tbl TEXT;
BEGIN
    FOR tbl IN
        SELECT unnest(ARRAY[
            'stocks','mutual_funds','daily_prices','mf_nav_history',
            'mf_stock_holdings','mf_metrics','trendlyne_qvt','trendlyne_swot',
            'scraper_runs','scraper_run_items','scraper_errors'
        ])
    LOOP
        EXECUTE format('DROP POLICY IF EXISTS service_write ON %I', tbl);
        EXECUTE format(
            'CREATE POLICY service_write ON %I FOR ALL TO service_role USING (true) WITH CHECK (true)',
            tbl
        );
    END LOOP;
END $$;
