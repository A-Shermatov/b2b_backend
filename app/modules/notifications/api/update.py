from typing import Annotated

import asyncpg
from fastapi import Depends

from app.modules.notifications.api.common import (
    BuyerContext,
    NotificationService,
    SupplierContext,
    buyer_router,
    raw_db_dependency,
    require_buyer_ctx,
    require_supplier_ctx,
    supplier_router,
)


@buyer_router.patch("/{notification_id}/read")
async def mark_buyer_read(
    notification_id: int,
    ctx: Annotated[BuyerContext, Depends(require_buyer_ctx)],
    db: Annotated[asyncpg.Connection, Depends(raw_db_dependency)],
):
    n = await NotificationService(db).mark_read(notification_id, ctx.user.id)
    return {"id": n.id if n else notification_id, "read": True}


@supplier_router.patch("/{notification_id}/read")
async def mark_supplier_read(
    notification_id: int,
    ctx: Annotated[SupplierContext, Depends(require_supplier_ctx)],
    db: Annotated[asyncpg.Connection, Depends(raw_db_dependency)],
):
    n = await NotificationService(db).mark_read(notification_id, ctx.user.id)
    return {"id": n.id if n else notification_id, "read": True}


@buyer_router.post("/read-all", status_code=204)
async def mark_buyer_all_read(
    ctx: Annotated[BuyerContext, Depends(require_buyer_ctx)],
    db: Annotated[asyncpg.Connection, Depends(raw_db_dependency)],
):
    await NotificationService(db).mark_all_read(ctx.user.id)


@supplier_router.post("/read-all", status_code=204)
async def mark_supplier_all_read(
    ctx: Annotated[SupplierContext, Depends(require_supplier_ctx)],
    db: Annotated[asyncpg.Connection, Depends(raw_db_dependency)],
):
    await NotificationService(db).mark_all_read(ctx.user.id)
