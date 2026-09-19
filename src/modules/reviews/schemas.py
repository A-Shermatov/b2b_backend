from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.modules.reviews.schemas import ReviewCreate, ReviewResponse

__all__ = [
    "PaymentHistoryItem",
    "PendingPaymentItem",
    "ReviewCreate",
    "ReviewResponse",
    "NotificationSchema",
]


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


class NotificationSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    type: str
    title: str
    body: str
    href: str | None
    read: bool
    created_at: datetime
