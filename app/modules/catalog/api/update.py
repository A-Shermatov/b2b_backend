from typing import Annotated

from fastapi import Depends

from app.deps import SupplierContext, require_supplier_ctx
from app.modules.catalog.api.common import supplier_router
from app.modules.catalog.schemas import CatalogItemInput, CatalogItemWithRelations
from app.modules.catalog.services import CatalogItemService
from app.modules.subscription.services import SubscriptionService


@supplier_router.patch("/items/{item_id}", response_model=CatalogItemWithRelations)
async def update_item(
    item_id: int,
    data: CatalogItemInput,
    ctx: Annotated[SupplierContext, Depends(require_supplier_ctx)],
):
    return await CatalogItemService().update(item_id, ctx.actor.id, data)


@supplier_router.post("/items/{item_id}/publish", response_model=CatalogItemWithRelations)
async def publish_item(
    item_id: int,
    ctx: Annotated[SupplierContext, Depends(require_supplier_ctx)],
):
    await SubscriptionService().check_catalog_limit(ctx.user.id, ctx.actor.id)
    return await CatalogItemService().set_status(item_id, ctx.actor.id, "pending_review")


@supplier_router.post("/items/{item_id}/archive", response_model=CatalogItemWithRelations)
async def archive_item(
    item_id: int,
    ctx: Annotated[SupplierContext, Depends(require_supplier_ctx)],
):
    return await CatalogItemService().set_status(item_id, ctx.actor.id, "archived")


@supplier_router.delete("/items/{item_id}", status_code=204)
async def delete_item(
    item_id: int,
    ctx: Annotated[SupplierContext, Depends(require_supplier_ctx)],
):
    await CatalogItemService().delete(item_id, ctx.actor.id)
