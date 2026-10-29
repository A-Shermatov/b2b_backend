from typing import Annotated, Literal

from fastapi import Depends, File, Query, UploadFile

from app.deps import CurrentUser, admin_perm
from app.modules.admin.api.common import AdminService, router
from app.modules.admin.schemas import (
    AdminCatalogBulkActionRequest,
    AdminCatalogBulkActionResponse,
    AdminCatalogDetail,
    AdminCatalogItemUpdate,
    AdminCatalogManageListResponse,
    AdminCatalogMediaOrder,
    CatalogItemStatusLiteral,
)
from app.utils.storage import storage_service


@router.get(
    "/catalog/items",
    response_model=AdminCatalogManageListResponse,
    dependencies=[Depends(admin_perm("admin_catalog.view"))],
)
async def admin_list_catalog_items(
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    type: Literal["product", "service"] | None = None,
    status: Annotated[list[CatalogItemStatusLiteral] | None, Query()] = None,
    company_id: Annotated[int | None, Query(ge=1)] = None,
    actor_id: Annotated[int | None, Query(ge=1)] = None,
    supplier: Annotated[str | None, Query(max_length=200)] = None,
    category_id: Annotated[int | None, Query(ge=1)] = None,
    price_min: Annotated[float | None, Query(ge=0)] = None,
    price_max: Annotated[float | None, Query(ge=0)] = None,
    has_reports: bool | None = None,
    query: Annotated[str | None, Query(max_length=200)] = None,
    sort: Literal["created_at", "updated_at", "price", "title", "views", "reports"] = "created_at",
    order: Literal["asc", "desc"] = "desc",
    include_deleted: bool = False,
):
    return await AdminService().list_catalog_manage(
        page=page,
        page_size=page_size,
        item_type=type,
        statuses=status,
        company_id=company_id,
        actor_id=actor_id,
        supplier=supplier,
        category_id=category_id,
        price_min=price_min,
        price_max=price_max,
        has_reports=has_reports,
        query=query,
        sort=sort,
        order=order,
        include_deleted=include_deleted,
    )


@router.post("/catalog/bulk-action", response_model=AdminCatalogBulkActionResponse)
async def admin_bulk_catalog_action(
    data: AdminCatalogBulkActionRequest,
    current_user: Annotated[CurrentUser, Depends(admin_perm("admin_catalog.update", "admin_catalog.delete"))],
):
    return await AdminService().bulk_catalog_action(
        ids=data.ids,
        action=data.action,
        current_user=current_user,
        reason=data.reason,
    )


@router.patch("/catalog/{item_id}", response_model=AdminCatalogDetail)
async def admin_update_catalog_item(
    item_id: int,
    data: AdminCatalogItemUpdate,
    current_user: Annotated[CurrentUser, Depends(admin_perm("admin_catalog.update"))],
):
    return await AdminService().update_catalog_item_admin(item_id, data, current_user)


@router.post("/catalog/{item_id}/media", response_model=AdminCatalogDetail)
async def admin_upload_catalog_media(
    item_id: int,
    current_user: Annotated[CurrentUser, Depends(admin_perm("admin_catalog.update"))],
    file: UploadFile = File(...),
):
    service = AdminService()
    actor_id = await service.get_editable_catalog_item_actor(item_id)
    key, _ = await storage_service.upload(file, f"catalog/{actor_id}")
    return await service.add_catalog_media_admin(
        item_id,
        current_user,
        file_name=file.filename or "file",
        key=key,
        content_type=file.content_type,
    )


@router.delete("/catalog/{item_id}/media/{media_id}", response_model=AdminCatalogDetail)
async def admin_delete_catalog_media(
    item_id: int,
    media_id: int,
    current_user: Annotated[CurrentUser, Depends(admin_perm("admin_catalog.update"))],
):
    return await AdminService().delete_catalog_media_admin(item_id, media_id, current_user)


@router.put("/catalog/{item_id}/media/order", response_model=AdminCatalogDetail)
async def admin_reorder_catalog_media(
    item_id: int,
    data: AdminCatalogMediaOrder,
    current_user: Annotated[CurrentUser, Depends(admin_perm("admin_catalog.update"))],
):
    return await AdminService().reorder_catalog_media_admin(item_id, data.media_ids, current_user)
