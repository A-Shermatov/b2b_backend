from fastapi import Request

from app.config import get_settings
from app.core.exceptions import RateLimitError
from app.modules.auth.api.common import (
    AuthService,
    LoginRequest,
    LoginResult,
    LogoutRequest,
    RefreshRequest,
    RegisterRequest,
    TokenResponse,
    TwoFactorVerifyRequest,
    check_rate_limit,
    get_client_ip,
    router,
)

settings = get_settings()


@router.post("/register", response_model=TokenResponse)
async def register(data: RegisterRequest, request: Request):
    ip = await get_client_ip(request)
    allowed = await check_rate_limit(
        f"register:{ip}", settings.rate_limit_register_per_minute, 60
    )
    if not allowed:
        raise RateLimitError("Too many registration attempts. Try again later.")
    return await AuthService().register(data, ip)


@router.post("/login", response_model=LoginResult)
async def login(data: LoginRequest, request: Request):
    ip = await get_client_ip(request)
    allowed = await check_rate_limit(
        f"login:{ip}", settings.rate_limit_login_per_minute, 60
    )
    if not allowed:
        raise RateLimitError("Too many login attempts. Try again later.")
    return await AuthService().login(data, ip)


@router.post("/2fa/verify", response_model=TokenResponse)
async def verify_2fa(data: TwoFactorVerifyRequest, request: Request):
    ip = await get_client_ip(request)
    allowed = await check_rate_limit(
        f"2fa_verify:{ip}", settings.rate_limit_2fa_verify_per_minute, 60
    )
    if not allowed:
        raise RateLimitError("Too many 2FA attempts. Try again later.")
    return await AuthService().verify_2fa(data, ip)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(data: RefreshRequest, request: Request):
    ip = await get_client_ip(request)
    allowed = await check_rate_limit(
        f"refresh:{ip}", settings.rate_limit_refresh_per_minute, 60
    )
    if not allowed:
        raise RateLimitError("Too many refresh attempts. Try again later.")
    return await AuthService().refresh(data.refresh_token)


@router.post("/logout", status_code=204)
async def logout(data: LogoutRequest):
    await AuthService().logout(data.refresh_token)
