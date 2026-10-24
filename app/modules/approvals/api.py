from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query

from app.deps import (
    BuyerContext,
    PaginationDep,
    require_buyer_ctx,
    require_buyer_company_roles,
)
from app.modules.approvals.schemas import (
    CartCheckoutRequest,
    CartCheckoutResponse,
    PurchaseApprovalDecide,
    PurchaseApprovalSchema,
)
from app.modules.approvals.service import ApprovalService

buyer_router = APIRouter(prefix="/approvals", tags=["buyer-approvals"])


@buyer_router.get("", response_model=dict)
async def list_approvals(
    ctx: Annotated[BuyerContext, Depends(require_buyer_ctx)],
    pagination: PaginationDep,
    status: Literal["wait", "hist", "all"] | None = Query("all"),
):
    company_id = ctx.company.id if ctx.company else None
    filter_status = None if status == "all" else status
    return await ApprovalService().list_for_buyer(
        company_id=company_id,
        user_id=ctx.user.id,
        status=filter_status,
        page=pagination.page,
        page_size=pagination.page_size,
    )


@buyer_router.post("/checkout", response_model=CartCheckoutResponse)
async def checkout_cart(
    data: CartCheckoutRequest,
    ctx: Annotated[BuyerContext, Depends(require_buyer_ctx)],
):
    company_id = ctx.company.id if ctx.company else None
    return await ApprovalService().checkout_cart(
        company_id=company_id,
        user_id=ctx.user.id,
        buyer_actor_id=ctx.actor.id,
        actor_kind=ctx.actor.kind,
        company_role=ctx.company_role,
        items=data.items,
        idempotency_key=data.idempotency_key,
    )


@buyer_router.post("/{approval_id}/decide", response_model=PurchaseApprovalSchema)
async def decide_approval(
    approval_id: int,
    data: PurchaseApprovalDecide,
    ctx: Annotated[
        BuyerContext, Depends(require_buyer_company_roles("director", "admin"))
    ],
):
    company_id = ctx.company.id if ctx.company else None
    return await ApprovalService().decide(
        approval_id=approval_id,
        company_id=company_id,
        user_id=ctx.user.id,
        buyer_actor_id=ctx.actor.id,
        company_role=ctx.company_role,
        action=data.action,
    )
