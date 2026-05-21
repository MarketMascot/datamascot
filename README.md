# dastock

Open-source scraper for Indian stock market data — stocks (NSE/BSE via Dhan), mutual funds (mfapi.in + Rupeevest), analytical scores (Trendlyne), and sector classification (Scan360). Stores everything in Supabase (PostgreSQL).

## Status

**Under active construction.** Session 1 of 8 complete (project scaffold + database migrations).

## Architecture

```
GitHub Actions (free cron) ──> Supabase Cloud (PostgreSQL)
            ↓
   ┌────────┼─────────┬──────────┬──────────┐
 Dhan    mfapi.in  Rupeevest  Trendlyne  Scan360
```

Detailed plan: see `C:\Users\MyPC\.claude\plans\i-want-to-create-precious-curry.md`.

## Setup (Supabase — do this once)

1. Create a Supabase project at [supabase.com](https://supabase.com) (free tier is fine).
2. Project Settings → API → copy your `Project URL`, `anon` key, and `service_role` key.
3. Project Settings → Database → copy the Connection string (Transaction pooler, port 6543).
4. Apply migrations via Supabase SQL Editor (Database → SQL Editor → New query). Paste and run each file in order:
   - `migrations/001_bible_tables.sql`
   - `migrations/002_market_data.sql`
   - `migrations/003_analytics.sql`
   - `migrations/004_operations.sql`
   - `migrations/005_rls_policies.sql`
5. Verify tables: Database → Tables → confirm `stocks`, `mutual_funds`, `daily_prices`, `mf_nav_history`, `mf_stock_holdings`, `mf_metrics`, `trendlyne_qvt`, `trendlyne_swot`, `scraper_runs`, `scraper_run_items`, `scraper_errors` all exist.

## Local development (after Session 2)

```bash
# Install uv: https://docs.astral.sh/uv/
uv sync

# Copy env template
cp .env.example .env
# Fill in SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, DHAN_* values

# (scraper commands come in future sessions)
```

## License

MIT
