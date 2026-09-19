from typing import Annotated

from fastapi import Depends

from app.modules.proposals.api.common import (
    BuyerContext,
    ProposalAcceptRequest,
    ProposalService,
    ProposalUpdate,
    SupplierContext,
    buyer_router,
    require_buyer_ctx,
    require_supplier_ctx,
    supplier_router,
)


@buyer_router.post("/proposals/{proposal_id}/shortlist")
async def shortlist_proposal(
    proposal_id: int,
    ctx: Annotated[BuyerContext, Depends(require_buyer_ctx)],
):
    return await ProposalService().shortlist(proposal_id, ctx.actor.id)


@buyer_router.post("/proposals/{proposal_id}/reject")
async def reject_proposal(
    proposal_id: int,
    ctx: Annotated[BuyerContext, Depends(require_buyer_ctx)],
):
    return await ProposalService().reject(proposal_id, ctx.actor.id)


@buyer_router.post("/proposals/{proposal_id}/accept")
async def accept_proposal(
    proposal_id: int,
    ctx: Annotated[BuyerContext, Depends(require_buyer_ctx)],
    data: ProposalAcceptRequest | None = None,
):
    payment_type = data.payment_type if data else None
    milestones = (
        [m.model_dump() for m in data.milestones] if data and data.milestones else None
    )
    return await ProposalService().accept(
        proposal_id,
        ctx.actor.id,
        payment_type=payment_type,
        milestones=milestones,
        buyer_user_id=ctx.user.id,
    )


@supplier_router.patch("/{proposal_id}")
async def update_proposal(
    proposal_id: int,
    data: ProposalUpdate,
    ctx: Annotated[SupplierContext, Depends(require_supplier_ctx)],
):
    return await ProposalService().update_proposal(proposal_id, ctx.actor.id, data)


@supplier_router.post("/{proposal_id}/withdraw")
async def withdraw_proposal(
    proposal_id: int,
    ctx: Annotated[SupplierContext, Depends(require_supplier_ctx)],
):
    return await ProposalService().withdraw(proposal_id, ctx.actor.id)
