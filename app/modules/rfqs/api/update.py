from typing import Annotated

from fastapi import Depends

from app.deps import buyer_perm
from app.modules.rfqs.api.common import (
    BuyerContext,
    InviteSuppliersRequest,
    RfqService,
    RfqUpdate,
    buyer_router,
)


@buyer_router.patch("/{rfq_id}")
async def update_rfq(
    rfq_id: str,
    data: RfqUpdate,
    ctx: Annotated[
        BuyerContext,
        Depends(buyer_perm("rfqs.update")),
    ],
):
    return await RfqService().update(rfq_id, ctx.actor.id, data)


@buyer_router.post("/{rfq_id}/publish")
async def publish_rfq(
    rfq_id: str,
    ctx: Annotated[BuyerContext, Depends(buyer_perm("rfqs.publish"))],
):
    return await RfqService().publish(rfq_id, ctx.actor.id)


@buyer_router.post("/{rfq_id}/close")
async def close_rfq(
    rfq_id: str,
    ctx: Annotated[BuyerContext, Depends(buyer_perm("rfqs.close"))],
):
    return await RfqService().close(rfq_id, ctx.actor.id)


@buyer_router.delete("/{rfq_id}/attachments/{attachment_id}")
async def delete_attachment(
    rfq_id: str,
    attachment_id: str,
    ctx: Annotated[BuyerContext, Depends(buyer_perm("rfqs.update"))],
):
    return await RfqService().remove_attachment(rfq_id, ctx.actor.id, attachment_id)


@buyer_router.post("/{rfq_id}/invite")
async def invite_suppliers(
    rfq_id: str,
    data: InviteSuppliersRequest,
    ctx: Annotated[BuyerContext, Depends(buyer_perm("rfqs.invite"))],
):
    return await RfqService().invite_suppliers(rfq_id, ctx.actor.id, data.supplier_ids)
