from typing import Annotated

from fastapi import Depends

from app.deps import supplier_perm
from app.modules.finance.api.common import (
    SupplierContext,
    SupplierFinanceService,
    WithdrawalCreate,
    WithdrawalDestinationCreate,
    WithdrawalDestinationSchema,
    WithdrawalSchema,
    supplier_router,
)


@supplier_router.post("/destinations", response_model=WithdrawalDestinationSchema)
async def create_destination(
    data: WithdrawalDestinationCreate,
    ctx: Annotated[SupplierContext, Depends(supplier_perm("finance.create"))],
):
    return await SupplierFinanceService().create_destination(ctx.actor.id, data)


@supplier_router.post("/withdrawals", response_model=WithdrawalSchema)
async def request_withdrawal(
    data: WithdrawalCreate,
    ctx: Annotated[SupplierContext, Depends(supplier_perm("finance.create"))],
):
    return await SupplierFinanceService().request_withdrawal(ctx.actor.id, data)
