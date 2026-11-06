"""Admin: subscription plans and reliability marks set by hand."""

from datetime import datetime
from typing import Annotated, Literal

from fastapi import Depends
from pydantic import BaseModel, Field

from app.deps import CurrentUser, require_admin
from app.modules.admin.api.common import AdminService, router


class AdminSubscriptionState(BaseModel):
    plan: str
    active_until: datetime | None = None
    is_active: bool


class AdminSupplierMark(BaseModel):
    actor_id: int
    display_name: str
    tier_auto: str
    tier_override: str | None = None
    tier: str
    override_reason: str | None = None
    override_at: datetime | None = None


class AdminMarks(BaseModel):
    user_id: int
    subscription: AdminSubscriptionState
    suppliers: list[AdminSupplierMark]


class AdminSubscriptionGrant(BaseModel):
    plan: Literal["start", "pro", "business", "none"]
    months: int = Field(default=1, ge=1, le=24)
    reason: str | None = Field(default=None, max_length=2000)


class AdminReputationSet(BaseModel):
    tier: Literal["auto", "none", "silver", "gold", "diamond"]
    reason: str | None = Field(default=None, max_length=2000)


@router.get("/users/{user_id}/marks", response_model=AdminMarks)
async def admin_user_marks(user_id: int, current_user: Annotated[CurrentUser, Depends(require_admin)]):
    return await AdminService().get_user_marks(user_id, current_user)


@router.get("/companies/{company_id}/marks", response_model=AdminMarks)
async def admin_company_marks(company_id: int, current_user: Annotated[CurrentUser, Depends(require_admin)]):
    return await AdminService().get_company_marks(company_id, current_user)


@router.put("/users/{user_id}/subscription", response_model=AdminSubscriptionState)
async def admin_set_subscription(
    user_id: int,
    data: AdminSubscriptionGrant,
    current_user: Annotated[CurrentUser, Depends(require_admin)],
):
    return await AdminService().set_user_subscription(
        user_id, data.plan, current_user, months=data.months, reason=data.reason
    )


@router.put("/suppliers/{actor_id}/reputation", response_model=AdminSupplierMark)
async def admin_set_reputation(
    actor_id: int,
    data: AdminReputationSet,
    current_user: Annotated[CurrentUser, Depends(require_admin)],
):
    return await AdminService().set_supplier_reputation(
        actor_id, data.tier, current_user, reason=data.reason
    )
