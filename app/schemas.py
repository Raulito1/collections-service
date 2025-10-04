from pydantic import BaseModel
from datetime import date
from typing import Optional
from uuid import UUID

class InvoiceCreate(BaseModel):
    customer_id: UUID
    invoice_date: date
    due_date: Optional[date] = None
    amount: float
    open_balance: float
    status: Optional[str] = "OPEN"

class InvoiceRead(InvoiceCreate):
    id: UUID
    class Config:
        from_attributes = True