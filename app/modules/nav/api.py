from typing import Annotated

from fastapi import APIRouter, Depends

from app.deps import (
    BuyerContext,
    SupplierContext,
    require_admin,
    require_buyer_ctx,
    require_supplier_ctx,
)
from app.modules.nav.schemas import AdminNavCounts, BuyerNavCounts, SupplierNavCounts
from app.modules.nav.service import NavService

buyer_router = APIRouter(tags=["buyer-nav"])
supplier_router = APIRouter(tags=["supplier-nav"])
admin_router = APIRouter(
    prefix="/admin",
    tags=["admin-nav"],
    dependencies=[Depends(require_admin)],
)


@buyer_router.get("/nav-counts", response_model=BuyerNavCounts)
async def buyer_nav_counts(
    ctx: Annotated[BuyerContext, Depends(require_buyer_ctx)],
):
    return await NavService().buyer_counts(actor_id=ctx.actor.id, user_id=ctx.user.id)


@supplier_router.get("/nav-counts", response_model=SupplierNavCounts)
async def supplier_nav_counts(
    ctx: Annotated[SupplierContext, Depends(require_supplier_ctx)],
):
    return await NavService().supplier_counts(actor_id=ctx.actor.id, user_id=ctx.user.id)


@admin_router.get("/nav-counts", response_model=AdminNavCounts)
async def admin_nav_counts():
    return await NavService().admin_counts()
