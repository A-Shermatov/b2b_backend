from datetime import datetime
from typing import Literal

from pydantic import BaseModel

AdminLogAudience = Literal["users", "staff"]
AdminLogKind = Literal["login", "register", "create", "update", "delete", "view"]
AdminLogStatus = Literal[
    "success",
    "invalid",
    "unauthorized",
    "forbidden",
    "not_found",
    "conflict",
    "rate_limited",
    "rejected",
    "server_error",
]
AdminLogSection = Literal[
    "auth",
    "rfqs",
    "proposals",
    "contracts",
    "finance",
    "catalog",
    "companies",
    "staff",
    "roles",
    "disputes",
    "reports",
    "reviews",
    "notifications",
    "subscription",
    "users",
    "settings",
    "other",
]
AdminLogPeriod = Literal["24h", "7d", "30d", "all"]


class AdminLogUser(BaseModel):
    id: int
    email: str
    name: str
    role: str


class AdminLogItem(BaseModel):
    id: int
    created_at: datetime
    audience: AdminLogAudience
    user: AdminLogUser | None = None
    guest_email: str | None = None
    kind: AdminLogKind
    section: AdminLogSection
    status: AdminLogStatus
    object_id: str | None = None
    ip_address: str | None = None


class AdminLogField(BaseModel):
    key: str
    value: str
    hidden: bool = False


class AdminLogDetail(AdminLogItem):
    fields: list[AdminLogField] = []


class AdminLogListResponse(BaseModel):
    items: list[AdminLogItem]
    total: int
    page: int
    page_size: int
    pages: int
    audience_counts: dict[AdminLogAudience, int]
