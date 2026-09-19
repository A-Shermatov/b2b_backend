from datetime import datetime

from pydantic import BaseModel


class SubscriptionResponse(BaseModel):
    plan: str
    active_until: datetime | None
    is_active: bool


class ActivateSubscriptionRequest(BaseModel):
    plan: str
