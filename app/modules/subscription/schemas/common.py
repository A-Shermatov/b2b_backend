from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class SubscriptionResponse(BaseModel):
    plan: str
    active_until: datetime | None
    is_active: bool


class ActivateSubscriptionRequest(BaseModel):
    plan: str
    method: Literal["alif", "dc", "eskhata"] = "alif"


class SubscriptionCheckoutResponse(BaseModel):
    plan: str
    active_until: datetime | None = None
    is_active: bool = False
    payment_status: str
    order_id: str | None = None
    payment_url: str | None = None
    gateway: str = "mock"
    message: str | None = None


class SubscriptionPaymentConfig(BaseModel):
    provider: Literal["alif", "mock"]
    currency: str
    methods: list[dict] = Field(default_factory=list)


class SubscriptionOrderSyncResponse(BaseModel):
    order_id: str
    payment_status: str
    plan: str | None = None
    is_active: bool = False
    active_until: datetime | None = None
