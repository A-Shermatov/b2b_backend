from datetime import datetime
from typing import Literal

from pydantic import BaseModel, EmailStr, Field


class AdminStaffMember(BaseModel):
    id: int
    email: str
    first_name: str
    last_name: str
    phone: str | None = None
    platform_role: str
    status: str
    role_id: int | None = None
    role_name: str | None = None
    role_description: str | None = None
    created_at: datetime
    updated_at: datetime


class AdminStaffListResponse(BaseModel):
    items: list[AdminStaffMember]
    total: int


class AdminStaffCreate(BaseModel):
    email: EmailStr
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    phone: str | None = Field(default=None, max_length=50)
    password: str = Field(min_length=8, max_length=128)
    role_id: int = Field(ge=1)


class AdminStaffUpdate(BaseModel):
    first_name: str | None = Field(default=None, min_length=1, max_length=100)
    last_name: str | None = Field(default=None, min_length=1, max_length=100)
    phone: str | None = Field(default=None, max_length=50)
    role_id: int | None = Field(default=None, ge=1)
    status: Literal["active", "blocked"] | None = None
    password: str | None = Field(default=None, min_length=8, max_length=128)
