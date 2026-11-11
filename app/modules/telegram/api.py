from typing import Annotated

from fastapi import APIRouter, Depends

from app.core.permissions import ensure_permission, has_permission
from app.deps import CurrentUser, get_current_user, require_admin
from app.modules.telegram import links, service
from app.modules.telegram.schemas import (
    TelegramAccountView,
    TelegramActionResult,
    TelegramLinkStart,
    TelegramSettingsUpdate,
    TelegramSettingsView,
)

# The bot is part of the platform settings: the same permissions guard it.
VIEW_PERMISSION = "platform_settings.view"
UPDATE_PERMISSION = "platform_settings.update"

admin_router = APIRouter(
    prefix="/admin/telegram", tags=["admin-telegram"], dependencies=[Depends(require_admin)]
)
# A person's own link to the bot: any signed-in user, whatever the cabinet.
account_router = APIRouter(prefix="/telegram/account", tags=["telegram-account"])


@account_router.get("", response_model=TelegramAccountView)
async def get_telegram_account(user: Annotated[CurrentUser, Depends(get_current_user)]):
    return await links.link_status(user.id)


@account_router.post("/link", response_model=TelegramLinkStart)
async def start_telegram_link(user: Annotated[CurrentUser, Depends(get_current_user)]):
    """A one-time link that opens the bot; pressing Start there finishes the linking."""
    return await links.create_link(user.id)


@account_router.delete("", response_model=TelegramAccountView)
async def remove_telegram_account(user: Annotated[CurrentUser, Depends(get_current_user)]):
    return await links.unlink(user.id)


async def _view(user: CurrentUser) -> dict:
    return {**await service.admin_view(), "can_update": has_permission(user, UPDATE_PERMISSION)}


@admin_router.get("", response_model=TelegramSettingsView)
async def get_telegram_settings(user: Annotated[CurrentUser, Depends(require_admin)]):
    ensure_permission(user, VIEW_PERMISSION)
    return await _view(user)


@admin_router.put("", response_model=TelegramSettingsView)
async def update_telegram_settings(
    data: TelegramSettingsUpdate,
    user: Annotated[CurrentUser, Depends(require_admin)],
):
    ensure_permission(user, UPDATE_PERMISSION)
    await service.update_settings(data.model_dump(), user.id)
    return await _view(user)


@admin_router.get("/preview")
async def preview_telegram_post(user: Annotated[CurrentUser, Depends(require_admin)]):
    """The picture and caption the next post would get."""
    ensure_permission(user, VIEW_PERMISSION)
    return await service.preview()


@admin_router.post("/test", response_model=TelegramActionResult)
async def send_telegram_test(user: Annotated[CurrentUser, Depends(require_admin)]):
    ensure_permission(user, UPDATE_PERMISSION)
    return await service.send_test(user.id)


@admin_router.post("/posts/{rfq_id}/resend", response_model=TelegramActionResult)
async def resend_telegram_post(rfq_id: str, user: Annotated[CurrentUser, Depends(require_admin)]):
    ensure_permission(user, UPDATE_PERMISSION)
    status = await service.post_request(rfq_id, force=True)
    return {"ok": status == "sent", "status": status}
