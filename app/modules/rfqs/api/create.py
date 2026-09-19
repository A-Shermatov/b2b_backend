from typing import Annotated

from fastapi import Depends, File, UploadFile

from app.modules.proposals.schemas import ProposalCreate
from app.modules.proposals.services import ProposalService
from app.modules.rfqs.api.common import (
    BuyerContext,
    ProductRfqCreate,
    RFQ_ROLES,
    RfqService,
    ServiceRfqCreate,
    SupplierContext,
    buyer_router,
    require_buyer_company_roles,
    require_buyer_ctx,
    require_supplier_ctx,
    supplier_router,
)
from app.utils.storage import storage_service


@buyer_router.post("/")
async def create_rfq(
    data: ProductRfqCreate | ServiceRfqCreate,
    ctx: Annotated[
        BuyerContext,
        Depends(require_buyer_company_roles(*RFQ_ROLES)),
    ],
):
    created_by = f"{ctx.user.first_name} {ctx.user.last_name}".strip()
    return await RfqService().create(ctx.actor.id, created_by, data)


@buyer_router.post("/{rfq_id}/attachments")
async def upload_attachment(
    rfq_id: str,
    ctx: Annotated[BuyerContext, Depends(require_buyer_ctx)],
    file: UploadFile = File(...),
):
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
    ctx: Annotated[SupplierContext, Depends(require_supplier_ctx)],
):
    return await ProposalService().submit(rfq_id, ctx.actor.id, data)
