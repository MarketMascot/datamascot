"""Tests for Trendlyne models + scraper."""

from __future__ import annotations

import pytest
import respx
import httpx

from dastock.models.trendlyne import TrendlyneQvt, TrendlyneSwot
from dastock.scrapers.trendlyne import TrendlyneScraper, _is_empty_state


# ─── TrendlyneQvt ────────────────────────────────────────────────────────────


class TestTrendlyneQvt:
    def test_parses_valid_scores(self) -> None:
        m = TrendlyneQvt.model_validate(
            {
                "nse_symbol": "RELIANCE",
                "quality_score": 85,
                "quality_insight": "Strong fundamentals",
                "valuation_score": 40,
                "valuation_insight": "Fairly valued",
                "technical_score": 70,
                "technical_insight": "Bullish trend",
            }
        )
        assert m.quality_score == 85
        assert m.valuation_score == 40
        assert m.technical_score == 70
        assert m.has_data is True

    def test_coerces_score_string(self) -> None:
        m = TrendlyneQvt.model_validate(
            {"nse_symbol": "TCS", "quality_score": "72.0", "valuation_score": "55%"}
        )
        assert m.quality_score == 72
        assert m.valuation_score == 55

    def test_na_score_becomes_none(self) -> None:
        m = TrendlyneQvt.model_validate({"nse_symbol": "X", "quality_score": "N/A"})
        assert m.quality_score is None

    def test_clamps_score_above_100(self) -> None:
        m = TrendlyneQvt.model_validate({"nse_symbol": "X", "quality_score": 150})
        assert m.quality_score == 100

    def test_clamps_score_below_0(self) -> None:
        m = TrendlyneQvt.model_validate({"nse_symbol": "X", "technical_score": -5})
        assert m.technical_score == 0

    def test_has_data_false_when_all_none(self) -> None:
        m = TrendlyneQvt(nse_symbol="NEWCO")
        assert m.has_data is False


# ─── TrendlyneSwot ───────────────────────────────────────────────────────────


class TestTrendlyneSwot:
    def test_parses_valid_swot(self) -> None:
        m = TrendlyneSwot.model_validate(
            {
                "nse_symbol": "INFY",
                "strengths_count": 5,
                "strengths_text": "Strong exports\nDiversified revenue",
                "weakness_count": 2,
                "weakness_text": "High attrition",
                "opportunities_count": 3,
                "opportunities_text": "Cloud growth",
                "threats_count": 1,
                "threats_text": "Wage inflation",
            }
        )
        assert m.strengths_count == 5
        assert m.weakness_count == 2
        assert m.has_data is True

    def test_has_data_false_when_all_none(self) -> None:
        m = TrendlyneSwot(nse_symbol="NEWCO")
        assert m.has_data is False

    def test_dash_count_becomes_none(self) -> None:
        m = TrendlyneSwot.model_validate({"nse_symbol": "X", "strengths_count": "--"})
        assert m.strengths_count is None


# ─── _is_empty_state ─────────────────────────────────────────────────────────


def test_empty_state_detects_missing_markers() -> None:
    assert _is_empty_state("<html><body>No data available</body></html>") is True


def test_empty_state_false_for_real_qvt_html() -> None:
    html = '<div class="param-wrapper"><span class="percent_number">75</span></div>'
    assert _is_empty_state(html) is False


def test_empty_state_false_for_real_swot_html() -> None:
    html = '<div class="swot_cards" data-value="Strengths"><span class="tag_number">5</span></div>'
    assert _is_empty_state(html) is False


# ─── TrendlyneScraper HTML parsing ───────────────────────────────────────────


@pytest.fixture
def scraper() -> TrendlyneScraper:
    s = TrendlyneScraper()
    yield s
    s.close()


