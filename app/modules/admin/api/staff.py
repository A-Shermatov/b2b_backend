from typing import Annotated

from fastapi import Depends, Query

from app.deps import CurrentUser, admin_perm
from app.modules.admin.api.common import AdminService, router
from app.modules.admin.schemas import (
    AdminStaffCreate,
    AdminStaffListResponse,
    AdminStaffMember,
    AdminStaffUpdate,
)


@router.get(
    "/staff",
    response_model=AdminStaffListResponse,
    dependencies=[Depends(admin_perm("admin_staff.view"))],
)
async def admin_list_staff(
    query: Annotated[str | None, Query(max_length=255)] = None,
):
    return await AdminService().list_staff(query=query)


@router.post("/staff", response_model=AdminStaffMember, status_code=201)
async def admin_create_staff(
    data: AdminStaffCreate,
    current_user: Annotated[CurrentUser, Depends(admin_perm("admin_staff.create"))],
):
    return await AdminService().create_staff(data, current_user)


@router.patch("/staff/{user_id}", response_model=AdminStaffMember)
async def admin_update_staff(
    user_id: int,
    data: AdminStaffUpdate,
    current_user: Annotated[CurrentUser, Depends(admin_perm("admin_staff.update"))],
):
    return await AdminService().update_staff(user_id, data, current_user)
