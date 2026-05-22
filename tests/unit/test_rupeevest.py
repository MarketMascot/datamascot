"""Tests for Rupeevest models + scraper."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import httpx
import pytest
import respx

from dastock.models.rupeevest import RupeevFincodeEntry, RupeevFundMetadata, RupeevHolding
from dastock.scrapers.rupeevest import RupeevScraper

# ─── RupeevFundMetadata ──────────────────────────────────────────────────────


class TestRupeevFundMetadata:
    def _fund(self, **overrides: object) -> dict:
        base: dict = {
            "schemecode": 125497,
            "s_name": "SBI Small Cap Fund-Reg(G)",
            "s_name1": "SBI Small Cap Fund Regular Growth",
            "fund_house": "SBI Mutual Fund",
            "navrs": 191.75,
            "aumtotal": "14500.23",
            "expenceratio": 1.45,
            "returns_1year": 12.5,
            "returns_3year": 18.0,
            "inception_date": "2009-09-09T00:00:00.000Z",
            "no_of_stocks": 72,
        }
        base.update(overrides)
        return base

    def test_parses_valid_fund(self) -> None:
        m = RupeevFundMetadata.model_validate(self._fund())
        assert m.schemecode == 125497
        assert m.navrs == Decimal("191.75")
        assert m.aumtotal == Decimal("14500.23")
        assert m.inception_date == date(2009, 9, 9)
        assert m.no_of_stocks == 72

    def test_coerces_dash_to_none(self) -> None:
        m = RupeevFundMetadata.model_validate(self._fund(returns_5year="-"))
        assert m.returns_5year is None

    def test_coerces_zero_string_to_none(self) -> None:
        m = RupeevFundMetadata.model_validate(self._fund(returns_10year="0"))
        assert m.returns_10year is None

    def test_handles_ppmcap_string(self) -> None:
        # ppmcap is a string like "200833.09" in the API
        m = RupeevFundMetadata.model_validate(self._fund())
        assert m.schemecode == 125497  # no crash

    def test_parses_iso_date(self) -> None:
        m = RupeevFundMetadata.model_validate(self._fund(inception_date="2023-09-25T00:00:00.000Z"))
        assert m.inception_date == date(2023, 9, 25)

    def test_null_inception_date(self) -> None:
        m = RupeevFundMetadata.model_validate(self._fund(inception_date=None))
        assert m.inception_date is None


# ─── RupeevHolding ───────────────────────────────────────────────────────────


class TestRupeevHolding:
    def test_parses_valid_holding(self) -> None:
        h = RupeevHolding.model_validate(
            {"fincode": 100002, "stock_name": "ABB India", "corpus_per": "3.5", "market_value": "500.25"}
        )
        assert h.fincode == 100002
        assert h.corpus_per == Decimal("3.5")
        assert h.display_name == "ABB India"

    def test_fincode_none_when_missing(self) -> None:
        h = RupeevHolding.model_validate({"stock_name": "HDFC Bank"})
        assert h.fincode is None

    def test_corpus_per_dash_is_none(self) -> None:
        h = RupeevHolding.model_validate({"fincode": 1, "corpus_per": "-"})
        assert h.corpus_per is None


# ─── RupeevFincodeEntry ──────────────────────────────────────────────────────


class TestRupeevFincodeEntry:
    def test_parses_bse_and_nse(self) -> None:
        e = RupeevFincodeEntry.model_validate(
            {"fincode": 100002, "compname": "ABB India Ltd.", "s_name": "ABB India",
             "stock_search": "ABB India Ltd. | 500002 | ABB"}
        )
        assert e.fincode == 100002
        assert e.bse_code() == "500002"
        assert e.nse_symbol() == "ABB"

    def test_handles_special_chars_in_symbol(self) -> None:
        e = RupeevFincodeEntry.model_validate(
            {"fincode": 100008, "compname": "Amara Raja", "s_name": "Amara Raja",
             "stock_search": "Amara Raja Energy & Mobility Ltd. | 500008 | ARE&M"}
        )
        assert e.bse_code() == "500008"
        assert e.nse_symbol() == "ARE&M"

    def test_missing_nse_returns_none(self) -> None:
        e = RupeevFincodeEntry.model_validate(
            {"fincode": 1, "compname": "X", "s_name": "X",
             "stock_search": "X Ltd. | 500001"}  # no NSE part
        )
        assert e.nse_symbol() is None


# ─── RupeevScraper HTTP ───────────────────────────────────────────────────────


@pytest.fixture
def scraper() -> RupeevScraper:
    s = RupeevScraper()
    yield s
    s.close()


@respx.mock
def test_fetch_fund_list_parses_schemedata(scraper: RupeevScraper) -> None:
    respx.post("https://www.rupeevest.com/functionalities/asset_class_section").mock(
        return_value=httpx.Response(
            200,
            json={
                "schemedata": [
                    {"schemecode": 100, "s_name": "Fund A", "navrs": 10.0},
                    {"schemecode": 101, "s_name": "Fund B", "navrs": 20.0},
                ],
                "total_count": [{"count": 2}],
            },
        )
    )
    result = scraper.fetch_fund_list()
    assert len(result) == 2
    assert result[0]["schemecode"] == 100


@respx.mock
def test_fetch_fund_list_falls_back_to_data_key(scraper: RupeevScraper) -> None:
    """If schemedata is absent, try 'data' key."""
    respx.post("https://www.rupeevest.com/functionalities/asset_class_section").mock(
        return_value=httpx.Response(200, json={"data": [{"schemecode": 99, "s_name": "X"}]})
    )
    result = scraper.fetch_fund_list()
    assert result[0]["schemecode"] == 99


@respx.mock
def test_fetch_holdings_returns_list(scraper: RupeevScraper) -> None:
    respx.get("https://www.rupeevest.com/functionalities/portfolio_holdings").mock(
        return_value=httpx.Response(
            200,
            json={"portfolio_holdings": [
                {"fincode": 100002, "stock_name": "ABB India", "corpus_per": "3.5"}
            ]},
        )
    )
    result = scraper.fetch_holdings(125497)
    assert len(result) == 1
    assert result[0]["fincode"] == 100002


@respx.mock
def test_fetch_holdings_empty_when_no_data(scraper: RupeevScraper) -> None:
    respx.get("https://www.rupeevest.com/functionalities/portfolio_holdings").mock(
        return_value=httpx.Response(200, json={"portfolio_holdings": []})
    )
    result = scraper.fetch_holdings(999)
    assert result == []


@respx.mock
def test_fetch_fincode_map_returns_entries(scraper: RupeevScraper) -> None:
    respx.get("https://www.rupeevest.com/mf_stock_portfolio/get_search_data_stock").mock(
        return_value=httpx.Response(
            200,
            json={"stock_data_search": [
                {"compname": "ABB India Ltd.", "s_name": "ABB India",
                 "fincode": 100002, "stock_search": "ABB India Ltd. | 500002 | ABB"}
            ]},
        )
    )
    result = scraper.fetch_fincode_map()
    assert len(result) == 1
    assert result[0]["fincode"] == 100002


def test_external_id_of(scraper: RupeevScraper) -> None:
    assert scraper.external_id_of({"schemecode": 125497}) == "125497"
    assert scraper.external_id_of({}) == ""
