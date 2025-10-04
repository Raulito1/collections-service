# app/routers/quickbooks.py
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse, JSONResponse

from ..config import settings
from ..aging import simplify_ar_aging
from ..qbo_store import fetch_connection, upsert_connection
from . import deps

AUTH_BASE = "https://appcenter.intuit.com/connect/oauth2"
TOKEN_URL = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"

router = APIRouter(tags=["quickbooks"])

# super simple ephemeral state/CSRF storage (replace with a real cache/session)
class StateStore:
    _mem: Dict[str, Dict[str, Optional[str]]] = {}
    @classmethod
    def issue(cls, user_id: str, return_url: Optional[str] = None) -> str:
        state = secrets.token_urlsafe(24)
        cls._mem[state] = {"user_id": user_id, "return_url": return_url}
        return state
    @classmethod
    def consume(cls, state: str) -> Optional[Dict[str, Optional[str]]]:
        data = cls._mem.pop(state, None)
        if not data:
            return None
        return data

def api_base() -> str:
    return ("https://quickbooks.api.intuit.com"
            if settings.QBO_ENV == "production"
            else "https://quickbooks.api.intuit.com")

# ---------------------- OAuth helpers ------------------------------
def auth_url(state: str) -> str:
    scope = "com.intuit.quickbooks.accounting"
    from urllib.parse import urlencode
    qs = urlencode({
        "client_id": settings.QBO_CLIENT_ID,
        "response_type": "code",
        "scope": scope,
        "redirect_uri": settings.QBO_REDIRECT_URL,
        "state": state
    })
    return f"{AUTH_BASE}?{qs}"

async def exchange_code_for_tokens(code: str) -> Dict[str, Any]:
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(
            TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": settings.QBO_REDIRECT_URL,
            },
            auth=(settings.QBO_CLIENT_ID, settings.QBO_CLIENT_SECRET),
            headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
        )
        r.raise_for_status()
        return r.json()

async def refresh_if_needed(user_id: str, supabase) -> Dict[str, Any]:
    rec = await fetch_connection(supabase, user_id)
    if not rec:
        raise RuntimeError("QuickBooks is not connected for this user")

    now = datetime.now(timezone.utc)
    expires_at = rec.get("expires_at")
    if expires_at and expires_at > now + timedelta(seconds=60):
        return rec

    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(
            TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": rec["refresh_token"],
            },
            auth=(settings.QBO_CLIENT_ID, settings.QBO_CLIENT_SECRET),
            headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
        )
        r.raise_for_status()
        tok = r.json()

    rec.update({
        "access_token": tok["access_token"],
        "refresh_token": tok.get("refresh_token", rec["refresh_token"]),
        "expires_at": now + timedelta(seconds=int(tok["expires_in"]))
    })
    await upsert_connection(supabase, rec)
    return rec

# ---------------------- QBO Query helper ---------------------------
async def qbo_query(user_id: str, supabase, sql: str, minorversion: str = "73") -> Dict[str, Any]:
    rec = await refresh_if_needed(user_id, supabase)
    url = f"{api_base()}/v3/company/{rec['realm_id']}/query"
    headers = {
        "Authorization": f"Bearer {rec['access_token']}",
        "Accept": "application/json",
        "Content-Type": "text/plain",
    }
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(url, params={"minorversion": minorversion}, headers=headers, content=sql)
        r.raise_for_status()
        return r.json()


async def qbo_report(user_id: str, supabase, report: str, params: Optional[Dict[str, Any]] = None, minorversion: str = "73") -> Dict[str, Any]:
    rec = await refresh_if_needed(user_id, supabase)
    url = f"{api_base()}/v3/company/{rec['realm_id']}/reports/{report}"
    headers = {
        "Authorization": f"Bearer {rec['access_token']}",
        "Accept": "application/json",
    }
    query_params: Dict[str, Any] = {"minorversion": minorversion}
    if params:
        query_params.update(params)

    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(url, params=query_params, headers=headers)
        r.raise_for_status()
        return r.json()


# ---------------------- FastAPI routes ------------------------------

@router.get("/auth/quickbooks/login")
async def qbo_login(
    user_id: str = Depends(deps.get_user_id),
    return_url: bool = Query(False, description="Set true to get the Intuit URL instead of redirecting."),
    return_to: Optional[str] = Query(None, description="Optional URL to redirect to after successful connection."),
):
    """
    Starts the Intuit OAuth flow. Generates a per-user state token and redirects to Intuit.
    """
    state = StateStore.issue(user_id, return_to)
    url = auth_url(state)
    if return_url:
        return JSONResponse({"redirect_url": url, "state": state})
    return RedirectResponse(url=url)


