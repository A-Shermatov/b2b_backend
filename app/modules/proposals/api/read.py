from typing import Annotated

from fastapi import Depends, Query

from app.deps import PaginationDep
from app.enums import ProposalStatus
from app.modules.proposals.api.common import (
    BuyerContext,
    ProposalService,
    SupplierContext,
    buyer_router,
    require_buyer_ctx,
    require_supplier_ctx,
    supplier_router,
)
from app.modules.proposals.schemas import ProposalMessageThread


@buyer_router.get("/rfqs/{rfq_id}/proposals")
async def list_proposals(
    rfq_id: str,
    ctx: Annotated[BuyerContext, Depends(require_buyer_ctx)],
    pagination: PaginationDep,
):
    return await ProposalService().list_for_rfq(
        rfq_id, ctx.actor.id, pagination.page, pagination.page_size
    )


@supplier_router.get("/")
async def list_my_proposals(
    ctx: Annotated[SupplierContext, Depends(require_supplier_ctx)],
    pagination: PaginationDep,
    status: ProposalStatus | None = Query(None),
):
    return await ProposalService().list_for_supplier(
        ctx.actor.id,
        pagination.page,
        pagination.page_size,
        status.value if status else None,
    )


@buyer_router.get(
    "/proposals/message-threads",
    response_model=list[ProposalMessageThread],
)
async def list_buyer_message_threads(
    ctx: Annotated[BuyerContext, Depends(require_buyer_ctx)],
):
    return await ProposalService().list_message_threads(ctx.actor.id, as_buyer=True)


@supplier_router.get(
    "/message-threads",
    response_model=list[ProposalMessageThread],
)
async def list_supplier_message_threads(
    ctx: Annotated[SupplierContext, Depends(require_supplier_ctx)],
):
    return await ProposalService().list_message_threads(ctx.actor.id, as_buyer=False)


@buyer_router.get(
    "/proposals/{proposal_id}/thread",
    response_model=ProposalMessageThread,
)
async def get_buyer_message_thread(
    proposal_id: int,
    ctx: Annotated[BuyerContext, Depends(require_buyer_ctx)],
):
    return await ProposalService().get_message_thread(
        proposal_id, ctx.actor.id, as_buyer=True
    )


@supplier_router.get(
    "/{proposal_id}/thread",
    response_model=ProposalMessageThread,
)
async def get_supplier_message_thread(
    proposal_id: int,
    ctx: Annotated[SupplierContext, Depends(require_supplier_ctx)],
):
    return await ProposalService().get_message_thread(
        proposal_id, ctx.actor.id, as_buyer=False
    )


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
