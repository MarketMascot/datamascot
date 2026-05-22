"""Trendlyne scraper — QVT scores and SWOT analysis via HTML widgets.

Two modes (set by caller):
  qvt   — GET /web-widget/qvt-widget/Poppins/{symbol}/  → trendlyne_qvt table
  swot  — GET /web-widget/swot-widget/Poppins/{symbol}/ → trendlyne_swot table

Rate limit: 0.3 RPS (conservative; Trendlyne blocks aggressive scrapers).

CAPTCHA detection: if 5 consecutive responses are <500 bytes, the circuit
opens (they're serving a CAPTCHA page, not real data).
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import Any

from bs4 import BeautifulSoup
from pydantic import BaseModel

from dastock.models.trendlyne import TrendlyneQvt, TrendlyneSwot
from dastock.scrapers.base import BaseScraper

logger = logging.getLogger(__name__)

_BASE = "https://trendlyne.com/web-widget"
_QVT_PATH = "qvt-widget/Poppins"
_SWOT_PATH = "swot-widget/Poppins"
_WIDGET_PARAMS = "?posCol=00A25B&primaryCol=006AFF&negCol=EB3B00&neuCol=F7AE00"
_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://trendlyne.com/",
}

# If response body is smaller than this, it's likely a CAPTCHA page.
_MIN_BODY_BYTES = 500
_CAPTCHA_CONSECUTIVE_THRESHOLD = 5


class TrendlyneScraper(BaseScraper):
    """Scraper for Trendlyne QVT + SWOT HTML widgets."""

    SOURCE_NAME = "trendlyne"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._captcha_streak = 0

    # ─── QVT ─────────────────────────────────────────────────────────────────

    def fetch_qvt(self, nse_symbol: str) -> str:
        """Fetch raw HTML for the QVT widget. Returns empty string on empty-state page."""
        url = f"{_BASE}/{_QVT_PATH}/{nse_symbol}/{_WIDGET_PARAMS}"
        resp = self._get(url, headers=_HEADERS)
        return self._check_body(resp.text, nse_symbol, "qvt")

    def parse_qvt(self, nse_symbol: str, html: str) -> TrendlyneQvt:
        """Parse QVT widget HTML into a TrendlyneQvt model."""
        if not html:
            return TrendlyneQvt(nse_symbol=nse_symbol)

        soup = BeautifulSoup(html, "lxml")
        result: dict[str, Any] = {"nse_symbol": nse_symbol}

        for box in soup.select(".param-wrapper"):
            label_el = box.find("span", class_="name_text")
            score_el = box.find("span", class_="percent_number")
            insight_el = box.find("span", class_="insight_text")

            if not (label_el and score_el):
                continue

            label = label_el.get_text(strip=True).lower()
            score_text = score_el.get_text(strip=True)
            insight = insight_el.get_text(strip=True) if insight_el else None

            # Trendlyne uses "quality", "valuation", "technicals" as labels
            if "quality" in label:
                result["quality_score"] = score_text
                result["quality_insight"] = insight
            elif "valuation" in label:
                result["valuation_score"] = score_text
                result["valuation_insight"] = insight
            elif "technical" in label:
                result["technical_score"] = score_text
                result["technical_insight"] = insight

        return TrendlyneQvt.model_validate(result)

    # ─── SWOT ─────────────────────────────────────────────────────────────────

    def fetch_swot(self, nse_symbol: str) -> str:
        """Fetch raw HTML for the SWOT widget. Returns empty string on empty-state page."""
        url = f"{_BASE}/{_SWOT_PATH}/{nse_symbol}/{_WIDGET_PARAMS}"
        resp = self._get(url, headers=_HEADERS)
        return self._check_body(resp.text, nse_symbol, "swot")

    def parse_swot(self, nse_symbol: str, html: str) -> TrendlyneSwot:
        """Parse SWOT widget HTML into a TrendlyneSwot model."""
        if not html:
            return TrendlyneSwot(nse_symbol=nse_symbol)

        soup = BeautifulSoup(html, "lxml")
        result: dict[str, Any] = {"nse_symbol": nse_symbol}

        _SECTION_MAP = {
            "Strengths": "strengths",
            "Weakness": "weakness",
            "Opportunity": "opportunities",
            "Threats": "threats",
        }

        for section_name, key in _SECTION_MAP.items():
            card = soup.find("div", class_="swot_cards", attrs={"data-value": section_name})
            if not card:
                continue

            count_el = card.find("span", class_="tag_number")
            count_text = count_el.get_text(strip=True) if count_el else None

            list_block = soup.find("ul", {"data-value": section_name})
            items = [li.get_text(strip=True) for li in list_block.find_all("li")] if list_block else []
            text = "\n".join(items)[:2000] or None

            result[f"{key}_count"] = count_text
            result[f"{key}_text"] = text

        return TrendlyneSwot.model_validate(result)

    # ─── CAPTCHA / empty-state detection ─────────────────────────────────────

    def _check_body(self, html: str, symbol: str, mode: str) -> str:
        """Return html if it looks like real data, empty string if empty-state page.

        Also tracks consecutive tiny responses and opens the circuit breaker if
        they look like CAPTCHA pages.
        """
        body_size = len(html.encode("utf-8"))

        if body_size < _MIN_BODY_BYTES:
            self._captcha_streak += 1
            logger.debug(
                f"{symbol} {mode} response is {body_size}B "
                f"(captcha_streak={self._captcha_streak})"
            )
            if self._captcha_streak >= _CAPTCHA_CONSECUTIVE_THRESHOLD:
                # Escalate to circuit breaker: force it open so the caller aborts.
                self._circuit_open = True
                self._consecutive_failures = self._settings.circuit_breaker_threshold
                logger.error(
                    f"Trendlyne CAPTCHA detected after {self._captcha_streak} tiny responses "
                    f"— circuit opened"
                )
            return ""

        # Check for common "no data" markers in Trendlyne HTML
        if _is_empty_state(html):
            logger.debug(f"{symbol} {mode}: empty-state page (no data for this stock)")
            # Not a CAPTCHA — reset streak
            self._captcha_streak = 0
            return ""

        self._captcha_streak = 0
        return html

    # ─── BaseScraper abstract methods (default = QVT) ────────────────────────

    def fetch_raw(self) -> Any:
        raise NotImplementedError("Use fetch_qvt() or fetch_swot() directly.")

    def parse(self, raw: Any) -> Iterator[dict[str, Any]]:
        raise NotImplementedError("Use parse_qvt() or parse_swot() directly.")

    def transform(self, record: dict[str, Any]) -> BaseModel:
        raise NotImplementedError("Use parse_qvt() or parse_swot() directly.")

    def external_id_of(self, record: dict[str, Any]) -> str:
        return str(record.get("nse_symbol", ""))


# ─── HTML helpers ─────────────────────────────────────────────────────────────


def _is_empty_state(html: str) -> bool:
    """Detect Trendlyne's 'data not available' page.

    Trendlyne returns 200 with a sparse HTML page for newly-listed stocks or
    those without enough history. We check for absence of expected widget elements.
    """
    # Both widgets always contain at least one of these markers when they have real data.
    real_data_markers = [
        "param-wrapper",   # QVT
        "swot_cards",      # SWOT
        "percent_number",  # QVT score
        "tag_number",      # SWOT count
    ]
    return not any(marker in html for marker in real_data_markers)
