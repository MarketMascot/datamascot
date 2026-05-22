"""Tests for the Settings/config layer."""

from __future__ import annotations

from dastock.config import Settings, get_settings


def test_settings_loads_from_env() -> None:
    s = Settings()  # type: ignore[call-arg]
    assert str(s.supabase_url) == "https://test.supabase.co"
    assert s.supabase_anon_key.get_secret_value() == "test-anon-key"
    assert s.supabase_service_role_key.get_secret_value() == "test-service-role-key"


def test_rate_limit_for_known_source() -> None:
    s = Settings()  # type: ignore[call-arg]
    assert s.rate_limit_for("mfapi") == 5.0
    assert s.rate_limit_for("trendlyne") == 0.3
    assert s.rate_limit_for("dhan") == 2.0


def test_rate_limit_for_unknown_source_defaults_to_1() -> None:
    s = Settings()  # type: ignore[call-arg]
    assert s.rate_limit_for("nonexistent_source") == 1.0


def test_get_settings_is_cached() -> None:
    a = get_settings()
    b = get_settings()
    assert a is b
