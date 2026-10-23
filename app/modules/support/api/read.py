from typing import Annotated, Literal

from fastapi import Depends, Query

from app.core.pagination import Page
from app.deps import PaginationDep, admin_perm, require_buyer_ctx, require_supplier_ctx
from app.modules.support.api.common import (
    BuyerContext,
    CurrentUser,
    SupportService,
    SupportTicketDetail,
    SupportTicketListItem,
    SupportTicketResponse,
    SupplierContext,
    UnreadCountResponse,
    admin_router,
    buyer_router,
    supplier_router,
)


@buyer_router.get("/ticket", response_model=SupportTicketResponse)
async def buyer_get_ticket(ctx: Annotated[BuyerContext, Depends(require_buyer_ctx)]):
    return await SupportService().get_user_ticket(ctx.user.id, "buyer")


@supplier_router.get("/ticket", response_model=SupportTicketResponse)
async def supplier_get_ticket(ctx: Annotated[SupplierContext, Depends(require_supplier_ctx)]):
    return await SupportService().get_user_ticket(ctx.user.id, "supplier")


@buyer_router.get("/unread-count", response_model=UnreadCountResponse)
async def buyer_unread_count(ctx: Annotated[BuyerContext, Depends(require_buyer_ctx)]):
    return await SupportService().user_unread_count(ctx.user.id, "buyer")


@supplier_router.get("/unread-count", response_model=UnreadCountResponse)
async def supplier_unread_count(ctx: Annotated[SupplierContext, Depends(require_supplier_ctx)]):
    return await SupportService().user_unread_count(ctx.user.id, "supplier")


@admin_router.get("/tickets", response_model=Page[SupportTicketListItem])
async def admin_list_tickets(
    current_user: Annotated[CurrentUser, Depends(admin_perm("admin_support.view"))],
    pagination: PaginationDep,
    status: Literal["open", "closed", "all"] = Query("open"),
):
    return await SupportService().list_admin_tickets(
        status=status,
        page=pagination.page,
        page_size=pagination.page_size,
        viewer_id=current_user.id,
    )


@admin_router.get("/tickets/{ticket_id}", response_model=SupportTicketDetail)
async def admin_get_ticket(
    ticket_id: int,
    current_user: Annotated[CurrentUser, Depends(admin_perm("admin_support.view"))],
):
    return await SupportService().get_admin_ticket(ticket_id, current_user.id)


@admin_router.get("/unread-count", response_model=UnreadCountResponse)
async def admin_unread_count(
    current_user: Annotated[CurrentUser, Depends(admin_perm("admin_support.view"))],
):
    return await SupportService().admin_unread_count()
