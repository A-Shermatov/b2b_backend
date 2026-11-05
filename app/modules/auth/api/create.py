from fastapi import BackgroundTasks, Request

from app.config import get_settings
from app.core.exceptions import RateLimitError
from app.modules.auth.api.common import (
    AuthService,
    ForgotPasswordRequest,
    LoginRequest,
    LoginResult,
    LogoutRequest,
    MessageResponse,
    RefreshRequest,
    RegisterRequest,
    ResetPasswordRequest,
    TokenResponse,
    TwoFactorVerifyRequest,
    check_rate_limit,
    get_client_ip,
    router,
)

from app.modules.auth.services.password import FORGOT_GENERIC_MESSAGE  # noqa: E402
from app.utils import email as email_utils  # noqa: E402

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
    # Per account as well: password guessing from many IPs is limited too.
    account_allowed = await check_rate_limit(
        f"login_account:{data.email.lower()}",
        settings.rate_limit_login_per_account,
        settings.rate_limit_login_account_window_seconds,
    )
    if not account_allowed:
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


def _queue_email(background: BackgroundTasks, job) -> None:
    if job is not None:
        background.add_task(email_utils.send_email, job.to, job.subject, job.text, job.html)


@router.post("/password/forgot", response_model=MessageResponse)
async def forgot_password(
    data: ForgotPasswordRequest, request: Request, background: BackgroundTasks
):
    ip = await get_client_ip(request)
    allowed = await check_rate_limit(
        f"password_forgot:{ip}", settings.rate_limit_password_forgot_per_minute, 60
    )
    if not allowed:
        raise RateLimitError("Too many password reset attempts. Try again later.")
    job = await AuthService().request_password_reset(data, ip)
    _queue_email(background, job)
    return MessageResponse(detail=FORGOT_GENERIC_MESSAGE)


@router.post("/password/reset", response_model=MessageResponse)
async def reset_password(
    data: ResetPasswordRequest, request: Request, background: BackgroundTasks
):
    ip = await get_client_ip(request)
    allowed = await check_rate_limit(
        f"password_reset:{ip}", settings.rate_limit_password_reset_per_minute, 60
    )
    if not allowed:
        raise RateLimitError("Too many password reset attempts. Try again later.")
    job = await AuthService().reset_password(data, ip)
    _queue_email(background, job)
    return MessageResponse(detail="Пароль успешно изменён")
