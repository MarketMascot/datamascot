# dastock

Open-source scraper for Indian stock market data. Stores everything in Supabase (PostgreSQL). Runs free on GitHub Actions.

**Data sources:**
- **Stocks** (NSE/BSE) — [Dhan broker API](https://dhanhq.co/docs/v2/) — EOD OHLCV prices
- **Mutual Fund NAVs** — [mfapi.in](https://www.mfapi.in/) — daily, free, no auth
- **MF metadata + holdings** — [Rupeevest](https://www.rupeevest.com/) — returns, AUM, expense ratio, stock-level holdings
- **QVT + SWOT scores** — [Trendlyne](https://trendlyne.com/) — Quality/Valuation/Technical + SWOT analysis
- **Sector classification** — [Scan360](https://scan360.in/) — industry tags for NSE stocks

---

## Architecture

```
GitHub Actions (free cron)
        │
        ▼
┌───────────────────────────────────┐
│     Supabase (PostgreSQL)         │
│  stocks  ──  mutual_funds         │  ← bible tables (identity layer)
│  daily_prices                     │
│  mf_nav_history                   │
│  mf_metrics  mf_stock_holdings    │
│  trendlyne_qvt  trendlyne_swot    │
│  scraper_runs  scraper_errors     │
└───────────────────────────────────┘
```

**How identifier chaos is solved:** Every external ID (ISIN, NSE symbol, BSE code, Dhan security ID, Rupeevest fincode, AMFI code, Rupeevest schemecode) lives as a dedicated column on `stocks` or `mutual_funds`. Resolution is always a single `WHERE column = value` — no join tables, no guessing by name.

---

## Quick Start

### 1. Supabase setup (one-time)

1. Create a free project at [supabase.com](https://supabase.com).
2. **Settings → API** — copy your `Project URL`, `anon key`, and `service_role key`.
3. **Database → SQL Editor** — run each migration in order:
   - `migrations/001_bible_tables.sql`
   - `migrations/002_market_data.sql`
   - `migrations/003_analytics.sql`
   - `migrations/004_operations.sql`
   - `migrations/005_rls_policies.sql`

### 2. GitHub Secrets setup

In your fork: **Settings → Secrets and variables → Actions → New repository secret**

| Secret | Where to get it |
|---|---|
| `SUPABASE_URL` | Supabase → Settings → API → Project URL |
| `SUPABASE_SERVICE_ROLE_KEY` | Supabase → Settings → API → service_role key |
| `DHAN_CLIENT_ID` | [dhan.co/api](https://dhan.co/api) → My Profile |
| `DHAN_ACCESS_TOKEN` | Same; regenerate if it expires (24h for free accounts) |

### 3. Bootstrap (one-time identity seed)

Go to **Actions → Bootstrap (one-time identity seed) → Run workflow**.

This populates the `stocks` and `mutual_funds` bible tables from the Dhan security master and mfapi.in. All other scrapers depend on this having run first.

### 4. Local development

```bash
# Install uv: https://docs.astral.sh/uv/
uv sync

# Copy env template and fill in values
cp .env.example .env

# Run any scraper locally
uv run python scripts/run_mfapi.py --limit 10
uv run python scripts/run_rupeevest.py --mode metadata --limit 5
uv run python scripts/run_trendlyne.py --mode qvt --only-symbol RELIANCE
uv run python scripts/run_scan360.py --limit 50

# Run tests
uv run pytest tests/unit/
```

---

## Cron Schedule

| Workflow | IST time | Frequency | Expected runtime |
|---|---|---|---|
| Dhan EOD prices | Mon–Fri 7:00 PM | Daily (weekdays) | ~15 min |
| mfapi daily NAV | Daily 9:30 PM | Daily | ~5 min |
| Rupeevest MF metadata | Sunday 11:00 PM | Weekly | ~30 min |
| Rupeevest MF holdings | 1st of month 11:00 PM | Monthly | ~45 min |
| Trendlyne QVT | Saturday 2:00 AM | Weekly | ~2–3 hours |
| Trendlyne SWOT | Saturday 5:00 AM | Weekly | ~2–3 hours |
| Scan360 sectors | Saturday 12:00 AM | Weekly | ~5 min |

All workflows support manual `Run workflow` dispatch with `--resume`, `--only-symbol`, and `--limit` inputs.

---

## Failure handling

- **Per-row failures** (bad data, unresolved ID) → logged to `scraper_errors` table, run continues
- **HTTP 429** → exponential backoff + Retry-After header honoured
- **5 consecutive failures** → circuit breaker opens, run aborts (prevents hammering a broken source)
- **Resume** → `--resume` flag skips already-processed IDs from the last failed run
- **Run history** → every run writes a row to `scraper_runs`; query in Supabase Studio for at-a-glance health

---

## Security

- **Public read, service-role write** — the `anon` key (safe to expose to clients) can only read. Only `SUPABASE_SERVICE_ROLE_KEY` can write, and it never leaves GitHub Secrets / your `.env`.
- **`.env` is gitignored** — never commit credentials.
- **`original/` is gitignored** — the legacy codebase contained hardcoded passwords and is permanently excluded.

---

## Disclaimer

This project scrapes publicly accessible data. Check each source's Terms of Service before use in production. Not affiliated with Dhan, Rupeevest, Trendlyne, mfapi.in, or Scan360.

## License

MIT
