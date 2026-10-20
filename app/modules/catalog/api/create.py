from typing import Annotated

from fastapi import Depends, File, UploadFile

from app.deps import CurrentUser, SupplierContext, get_current_user, supplier_perm
from app.modules.catalog.api.common import catalog_router, supplier_router
from app.modules.catalog.schemas import (
    CatalogItemInput,
    CatalogItemReportCreate,
    CatalogItemReportResponse,
    CatalogItemWithRelations,
    CatalogMediaUploadResponse,
)
from app.modules.catalog.services import CatalogItemService
from app.modules.subscription.services import SubscriptionService
from app.utils.storage import storage_service


def _media_type_from_content_type(content_type: str | None) -> str:
    if not content_type:
        return "document"
    if content_type.startswith("image/"):
        return "image"
    if content_type.startswith("video/"):
        return "video"
    return "document"


@supplier_router.post("/media", response_model=CatalogMediaUploadResponse)
async def upload_catalog_media(
    ctx: Annotated[SupplierContext, Depends(supplier_perm("catalog.create"))],
    file: UploadFile = File(...),
):
    key, _ = await storage_service.upload(file, f"catalog/{ctx.actor.id}")
    return CatalogMediaUploadResponse(
        file_name=file.filename or "file",
        file_url=key,
        media_type=_media_type_from_content_type(file.content_type),
    )


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
