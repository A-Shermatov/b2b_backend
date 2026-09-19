from datetime import datetime

from pydantic import BaseModel, Field


class WithdrawalDestinationSchema(BaseModel):
    id: int
    actor_id: int
    type: str
    label: str
    details: str
    is_default: bool


class WithdrawalSchema(BaseModel):
    id: int
    actor_id: int
    destination_id: int
    amount: float
    currency: str
    status: str
    created_at: datetime
    completed_at: datetime | None


class InvoiceSchema(BaseModel):
    id: int
    actor_id: int
    contract_id: int | None
    number: str
    title: str
    amount: float
    currency: str
    status: str
    issued_at: datetime
    due_at: datetime | None
    paid_at: datetime | None


class WithdrawalDestinationCreate(BaseModel):
    type: str
    label: str
    details: str
    is_default: bool = False


class WithdrawalCreate(BaseModel):
    destination_id: int
    amount: float = Field(gt=0)
