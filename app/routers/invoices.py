from fastapi import APIRouter, Depends

from ..schemas import InvoiceRead
from ..auth import get_current_user
from ..qbo_store import list_invoices
from . import deps

router = APIRouter(prefix="/api/v1/invoices", tags=["invoices"])

@router.get("/", response_model=list[InvoiceRead])
async def list_invoices_route(
    supabase=Depends(deps.get_supabase),
    user=Depends(get_current_user),
):
    records = await list_invoices(supabase)
    return [InvoiceRead.model_validate(rec) for rec in records]
