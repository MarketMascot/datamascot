"""Tests for Dhan models + scraper."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import httpx
import pytest
import respx

from dastock.models.dhan import (
    DhanCandle,
    DhanHistoricalResponse,
    DhanSecurityMasterRow,
)
from dastock.scrapers.dhan import DhanScraper

# ─── Security master row ─────────────────────────────────────────────────────


class TestDhanSecurityMasterRow:
    def _row(self, **overrides: str | None) -> dict[str, str | None]:
        row: dict[str, str | None] = {
            "EXCH_ID": "NSE",
            "SECURITY_ID": "2885",
            "ISIN": "INE002A01018",
            "INSTRUMENT": "EQUITY",
            "UNDERLYING_SYMBOL": "RELIANCE",
            "SYMBOL_NAME": "RELIANCE INDUSTRIES LTD",
            "DISPLAY_NAME": "Reliance Industries",
            "SERIES": "EQ",
        }
        row.update(overrides)
        return row

    def test_parses_valid_nse_row(self) -> None:
        model = DhanSecurityMasterRow.model_validate(self._row())
        assert model.exchange == "NSE"
        assert model.security_id == "2885"
        assert model.isin == "INE002A01018"
        assert model.trading_symbol == "RELIANCE"

    def test_parses_valid_bse_row(self) -> None:
        model = DhanSecurityMasterRow.model_validate(
            self._row(EXCH_ID="BSE", SECURITY_ID="500325", UNDERLYING_SYMBOL="500325", SERIES="A")
        )
        assert model.exchange == "BSE"
        assert model.trading_symbol == "500325"
        assert model.series == "A"

    def test_coerces_na_isin_to_none(self) -> None:
        model = DhanSecurityMasterRow.model_validate(self._row(ISIN="NA"))
        assert model.isin is None

    def test_coerces_empty_isin_to_none(self) -> None:
        model = DhanSecurityMasterRow.model_validate(self._row(ISIN=""))
        assert model.isin is None

    def test_normalizes_exchange_case(self) -> None:
        model = DhanSecurityMasterRow.model_validate(self._row(EXCH_ID="nse"))
        assert model.exchange == "NSE"

    def test_strips_whitespace(self) -> None:
        model = DhanSecurityMasterRow.model_validate(
            self._row(SECURITY_ID="  2885  ", UNDERLYING_SYMBOL=" RELIANCE ")
        )
        assert model.security_id == "2885"
        assert model.trading_symbol == "RELIANCE"


# ─── Historical response ─────────────────────────────────────────────────────


class TestDhanHistoricalResponse:
    def test_parses_valid_response(self) -> None:
        resp = DhanHistoricalResponse.model_validate(
            {
                "open": [1344.0, 1362.9],
                "high": [1370.0, 1370.0],
                "low": [1318.4, 1312.6],
                "close": [1322.7, 1359.7],
                "volume": [21665501.0, 13248515.0],
                "timestamp": [1779129000.0, 1779215400.0],
            }
        )
        candles = resp.iter_candles()
        assert len(candles) == 2
        assert candles[0].trade_date == date(2026, 5, 18)
        assert candles[0].open == Decimal("1344.0")

    def test_rejects_mismatched_array_lengths(self) -> None:
        with pytest.raises(ValueError, match="mismatched"):
            DhanHistoricalResponse.model_validate(
                {
                    "open": [100.0, 101.0],
                    "high": [100.0],  # mismatched
                    "low": [99.0, 100.0],
                    "close": [99.5, 100.5],
                    "volume": [1000, 1000],
                    "timestamp": [1779129000.0, 1779215400.0],
                }
            )


# ─── Candle ──────────────────────────────────────────────────────────────────


class TestDhanCandle:
    def _candle(self, **overrides: Decimal | int | date) -> dict[str, Decimal | int | date]:
        defaults: dict[str, Decimal | int | date] = {
            "trade_date": date(2026, 5, 21),
            "open": Decimal("100.0"),
            "high": Decimal("105.0"),
            "low": Decimal("99.0"),
            "close": Decimal("102.0"),
            "volume": 1000000,
        }
        defaults.update(overrides)
        return defaults

    def test_parses_valid_candle(self) -> None:
        c = DhanCandle.model_validate(self._candle())
        assert c.close == Decimal("102.0")

    def test_rejects_negative_price(self) -> None:
        with pytest.raises(ValueError, match="positive"):
            DhanCandle.model_validate(self._candle(close=Decimal("-1.0")))

    def test_rejects_zero_price(self) -> None:
        with pytest.raises(ValueError, match="positive"):
            DhanCandle.model_validate(self._candle(close=Decimal("0")))

    def test_rejects_low_greater_than_open(self) -> None:
        with pytest.raises(ValueError, match="inconsistency"):
            DhanCandle.model_validate(self._candle(low=Decimal("110.0"), open=Decimal("100.0")))

    def test_rejects_high_below_close(self) -> None:
        with pytest.raises(ValueError, match="inconsistency"):
            DhanCandle.model_validate(self._candle(high=Decimal("90.0"), close=Decimal("100.0")))


# ─── DhanScraper HTTP ────────────────────────────────────────────────────────


@pytest.fixture
def scraper(monkeypatch: pytest.MonkeyPatch) -> DhanScraper:
    # Override the Dhan env vars so the scraper has fake creds for tests
    monkeypatch.setenv("DHAN_CLIENT_ID", "test-client")
    monkeypatch.setenv("DHAN_ACCESS_TOKEN", "test-token")
    from dastock.config import get_settings

    get_settings.cache_clear()
    s = DhanScraper()
    yield s
    s.close()


@respx.mock
def test_fetch_security_master_returns_csv(scraper: DhanScraper) -> None:
    csv_body = (
        "EXCH_ID,SECURITY_ID,ISIN,INSTRUMENT,UNDERLYING_SYMBOL,SYMBOL_NAME,SERIES\n"
        "NSE,2885,INE002A01018,EQUITY,RELIANCE,RELIANCE INDUSTRIES LTD,EQ\n"
        "BSE,500325,INE002A01018,EQUITY,500325,RELIANCE INDUSTRIES LTD,A\n"
    )
    respx.get("https://images.dhan.co/api-data/api-scrip-master-detailed.csv").mock(
        return_value=httpx.Response(200, text=csv_body)
    )
    result = scraper.fetch_security_master()
    assert "RELIANCE" in result


def test_parse_security_master_filters_to_equity_with_isin(scraper: DhanScraper) -> None:
    csv_body = (
        "EXCH_ID,SECURITY_ID,ISIN,INSTRUMENT,INSTRUMENT_TYPE,UNDERLYING_SYMBOL,SYMBOL_NAME,SERIES\n"
        "NSE,2885,INE002A01018,EQUITY,ES,RELIANCE,RELIANCE INDUSTRIES LTD,EQ\n"
        "NSE,9999,NA,EQUITY,ES,FOO,FOO LTD,EQ\n"
        "NSE,1234,INE123A01018,FUTSTK,ES,FOOFUT,FOO FUTURES,XX\n"
        "BSE,500325,INE002A01018,EQUITY,ES,500325,RELIANCE INDUSTRIES LTD,A\n"
        "BSE,533002,INF223J01051,EQUITY,MF,DWSFTS50AG,DEUTSCHE MF DW,F\n"
        "BSE,9876,INE999A01018,EQUITY,DEB,BONDX,BOND XYZ,N0\n"
    )
    rows = list(scraper.parse_security_master(csv_body))
    # Keep 2: NSE RELIANCE + BSE 500325 (both ES with ISIN).
    # Drop: NA-ISIN, FUTSTK instrument, MF instrument_type, DEB instrument_type.
    assert len(rows) == 2
    assert rows[0]["EXCH_ID"] == "NSE"
    assert rows[1]["EXCH_ID"] == "BSE"


def test_fetch_historical_uses_sdk(scraper: DhanScraper, monkeypatch: pytest.MonkeyPatch) -> None:
    """fetch_historical delegates to the dhanhq SDK's historical_daily_data."""
    sdk_result = {
        "open": [100.0],
        "high": [105.0],
        "low": [99.0],
        "close": [102.0],
        "volume": [1000000.0],
        "timestamp": [1779129000.0],
    }
    calls: list[dict] = []

    class _FakeSDK:
        NSE = "NSE_EQ"
        BSE = "BSE_EQ"

        def historical_daily_data(self, **kwargs: object) -> dict:
            calls.append(kwargs)
            return sdk_result

    monkeypatch.setattr(scraper, "_sdk", _FakeSDK())
    result = scraper.fetch_historical(
        security_id="2885",
        exchange="NSE",
        from_date=date(2026, 5, 19),
        to_date=date(2026, 5, 19),
    )
    assert len(calls) == 1
    assert calls[0]["security_id"] == "2885"
    assert calls[0]["exchange_segment"] == "NSE_EQ"
    assert calls[0]["from_date"] == "2026-05-19"
    assert result == sdk_result


