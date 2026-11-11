from typing import Annotated

from fastapi import Depends, File, UploadFile

from app.modules.proposals.schemas import ProposalCreate
from app.modules.proposals.services import ProposalService
from app.deps import buyer_perm, supplier_perm
from app.modules.rfqs.api.common import (
    BuyerContext,
    ProductRfqCreate,
    RfqService,
    ServiceRfqCreate,
    SupplierContext,
    buyer_router,
    supplier_router,
)
from app.utils.storage import storage_service


@buyer_router.post("/")
async def create_rfq(
    data: ProductRfqCreate | ServiceRfqCreate,
    ctx: Annotated[
        BuyerContext,
        Depends(buyer_perm("rfqs.create")),
    ],
):
    created_by = f"{ctx.user.first_name} {ctx.user.last_name}".strip()
    return await RfqService().create(ctx.actor.id, created_by, data, ctx.user.id)


@buyer_router.post("/{rfq_id}/attachments")
async def upload_attachment(
    rfq_id: str,
    ctx: Annotated[BuyerContext, Depends(buyer_perm("rfqs.update"))],
    file: UploadFile = File(...),
):
    # Only the owner of the request may store files under it.
    await RfqService().get(rfq_id, actor_id=ctx.actor.id)
    key, _ = await storage_service.upload(file, f"rfqs/{rfq_id}")
    return await RfqService().add_attachment(
        rfq_id,
        ctx.actor.id,
        file.filename or "file",
        key,
        file.content_type or "application/octet-stream",
    )


@supplier_router.post("/{rfq_id}/proposals")
async def submit_proposal(
    rfq_id: str,
    data: ProposalCreate,
    ctx: Annotated[SupplierContext, Depends(supplier_perm("proposals.create"))],
):
    return await ProposalService().submit(rfq_id, ctx.actor.id, data, ctx.user.id)
