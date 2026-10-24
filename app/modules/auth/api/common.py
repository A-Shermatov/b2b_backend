from fastapi import APIRouter

from app.deps import get_auth_context, get_current_user, raw_db_dependency
from app.modules.auth.schemas import (
    ActivateRoleRequest,
    LoginRequest,
    LoginResult,
    LogoutRequest,
    MeResponse,
    RefreshRequest,
    RegisterRequest,
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
from app.deps import AuthContext, get_client_ip
from app.utils.redis_client import check_rate_limit

router = APIRouter(prefix="/auth", tags=["auth"])
