from typing import Annotated

from fastapi import Depends, Query

from app.deps import admin_perm
from app.modules.admin.api.common import AdminService, router
from app.modules.admin.schemas import (
    AdminLogAudience,
    AdminLogEntityResponse,
    AdminLogEventCategory,
    AdminLogKind,
    AdminLogListResponse,
    AdminLogPeriod,
    AdminLogSection,
    AdminLogSource,
    AdminLogStatus,
    AnyAdminLogDetail,
)


@router.get(
    "/logs",
    response_model=AdminLogListResponse,
    dependencies=[Depends(admin_perm("audit_logs.view"))],
)
async def admin_list_logs(
    audience: AdminLogAudience = "users",
    source: AdminLogSource = "http",
    kind: AdminLogKind | None = None,
    section: AdminLogSection | None = None,
    status: AdminLogStatus | None = None,
    category: AdminLogEventCategory | None = None,
    action: Annotated[str | None, Query(max_length=100)] = None,
    resource_type: Annotated[str | None, Query(max_length=100)] = None,
    resource_id: Annotated[str | None, Query(max_length=100)] = None,
    user_id: Annotated[int | None, Query(ge=1)] = None,
    period: AdminLogPeriod = "7d",
    query: Annotated[str | None, Query(max_length=255)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
):
    return await AdminService().list_logs(
        audience=audience,
        source=source,
        kind=kind,
        section=section,
        status=status,
        category=category,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        user_id=user_id,
        query=query,
        period=period,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/logs/entity",
    response_model=AdminLogEntityResponse,
    dependencies=[Depends(admin_perm("audit_logs.view"))],
)
async def admin_entity_logs(
    resource_type: Annotated[str, Query(min_length=1, max_length=100)],
    resource_id: Annotated[str, Query(min_length=1, max_length=100)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
):
    return await AdminService().list_entity_logs(
        resource_type=resource_type,
        resource_id=resource_id,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/logs/{log_id}",
    response_model=AnyAdminLogDetail,
    dependencies=[Depends(admin_perm("audit_logs.view"))],
)
async def admin_get_log(log_id: int):
    return await AdminService().get_log(log_id)
