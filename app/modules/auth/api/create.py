from fastapi import BackgroundTasks, Request, Response

from app.config import get_settings
from app.core.exceptions import RateLimitError, UnauthorizedError
from app.utils.redis_client import rate_limit_reached
from app.modules.auth.api.common import (
    AuthService,
    ForgotPasswordRequest,
    LoginRequest,
    LoginResult,
    LogoutRequest,
    MessageResponse,
    RefreshRequest,
    REFRESH_COOKIE,
    RegisterRequest,
    ResetPasswordRequest,
    TokenResponse,
    TwoFactorVerifyRequest,
    check_rate_limit,
    clear_refresh_cookie,
    deliver_refresh_token,
    get_client_ip,
    router,
)

from app.modules.auth.services.password import FORGOT_GENERIC_MESSAGE  # noqa: E402
from app.utils import email as email_utils  # noqa: E402

settings = get_settings()


@router.post("/register", response_model=TokenResponse)
async def register(data: RegisterRequest, request: Request, response: Response):
    ip = await get_client_ip(request)
    allowed = await check_rate_limit(
        f"register:{ip}", settings.rate_limit_register_per_minute, 60
    )
    if not allowed:
        raise RateLimitError("Too many registration attempts. Try again later.")
    tokens = await AuthService().register(data, ip)
    return deliver_refresh_token(request, response, tokens)


@router.post("/login", response_model=LoginResult)
async def login(data: LoginRequest, request: Request, response: Response):
    ip = await get_client_ip(request)
    allowed = await check_rate_limit(
        f"login:{ip}", settings.rate_limit_login_per_minute, 60
    )
    if not allowed:
        raise RateLimitError("Too many login attempts. Try again later.")
    # Per account as well: password guessing from many IPs is limited too. Only
    # failed attempts count, so the owner's own successful logins never lock them out.
    account_key = f"login_account:{data.email.lower()}"
    if await rate_limit_reached(
        account_key,
        settings.rate_limit_login_per_account,
        settings.rate_limit_login_account_window_seconds,
    ):
        raise RateLimitError("Too many login attempts. Try again later.")
    try:
        result = await AuthService().login(data, ip)
        return deliver_refresh_token(request, response, result)
    except UnauthorizedError:
        await check_rate_limit(
            account_key,
            settings.rate_limit_login_per_account,
            settings.rate_limit_login_account_window_seconds,
        )
        raise


@router.post("/2fa/verify", response_model=TokenResponse)
async def verify_2fa(data: TwoFactorVerifyRequest, request: Request, response: Response):
    ip = await get_client_ip(request)
    allowed = await check_rate_limit(
        f"2fa_verify:{ip}", settings.rate_limit_2fa_verify_per_minute, 60
    )
    if not allowed:
        raise RateLimitError("Too many 2FA attempts. Try again later.")
    tokens = await AuthService().verify_2fa(data, ip)
    return deliver_refresh_token(request, response, tokens)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(request: Request, response: Response, data: RefreshRequest | None = None):
    ip = await get_client_ip(request)
    allowed = await check_rate_limit(
        f"refresh:{ip}", settings.rate_limit_refresh_per_minute, 60
    )
    if not allowed:
        raise RateLimitError("Too many refresh attempts. Try again later.")
    body_token = data.refresh_token if data else None
    cookie_token = request.cookies.get(REFRESH_COOKIE)
    token = body_token or cookie_token
    if not token:
        raise UnauthorizedError("Invalid refresh token")
    try:
        tokens = await AuthService().refresh(token)
    except UnauthorizedError:
        if not body_token:
            clear_refresh_cookie(response)
        raise
    return deliver_refresh_token(request, response, tokens, from_cookie=not body_token)


@router.post("/logout", status_code=204)
async def logout(request: Request, response: Response, data: LogoutRequest | None = None):
    token = (data.refresh_token if data else None) or request.cookies.get(REFRESH_COOKIE)
    if token:
        await AuthService().logout(token)
    clear_refresh_cookie(response)


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
