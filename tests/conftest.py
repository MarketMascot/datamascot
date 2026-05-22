"""Pytest fixtures shared across all tests."""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def _isolate_env(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Provide deterministic dummy env vars so unit tests never read the real .env.

    Integration tests (under tests/integration/) skip this isolation and use
    the real .env values — that's the whole point of integration tests.
    """
    if "integration" in request.node.nodeid.replace("\\", "/").split("/"):
        # Still clear caches so the integration test sees a fresh Settings/client
        from dastock.config import get_settings
        from dastock.db.client import get_supabase

        get_settings.cache_clear()
        get_supabase.cache_clear()
        return

    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test-anon-key")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-service-role-key")
    monkeypatch.delenv("SUPABASE_DB_URL", raising=False)
    from dastock.config import get_settings

    get_settings.cache_clear()


@pytest.fixture
def mock_supabase() -> Iterator[MagicMock]:
    """A MagicMock that mimics the chained-builder API of supabase-py."""
    client = MagicMock(name="supabase_client")
    # Default behavior: every chain returns the same mock, .execute() returns empty data
    table = MagicMock(name="table")
    table.select.return_value = table
    table.insert.return_value = table
    table.upsert.return_value = table
    table.update.return_value = table
    table.eq.return_value = table
    table.gte.return_value = table
    table.order.return_value = table
    table.limit.return_value = table
    table.execute.return_value = MagicMock(data=[])
    client.table.return_value = table
    yield client
