from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_serializer, field_validator

from app.core.exceptions import ValidationError as AppValidationError
from app.utils.storage import public_file_url, stored_file_key


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
    # Who registers: a private person (freelancer, craftsman) or a company / sole
    # trader. A company gets its cabinet at once; requisites are filled in later.
    account_type: Literal["individual", "company"] = "individual"
    company_title: str | None = Field(default=None, max_length=200)
    # A private supplier says in one line what they do.
    headline: str | None = Field(default=None, max_length=120)
    # Registering from an invitation link: the person joins that company.
    invite_token: str | None = Field(default=None, max_length=200)


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
    # Omitted when the browser sends the refresh token as an httpOnly cookie.
    refresh_token: str | None = None


class LogoutRequest(BaseModel):
    refresh_token: str | None = None


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
    # Public link to the person's photo; None until they upload one.
    avatar_url: str | None = None
    created_at: datetime
    updated_at: datetime
    # Name, photo or page changes waiting for an admin.
    pending_changes: list[str] = []


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
    # True when the person is listed among suppliers as a private person.
    accepts_orders: bool = False
    # True when the cabinet should open in the person's company, not as themselves.
    prefers_company: bool = False
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


def _clean_text(value: str | None) -> str | None:
    text = (value or "").strip()
    return text or None


def _web_link(value: str | None) -> str | None:
    link = _clean_text(value)
    if link is None:
        return None
    if not link.lower().startswith(("http://", "https://")):
        link = f"https://{link}"
    if any(ch in link for ch in " <>\"'") or "." not in link:
        raise ValueError("Укажите ссылку вида https://example.com")
    return link


class PortfolioItem(BaseModel):
    """One piece of work on a private person's page."""

    title: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    # A picture uploaded through POST /auth/me/page/image.
    image: str | None = None
    link: str | None = Field(default=None, max_length=300)

    @field_validator("title")
    @classmethod
    def title_not_blank(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("Назовите работу")
        return text

    @field_validator("description")
    @classmethod
    def clean_description(cls, value: str | None) -> str | None:
        return _clean_text(value)

    @field_validator("link")
    @classmethod
    def link_is_web(cls, value: str | None) -> str | None:
        return _web_link(value)

    @field_validator("image")
    @classmethod
    def image_is_ours(cls, value: str | None) -> str | None:
        try:
            return stored_file_key(value) or None
        except AppValidationError as exc:
            raise ValueError("Загрузите картинку через форму загрузки") from exc

    @field_serializer("image")
    def serialize_image(self, value: str | None) -> str | None:
        return public_file_url(value) if value else None


class IndividualPageUpdate(BaseModel):
    headline: str | None = Field(default=None, max_length=120)
    about: str | None = Field(default=None, max_length=3000)
    skills: list[str] = Field(default_factory=list, max_length=20)
    city: str | None = Field(default=None, max_length=100)
    website: str | None = Field(default=None, max_length=255)
    rate_amount: float | None = Field(default=None, ge=0, le=1_000_000_000)
    rate_currency: Literal["TJS", "USD", "EUR", "KZT", "CNY"] = "TJS"
    rate_unit: Literal["hour", "project"] = "project"
    portfolio: list[PortfolioItem] = Field(default_factory=list, max_length=12)
    # Listed among suppliers as a private person.
    accepts_orders: bool = False

    @field_validator("headline", "about", "city")
    @classmethod
    def clean(cls, value: str | None) -> str | None:
        return _clean_text(value)

    @field_validator("website")
    @classmethod
    def website_is_web(cls, value: str | None) -> str | None:
        return _web_link(value)

    @field_validator("skills")
    @classmethod
    def clean_skills(cls, value: list[str]) -> list[str]:
        seen: dict[str, str] = {}
        for raw in value:
            skill = " ".join(str(raw).split())[:40]
            if skill and skill.lower() not in seen:
                seen[skill.lower()] = skill
        return list(seen.values())


class IndividualPage(IndividualPageUpdate):
    # The person's page among suppliers: /performers/{supplier_actor_id}.
    supplier_actor_id: int | None = None
    # Edits wait for an admin; until then the public page shows the last approved one.
    pending_review: bool = False


class PageImageUploadResponse(BaseModel):
    image: str

    @field_serializer("image")
    def serialize_image(self, value: str) -> str:
        return public_file_url(value)
