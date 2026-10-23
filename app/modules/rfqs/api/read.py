from typing import Annotated

from fastapi import Depends, Query

from app.deps import PaginationDep
from app.modules.rfqs.api.common import (
    BuyerContext,
    RfqService,
    SupplierContext,
    buyer_router,
    public_router,
    require_buyer_ctx,
    require_supplier_ctx,
    supplier_router,
)


@buyer_router.get("/")
async def list_rfqs(
    ctx: Annotated[BuyerContext, Depends(require_buyer_ctx)],
    pagination: PaginationDep,
    tab: str | None = Query(None),
    q: str | None = Query(None),
    type: str | None = Query(None),
):
    return await RfqService().list_for_buyer(
        ctx.actor.id,
        tab,
        pagination.page,
        pagination.page_size,
        q=q,
        rfq_type=type,
    )


@buyer_router.get("/{rfq_id}")
async def get_buyer_rfq(
    rfq_id: str,
    ctx: Annotated[BuyerContext, Depends(require_buyer_ctx)],
):
    return await RfqService().get(rfq_id, actor_id=ctx.actor.id)


@supplier_router.get("/board")
async def rfq_board(
    ctx: Annotated[SupplierContext, Depends(require_supplier_ctx)],
    pagination: PaginationDep,
    q: str | None = Query(None),
    source: str | None = Query(None),
):
    return await RfqService().list_board_for_supplier(
        ctx.actor.id,
        pagination.page,
        pagination.page_size,
        q=q,
        source=source,
    )


@supplier_router.get("/{rfq_id}")
async def get_supplier_rfq(
    rfq_id: str,
    ctx: Annotated[SupplierContext, Depends(require_supplier_ctx)],
):
    return await RfqService().get(rfq_id, supplier_id=ctx.actor.id)


@public_router.get("/rfqs")
async def public_rfqs(pagination: PaginationDep):
    return await RfqService().list_public_board(pagination.page, pagination.page_size)
