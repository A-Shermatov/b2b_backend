from typing import Annotated

from fastapi import Depends, Request

from app.config import get_settings
from app.core.exceptions import ForbiddenError
from app.modules.auth.api.common import (
    AuthService,
    LoginRequest,
    LogoutRequest,
    RefreshRequest,
    RegisterRequest,
    TokenResponse,
    check_rate_limit,
    get_client_ip,
    router,
)

settings = get_settings()


@router.post("/register", response_model=TokenResponse)
async def register(data: RegisterRequest, request: Request):
    ip = await get_client_ip(request)
    return await AuthService().register(data, ip)


@router.post("/login", response_model=TokenResponse)
async def login(data: LoginRequest, request: Request):
    ip = await get_client_ip(request)
    allowed = await check_rate_limit(
        f"login:{ip}", settings.rate_limit_login_per_minute, 60
    )
    if not allowed:
        raise ForbiddenError("Too many login attempts. Try again later.")
    return await AuthService().login(data, ip)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(data: RefreshRequest):
    return await AuthService().refresh(data.refresh_token)


@router.post("/logout", status_code=204)
async def logout(data: LogoutRequest):
    await AuthService().logout(data.refresh_token)
