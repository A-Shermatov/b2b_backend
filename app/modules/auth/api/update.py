from typing import Annotated

from fastapi import BackgroundTasks, Depends, Request

from app.config import get_settings
from app.core.exceptions import RateLimitError
from app.modules.auth.api.common import (
    ActivateRoleRequest,
    AuthService,
    ChangePasswordRequest,
    MeResponse,
    SwitchActorRequest,
    SwitchCompanyRequest,
    TokenResponse,
    TwoFactorConfirmRequest,
    TwoFactorDisableRequest,
    TwoFactorSetupResponse,
    UserPublic,
    UserUpdateRequest,
    check_rate_limit,
    get_client_ip,
    get_current_user,
    router,
)
from app.core.security_core import decode_token
from app.deps import CurrentUser
from app.utils import email as email_utils

settings = get_settings()


@router.patch("/me", response_model=UserPublic)
async def update_me(
    data: UserUpdateRequest,
    user: Annotated[CurrentUser, Depends(get_current_user)],
):
    return await AuthService().update_me(user.id, data)


@router.post("/2fa/setup", response_model=TwoFactorSetupResponse)
async def setup_2fa(
    request: Request,
    user: Annotated[CurrentUser, Depends(get_current_user)],
):
    ip = await get_client_ip(request)
    allowed = await check_rate_limit(
        f"2fa_setup:{ip}:{user.id}", settings.rate_limit_2fa_setup_per_minute, 60
    )
    if not allowed:
        raise RateLimitError("Too many 2FA setup attempts. Try again later.")
    return await AuthService().setup_2fa(user.id)


@router.post("/2fa/confirm", response_model=UserPublic)
async def confirm_2fa(
    data: TwoFactorConfirmRequest,
    user: Annotated[CurrentUser, Depends(get_current_user)],
):
    return await AuthService().confirm_2fa(user.id, data)


@router.post("/2fa/disable", response_model=UserPublic)
async def disable_2fa(
    data: TwoFactorDisableRequest,
    user: Annotated[CurrentUser, Depends(get_current_user)],
):
    return await AuthService().disable_2fa(user.id, data)


@router.post("/switch-actor", response_model=MeResponse)
async def switch_actor(
    data: SwitchActorRequest,
    user: Annotated[CurrentUser, Depends(get_current_user)],
):
    return await AuthService().switch_actor(user.id, data.actor_id)


@router.post("/switch-company", response_model=MeResponse)
async def switch_company(
    data: SwitchCompanyRequest,
    user: Annotated[CurrentUser, Depends(get_current_user)],
):
    return await AuthService().switch_company(user.id, data.company_id)


@router.post("/activate-role", response_model=MeResponse)
async def activate_role(
    data: ActivateRoleRequest,
    user: Annotated[CurrentUser, Depends(get_current_user)],
):
    return await AuthService().activate_role(user.id, data)


def _token_side(request: Request) -> str | None:
    header = request.headers.get("authorization") or ""
    if not header.lower().startswith("bearer "):
        return None
    try:
        side = decode_token(header[7:].strip()).get("side")
    except ValueError:
        return None
    return side if side in ("buyer", "supplier") else None


@router.post("/password/change", response_model=TokenResponse)
async def change_password(
    data: ChangePasswordRequest,
    request: Request,
    background: BackgroundTasks,
    user: Annotated[CurrentUser, Depends(get_current_user)],
):
    ip = await get_client_ip(request)
    allowed = await check_rate_limit(
        f"password_change:{ip}:{user.id}", settings.rate_limit_password_reset_per_minute, 60
    )
    if not allowed:
        raise RateLimitError("Too many password change attempts. Try again later.")
    tokens, job = await AuthService().change_password(
        user.id, data, side=_token_side(request), ip=ip
    )
    background.add_task(email_utils.send_email, job.to, job.subject, job.text, job.html)
    return tokens
