from typing import Annotated

from fastapi import Depends

from app.modules.proposals.api.common import (
    BuyerContext,
    ProposalMessageCreate,
    ProposalService,
    SupplierContext,
    buyer_router,
    require_buyer_ctx,
    require_supplier_ctx,
    supplier_router,
)


@buyer_router.post("/proposals/{proposal_id}/messages")
async def send_buyer_proposal_message(
    proposal_id: int,
    data: ProposalMessageCreate,
    ctx: Annotated[BuyerContext, Depends(require_buyer_ctx)],
):
    return await ProposalService().add_message(
        proposal_id, ctx.actor.id, ctx.user.id, data, as_buyer=True
    )


@supplier_router.post("/{proposal_id}/messages")
async def send_supplier_proposal_message(
    proposal_id: int,
    data: ProposalMessageCreate,
    ctx: Annotated[SupplierContext, Depends(require_supplier_ctx)],
):
    return await ProposalService().add_message(
        proposal_id, ctx.actor.id, ctx.user.id, data, as_buyer=False
    )
