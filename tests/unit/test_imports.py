"""Verify all public modules import cleanly."""

from __future__ import annotations


def test_all_imports() -> None:
    from dastock.config import Settings, get_settings  # noqa: F401
    from dastock.db.client import get_supabase  # noqa: F401
    from dastock.db.repositories.analytics import AnalyticsRepository  # noqa: F401
    from dastock.db.repositories.mutual_funds import MutualFundRepository  # noqa: F401
    from dastock.db.repositories.stocks import StockRepository  # noqa: F401
    from dastock.identity.resolver import IdentityResolver  # noqa: F401
    from dastock.models.dhan import DhanCandle, DhanSecurityMasterRow  # noqa: F401
    from dastock.models.mfapi import MfapiNavPoint, MfapiSchemeListEntry  # noqa: F401
    from dastock.models.rupeevest import (  # noqa: F401
        RupeevFincodeEntry,
        RupeevFundMetadata,
        RupeevHolding,
    )
    from dastock.models.scan360 import Scan360IndustryRecord, Scan360Stock  # noqa: F401
    from dastock.models.trendlyne import TrendlyneQvt, TrendlyneSwot  # noqa: F401
    from dastock.pipeline.dead_letter import DeadLetterLogger  # noqa: F401
    from dastock.pipeline.run_tracker import RunTracker  # noqa: F401
    from dastock.pipeline.runner import run_pipeline, skip_record  # noqa: F401
    from dastock.scrapers.base import BaseScraper  # noqa: F401
    from dastock.scrapers.dhan import DhanScraper  # noqa: F401
    from dastock.scrapers.exceptions import (  # noqa: F401
        CircuitOpenError,
        EmptyRunError,
        TokenExpiredError,
    )
    from dastock.scrapers.mfapi import MfapiScraper  # noqa: F401
    from dastock.scrapers.rupeevest import RupeevScraper  # noqa: F401
    from dastock.scrapers.scan360 import Scan360Scraper  # noqa: F401
    from dastock.scrapers.trendlyne import TrendlyneScraper  # noqa: F401
