from typing import Annotated

from fastapi import Depends

from app.modules.contracts.api.common import (
    BuyerContext,
    ContractService,
    SupplierContext,
    buyer_router,
    require_buyer_ctx,
    require_supplier_ctx,
    supplier_router,
)


@buyer_router.post("/{contract_id}/messages/read")
async def buyer_mark_messages_read(
    contract_id: int,
    ctx: Annotated[BuyerContext, Depends(require_buyer_ctx)],
):
    return await ContractService().mark_messages_read(
        contract_id, ctx.user.id, ctx.actor.id
    )


@supplier_router.post("/{contract_id}/messages/read")
async def supplier_mark_messages_read(
    contract_id: int,
    ctx: Annotated[SupplierContext, Depends(require_supplier_ctx)],
):
    return await ContractService().mark_messages_read(
        contract_id, ctx.user.id, ctx.actor.id
    )


@buyer_router.post("/{contract_id}/messages/{message_id}/delivered")
async def buyer_mark_message_delivered(
    contract_id: int,
    message_id: int,
    ctx: Annotated[BuyerContext, Depends(require_buyer_ctx)],
):
    return await ContractService().mark_message_delivered(
        contract_id, message_id, ctx.user.id, ctx.actor.id
    )


@supplier_router.post("/{contract_id}/messages/{message_id}/delivered")
async def supplier_mark_message_delivered(
    contract_id: int,
    message_id: int,
    ctx: Annotated[SupplierContext, Depends(require_supplier_ctx)],
):
    return await ContractService().mark_message_delivered(
        contract_id, message_id, ctx.user.id, ctx.actor.id
    )


@buyer_router.post("/{contract_id}/submissions/{submission_id}/approve")
async def approve_submission(
    contract_id: int,
    submission_id: int,
    ctx: Annotated[BuyerContext, Depends(require_buyer_ctx)],
):
    return await ContractService().approve_submission(
        contract_id, submission_id, ctx.actor.id
    )


@buyer_router.post("/{contract_id}/submissions/{submission_id}/reject")
async def reject_submission(
    contract_id: int,
    submission_id: int,
    ctx: Annotated[BuyerContext, Depends(require_buyer_ctx)],
):
    return await ContractService().reject_submission(
        contract_id, submission_id, ctx.actor.id
    )
