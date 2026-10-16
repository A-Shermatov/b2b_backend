from typing import Annotated

from fastapi import Depends

from app.deps import PaginationDep
from app.modules.contracts.api.common import (
    BuyerContext,
    ContractService,
    SupplierContext,
    buyer_router,
    require_buyer_ctx,
    require_supplier_ctx,
    supplier_router,
)


@buyer_router.get("/")
async def buyer_list_contracts(
    ctx: Annotated[BuyerContext, Depends(require_buyer_ctx)],
    pagination: PaginationDep,
):
    return await ContractService().list_for_actor(
        ctx.actor.id, "buyer", pagination.page, pagination.page_size
    )


@supplier_router.get("/")
async def supplier_list_contracts(
    ctx: Annotated[SupplierContext, Depends(require_supplier_ctx)],
    pagination: PaginationDep,
):
    return await ContractService().list_for_actor(
        ctx.actor.id, "supplier", pagination.page, pagination.page_size
    )


@buyer_router.get("/{contract_id}")
async def buyer_get_contract(
    contract_id: int,
    ctx: Annotated[BuyerContext, Depends(require_buyer_ctx)],
):
    return await ContractService().get(contract_id, ctx.actor.id)


@supplier_router.get("/{contract_id}")
async def supplier_get_contract(
    contract_id: int,
    ctx: Annotated[SupplierContext, Depends(require_supplier_ctx)],
):
    return await ContractService().get(contract_id, ctx.actor.id)


@buyer_router.get("/{contract_id}/messages")
async def buyer_get_messages(
    contract_id: int,
    ctx: Annotated[BuyerContext, Depends(require_buyer_ctx)],
):
    contract = await ContractService().get(contract_id, ctx.actor.id)
    return contract.get("conversation")


@supplier_router.get("/{contract_id}/messages")
async def supplier_get_messages(
    contract_id: int,
    ctx: Annotated[SupplierContext, Depends(require_supplier_ctx)],
):
    contract = await ContractService().get(contract_id, ctx.actor.id)
    return contract.get("conversation")
