from typing import Annotated

from fastapi import Depends

from app.deps import buyer_perm, supplier_perm
from app.modules.proposals.api.common import (
    BuyerContext,
    ProposalAcceptRequest,
    ProposalService,
    ProposalUpdate,
    SupplierContext,
    buyer_router,
    supplier_router,
)


@buyer_router.post("/proposals/{proposal_id}/shortlist")
async def shortlist_proposal(
    proposal_id: int,
    ctx: Annotated[BuyerContext, Depends(buyer_perm("proposals.accept"))],
):
    return await ProposalService().shortlist(proposal_id, ctx.actor.id)


@buyer_router.post("/proposals/{proposal_id}/reject")
async def reject_proposal(
    proposal_id: int,
    ctx: Annotated[BuyerContext, Depends(buyer_perm("proposals.accept"))],
):
    return await ProposalService().reject(proposal_id, ctx.actor.id)


@buyer_router.post("/proposals/{proposal_id}/accept")
async def accept_proposal(
    proposal_id: int,
    # Accepting an offer commits the company to a deal: director/admin only.
    ctx: Annotated[BuyerContext, Depends(buyer_perm("proposals.accept", full_role=True))],
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
        payment_mode=data.payment_mode if data else None,
        expected_price=data.expected_price if data else None,
    )


@buyer_router.post("/proposals/{proposal_id}/withdraw")
async def buyer_withdraw_offer(
    proposal_id: int,
    ctx: Annotated[BuyerContext, Depends(buyer_perm("proposals.accept"))],
):
    return await ProposalService().buyer_withdraw_offer(proposal_id, ctx.actor.id)


@supplier_router.patch("/{proposal_id}")
async def update_proposal(
    proposal_id: int,
    data: ProposalUpdate,
    ctx: Annotated[SupplierContext, Depends(supplier_perm("proposals.update"))],
):
    return await ProposalService().update_proposal(proposal_id, ctx.actor.id, data)


@supplier_router.post("/{proposal_id}/withdraw")
async def withdraw_proposal(
    proposal_id: int,
    ctx: Annotated[SupplierContext, Depends(supplier_perm("proposals.update"))],
):
    return await ProposalService().withdraw(proposal_id, ctx.actor.id)


@supplier_router.post("/{proposal_id}/accept")
async def supplier_accept_offer(
    proposal_id: int,
    ctx: Annotated[SupplierContext, Depends(supplier_perm("proposals.update"))],
):
    return await ProposalService().supplier_accept(proposal_id, ctx.actor.id)


@supplier_router.post("/{proposal_id}/reject")
async def supplier_reject_offer(
    proposal_id: int,
    ctx: Annotated[SupplierContext, Depends(supplier_perm("proposals.update"))],
):
    return await ProposalService().supplier_reject(proposal_id, ctx.actor.id)
