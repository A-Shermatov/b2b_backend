from typing import Annotated, Literal

from fastapi import Depends, File, Form, UploadFile
from pydantic import BaseModel

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
from app.utils.storage import storage_service


@buyer_router.post("/ticket", response_model=SupportTicketSchema)
async def buyer_create_ticket(ctx: Annotated[BuyerContext, Depends(require_buyer_ctx)]):
    return await SupportService().create_user_ticket(ctx.user.id, "buyer")


@supplier_router.post("/ticket", response_model=SupportTicketSchema)
async def supplier_create_ticket(ctx: Annotated[SupplierContext, Depends(require_supplier_ctx)]):
    return await SupportService().create_user_ticket(ctx.user.id, "supplier")


async def _upload_optional(file: UploadFile | None, prefix: str) -> tuple[str | None, str | None, str | None]:
    if file is None or not file.filename:
        return None, None, None
    key, _ = await storage_service.upload(file, prefix)
    return file.filename or "file", key, file.content_type or "application/octet-stream"


@buyer_router.post("/ticket/{ticket_id}/messages", response_model=SupportMessageSchema)
async def buyer_send_message(
    ticket_id: int,
    ctx: Annotated[BuyerContext, Depends(require_buyer_ctx)],
    text: str = Form(""),
    file: UploadFile | None = File(None),
):
    file_name, file_url, file_type = await _upload_optional(file, f"support/{ticket_id}")
    return await SupportService().add_user_message(
        ticket_id,
        ctx.user.id,
        text,
        file_name=file_name,
        file_url=file_url,
        file_type=file_type,
    )


@supplier_router.post("/ticket/{ticket_id}/messages", response_model=SupportMessageSchema)
async def supplier_send_message(
    ticket_id: int,
    ctx: Annotated[SupplierContext, Depends(require_supplier_ctx)],
    text: str = Form(""),
    file: UploadFile | None = File(None),
):
    file_name, file_url, file_type = await _upload_optional(file, f"support/{ticket_id}")
    return await SupportService().add_user_message(
        ticket_id,
        ctx.user.id,
        text,
        file_name=file_name,
        file_url=file_url,
        file_type=file_type,
    )


@admin_router.post("/tickets/{ticket_id}/messages", response_model=SupportMessageSchema)
async def admin_send_message(
    ticket_id: int,
    current_user: Annotated[CurrentUser, Depends(admin_perm("admin_support.update"))],
    text: str = Form(""),
    file: UploadFile | None = File(None),
):
    file_name, file_url, file_type = await _upload_optional(file, f"support/{ticket_id}")
    return await SupportService().add_admin_message(
        ticket_id,
        current_user.id,
        text,
        file_name=file_name,
        file_url=file_url,
        file_type=file_type,
    )


class AdminOpenTicketRequest(BaseModel):
    # The cabinet the person will see the chat in; by default the one they have.
    side: Literal["buyer", "supplier"] | None = None


@admin_router.post("/users/{user_id}/ticket", response_model=SupportTicketSchema)
async def admin_open_ticket(
    user_id: int,
    data: AdminOpenTicketRequest,
    current_user: Annotated[CurrentUser, Depends(admin_perm("admin_support.update"))],
):
    """Start a conversation with a person from the admin panel."""
    return await SupportService().admin_open_ticket(user_id, current_user.id, data.side)
