from typing import Annotated

from fastapi import Depends

from app.core.pagination import Page
from app.deps import PaginationDep
from app.modules.finance.api.common import (
    InvoiceSchema,
    SupplierContext,
    SupplierFinanceService,
    WithdrawalDestinationSchema,
    WithdrawalSchema,
    require_supplier_ctx,
    supplier_router,
)


@supplier_router.get("/destinations", response_model=Page[WithdrawalDestinationSchema])
async def list_destinations(
    ctx: Annotated[SupplierContext, Depends(require_supplier_ctx)],
    pagination: PaginationDep,
):
    return await SupplierFinanceService().list_destinations(
        ctx.actor.id, pagination.page, pagination.page_size
    )


@supplier_router.get("/withdrawals", response_model=Page[WithdrawalSchema])
async def list_withdrawals(
    ctx: Annotated[SupplierContext, Depends(require_supplier_ctx)],
    pagination: PaginationDep,
):
    return await SupplierFinanceService().list_withdrawals(
        ctx.actor.id, pagination.page, pagination.page_size
    )


@supplier_router.get("/invoices", response_model=Page[InvoiceSchema])
async def list_invoices(
    ctx: Annotated[SupplierContext, Depends(require_supplier_ctx)],
    pagination: PaginationDep,
):
    return await SupplierFinanceService().list_invoices(
        ctx.actor.id, pagination.page, pagination.page_size
    )
