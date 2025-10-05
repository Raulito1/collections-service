from pydantic import BaseModel, model_validator
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

    @model_validator(mode="after")
    def _validate_identifier(cls, model: "CustomerStatusUpdate"):
        if not model.customer_id and not model.external_ref:
            raise ValueError("customer_id or external_ref is required")
        return model
