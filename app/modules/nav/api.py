from typing import Annotated

from fastapi import APIRouter, Depends

from app.deps import (
    BuyerContext,
    SupplierContext,
    require_admin,
    require_buyer_ctx,
    require_supplier_ctx,
)
from app.modules.nav.schemas import (
    AdminNavCounts,
    BuyerNavCounts,
    NavSeenBody,
    SupplierNavCounts,
)
from app.modules.nav.service import BUYER_SECTIONS, SUPPLIER_SECTIONS, NavService

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


@buyer_router.post("/nav-seen")
async def buyer_mark_nav_seen(
    body: NavSeenBody,
    ctx: Annotated[BuyerContext, Depends(require_buyer_ctx)],
):
    await NavService().mark_seen(
        user_id=ctx.user.id,
        actor_id=ctx.actor.id,
        section=body.section,
        allowed=BUYER_SECTIONS,
    )
    return {"ok": True}


@supplier_router.post("/nav-seen")
async def supplier_mark_nav_seen(
    body: NavSeenBody,
    ctx: Annotated[SupplierContext, Depends(require_supplier_ctx)],
):
    await NavService().mark_seen(
        user_id=ctx.user.id,
        actor_id=ctx.actor.id,
        section=body.section,
        allowed=SUPPLIER_SECTIONS,
    )
    return {"ok": True}


@admin_router.get("/nav-counts", response_model=AdminNavCounts)
async def admin_nav_counts():
    return await NavService().admin_counts()
