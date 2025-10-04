# app/routers/deps.py
from fastapi import Depends

from ..auth import get_current_user
from ..supabase_client import get_supabase_client


async def get_user_id(user=Depends(get_current_user)) -> str:
    """Extract the authenticated Supabase user id."""
    return user["sub"]


def get_supabase():
    """Return a shared Supabase client instance."""
    return get_supabase_client()
