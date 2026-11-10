from fastapi import APIRouter, Request, Response

from app.deps import get_auth_context, get_current_user
from app.modules.auth.schemas import (
    ActivateRoleRequest,
    ChangePasswordRequest,
    ForgotPasswordRequest,
    LoginRequest,
    LoginResult,
    LogoutRequest,
    MeResponse,
    MessageResponse,
    RefreshRequest,
    RegisterRequest,
    ResetPasswordRequest,
    SwitchActorRequest,
    SwitchCompanyRequest,
    TokenResponse,
    TwoFactorConfirmRequest,
    TwoFactorDisableRequest,
    TwoFactorSetupResponse,
    TwoFactorVerifyRequest,
    UserPublic,
    UserUpdateRequest,
)
from app.modules.auth.services import AuthService
from app.deps import get_client_ip
from app.utils.redis_client import check_rate_limit

router = APIRouter(prefix="/auth", tags=["auth"])


# The browser front keeps the refresh token in an httpOnly cookie, out of reach of
# page scripts: it asks for that with this header on login/2FA/register/password
# change, and then never gets the refresh token in a response body.
REFRESH_COOKIE = "bm_refresh"
REFRESH_COOKIE_HEADER = "x-refresh-cookie"


def wants_refresh_cookie(request: Request) -> bool:
    return request.headers.get(REFRESH_COOKIE_HEADER) == "1"


def _cookie_path() -> str:
    from app.config import get_settings

    return f"{get_settings().api_v1_prefix}/auth"


def deliver_refresh_token(request: Request, response: Response, tokens, *, from_cookie: bool = False):
    """Put the refresh token in the cookie (and out of the body) when the client uses one."""
    from app.config import get_settings

    refresh = getattr(tokens, "refresh_token", None)
    if not refresh or not (from_cookie or wants_refresh_cookie(request)):
        return tokens
    settings = get_settings()
    response.set_cookie(
        REFRESH_COOKIE,
        refresh,
        max_age=settings.refresh_token_expire_days * 24 * 3600,
        path=_cookie_path(),
        httponly=True,
        secure=not settings.is_dev_env,
        samesite="strict",
    )
    tokens.refresh_token = ""
    return tokens


def clear_refresh_cookie(response: Response) -> None:
    from app.config import get_settings

    response.delete_cookie(
        REFRESH_COOKIE,
        path=_cookie_path(),
        httponly=True,
        secure=not get_settings().is_dev_env,
        samesite="strict",
    )
