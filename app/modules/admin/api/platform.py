from typing import Annotated, Literal

from fastapi import Depends, Query

from app.core.permissions import ensure_permission
from app.deps import CurrentUser, require_admin
from app.modules.admin.api.common import AdminService, router
from app.modules.admin.schemas import (
    AdminAnalyticsResponse,
    AdminCategory,
    AdminCategoryCreate,
    AdminCategoryDeleteResponse,
    AdminCategoryListResponse,
    AdminCategoryUpdate,
    AdminEscrowResponse,
    AdminMerchantBalancesResponse,
)


@router.get("/categories", response_model=AdminCategoryListResponse)
async def admin_list_categories(current_user: Annotated[CurrentUser, Depends(require_admin)]):
    ensure_permission(current_user, "admin_catalog.view")
    return await AdminService().list_categories()


@router.post("/categories", response_model=AdminCategory, status_code=201)
async def admin_create_category(
    data: AdminCategoryCreate,
    current_user: Annotated[CurrentUser, Depends(require_admin)],
):
    return await AdminService().create_category(data, current_user)


@router.patch("/categories/{category_id}", response_model=AdminCategory)
async def admin_update_category(
    category_id: int,
    data: AdminCategoryUpdate,
    current_user: Annotated[CurrentUser, Depends(require_admin)],
):
    return await AdminService().update_category(category_id, data, current_user)


@router.delete("/categories/{category_id}", response_model=AdminCategoryDeleteResponse)
async def admin_delete_category(
    category_id: int,
    current_user: Annotated[CurrentUser, Depends(require_admin)],
):
    return await AdminService().delete_category(category_id, current_user)


@router.get("/escrow", response_model=AdminEscrowResponse)
async def admin_escrow(
    current_user: Annotated[CurrentUser, Depends(require_admin)],
    view: Literal["held", "disputed", "awaiting"] = "held",
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    query: Annotated[str | None, Query(max_length=255)] = None,
):
    ensure_permission(current_user, "admin_finance.view")
    return await AdminService().get_escrow(view=view, page=page, page_size=page_size, query=query)


@router.get("/merchant-balances", response_model=AdminMerchantBalancesResponse)
async def admin_merchant_balances(
    current_user: Annotated[CurrentUser, Depends(require_admin)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    query: Annotated[str | None, Query(max_length=255)] = None,
):
    ensure_permission(current_user, "admin_finance.view")
    return await AdminService().get_merchant_balances(
        page=page, page_size=page_size, query=query
    )


@router.get("/analytics", response_model=AdminAnalyticsResponse)
async def admin_analytics(
    current_user: Annotated[CurrentUser, Depends(require_admin)],
    period: Literal["30d", "90d", "12m"] = "30d",
):
    ensure_permission(current_user, "admin_dashboard.view")
    return await AdminService().get_analytics(period)
