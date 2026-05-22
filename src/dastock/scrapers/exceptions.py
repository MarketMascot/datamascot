"""Custom exceptions used by scrapers."""

from __future__ import annotations


class ScraperError(Exception):
    """Base for all scraper-specific errors."""


class CircuitOpenError(ScraperError):
    """Raised when a scraper's circuit breaker has tripped open.

    Indicates the source has had too many consecutive failures and we
    should abort the run rather than keep hammering it.
    """


class EmptyRunError(ScraperError):
    """Raised when a full scraper run produced zero valid records.

    Usually means the source's response format has changed silently.
    """


class TokenExpiredError(ScraperError):
    """Raised when an auth token (e.g. Dhan access_token) is rejected as expired."""
