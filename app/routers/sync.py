from datetime import date

import httpx
from fastapi import APIRouter, Depends, HTTPException

from ..auth import get_current_user
from ..qbo_store import get_or_create_customer_id, insert_invoice
from ..routers import deps
from ..routers.quickbooks import api_base, refresh_if_needed

router = APIRouter(prefix="/api/v1/quickbooks", tags=["quickbooks"])


@router.post("/sync")
async def sync_quickbooks(
    supabase=Depends(deps.get_supabase),
    user=Depends(get_current_user),
):
    qb = await refresh_if_needed(user["sub"], supabase)
    headers = {
        "Authorization": f"Bearer {qb['access_token']}",
        "Accept": "application/json",
        "Content-Type": "application/text",
    }
    query_url = f"{api_base()}/v3/company/{qb['realm_id']}/query"
    sql = "select Id, TotalAmt, Balance, TxnDate, DueDate, CustomerRef from Invoice order by MetaData.CreateTime desc maxresults 200"

    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            query_url,
            params={"minorversion": "73"},
            headers=headers,
            content=sql,
        )
        r.raise_for_status()
        data = r.json()

    items = (data.get("QueryResponse", {}) or {}).get("Invoice", []) or []

    def to_date(v: str | None) -> str | None:
        return date.fromisoformat(v).isoformat() if v else None

    for inv in items:
        cust_ref = inv.get("CustomerRef", {}) or {}
        cust_id = cust_ref.get("value")
        if not cust_id:
            raise HTTPException(status_code=400, detail="Invoice missing customer reference")
        cust_name = cust_ref.get("name") or "Unknown"
        customer_id = await get_or_create_customer_id(supabase, cust_id, cust_name)

        row = {
            "customer_id": customer_id,
            "invoice_date": to_date(inv.get("TxnDate")),
            "due_date": to_date(inv.get("DueDate")),
            "amount": float(inv.get("TotalAmt") or 0),
            "open_balance": float(inv.get("Balance") or 0),
            "status": "OPEN" if float(inv.get("Balance") or 0) > 0 else "PAID",
        }
        await insert_invoice(supabase, row)

    return {"ok": True, "imported": len(items)}
