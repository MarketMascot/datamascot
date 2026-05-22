"""Tests for BaseScraper — rate limiting, retry, circuit breaker."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx
import pytest
import respx
from pydantic import BaseModel

from dastock.scrapers.base import BaseScraper
from dastock.scrapers.exceptions import CircuitOpenError, TokenExpiredError


class _DummyModel(BaseModel):
    value: int


class _DummyScraper(BaseScraper):
    """Minimal concrete scraper for testing the base class."""

    SOURCE_NAME = "mfapi"  # use real source name so rate limits resolve

    def fetch_raw(self) -> Any:
        return self._get("https://example.test/data").json()

    def parse(self, raw: Any) -> Iterator[dict[str, Any]]:
        yield from raw

    def transform(self, record: dict[str, Any]) -> BaseModel:
        return _DummyModel(value=record["value"])


@pytest.fixture
def scraper() -> Iterator[_DummyScraper]:
    s = _DummyScraper()
    try:
        yield s
    finally:
        s.close()


# ─── Happy path ──────────────────────────────────────────────────────────────


@respx.mock
def test_get_returns_success(scraper: _DummyScraper) -> None:
    respx.get("https://example.test/data").mock(
        return_value=httpx.Response(200, json=[{"value": 1}])
    )
    result = scraper.fetch_raw()
    assert result == [{"value": 1}]
    assert scraper._consecutive_failures == 0


# ─── Retry on transient errors ───────────────────────────────────────────────


@respx.mock
def test_get_retries_on_5xx(scraper: _DummyScraper) -> None:
    route = respx.get("https://example.test/data").mock(
        side_effect=[
            httpx.Response(503),
            httpx.Response(503),
            httpx.Response(200, json=[{"value": 42}]),
        ]
    )
    result = scraper.fetch_raw()
    assert result == [{"value": 42}]
    assert route.call_count == 3


@respx.mock
def test_get_gives_up_after_5_attempts(scraper: _DummyScraper) -> None:
    respx.get("https://example.test/data").mock(return_value=httpx.Response(503))
    with pytest.raises(httpx.HTTPStatusError):
        scraper.fetch_raw()


# ─── Auth errors are NOT retried ─────────────────────────────────────────────


@respx.mock
def test_get_raises_token_expired_on_401(scraper: _DummyScraper) -> None:
    route = respx.get("https://example.test/data").mock(return_value=httpx.Response(401))
    with pytest.raises(TokenExpiredError):
        scraper.fetch_raw()
    assert route.call_count == 1  # NOT retried


@respx.mock
def test_get_raises_token_expired_on_403(scraper: _DummyScraper) -> None:
    route = respx.get("https://example.test/data").mock(return_value=httpx.Response(403))
    with pytest.raises(TokenExpiredError):
        scraper.fetch_raw()
    assert route.call_count == 1


# ─── 429 handling ────────────────────────────────────────────────────────────


@respx.mock
def test_429_honors_retry_after(scraper: _DummyScraper, monkeypatch: pytest.MonkeyPatch) -> None:
    sleep_calls: list[float] = []
    monkeypatch.setattr("dastock.scrapers.base.time.sleep", lambda s: sleep_calls.append(s))

    respx.get("https://example.test/data").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "3"}),
            httpx.Response(200, json=[]),
        ]
    )
    scraper.fetch_raw()
    assert 3.0 in sleep_calls


@respx.mock
def test_429_defaults_to_5s_when_header_missing(
    scraper: _DummyScraper, monkeypatch: pytest.MonkeyPatch
) -> None:
    sleep_calls: list[float] = []
    monkeypatch.setattr("dastock.scrapers.base.time.sleep", lambda s: sleep_calls.append(s))

    respx.get("https://example.test/data").mock(
        side_effect=[httpx.Response(429), httpx.Response(200, json=[])]
    )
    scraper.fetch_raw()
    assert 5.0 in sleep_calls


# ─── Circuit breaker ─────────────────────────────────────────────────────────


def test_circuit_opens_after_threshold_failures(scraper: _DummyScraper) -> None:
    threshold = scraper._settings.circuit_breaker_threshold
    for _ in range(threshold):
        scraper._record_failure()
    assert scraper._circuit_open


def test_circuit_open_raises_immediately(scraper: _DummyScraper) -> None:
    scraper._circuit_open = True
    with pytest.raises(CircuitOpenError):
        scraper._get("https://example.test/anything")


def test_successful_request_resets_failure_counter(scraper: _DummyScraper) -> None:
    scraper._consecutive_failures = 3
    with respx.mock:
        respx.get("https://example.test/ok").mock(
            return_value=httpx.Response(200, json={})
        )
        scraper._get("https://example.test/ok")
    assert scraper._consecutive_failures == 0


# ─── Context manager ─────────────────────────────────────────────────────────


def test_scraper_works_as_context_manager() -> None:
    with _DummyScraper() as s:
        assert not s._http.is_closed
    assert s._http.is_closed


# ─── SOURCE_NAME validation ──────────────────────────────────────────────────


def test_missing_source_name_raises() -> None:
    class _NoName(BaseScraper):
        SOURCE_NAME = ""

        def fetch_raw(self) -> Any:
            return None

        def parse(self, raw: Any) -> Iterator[dict[str, Any]]:
            yield from []

        def transform(self, record: dict[str, Any]) -> BaseModel:
            return _DummyModel(value=0)

    with pytest.raises(ValueError, match="SOURCE_NAME"):
        _NoName()