_QVT_HTML = """
<html><body>
  <div class="param-wrapper">
    <span class="name_text">Quality</span>
    <span class="percent_number">80</span>
    <span class="insight_text">Strong business</span>
  </div>
  <div class="param-wrapper">
    <span class="name_text">Valuation</span>
    <span class="percent_number">45</span>
    <span class="insight_text">Overvalued</span>
  </div>
  <div class="param-wrapper">
    <span class="name_text">Technicals</span>
    <span class="percent_number">60</span>
    <span class="insight_text">Neutral</span>
  </div>
</body></html>
"""

_SWOT_HTML = """
<html><body>
  <div class="swot_cards" data-value="Strengths">
    <span class="tag_number">5</span>
  </div>
  <ul data-value="Strengths">
    <li>Market leader</li>
    <li>Strong brand</li>
  </ul>
  <div class="swot_cards" data-value="Weakness">
    <span class="tag_number">2</span>
  </div>
  <ul data-value="Weakness">
    <li>High debt</li>
  </ul>
  <div class="swot_cards" data-value="Opportunity">
    <span class="tag_number">3</span>
  </div>
  <ul data-value="Opportunity">
    <li>Exports</li>
  </ul>
  <div class="swot_cards" data-value="Threats">
    <span class="tag_number">1</span>
  </div>
  <ul data-value="Threats">
    <li>Competition</li>
  </ul>
</body></html>
"""


def test_parse_qvt_extracts_scores(scraper: TrendlyneScraper) -> None:
    model = scraper.parse_qvt("RELIANCE", _QVT_HTML)
    assert model.quality_score == 80
    assert model.quality_insight == "Strong business"
    assert model.valuation_score == 45
    assert model.technical_score == 60
    assert model.has_data is True


def test_parse_qvt_empty_html_returns_no_data(scraper: TrendlyneScraper) -> None:
    model = scraper.parse_qvt("NEWCO", "")
    assert model.has_data is False


def test_parse_swot_extracts_sections(scraper: TrendlyneScraper) -> None:
    model = scraper.parse_swot("INFY", _SWOT_HTML)
    assert model.strengths_count == 5
    assert "Market leader" in (model.strengths_text or "")
    assert model.weakness_count == 2
    assert model.opportunities_count == 3
    assert model.threats_count == 1
    assert model.has_data is True


def test_parse_swot_empty_html_returns_no_data(scraper: TrendlyneScraper) -> None:
    model = scraper.parse_swot("NEWCO", "")
    assert model.has_data is False


@respx.mock
def test_fetch_qvt_returns_html(scraper: TrendlyneScraper) -> None:
    respx.get(
        url__startswith="https://trendlyne.com/web-widget/qvt-widget/Poppins/RELIANCE/"
    ).mock(return_value=httpx.Response(200, text=_QVT_HTML))
    html = scraper.fetch_qvt("RELIANCE")
    assert "param-wrapper" in html


@respx.mock
def test_fetch_qvt_empty_state_returns_empty_string(scraper: TrendlyneScraper) -> None:
    respx.get(
        url__startswith="https://trendlyne.com/web-widget/qvt-widget/Poppins/NEWCO/"
    ).mock(return_value=httpx.Response(200, text="<html><body>No data</body></html>"))
    html = scraper.fetch_qvt("NEWCO")
    assert html == ""


@respx.mock
def test_fetch_swot_returns_html(scraper: TrendlyneScraper) -> None:
    respx.get(
        url__startswith="https://trendlyne.com/web-widget/swot-widget/Poppins/TCS/"
    ).mock(return_value=httpx.Response(200, text=_SWOT_HTML))
    html = scraper.fetch_swot("TCS")
    assert "swot_cards" in html


@respx.mock
def test_captcha_opens_circuit_after_threshold(scraper: TrendlyneScraper) -> None:
    tiny_body = "x" * 100  # < 500 bytes
    respx.get(
        url__startswith="https://trendlyne.com/web-widget/qvt-widget/Poppins/"
    ).mock(return_value=httpx.Response(200, text=tiny_body))

    symbols = ["A", "B", "C", "D", "E"]
    for sym in symbols:
        scraper.fetch_qvt(sym)

    assert scraper._circuit_open is True
