from typing import Annotated

from fastapi import Depends, File, Form, UploadFile

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
from app.modules.contracts.schemas import DisputeStatementRequest
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
    # Check participation before anything is written to storage.
    await ContractService().ensure_participant(contract_id, ctx.actor.id)
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
    # Check participation before anything is written to storage.
    await ContractService().ensure_participant(contract_id, ctx.actor.id)
    key, _ = await storage_service.upload(file, f"contracts/{contract_id}")
    return await ContractService().add_file(
        contract_id,
        ctx.user.id,
        ctx.actor.id,
        file.filename or "file",
        key,
        file.content_type or "application/octet-stream",
    )


async def _upload_dispute_evidence(
    contract_id: int,
    actor_id: int,
    file: UploadFile,
    note: str | None,
):
    service = ContractService()
    dispute_id = await service.ensure_active_dispute(contract_id, actor_id)
    key, _ = await storage_service.upload(file, f"disputes/{dispute_id}")
    return await service.add_dispute_evidence(
        contract_id,
        actor_id,
        file.filename or "file",
        key,
        file.content_type or "application/octet-stream",
        (note or "").strip() or None,
    )


@buyer_router.post("/{contract_id}/dispute/evidence")
async def buyer_upload_dispute_evidence(
    contract_id: int,
    ctx: Annotated[BuyerContext, Depends(buyer_perm("contracts.update"))],
    file: UploadFile = File(...),
    note: str | None = Form(None),
):
    return await _upload_dispute_evidence(contract_id, ctx.actor.id, file, note)


@supplier_router.post("/{contract_id}/dispute/evidence")
async def supplier_upload_dispute_evidence(
    contract_id: int,
    ctx: Annotated[SupplierContext, Depends(supplier_perm("contracts.update"))],
    file: UploadFile = File(...),
    note: str | None = Form(None),
):
    return await _upload_dispute_evidence(contract_id, ctx.actor.id, file, note)


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


@buyer_router.post("/{contract_id}/dispute/statement")
async def buyer_dispute_statement(
    contract_id: int,
    data: DisputeStatementRequest,
    ctx: Annotated[BuyerContext, Depends(buyer_perm("contracts.update"))],
):
    return await ContractService().add_dispute_statement(contract_id, ctx.actor.id, data)


@supplier_router.post("/{contract_id}/dispute/statement")
async def supplier_dispute_statement(
    contract_id: int,
    data: DisputeStatementRequest,
    ctx: Annotated[SupplierContext, Depends(supplier_perm("contracts.update"))],
):
    return await ContractService().add_dispute_statement(contract_id, ctx.actor.id, data)
