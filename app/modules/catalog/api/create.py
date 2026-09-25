from typing import Annotated

from fastapi import Depends

from app.deps import CurrentUser, SupplierContext, get_current_user, supplier_perm
from app.modules.catalog.api.common import catalog_router, supplier_router
from app.modules.catalog.schemas import (
    CatalogItemInput,
    CatalogItemReportCreate,
    CatalogItemReportResponse,
    CatalogItemWithRelations,
)
from app.modules.catalog.services import CatalogItemService
from app.modules.subscription.services import SubscriptionService


@supplier_router.post("/items", response_model=CatalogItemWithRelations)
async def create_item(
    data: CatalogItemInput,
    ctx: Annotated[SupplierContext, Depends(supplier_perm("catalog.create"))],
):
    if data.status in {"active", "pending_review"}:
        await SubscriptionService().check_catalog_limit(ctx.user.id, ctx.actor.id)
    return await CatalogItemService().create(ctx.actor.id, data)


@catalog_router.post(
    "/items/{item_id}/reports",
    response_model=CatalogItemReportResponse,
)
async def create_catalog_item_report(
    item_id: int,
    data: CatalogItemReportCreate,
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
):
    return await CatalogItemService().create_report(item_id, current_user.id, data)
