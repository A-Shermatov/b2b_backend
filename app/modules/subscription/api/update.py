from typing import Annotated

import asyncpg
from fastapi import Depends

from app.deps import supplier_perm
from app.modules.subscription.api.common import (
    ActivateSubscriptionRequest,
    SubscriptionResponse,
    SubscriptionService,
    SupplierContext,
    raw_db_dependency,
    supplier_router,
)


@supplier_router.post("/activate", response_model=SubscriptionResponse)
async def activate_subscription(
    data: ActivateSubscriptionRequest,
    ctx: Annotated[SupplierContext, Depends(supplier_perm("subscription.update"))],
    db: Annotated[asyncpg.Connection, Depends(raw_db_dependency)],
):
    return await SubscriptionService(db).activate(ctx.user.id, data.plan)


@supplier_router.post("/cancel", response_model=SubscriptionResponse)
async def cancel_subscription(
    ctx: Annotated[SupplierContext, Depends(supplier_perm("subscription.update"))],
    db: Annotated[asyncpg.Connection, Depends(raw_db_dependency)],
):
    return await SubscriptionService(db).cancel(ctx.user.id)
