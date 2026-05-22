"""BaseScraper — abstract base class that every scraper extends.

Provides for free:
  - httpx client (connection pooling, HTTP/2-capable, clean timeout API)
  - tenacity-driven retry with exponential backoff
  - per-source token-bucket rate limiting (pyrate_limiter)
  - simple consecutive-failure circuit breaker
  - 429 Retry-After header handling

Subclasses must define:
  - SOURCE_NAME — the name used for rate-limit lookups and run tracking
  - fetch_raw() — download the source data (returns whatever shape is natural)
  - parse(raw) — yield one dict per logical record (streaming)
  - transform(record) — validate one record, return a Pydantic model
"""

from __future__ import annotations

import abc
import logging
import time
from collections.abc import Iterator
from typing import Any

import httpx
from pydantic import BaseModel
from pyrate_limiter import Duration, Limiter, Rate
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from dastock.config import Settings, get_settings
from dastock.scrapers.exceptions import CircuitOpenError, TokenExpiredError

logger = logging.getLogger(__name__)


class BaseScraper(abc.ABC):
    """Abstract base for all data source scrapers."""

    #: Short name used for config lookups and run tracking. Subclass must set.
    SOURCE_NAME: str = ""

    def __init__(self, settings: Settings | None = None) -> None:
        if not self.SOURCE_NAME:
            raise ValueError(f"{type(self).__name__} must define SOURCE_NAME")

        self._settings = settings or get_settings()
        self._logger = logging.getLogger(f"dastock.scrapers.{self.SOURCE_NAME}")

        rps = self._settings.rate_limit_for(self.SOURCE_NAME)
        # Token bucket: rps * 60 tokens per minute. Smoother than per-second.
        self._limiter = Limiter(Rate(max(1, int(rps * 60)), Duration.MINUTE))

        self._consecutive_failures = 0
        self._circuit_open = False
        self._http = self._build_http_client()

    def _build_http_client(self) -> httpx.Client:
        return httpx.Client(
            timeout=httpx.Timeout(
                self._settings.http_timeout_seconds,
                connect=self._settings.http_connect_timeout_seconds,
            ),
            headers={"User-Agent": self._settings.scraper_user_agent},
            follow_redirects=True,
        )

    def close(self) -> None:
        """Close the HTTP client. Call from a finally block or context manager."""
        self._http.close()

    def __enter__(self) -> BaseScraper:
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    # ─── HTTP helpers with retry + rate limit + circuit breaker ─────────────

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=2, min=2, max=60),
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.HTTPStatusError)),
        reraise=True,
    )
    def _request(
        self,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> httpx.Response:
        """Make an HTTP request with rate limiting + retry + circuit breaker.

        Retries on timeouts and 5xx/429 (which raise HTTPStatusError via raise_for_status).
        Auth errors (401/403) are NOT retried — they're typically permanent.
        """
        if self._circuit_open:
            raise CircuitOpenError(
                f"{self.SOURCE_NAME} circuit is open after "
                f"{self._consecutive_failures} consecutive failures"
            )

        # Layer 1: token bucket — blocks until a token is available
        # (pyrate_limiter v3+ uses `timeout` in seconds; -1 = block indefinitely,
        # 0 = non-blocking, positive = max wait. We use 30s max wait.)
        try:
            self._limiter.try_acquire(self.SOURCE_NAME, timeout=30)
        except Exception as e:  # pyrate_limiter raises BucketFullException on timeout
            self._logger.warning(f"Rate limit acquire failed: {e}")
            time.sleep(1.0)  # back off briefly then let tenacity retry the actual call

        try:
            resp = self._http.request(method, url, **kwargs)
        except httpx.TimeoutException:
            self._record_failure()
            raise

        # Auth errors — don't retry, surface immediately
        if resp.status_code in (401, 403):
            self._record_failure()
            raise TokenExpiredError(
                f"{self.SOURCE_NAME} auth failed ({resp.status_code}) at {url}"
            )

        # Rate limit — honor Retry-After then let tenacity backoff
        if resp.status_code == 429:
            retry_after = self._parse_retry_after(resp.headers.get("Retry-After"))
            self._logger.warning(
                f"{self.SOURCE_NAME} 429 from {url}; sleeping {retry_after}s"
            )
            time.sleep(retry_after)
            self._record_failure()
            resp.raise_for_status()  # raises HTTPStatusError → tenacity retries

        # Other HTTP errors
        if resp.is_error:
            self._record_failure()
            resp.raise_for_status()

        # Success — reset failure counter
        self._consecutive_failures = 0
        return resp

    def _get(self, url: str, **kwargs: Any) -> httpx.Response:
        """Convenience GET wrapper."""
        return self._request("GET", url, **kwargs)

    def _post(self, url: str, **kwargs: Any) -> httpx.Response:
        """Convenience POST wrapper."""
        return self._request("POST", url, **kwargs)

    def _record_failure(self) -> None:
        self._consecutive_failures += 1
        threshold = self._settings.circuit_breaker_threshold
        if self._consecutive_failures >= threshold and not self._circuit_open:
            self._circuit_open = True
            self._logger.error(
                f"Circuit OPENED for {self.SOURCE_NAME} after "
                f"{self._consecutive_failures} consecutive failures"
            )

    @staticmethod
    def _parse_retry_after(header_value: str | None) -> float:
        """Parse a Retry-After header value. Defaults to 5s if absent/invalid."""
        if not header_value:
            return 5.0
        try:
            return float(header_value)
        except ValueError:
            # HTTP-date format is also valid but rarely used; default
            return 5.0

    # ─── Abstract interface — subclasses must implement ─────────────────────

    @abc.abstractmethod
    def fetch_raw(self) -> Any:
        """Download raw data from the source. Returns source-specific raw object."""

    @abc.abstractmethod
    def parse(self, raw: Any) -> Iterator[dict[str, Any]]:
        """Parse raw data into a stream of dicts. One dict per logical record."""

    @abc.abstractmethod
    def transform(self, record: dict[str, Any]) -> BaseModel:
        """Validate a parsed dict and return a Pydantic model. Raises on bad data."""

    def external_id_of(self, record: dict[str, Any]) -> str:
        """Return the external identifier from a parsed record.

        Used by the runner for progress tracking (scraper_run_items.external_id).
        Subclasses should override to return whatever ID is canonical for their source.
        Default: stringify the record's "id" key.
        """
        return str(record.get("id", ""))
