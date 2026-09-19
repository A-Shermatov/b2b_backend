from typing import Annotated

from fastapi import Depends

from app.modules.proposals.api.common import (
    BuyerContext,
    ProposalService,
    SupplierContext,
    buyer_router,
    require_buyer_ctx,
    require_supplier_ctx,
    supplier_router,
)


@buyer_router.get("/rfqs/{rfq_id}/proposals")
async def list_proposals(
    rfq_id: str,
    ctx: Annotated[BuyerContext, Depends(require_buyer_ctx)],
):
    return await ProposalService().list_for_rfq(rfq_id, ctx.actor.id)


@supplier_router.get("/")
async def list_my_proposals(
    ctx: Annotated[SupplierContext, Depends(require_supplier_ctx)],
):
    return await ProposalService().list_for_supplier(ctx.actor.id)


@buyer_router.get("/proposals/{proposal_id}/messages")
async def list_buyer_proposal_messages(
    proposal_id: int,
    ctx: Annotated[BuyerContext, Depends(require_buyer_ctx)],
):
    return await ProposalService().list_messages(proposal_id, ctx.actor.id, as_buyer=True)


@supplier_router.get("/{proposal_id}/messages")
async def list_supplier_proposal_messages(
    proposal_id: int,
    ctx: Annotated[SupplierContext, Depends(require_supplier_ctx)],
):
    return await ProposalService().list_messages(proposal_id, ctx.actor.id, as_buyer=False)
