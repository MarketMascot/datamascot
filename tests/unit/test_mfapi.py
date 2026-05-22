"""Tests for mfapi.in scraper + models."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import httpx
import pytest
import respx

from dastock.models.mfapi import (
    MfapiNavPoint,
    MfapiSchemeLatest,
    MfapiSchemeListEntry,
)
from dastock.scrapers.mfapi import MfapiScraper

# ─── Pydantic models ─────────────────────────────────────────────────────────


class TestMfapiSchemeListEntry:
    def test_parses_camel_case_from_list_endpoint(self) -> None:
        entry = MfapiSchemeListEntry.model_validate(
            {
                "schemeCode": 125497,
                "schemeName": "SBI Small Cap Fund - Direct Plan - Growth",
                "isinGrowth": "INF200K01T51",
                "isinDivReinvestment": None,
            }
        )
        assert entry.scheme_code == 125497
        assert entry.isin_growth == "INF200K01T51"
        assert entry.isin_div_reinvestment is None

    def test_accepts_snake_case_too(self) -> None:
        """populate_by_name allows either; useful for tests/fixtures."""
        entry = MfapiSchemeListEntry.model_validate(
            {"scheme_code": 100, "scheme_name": "X", "isin_growth": "ABC"}
        )
        assert entry.scheme_code == 100


class TestMfapiNavPoint:
    def test_parses_dd_mm_yyyy_date(self) -> None:
        p = MfapiNavPoint.model_validate({"date": "21-05-2026", "nav": "191.75750"})
        assert p.date == date(2026, 5, 21)
        assert p.nav == Decimal("191.75750")

    def test_accepts_already_typed_date(self) -> None:
        p = MfapiNavPoint.model_validate({"date": date(2026, 1, 1), "nav": "10.0"})
        assert p.date == date(2026, 1, 1)

    def test_rejects_negative_nav(self) -> None:
        with pytest.raises(ValueError, match="non-negative"):
            MfapiNavPoint.model_validate({"date": "01-01-2026", "nav": "-1.0"})

    def test_strips_whitespace_in_nav(self) -> None:
        p = MfapiNavPoint.model_validate({"date": "01-01-2026", "nav": "  10.5  "})
        assert p.nav == Decimal("10.5")


class TestMfapiSchemeLatest:
    def _payload(
        self, status: str = "SUCCESS", data: list | None = None
    ) -> dict:
        return {
            "meta": {
                "fund_house": "SBI Mutual Fund",
                "scheme_type": "Open Ended Schemes",
                "scheme_category": "Equity Scheme - Small Cap Fund",
                "scheme_code": 125497,
                "scheme_name": "SBI Small Cap Fund - Direct Plan - Growth",
                "isin_growth": "INF200K01T51",
                "isin_div_reinvestment": None,
            },
            "data": data
            if data is not None
            else [{"date": "21-05-2026", "nav": "191.75750"}],
            "status": status,
        }

    def test_parses_valid_response(self) -> None:
        model = MfapiSchemeLatest.model_validate(self._payload())
        assert model.meta.scheme_code == 125497
        assert model.latest_nav_point.nav == Decimal("191.75750")

    def test_rejects_non_success_status(self) -> None:
        with pytest.raises(ValueError, match="status="):
            MfapiSchemeLatest.model_validate(self._payload(status="FAIL"))

    def test_rejects_empty_data(self) -> None:
        with pytest.raises(ValueError, match="empty data"):
            MfapiSchemeLatest.model_validate(self._payload(data=[]))

    def test_latest_nav_point_returns_first(self) -> None:
        """mfapi returns data newest-first; latest = data[0]."""
        payload = self._payload(
            data=[
                {"date": "21-05-2026", "nav": "192.0"},
                {"date": "20-05-2026", "nav": "191.0"},
            ]
        )
        model = MfapiSchemeLatest.model_validate(payload)
        assert model.latest_nav_point.date == date(2026, 5, 21)


# ─── MfapiScraper HTTP calls ─────────────────────────────────────────────────


@pytest.fixture
def scraper() -> MfapiScraper:
    s = MfapiScraper()
    yield s
    s.close()


@respx.mock
def test_fetch_scheme_list_returns_array(scraper: MfapiScraper) -> None:
    respx.get("https://api.mfapi.in/mf").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"schemeCode": 100, "schemeName": "A", "isinGrowth": "X"},
                {"schemeCode": 101, "schemeName": "B", "isinGrowth": None},
            ],
        )
    )
    result = scraper.fetch_scheme_list()
    assert len(result) == 2
    assert result[0]["schemeCode"] == 100


@respx.mock
def test_fetch_latest_for_returns_dict(scraper: MfapiScraper) -> None:
    respx.get("https://api.mfapi.in/mf/125497/latest").mock(
        return_value=httpx.Response(
            200,
            json={
                "meta": {"scheme_code": 125497, "scheme_name": "X"},
                "data": [{"date": "21-05-2026", "nav": "100.0"}],
                "status": "SUCCESS",
            },
        )
    )
    result = scraper.fetch_latest_for(125497)
    assert result["meta"]["scheme_code"] == 125497


def test_external_id_of_handles_both_casings(scraper: MfapiScraper) -> None:
    """Used during runner-orchestrated bootstrap (list endpoint, camelCase)
    and any future generic mode (snake_case)."""
    assert scraper.external_id_of({"schemeCode": 100}) == "100"
    assert scraper.external_id_of({"scheme_code": 200}) == "200"
    assert scraper.external_id_of({}) == ""


def test_transform_scheme_validates_list_entry(scraper: MfapiScraper) -> None:
    entry = scraper.transform_scheme(
        {"schemeCode": 1, "schemeName": "X", "isinGrowth": "Y"}
    )
    assert isinstance(entry, MfapiSchemeListEntry)
    assert entry.scheme_code == 1


def test_transform_latest_validates_response(scraper: MfapiScraper) -> None:
    model = scraper.transform_latest(
        {
            "meta": {"scheme_code": 1, "scheme_name": "X"},
            "data": [{"date": "01-01-2026", "nav": "10.0"}],
            "status": "SUCCESS",
        }
    )
    assert isinstance(model, MfapiSchemeLatest)
    assert model.latest_nav_point.nav == Decimal("10.0")