@router.get("/auth/quickbooks/callback")
async def qbo_callback(
    code: str,
    state: str,
    realmId: Optional[str] = None,
    supabase=Depends(deps.get_supabase),
):
    """
    Handles Intuit redirect. Exchanges the auth code for tokens and stores them.
    """
    state_data = StateStore.consume(state)
    if not state_data or not state_data.get("user_id"):
        raise HTTPException(status_code=400, detail="Invalid or expired state")
    user_id = state_data["user_id"]
    return_to = state_data.get("return_url")

    if not realmId:
        raise HTTPException(status_code=400, detail="Missing realmId (company id) from Intuit callback")

    tok = await exchange_code_for_tokens(code)
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=int(tok["expires_in"]))

    await upsert_connection(
        supabase,
        {
            "user_id": user_id,
            "realm_id": realmId,
            "access_token": tok["access_token"],
            "refresh_token": tok["refresh_token"],
            "expires_at": expires_at,
        },
    )

    if return_to:
        from urllib.parse import urlencode

        query = urlencode({
            "ok": "true",
            "message": "Connected to QuickBooks",
            "realmId": realmId,
        })
        redirect_url = f"{return_to}?{query}"
        return RedirectResponse(url=redirect_url, status_code=303)

    return JSONResponse({"ok": True, "message": "Connected to QuickBooks", "realmId": realmId})


@router.get("/qbo/company")
async def qbo_company_info(
    user_id: str = Depends(deps.get_user_id),
    supabase=Depends(deps.get_supabase),
):
    """
    Test endpoint: returns CompanyInfo from QBO to verify the connection.
    """
    try:
        payload = await qbo_query(user_id, supabase, "select * from CompanyInfo")
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=e.response.text)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return payload


@router.get("/qbo/invoices/latest")
async def qbo_latest_invoices(
    user_id: str = Depends(deps.get_user_id),
    supabase=Depends(deps.get_supabase),
):
    """
    Returns the latest invoices for the authenticated user/company.
    """
    sql = (
        "select Id, TotalAmt, Balance, TxnDate, DueDate, CustomerRef "
        "from Invoice order by MetaData.CreateTime desc maxresults 25"
    )
    try:
        payload = await qbo_query(user_id, supabase, sql)
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=e.response.text)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return JSONResponse(payload)


@router.get("/qbo/reports/ar-aging-detail")
async def qbo_ar_aging_detail(
    user_id: str = Depends(deps.get_user_id),
    supabase=Depends(deps.get_supabase),
    report_date: Optional[str] = Query(None, description="YYYY-MM-DD date for the report."),
    past_due: Optional[bool] = Query(None, description="True to show only past due balances."),
    aging_method: Optional[str] = Query(None, description="Aging method (e.g., 'CURRENT', 'REPORT_DATE')."),
    num_periods: Optional[int] = Query(None, ge=1, le=6, description="Number of aging periods (1-6)."),
    columns: Optional[str] = Query(
        None,
        description="Comma-separated list of columns to include. See QBO docs for allowed values.",
    ),
):
    """
    Fetches the QuickBooks A/R Aging Detail report for the connected company.
    """
    params: Dict[str, Any] = {}
    if report_date:
        params["report_date"] = report_date
    if past_due is not None:
        params["pastdue"] = "true" if past_due else "false"
    if aging_method:
        params["aging_method"] = aging_method
    if num_periods is not None:
        params["num_periods"] = str(num_periods)
    if columns:
        params["columns"] = columns

    try:
        payload = await qbo_report(user_id, supabase, "AgedReceivableDetail", params=params)
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=e.response.text)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return JSONResponse(payload)


@router.get("/qbo/reports/ar-aging-detail/simplified")
async def qbo_ar_aging_detail_simplified(
    user_id: str = Depends(deps.get_user_id),
    supabase=Depends(deps.get_supabase),
    report_date: Optional[str] = Query(None, description="YYYY-MM-DD date for the report."),
    past_due: Optional[bool] = Query(None, description="True to show only past due balances."),
    aging_method: Optional[str] = Query(None, description="Aging method (e.g., 'CURRENT', 'REPORT_DATE')."),
    num_periods: Optional[int] = Query(None, ge=1, le=6, description="Number of aging periods (1-6)."),
    columns: Optional[str] = Query(
        None,
        description="Comma-separated list of columns to include. See QBO docs for allowed values.",
    ),
):
    """
    Returns a policy-aware collections summary derived from the QBO aging detail report.
    """
    params: Dict[str, Any] = {}
    if report_date:
        params["report_date"] = report_date
    if past_due is not None:
        params["pastdue"] = "true" if past_due else "false"
    if aging_method:
        params["aging_method"] = aging_method
    if num_periods is not None:
        params["num_periods"] = str(num_periods)
    if columns:
        params["columns"] = columns

    try:
        raw = await qbo_report(user_id, supabase, "AgedReceivableDetail", params=params)
        summary = simplify_ar_aging(raw)
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=e.response.text)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return JSONResponse(summary)
