# Contributing to dastock

## Project Status

Under active construction — full contribution guide will be finalized in Session 7 once the codebase stabilizes.

## High-level contract for adding a new scraper (future)

1. Create `src/dastock/scrapers/mysource.py` extending `BaseScraper` with `SOURCE_NAME = "mysource"`.
2. Create `src/dastock/models/mysource.py` with a Pydantic v2 model for the output.
3. Add a new migration in `migrations/` if your scraper needs new columns on `stocks`/`mutual_funds` or new data tables.
4. Create `scripts/run_mysource.py` and a matching `.github/workflows/scrape-mysource.yml`.

Each scraper automatically inherits: HTTP retry with exponential backoff, per-source rate limiting, circuit breaking, dead-letter logging, and the resume/retry CLI flags.

## License

By contributing you agree your contributions will be licensed under the MIT License.
