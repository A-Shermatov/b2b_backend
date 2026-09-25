from typing import Annotated

from fastapi import Depends, File, UploadFile

from app.deps import buyer_perm, supplier_perm
from app.modules.contracts.api.common import (
    BuyerContext,
    ContractService,
    DisputeRequest,
    MessageCreate,
    SupplierContext,
    WorkSubmissionCreate,
    buyer_router,
    supplier_router,
)
from app.utils.storage import storage_service


@buyer_router.post("/{contract_id}/messages")
async def buyer_post_message(
    contract_id: int,
    data: MessageCreate,
    ctx: Annotated[BuyerContext, Depends(buyer_perm("contracts.update"))],
):
    return await ContractService().add_message(
        contract_id, ctx.user.id, ctx.actor.id, data
    )


@supplier_router.post("/{contract_id}/messages")
async def supplier_post_message(
    contract_id: int,
    data: MessageCreate,
    ctx: Annotated[SupplierContext, Depends(supplier_perm("contracts.update"))],
):
    return await ContractService().add_message(
        contract_id, ctx.user.id, ctx.actor.id, data
    )


@buyer_router.post("/{contract_id}/files")
async def buyer_upload_file(
    contract_id: int,
    ctx: Annotated[BuyerContext, Depends(buyer_perm("contracts.update"))],
    file: UploadFile = File(...),
):
    key, _ = await storage_service.upload(file, f"contracts/{contract_id}")
    return await ContractService().add_file(
        contract_id,
        ctx.user.id,
        ctx.actor.id,
        file.filename or "file",
        key,
        file.content_type or "application/octet-stream",
    )


@supplier_router.post("/{contract_id}/files")
async def supplier_upload_file(
    contract_id: int,
    ctx: Annotated[SupplierContext, Depends(supplier_perm("contracts.update"))],
    file: UploadFile = File(...),
):
    key, _ = await storage_service.upload(file, f"contracts/{contract_id}")
    return await ContractService().add_file(
        contract_id,
        ctx.user.id,
        ctx.actor.id,
        file.filename or "file",
        key,
        file.content_type or "application/octet-stream",
    )


@supplier_router.post("/{contract_id}/submissions")
async def submit_work(
    contract_id: int,
    data: WorkSubmissionCreate,
    ctx: Annotated[SupplierContext, Depends(supplier_perm("contracts.update"))],
):
    return await ContractService().submit_work(contract_id, ctx.actor.id, data)


@buyer_router.post("/{contract_id}/dispute")
async def buyer_open_dispute(
    contract_id: int,
    data: DisputeRequest,
    ctx: Annotated[BuyerContext, Depends(buyer_perm("contracts.update"))],
):
    return await ContractService().open_dispute(contract_id, ctx.actor.id, data)


@supplier_router.post("/{contract_id}/dispute")
async def supplier_open_dispute(
    contract_id: int,
    data: DisputeRequest,
    ctx: Annotated[SupplierContext, Depends(supplier_perm("contracts.update"))],
):
    return await ContractService().open_dispute(contract_id, ctx.actor.id, data)
