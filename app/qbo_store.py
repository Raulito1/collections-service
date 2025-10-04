# app/qbo_store.py
"""Helpers for persisting QuickBooks data in Supabase."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Dict, Optional

from supabase import Client

CONNECTION_TABLE = "qb_connections"
CUSTOMERS_TABLE = "customers"
INVOICES_TABLE = "invoices"


def _parse_iso_datetime(value: Any) -> Any:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        iso = value.rstrip("Z") + ("+00:00" if value.endswith("Z") else "")
        try:
            return datetime.fromisoformat(iso)
        except ValueError:
            return value
    return value


def _format_iso_datetime(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _fetch_connection_sync(supabase: Client, user_id: str):
    return (
        supabase
        .table(CONNECTION_TABLE)
        .select("*")
        .eq("user_id", user_id)
        .maybe_single()
        .execute()
    )


async def fetch_connection(supabase: Client, user_id: str) -> Optional[Dict[str, Any]]:
    resp = await asyncio.to_thread(_fetch_connection_sync, supabase, user_id)
    data = resp.data if resp else None
    if not data:
        return None
    if "expires_at" in data:
        data["expires_at"] = _parse_iso_datetime(data["expires_at"])
    return data


def _upsert_connection_sync(supabase: Client, record: Dict[str, Any]):
    return (
        supabase
        .table(CONNECTION_TABLE)
        .upsert(record, on_conflict="user_id")
        .execute()
    )


async def upsert_connection(supabase: Client, record: Dict[str, Any]) -> None:
    to_store = {**record}
    if "expires_at" in to_store:
        to_store["expires_at"] = _format_iso_datetime(to_store["expires_at"])
    await asyncio.to_thread(_upsert_connection_sync, supabase, to_store)


def _select_customer_sync(supabase: Client, external_ref: str):
    return (
        supabase
        .table(CUSTOMERS_TABLE)
        .select("id")
        .eq("external_ref", external_ref)
        .maybe_single()
        .execute()
    )


def _insert_customer_sync(supabase: Client, payload: Dict[str, Any]):
    return (
        supabase
        .table(CUSTOMERS_TABLE)
        .insert(payload)
        .select("id")
        .single()
        .execute()
    )


async def get_or_create_customer_id(supabase: Client, external_ref: str, name: str) -> str:
    existing = await asyncio.to_thread(_select_customer_sync, supabase, external_ref)
    if existing.data:
        return existing.data["id"]
    created = await asyncio.to_thread(
        _insert_customer_sync,
        supabase,
        {"external_ref": external_ref, "name": name},
    )
    return created.data["id"]


def _insert_invoice_sync(supabase: Client, payload: Dict[str, Any]):
    return supabase.table(INVOICES_TABLE).insert(payload).execute()


async def insert_invoice(supabase: Client, payload: Dict[str, Any]) -> None:
    await asyncio.to_thread(_insert_invoice_sync, supabase, payload)


def _list_invoices_sync(supabase: Client, limit: int):
    return (
        supabase
        .table(INVOICES_TABLE)
        .select("*")
        .order("invoice_date", desc=True)
        .limit(limit)
        .execute()
    )


async def list_invoices(supabase: Client, limit: int = 1000) -> list[Dict[str, Any]]:
    resp = await asyncio.to_thread(_list_invoices_sync, supabase, limit)
    return resp.data or []


__all__ = [
    "fetch_connection",
    "upsert_connection",
    "get_or_create_customer_id",
    "insert_invoice",
    "list_invoices",
]
