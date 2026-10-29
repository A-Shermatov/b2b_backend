from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

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
AdminLogSource = Literal["events", "http", "all"]
AdminLogEventCategory = Literal[
    "auth",
    "admin.company",
    "admin.user",
    "admin.catalog",
    "admin.contract",
    "admin.dispute",
    "admin.finance",
    "admin.rfq",
    "admin.proposal",
    "admin.report",
    "admin.staff",
    "rfq",
    "proposal",
    "contract",
    "payment",
    "catalog",
    "company",
    "review",
    "dispute",
    "finance",
    "other",
]


class AdminLogUser(BaseModel):
    id: int
    email: str
    name: str
    role: str


class AdminLogItem(BaseModel):
    source: Literal["http"] = "http"
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


class AdminLogEventItem(BaseModel):
    source: Literal["event"] = "event"
    id: int
    created_at: datetime
    audience: AdminLogAudience
    user: AdminLogUser | None = None
    action: str
    category: AdminLogEventCategory
    resource_type: str
    resource_id: str
    actor_id: int | None = None
    company_id: int | None = None
    details: dict[str, Any] = {}
    ip_address: str | None = None


class AdminLogEventDetail(AdminLogEventItem):
    user_agent: str | None = None


AnyAdminLogItem = Annotated[AdminLogItem | AdminLogEventItem, Field(discriminator="source")]
AnyAdminLogDetail = Annotated[AdminLogDetail | AdminLogEventDetail, Field(discriminator="source")]


class AdminLogListResponse(BaseModel):
    items: list[AnyAdminLogItem]
    total: int
    page: int
    page_size: int
    pages: int
    audience_counts: dict[AdminLogAudience, int]


class AdminLogEntityResponse(BaseModel):
    items: list[AdminLogEventItem]
    total: int
    page: int
    page_size: int
    pages: int
