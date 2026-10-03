from typing import Annotated

from fastapi import Depends, Query

from app.deps import admin_perm
from app.modules.admin.api.common import AdminService, router
from app.modules.admin.schemas import (
    AdminLogAudience,
    AdminLogDetail,
    AdminLogKind,
    AdminLogListResponse,
    AdminLogPeriod,
    AdminLogSection,
    AdminLogStatus,
)


@router.get(
    "/logs",
    response_model=AdminLogListResponse,
    dependencies=[Depends(admin_perm("audit_logs.view"))],
)
async def admin_list_logs(
    audience: AdminLogAudience = "users",
    kind: AdminLogKind | None = None,
    section: AdminLogSection | None = None,
    status: AdminLogStatus | None = None,
    period: AdminLogPeriod = "7d",
    query: Annotated[str | None, Query(max_length=255)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
):
    return await AdminService().list_logs(
        audience=audience,
        kind=kind,
        section=section,
        status=status,
        query=query,
        period=period,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/logs/{log_id}",
    response_model=AdminLogDetail,
    dependencies=[Depends(admin_perm("audit_logs.view"))],
)
async def admin_get_log(log_id: int):
    return await AdminService().get_log(log_id)
