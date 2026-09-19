from typing import Annotated

import asyncpg
from fastapi import Depends

from app.modules.notifications.api.common import (
    BuyerContext,
    NotificationSchema,
    NotificationService,
    SupplierContext,
    buyer_router,
    raw_db_dependency,
    require_buyer_ctx,
    require_supplier_ctx,
    supplier_router,
)


@buyer_router.get("/", response_model=list[NotificationSchema])
async def list_buyer_notifications(
    ctx: Annotated[BuyerContext, Depends(require_buyer_ctx)],
    db: Annotated[asyncpg.Connection, Depends(raw_db_dependency)],
):
    return await NotificationService(db).list_for_user(ctx.user.id)


@supplier_router.get("/", response_model=list[NotificationSchema])
async def list_supplier_notifications(
    ctx: Annotated[SupplierContext, Depends(require_supplier_ctx)],
    db: Annotated[asyncpg.Connection, Depends(raw_db_dependency)],
):
    return await NotificationService(db).list_for_user(ctx.user.id)
