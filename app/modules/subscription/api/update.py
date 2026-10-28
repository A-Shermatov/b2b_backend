from typing import Annotated

import asyncpg
from fastapi import Depends

from app.deps import supplier_perm
from app.modules.subscription.api.common import (
    ActivateSubscriptionRequest,
    SubscriptionCheckoutResponse,
    SubscriptionOrderSyncResponse,
    SubscriptionService,
    SupplierContext,
    raw_db_dependency,
    supplier_router,
)


@supplier_router.post("/checkout", response_model=SubscriptionCheckoutResponse)
async def checkout_subscription(
    data: ActivateSubscriptionRequest,
    ctx: Annotated[SupplierContext, Depends(supplier_perm("subscription.update"))],
    db: Annotated[asyncpg.Connection, Depends(raw_db_dependency)],
):
    return await SubscriptionService(db).checkout(ctx.user.id, data.plan, data.method)


@supplier_router.post("/activate", response_model=SubscriptionCheckoutResponse)
async def activate_subscription(
    data: ActivateSubscriptionRequest,
    ctx: Annotated[SupplierContext, Depends(supplier_perm("subscription.update"))],
    db: Annotated[asyncpg.Connection, Depends(raw_db_dependency)],
):
    """Alias of checkout for backward compatibility."""
    return await SubscriptionService(db).checkout(ctx.user.id, data.plan, data.method)


@supplier_router.post("/cancel", response_model=SubscriptionCheckoutResponse)
async def cancel_subscription(
    ctx: Annotated[SupplierContext, Depends(supplier_perm("subscription.update"))],
    db: Annotated[asyncpg.Connection, Depends(raw_db_dependency)],
):
    sub = await SubscriptionService(db).cancel(ctx.user.id)
    return SubscriptionCheckoutResponse(
        plan=sub.plan,
        active_until=sub.active_until,
        is_active=sub.is_active,
        payment_status="canceled",
        gateway="none",
    )


@supplier_router.post(
    "/alif/orders/{order_id}/sync",
    response_model=SubscriptionOrderSyncResponse,
)
async def sync_subscription_alif_order(
    order_id: str,
    ctx: Annotated[SupplierContext, Depends(supplier_perm("subscription.update"))],
    db: Annotated[asyncpg.Connection, Depends(raw_db_dependency)],
):
    return await SubscriptionService(db).sync_subscription_order(order_id, ctx.user.id)
