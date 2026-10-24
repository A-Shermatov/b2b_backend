from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class PurchaseApprovalSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    company_id: int | None
    requester_user_id: int
    who_name: str
    who_role: str
    limit_label: str
    title: str
    supplier_name: str
    amount: float
    currency: str
    reason: str
    status: Literal["wait", "auto", "approved", "rejected"]
    rfq_id: str | None = None
    rfq_ids: list[str] = []
    cart_items: list[dict[str, Any]] = []
    decided_by: int | None = None
    decided_at: datetime | None = None
    created_at: datetime


class PurchaseApprovalDecide(BaseModel):
    action: Literal["approved", "rejected"]


class CartCheckoutItem(BaseModel):
    item_id: int = Field(gt=0)
    qty: int = Field(default=1, ge=1, le=9999)


class CartCheckoutRequest(BaseModel):
    items: list[CartCheckoutItem] = Field(min_length=1, max_length=50)
    idempotency_key: str | None = Field(default=None, max_length=64)


class CartCheckoutResponse(BaseModel):
    approval: PurchaseApprovalSchema
    rfq_ids: list[str] = []
    offers_created: bool
