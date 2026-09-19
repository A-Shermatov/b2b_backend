from typing import Annotated

from fastapi import Depends

from app.modules.finance.api.common import (
    InvoiceSchema,
    SupplierContext,
    SupplierFinanceService,
    WithdrawalDestinationSchema,
    WithdrawalSchema,
    require_supplier_ctx,
    supplier_router,
)


@supplier_router.get("/destinations", response_model=list[WithdrawalDestinationSchema])
async def list_destinations(ctx: Annotated[SupplierContext, Depends(require_supplier_ctx)]):
    return await SupplierFinanceService().list_destinations(ctx.actor.id)


@supplier_router.get("/withdrawals", response_model=list[WithdrawalSchema])
async def list_withdrawals(ctx: Annotated[SupplierContext, Depends(require_supplier_ctx)]):
    return await SupplierFinanceService().list_withdrawals(ctx.actor.id)


@supplier_router.get("/invoices", response_model=list[InvoiceSchema])
async def list_invoices(ctx: Annotated[SupplierContext, Depends(require_supplier_ctx)]):
    return await SupplierFinanceService().list_invoices(ctx.actor.id)
