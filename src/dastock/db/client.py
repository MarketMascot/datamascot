"""Supabase client singleton.

All DB operations go through this. Uses the service_role key, which
bypasses RLS — this module must never be exposed to client-side code.
"""

from __future__ import annotations

from functools import lru_cache

from supabase import Client, create_client

from dastock.config import get_settings


@lru_cache(maxsize=1)
def get_supabase() -> Client:
    """Return the singleton Supabase client.

    Uses the service_role key, which bypasses RLS. Only suitable for
    server-side (scraper) use. Tests should monkey-patch this function.
    """
    settings = get_settings()
    return create_client(
        settings.supabase_url,
        settings.supabase_service_role_key.get_secret_value(),
    )
