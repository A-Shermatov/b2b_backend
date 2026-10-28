from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    role: Literal["buyer", "supplier"] | None = None
    requires_2fa: bool = False
    challenge_token: str | None = None


class LoginResult(BaseModel):
    """Login may return tokens or a 2FA challenge."""

    access_token: str | None = None
    refresh_token: str | None = None
    token_type: str = "bearer"
    role: Literal["buyer", "supplier"] | None = None
    requires_2fa: bool = False
    challenge_token: str | None = None


class TwoFactorSetupResponse(BaseModel):
    otpauth_url: str
    secret: str
    backup_codes: list[str]
    qr_data_url: str


class TwoFactorConfirmRequest(BaseModel):
    code: str = Field(min_length=6, max_length=12)


class TwoFactorDisableRequest(BaseModel):
    password: str
    code: str = Field(min_length=6, max_length=32)


class TwoFactorVerifyRequest(BaseModel):
    challenge_token: str
    code: str = Field(min_length=6, max_length=32)


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    phone: str | None = None
    role: Literal["buyer", "supplier", "both"] = "buyer"


class LoginRequest(BaseModel):
    email: EmailStr
    password: str
    role: Literal["buyer", "supplier"] | None = Field(
        default=None,
        description="Роль входа: buyer — заказчик, supplier — исполнитель",
    )


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=8, max_length=128)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


class MessageResponse(BaseModel):
    detail: str


class RefreshRequest(BaseModel):
    refresh_token: str


class LogoutRequest(BaseModel):
    refresh_token: str


class UserPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    first_name: str
    last_name: str
    phone: str | None
    role: str
    status: str
    totp_enabled: bool = False
    created_at: datetime
    updated_at: datetime


class UserUpdateRequest(BaseModel):
    first_name: str | None = None
    last_name: str | None = None
    phone: str | None = None


class ActorSummary(BaseModel):
    id: int
    kind: Literal["individual", "company"]
    side: Literal["buyer", "supplier"]
    display_name: str
    trust_level: str
    company_id: int | None = None
    verification_status: str | None = None
    company_role: str | None = None


class CompanySummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    actor_type: str
    verification_status: str
    role: str | None = None


class StaffRoleSummary(BaseModel):
    id: int
    name: str
    description: str | None = None


class MeResponse(BaseModel):
    user: UserPublic
    actors: list[ActorSummary]
    active_actor_id: int | None = None
    capabilities: dict[str, bool]
    companies: list[CompanySummary] = []
    active_company_id: int | None = None
    permissions: list[str] = []
    staff_role: StaffRoleSummary | None = None


class SwitchActorRequest(BaseModel):
    actor_id: int


class SwitchCompanyRequest(BaseModel):
    company_id: int


class ActivateRoleRequest(BaseModel):
    side: Literal["buyer", "supplier"]
