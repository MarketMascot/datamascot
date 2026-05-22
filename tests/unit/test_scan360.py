"""Tests for Scan360 models + scraper."""

from __future__ import annotations

import httpx
import pytest
import respx

from dastock.models.scan360 import Scan360IndustryRecord, Scan360Stock
from dastock.scrapers.scan360 import Scan360Scraper


# ─── Scan360Stock ─────────────────────────────────────────────────────────────


class TestScan360Stock:
    def test_parses_valid_stock(self) -> None:
        s = Scan360Stock.model_validate({"symbol": "reliance", "name": "Reliance Industries"})
        assert s.symbol == "RELIANCE"
        assert s.name == "Reliance Industries"

    def test_uppercases_symbol(self) -> None:
        s = Scan360Stock.model_validate({"symbol": "tcs"})
        assert s.symbol == "TCS"

    def test_strips_whitespace_from_symbol(self) -> None:
        s = Scan360Stock.model_validate({"symbol": "  INFY  "})
        assert s.symbol == "INFY"

    def test_none_name_stays_none(self) -> None:
        s = Scan360Stock.model_validate({"symbol": "X", "name": ""})
        assert s.name is None


# ─── Scan360IndustryRecord ────────────────────────────────────────────────────


class TestScan360IndustryRecord:
    def test_parses_valid_record(self) -> None:
        r = Scan360IndustryRecord.model_validate(
            {"nse_symbol": "RELIANCE", "industry": "Oil & Gas"}
        )
        assert r.nse_symbol == "RELIANCE"
        assert r.industry == "Oil & Gas"


# ─── Scan360Scraper ───────────────────────────────────────────────────────────


@pytest.fixture
def scraper() -> Scan360Scraper:
    s = Scan360Scraper()
    yield s
    s.close()


_SAMPLE_RESPONSE = {
    "Information Technology": {
        "stocks": [
            {"symbol": "TCS", "name": "Tata Consultancy Services"},
            {"symbol": "INFY", "name": "Infosys"},
        ]
    },
    "Oil & Gas": {
        "stocks": [
            {"symbol": "RELIANCE", "name": "Reliance Industries"},
            {"symbol": "TCS", "name": "Tata Consultancy Services"},  # duplicate — should be skipped
        ]
    },
}


def test_parse_deduplicates_symbols(scraper: Scan360Scraper) -> None:
    records = list(scraper.parse(_SAMPLE_RESPONSE))
    symbols = [r["nse_symbol"] for r in records]
    assert len(symbols) == len(set(symbols)), "duplicate symbols found"
    assert "TCS" in symbols
    assert "RELIANCE" in symbols


def test_parse_first_industry_wins(scraper: Scan360Scraper) -> None:
    records = list(scraper.parse(_SAMPLE_RESPONSE))
    tcs_record = next(r for r in records if r["nse_symbol"] == "TCS")
    # TCS appears in IT first, then Oil & Gas — IT should win
    assert tcs_record["industry"] == "Information Technology"


def test_build_industry_map_returns_dict(scraper: Scan360Scraper) -> None:
    m = scraper.build_industry_map(_SAMPLE_RESPONSE)
    assert m["TCS"] == "Information Technology"
    assert m["INFY"] == "Information Technology"
    assert m["RELIANCE"] == "Oil & Gas"


def test_parse_skips_non_dict_buckets(scraper: Scan360Scraper) -> None:
    raw = {"BadBucket": "not a dict", "IT": {"stocks": [{"symbol": "TCS"}]}}
    records = list(scraper.parse(raw))
    assert len(records) == 1
    assert records[0]["nse_symbol"] == "TCS"


def test_parse_empty_response_yields_nothing(scraper: Scan360Scraper) -> None:
    assert list(scraper.parse({})) == []


def test_transform_returns_record(scraper: Scan360Scraper) -> None:
    record = scraper.transform({"nse_symbol": "TCS", "industry": "IT"})
    assert isinstance(record, Scan360IndustryRecord)
    assert record.nse_symbol == "TCS"


def test_external_id_of(scraper: Scan360Scraper) -> None:
    assert scraper.external_id_of({"nse_symbol": "TCS"}) == "TCS"
    assert scraper.external_id_of({}) == ""


@respx.mock
def test_fetch_raw_returns_dict(scraper: Scan360Scraper) -> None:
    respx.get("https://scan360.in/api/industries").mock(
        return_value=httpx.Response(200, json=_SAMPLE_RESPONSE)
    )
    result = scraper.fetch_raw()
    assert "Information Technology" in result
    assert len(result["Information Technology"]["stocks"]) == 2


@respx.mock
def test_fetch_raw_returns_empty_on_non_dict(scraper: Scan360Scraper) -> None:
    respx.get("https://scan360.in/api/industries").mock(
        return_value=httpx.Response(200, json=[])
    )
    result = scraper.fetch_raw()
    assert result == {}
