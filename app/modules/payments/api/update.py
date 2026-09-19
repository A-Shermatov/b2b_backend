from typing import Annotated

from fastapi import Depends, Header

from app.modules.payments.api.common import (
    BuyerContext,
    FINANCE_ROLES,
    PaymentService,
    buyer_router,
    require_buyer_company_roles,
)


@buyer_router.post("/milestones/{milestone_id}/fund")
async def fund_milestone(
    milestone_id: int,
    ctx: Annotated[BuyerContext, Depends(require_buyer_company_roles(*FINANCE_ROLES))],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
):
    milestone = await PaymentService().fund_milestone(
        milestone_id, ctx.actor.id, idempotency_key
    )
    return {"id": milestone["id"], "status": milestone["status"]}


@buyer_router.post("/milestones/{milestone_id}/approve")
async def approve_milestone(
    milestone_id: int,
    ctx: Annotated[BuyerContext, Depends(require_buyer_company_roles(*FINANCE_ROLES))],
):
    milestone = await PaymentService().approve_milestone(milestone_id, ctx.actor.id)
    return {"id": milestone["id"], "status": milestone["status"]}


@buyer_router.post("/mock/confirm/{milestone_id}")
async def mock_confirm_payment(
    milestone_id: int,
    ctx: Annotated[BuyerContext, Depends(require_buyer_company_roles(*FINANCE_ROLES))],
):
    return await PaymentService().mock_confirm_payment(milestone_id, ctx.actor.id)