def test_fetch_eod_candle_picks_last_candle_le_target(
    scraper: DhanScraper, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Window query returns multiple days; helper picks the right candle."""
    sdk_result = {
        "open": [100.0, 101.0, 102.0],
        "high": [105.0, 106.0, 107.0],
        "low": [99.0, 100.0, 101.0],
        "close": [102.0, 103.0, 104.0],
        "volume": [1000, 2000, 3000],
        "timestamp": [1779129000.0, 1779215400.0, 1779301800.0],
    }

    class _FakeSDK:
        NSE = "NSE_EQ"
        BSE = "BSE_EQ"

        def historical_daily_data(self, **kwargs: object) -> dict:
            return sdk_result

    monkeypatch.setattr(scraper, "_sdk", _FakeSDK())
    candle = scraper.fetch_eod_candle(
        security_id="2885",
        exchange="NSE",
        trade_date=date(2026, 5, 19),
    )
    assert candle is not None
    assert candle.trade_date == date(2026, 5, 19)
    assert candle.close == Decimal("103.0")


def test_external_id_of_returns_security_id(scraper: DhanScraper) -> None:
    assert scraper.external_id_of({"SECURITY_ID": "2885"}) == "2885"
    assert scraper.external_id_of({"SECURITY_ID": "  2885  "}) == "2885"
    assert scraper.external_id_of({}) == ""
