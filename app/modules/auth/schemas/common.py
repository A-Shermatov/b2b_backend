from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


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


class MeResponse(BaseModel):
    user: UserPublic
    actors: list[ActorSummary]
    active_actor_id: int | None = None
    capabilities: dict[str, bool]
    companies: list[CompanySummary] = []
    active_company_id: int | None = None


class SwitchActorRequest(BaseModel):
    actor_id: int


class SwitchCompanyRequest(BaseModel):
    company_id: int


class ActivateRoleRequest(BaseModel):
    side: Literal["buyer", "supplier"]
