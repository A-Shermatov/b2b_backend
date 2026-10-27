from typing import Annotated

from fastapi import APIRouter, Depends

from app.deps import BuyerContext, SupplierContext, require_buyer_ctx, require_supplier_ctx
from app.modules.direct_chats.schemas import (
    DirectChatOpenBuyer,
    DirectChatOpenSupplier,
    DirectChatThread,
    DirectMessageCreate,
)
from app.modules.direct_chats.service import DirectChatService

buyer_router = APIRouter(prefix="/direct-chats", tags=["buyer-direct-chats"])
supplier_router = APIRouter(prefix="/direct-chats", tags=["supplier-direct-chats"])


@buyer_router.post("/", response_model=DirectChatThread)
async def buyer_open_direct_chat(
    body: DirectChatOpenBuyer,
    ctx: Annotated[BuyerContext, Depends(require_buyer_ctx)],
):
    return await DirectChatService().open_for_buyer(
        ctx.actor.id, body.supplier_actor_id, ctx.user.id
    )


@supplier_router.post("/", response_model=DirectChatThread)
async def supplier_open_direct_chat(
    body: DirectChatOpenSupplier,
    ctx: Annotated[SupplierContext, Depends(require_supplier_ctx)],
):
    return await DirectChatService().open_for_supplier(
        ctx.actor.id, body.buyer_actor_id, ctx.user.id
    )


@buyer_router.get("/", response_model=list[DirectChatThread])
async def buyer_list_direct_chats(ctx: Annotated[BuyerContext, Depends(require_buyer_ctx)]):
    return await DirectChatService().list_for_actor(ctx.actor.id, ctx.user.id, as_buyer=True)


@supplier_router.get("/", response_model=list[DirectChatThread])
async def supplier_list_direct_chats(
    ctx: Annotated[SupplierContext, Depends(require_supplier_ctx)],
):
    return await DirectChatService().list_for_actor(ctx.actor.id, ctx.user.id, as_buyer=False)


@buyer_router.get("/{chat_id}", response_model=DirectChatThread)
async def buyer_get_direct_chat(
    chat_id: int,
    ctx: Annotated[BuyerContext, Depends(require_buyer_ctx)],
):
    return await DirectChatService().get(chat_id, ctx.actor.id, ctx.user.id, as_buyer=True)


@supplier_router.get("/{chat_id}", response_model=DirectChatThread)
async def supplier_get_direct_chat(
    chat_id: int,
    ctx: Annotated[SupplierContext, Depends(require_supplier_ctx)],
):
    return await DirectChatService().get(chat_id, ctx.actor.id, ctx.user.id, as_buyer=False)


@buyer_router.post("/{chat_id}/messages", response_model=DirectChatThread)
async def buyer_send_direct_message(
    chat_id: int,
    body: DirectMessageCreate,
    ctx: Annotated[BuyerContext, Depends(require_buyer_ctx)],
):
    return await DirectChatService().add_message(
        chat_id, ctx.actor.id, ctx.user.id, body.text, as_buyer=True
    )


@supplier_router.post("/{chat_id}/messages", response_model=DirectChatThread)
async def supplier_send_direct_message(
    chat_id: int,
    body: DirectMessageCreate,
    ctx: Annotated[SupplierContext, Depends(require_supplier_ctx)],
):
    return await DirectChatService().add_message(
        chat_id, ctx.actor.id, ctx.user.id, body.text, as_buyer=False
    )


@buyer_router.post("/{chat_id}/messages/read")
async def buyer_mark_direct_read(
    chat_id: int,
    ctx: Annotated[BuyerContext, Depends(require_buyer_ctx)],
):
    return await DirectChatService().mark_read(chat_id, ctx.actor.id, ctx.user.id)


@supplier_router.post("/{chat_id}/messages/read")
async def supplier_mark_direct_read(
    chat_id: int,
    ctx: Annotated[SupplierContext, Depends(require_supplier_ctx)],
):
    return await DirectChatService().mark_read(chat_id, ctx.actor.id, ctx.user.id)
