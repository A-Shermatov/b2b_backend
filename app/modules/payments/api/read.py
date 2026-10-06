from typing import Annotated

from fastapi import Depends

from app.config import get_settings
from app.modules.payments.api.common import (
    BuyerContext,
    PaymentService,
    SupplierContext,
    buyer_router,
    require_buyer_ctx,
    require_supplier_ctx,
    supplier_router,
)


@buyer_router.get("/config")
async def payment_config(_ctx: Annotated[BuyerContext, Depends(require_buyer_ctx)]):
    settings = get_settings()
    if settings.alif_enabled:
        return {"provider": "alif", "currency": settings.alif_currency}
    return {"provider": "mock", "currency": None}


@buyer_router.get("/contracts/{contract_id}/milestones")
async def buyer_get_milestones(
    contract_id: int,
    ctx: Annotated[BuyerContext, Depends(require_buyer_ctx)],
):
    return await PaymentService().get_milestones(contract_id, ctx.actor.id)


@supplier_router.get("/contracts/{contract_id}/milestones")
async def supplier_get_milestones(
    contract_id: int,
    ctx: Annotated[SupplierContext, Depends(require_supplier_ctx)],
):
    return await PaymentService().get_milestones(contract_id, ctx.actor.id)


@buyer_router.get("/history")
async def payment_history(ctx: Annotated[BuyerContext, Depends(require_buyer_ctx)]):
    return await PaymentService().payment_history(ctx.actor.id)


@supplier_router.get("/history")
async def supplier_payment_history(
    ctx: Annotated[SupplierContext, Depends(require_supplier_ctx)],
):
    return await PaymentService().supplier_payment_history(ctx.actor.id)


@buyer_router.get("/pending")
async def pending_payments(ctx: Annotated[BuyerContext, Depends(require_buyer_ctx)]):
    items = await PaymentService().pending_payments(ctx.actor.id)
    return {"count": len(items), "items": items}


@supplier_router.get("/pending")
async def pending_payouts(ctx: Annotated[SupplierContext, Depends(require_supplier_ctx)]):
    items = await PaymentService().supplier_pending_payouts(ctx.actor.id)
    return {"count": len(items), "items": items}


@supplier_router.get("/balance")
async def supplier_balance(ctx: Annotated[SupplierContext, Depends(require_supplier_ctx)]):
    return await PaymentService().supplier_balance(ctx.actor.id)
