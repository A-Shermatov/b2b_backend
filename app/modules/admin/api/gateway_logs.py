from typing import Annotated

from fastapi import Depends, Query

from app.deps import admin_perm
from app.modules.admin.api.common import AdminService, router
from app.modules.admin.schemas import (
    AdminGatewayLogDetail,
    AdminGatewayLogDirection,
    AdminGatewayLogExportResponse,
    AdminGatewayLogListResponse,
    AdminGatewayLogPeriod,
)


@router.get(
    "/gateway-logs",
    response_model=AdminGatewayLogListResponse,
    dependencies=[Depends(admin_perm("admin_finance.view"))],
)
async def admin_list_gateway_logs(
    order_id: Annotated[str | None, Query(max_length=64)] = None,
    gateway: Annotated[str | None, Query(max_length=20)] = None,
    operation: Annotated[str | None, Query(max_length=64)] = None,
    direction: AdminGatewayLogDirection | None = None,
    period: AdminGatewayLogPeriod = "7d",
    query: Annotated[str | None, Query(max_length=255)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
):
    return await AdminService().list_gateway_logs(
        order_id=order_id,
        gateway=gateway,
        operation=operation,
        direction=direction,
        period=period,
        query=query,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/gateway-logs/export",
    response_model=AdminGatewayLogExportResponse,
    dependencies=[Depends(admin_perm("admin_finance.view"))],
)
async def admin_export_gateway_logs(
    order_id: Annotated[str | None, Query(max_length=64)] = None,
    gateway: Annotated[str | None, Query(max_length=20)] = None,
    operation: Annotated[str | None, Query(max_length=64)] = None,
    direction: AdminGatewayLogDirection | None = None,
    period: AdminGatewayLogPeriod = "7d",
    query: Annotated[str | None, Query(max_length=255)] = None,
):
    """Full HTTP payloads for reconciliation with bank developers."""
    return await AdminService().export_gateway_logs(
        order_id=order_id,
        gateway=gateway,
        operation=operation,
        direction=direction,
        period=period,
        query=query,
    )


@router.get(
    "/gateway-logs/{log_id}",
    response_model=AdminGatewayLogDetail,
    dependencies=[Depends(admin_perm("admin_finance.view"))],
)
async def admin_get_gateway_log(log_id: int):
    return await AdminService().get_gateway_log(log_id)
