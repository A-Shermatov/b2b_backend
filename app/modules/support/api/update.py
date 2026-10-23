from typing import Annotated

from fastapi import Depends

from app.deps import admin_perm, require_buyer_ctx, require_supplier_ctx
from app.modules.support.api.common import (
    BuyerContext,
    CurrentUser,
    SupportMessageSchema,
    SupportService,
    SupportTicketSchema,
    SupplierContext,
    admin_router,
    buyer_router,
    supplier_router,
)


@buyer_router.post(
    "/ticket/{ticket_id}/messages/{message_id}/delivered",
    response_model=SupportMessageSchema,
)
async def buyer_mark_delivered(
    ticket_id: int,
    message_id: int,
    ctx: Annotated[BuyerContext, Depends(require_buyer_ctx)],
):
    return await SupportService().mark_message_delivered(ticket_id, message_id, ctx.user.id)


@supplier_router.post(
    "/ticket/{ticket_id}/messages/{message_id}/delivered",
    response_model=SupportMessageSchema,
)
async def supplier_mark_delivered(
    ticket_id: int,
    message_id: int,
    ctx: Annotated[SupplierContext, Depends(require_supplier_ctx)],
):
    return await SupportService().mark_message_delivered(ticket_id, message_id, ctx.user.id)


@admin_router.post(
    "/tickets/{ticket_id}/messages/{message_id}/delivered",
    response_model=SupportMessageSchema,
)
async def admin_mark_delivered(
    ticket_id: int,
    message_id: int,
    current_user: Annotated[
        CurrentUser, Depends(admin_perm("admin_support.view", "admin_support.update"))
    ],
):
    return await SupportService().mark_message_delivered(
        ticket_id, message_id, current_user.id, as_admin=True
    )


@buyer_router.post("/ticket/{ticket_id}/messages/read")
async def buyer_mark_read(
    ticket_id: int,
    ctx: Annotated[BuyerContext, Depends(require_buyer_ctx)],
):
    return await SupportService().mark_messages_read(ticket_id, ctx.user.id)


@supplier_router.post("/ticket/{ticket_id}/messages/read")
async def supplier_mark_read(
    ticket_id: int,
    ctx: Annotated[SupplierContext, Depends(require_supplier_ctx)],
):
    return await SupportService().mark_messages_read(ticket_id, ctx.user.id)


@admin_router.post("/tickets/{ticket_id}/messages/read")
async def admin_mark_read(
    ticket_id: int,
    current_user: Annotated[
        CurrentUser, Depends(admin_perm("admin_support.view", "admin_support.update"))
    ],
):
    return await SupportService().mark_messages_read(
        ticket_id, current_user.id, as_admin=True
    )


@buyer_router.post("/ticket/{ticket_id}/close", response_model=SupportTicketSchema)
async def buyer_close_ticket(
    ticket_id: int,
    ctx: Annotated[BuyerContext, Depends(require_buyer_ctx)],
):
    return await SupportService().close_user_ticket(ticket_id, ctx.user.id)


@supplier_router.post("/ticket/{ticket_id}/close", response_model=SupportTicketSchema)
async def supplier_close_ticket(
    ticket_id: int,
    ctx: Annotated[SupplierContext, Depends(require_supplier_ctx)],
):
    return await SupportService().close_user_ticket(ticket_id, ctx.user.id)


@admin_router.post("/tickets/{ticket_id}/close", response_model=SupportTicketSchema)
async def admin_close_ticket(
    ticket_id: int,
    current_user: Annotated[CurrentUser, Depends(admin_perm("admin_support.update"))],
):
    return await SupportService().close_admin_ticket(ticket_id, current_user.id)
