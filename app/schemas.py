from pydantic import BaseModel, root_validator
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


class CustomerStatusUpdate(BaseModel):
    customer_id: Optional[UUID] = None
    external_ref: Optional[str] = None
    action_taken: Optional[str] = None
    slack_updated: Optional[bool] = None
    follow_up: Optional[bool] = None
    escalation: Optional[bool] = None

    @root_validator
    def _validate_identifier(cls, values):
        if not values.get("customer_id") and not values.get("external_ref"):
            raise ValueError("customer_id or external_ref is required")
        return values
