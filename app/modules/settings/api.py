from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, Depends

from app.config import get_settings
from app.core.permissions import ensure_permission, has_permission
from app.database import pool
from app.modules.payments.gateways.alif import MIN_MARKETPLACE_PART
from app.deps import CurrentUser, require_admin
from app.modules.settings.schemas import (
    AdminPlatformSettings,
    PlatformSettingsUpdate,
    PublicPlatformSettings,
)
from app.modules.settings.service import (
    get_platform_settings,
    get_platform_settings_meta,
    update_platform_settings,
)

admin_router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])
public_router = APIRouter(tags=["public"])

VIEW_PERMISSION = "platform_settings.view"
UPDATE_PERMISSION = "platform_settings.update"


async def _admin_payload(current_user: CurrentUser) -> dict:
    async with pool.connection() as conn:
        settings = await get_platform_settings(conn, use_cache=False)
        meta = await get_platform_settings_meta(conn)
    return {
        **asdict(settings),
        **meta,
        "can_update": has_permission(current_user, UPDATE_PERMISSION),
    }


@admin_router.get("/settings", response_model=AdminPlatformSettings)
async def admin_get_settings(current_user: Annotated[CurrentUser, Depends(require_admin)]):
    ensure_permission(current_user, VIEW_PERMISSION)
    return await _admin_payload(current_user)


@admin_router.put("/settings", response_model=AdminPlatformSettings)
async def admin_update_settings(
    data: PlatformSettingsUpdate,
    current_user: Annotated[CurrentUser, Depends(require_admin)],
):
    ensure_permission(current_user, UPDATE_PERMISSION)
    await update_platform_settings(data.model_dump(), user_id=current_user.id)
    return await _admin_payload(current_user)


@public_router.get("/platform-settings", response_model=PublicPlatformSettings)
async def public_platform_settings():
    min_part = MIN_MARKETPLACE_PART if get_settings().alif_marketplace_enabled else 0
    return {**asdict(await get_platform_settings()), "min_payment_part": float(min_part)}
