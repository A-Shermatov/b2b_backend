from typing import Annotated

import asyncpg
from fastapi import Depends

from app.modules.subscription.api.common import (
    SubscriptionPaymentConfig,
    SubscriptionResponse,
    SubscriptionService,
    SupplierContext,
    raw_db_dependency,
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
