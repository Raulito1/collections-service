# app/supabase_client.py
"""Shared Supabase client for the backend."""

from functools import lru_cache
from typing import Optional

from supabase import Client, create_client

from .config import settings


@lru_cache(maxsize=1)
def get_supabase_client() -> Client:
    """Return a cached Supabase client instance."""
    url = settings.SUPABASE_URL
    key: Optional[str] = settings.SUPABASE_SERVICE_ROLE_KEY or settings.SUPABASE_ANON_KEY
    if not url or not key:
        raise RuntimeError("Supabase credentials are not configured")
    return create_client(url, key)


__all__ = ["get_supabase_client"]
