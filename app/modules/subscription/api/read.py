from typing import Annotated

import asyncpg
from fastapi import Depends

from app.modules.subscription.api.common import (
    BuyerContext,
    SubscriptionPaymentConfig,
    SubscriptionResponse,
    SubscriptionService,
    SupplierContext,
    buyer_router,
    raw_db_dependency,
    require_buyer_ctx,
    require_supplier_ctx,
    supplier_router,
)


@supplier_router.get("", response_model=SubscriptionResponse)
async def get_subscription(
    ctx: Annotated[SupplierContext, Depends(require_supplier_ctx)],
    db: Annotated[asyncpg.Connection, Depends(raw_db_dependency)],
):
    return await SubscriptionService(db).get_subscription(ctx.user.id)


@supplier_router.get("/payments/config", response_model=SubscriptionPaymentConfig)
async def get_subscription_payment_config(
    ctx: Annotated[SupplierContext, Depends(require_supplier_ctx)],
    db: Annotated[asyncpg.Connection, Depends(raw_db_dependency)],
):
    return SubscriptionService(db).payment_config()


@buyer_router.get("", response_model=SubscriptionResponse)
async def get_buyer_subscription(
    ctx: Annotated[BuyerContext, Depends(require_buyer_ctx)],
    db: Annotated[asyncpg.Connection, Depends(raw_db_dependency)],
):
    return await SubscriptionService(db, side="buyer").get_subscription(ctx.user.id)


@buyer_router.get("/payments/config", response_model=SubscriptionPaymentConfig)
async def get_buyer_subscription_payment_config(
    ctx: Annotated[BuyerContext, Depends(require_buyer_ctx)],
    db: Annotated[asyncpg.Connection, Depends(raw_db_dependency)],
):
    return SubscriptionService(db, side="buyer").payment_config()
