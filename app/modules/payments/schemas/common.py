from datetime import datetime

from pydantic import BaseModel


class PaymentHistoryItem(BaseModel):
    contract_id: int
    milestone_id: int
    title: str
    amount: float
    currency: str
    status: str
    event: str
    created_at: datetime


class PendingPaymentItem(BaseModel):
    contract_id: int
    milestone_id: int
    title: str
    amount: float
    currency: str
    status: str


class SupplierBalanceSchema(BaseModel):
    available: float
    pending: float
    escrow_locked: float
    currency: str
