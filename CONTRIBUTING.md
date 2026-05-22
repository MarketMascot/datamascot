# Contributing to dastock

Thanks for contributing! This guide covers everything you need.

---

## Development setup

```bash
# Install uv (https://docs.astral.sh/uv/)
uv sync

cp .env.example .env
# Fill in SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY (at minimum)

uv run pytest tests/unit/          # fast, no network
uv run pytest tests/integration/   # requires real Supabase creds in .env
```

Use your own Supabase project for development — never point at the production project.

---

## Adding a new scraper (4-step contract)

Every scraper in this codebase follows the same pattern. Adding a new one is mechanical:

### Step 1 — Pydantic model (`src/dastock/models/mysource.py`)

```python
from pydantic import BaseModel, ConfigDict

class MySourceRecord(BaseModel):
    model_config = ConfigDict(extra="ignore")
    nse_symbol: str
    some_value: float | None = None
```

Validate at the boundary. Coerce nullish strings (`"-"`, `"N/A"`, `"0"`) to `None`. Use `Decimal` for prices/ratios. All fields optional except the identity key.

### Step 2 — Scraper (`src/dastock/scrapers/mysource.py`)

```python
from dastock.scrapers.base import BaseScraper

class MySourceScraper(BaseScraper):
    SOURCE_NAME = "mysource"   # must match config key: mysource_rate_limit_rps

    def fetch_raw(self):
        return self._get("https://mysource.example/api/data").json()

    def parse(self, raw):
        for record in raw.get("items", []):
            yield record

    def transform(self, record):
        return MySourceRecord.model_validate(record)

    def external_id_of(self, record):
        return str(record.get("symbol", ""))
```

You get for free: HTTP retry (5 attempts, exponential backoff), rate limiting (token bucket), circuit breaker (opens after N consecutive failures), 429 Retry-After handling. Just implement the three abstract methods.

### Step 3 — Script (`scripts/run_mysource.py`)

```python
import click
from dastock.scrapers.mysource import MySourceScraper
from dastock.pipeline.run_tracker import RunTracker
from dastock.pipeline.dead_letter import DeadLetterLogger

@click.command()
@click.option("--resume", is_flag=True)
@click.option("--only-symbol", type=str, default=None)
@click.option("--limit", type=int, default=None)
def main(resume, only_symbol, limit):
    ...
```

Supported flags in all scripts: `--resume` (skip already-processed IDs), `--only-symbol` (debug single item), `--limit` (smoke test), `--rps` (override rate limit).

### Step 4 — Workflow (`.github/workflows/scrape-mysource.yml`)

Copy `scrape-scan360.yml` as a template. Set the cron, timeout-minutes, script name, and rate limit env var. Add `workflow_dispatch` inputs for `resume`, `only_symbol`, `limit`.

---

## Adding a new config key

Add it to `src/dastock/config.py` with a sensible default. For a new source rate limit:

```python
mysource_rate_limit_rps: float = 1.0
```

`BaseScraper.__init__` automatically calls `settings.rate_limit_for("mysource")` — no other changes needed.

---

## Adding a new database table

Create a new numbered migration file (e.g. `migrations/006_my_table.sql`). Apply it to your Supabase project via SQL Editor. Add a corresponding method to the relevant repository in `src/dastock/db/repositories/`.

---

## Code style

- Python 3.12+, type hints everywhere
- `uv run ruff check .` and `uv run ruff format .` before pushing
- No comments explaining WHAT the code does — only WHY (non-obvious constraints, workarounds)
- No unused imports, no print statements (use `logging`)

---

## Test conventions

- Unit tests in `tests/unit/` — no network, mock HTTP with `respx`
- Integration tests in `tests/integration/` — require real `.env` creds, run with `pytest -m integration`
- Every new scraper needs at least: model validation tests, HTTP mock tests for the main fetch path, empty-response handling

---

## License

By contributing you agree your contributions will be licensed under the MIT License.
