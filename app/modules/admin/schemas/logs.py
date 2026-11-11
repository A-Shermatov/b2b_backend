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
    "profiles",
    "messages",
    "support",
    "approvals",
    "overview",
    "journal",
    "service",
    "other",
]
AdminLogPeriod = Literal["1h", "24h", "7d", "30d", "all"]
AdminLogSource = Literal["events", "http", "pages", "all"]
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
    "telegram",
    "other",
]


class AdminLogUser(BaseModel):
    id: int
    email: str
    name: str
    role: str


class AdminLogDevice(BaseModel):
    """The device a row came from, as short as a table row needs it."""

    #: None for rows written before devices were kept.
    key: str | None = None
    #: The browser named itself; false when the device is told apart by its headers.
    identified: bool = False
    type: str = "other"
    #: «Windows 11», «Android 14».
    os: str | None = None
    #: «Chrome 142».
    browser: str | None = None
    model: str | None = None
    is_bot: bool = False


class AdminLogDeviceFull(AdminLogDevice):
    language: str | None = None
    timezone: str | None = None
    screen: str | None = None
    first_seen_at: datetime | None = None
    last_seen_at: datetime | None = None
    #: How many accounts have used this device.
    users_count: int = 0


class AdminLogTechnical(BaseModel):
    """The raw side of a row, for whoever investigates."""

    method: str | None = None
    path: str | None = None
    query: dict[str, Any] = {}
    status_code: int | None = None
    duration_ms: int | None = None
    request_id: str | None = None
    #: One browser tab from opening to closing.
    visit: str | None = None
    user_agent: str | None = None


class AdminLogRelated(BaseModel):
    """Another row written while the same request was served."""

    id: int
    source: Literal["http", "event", "page"]
    action: str
    created_at: datetime


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
    #: What was asked for. A probe for «/wp-login.php» says more than «Прочее».
    path: str | None = None
    ip_address: str | None = None
    country: str | None = None
    device: AdminLogDevice | None = None
    #: The same view came this many more times before `repeated_until`.
    repeats: int = 0
    repeated_until: datetime | None = None


class AdminLogField(BaseModel):
    key: str
    value: str
    hidden: bool = False


class AdminLogDetail(AdminLogItem):
    fields: list[AdminLogField] = []
    device: AdminLogDeviceFull | None = None
    technical: AdminLogTechnical = AdminLogTechnical()
    related: list[AdminLogRelated] = []


class AdminLogPageItem(BaseModel):
    """A page of the site somebody opened."""

    source: Literal["page"] = "page"
    id: int
    created_at: datetime
    audience: AdminLogAudience
    user: AdminLogUser | None = None
    path: str
    title: str | None = None
    query: dict[str, Any] = {}
    referrer: str | None = None
    #: Time on the page; None while it is still open (or the tab was closed abruptly).
    seconds: int | None = None
    active_seconds: int | None = None
    object_id: str | None = None
    ip_address: str | None = None
    country: str | None = None
    device: AdminLogDevice | None = None


class AdminLogPageDetail(AdminLogPageItem):
    device: AdminLogDeviceFull | None = None
    technical: AdminLogTechnical = AdminLogTechnical()
    related: list[AdminLogRelated] = []


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
    country: str | None = None
    device: AdminLogDevice | None = None


class AdminLogEventDetail(AdminLogEventItem):
    user_agent: str | None = None
    device: AdminLogDeviceFull | None = None
    technical: AdminLogTechnical = AdminLogTechnical()
    related: list[AdminLogRelated] = []


AnyAdminLogItem = Annotated[
    AdminLogItem | AdminLogEventItem | AdminLogPageItem, Field(discriminator="source")
]
AnyAdminLogDetail = Annotated[
    AdminLogDetail | AdminLogEventDetail | AdminLogPageDetail, Field(discriminator="source")
]


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


AdminDeviceFlag = Literal["shared", "bot", "automated", "unidentified"]
AdminDeviceType = Literal["desktop", "mobile", "tablet", "messenger", "bot", "other"]


class AdminDeviceUser(AdminLogUser):
    first_seen_at: datetime
    last_seen_at: datetime
    last_ip: str | None = None
    requests_count: int = 0


class AdminDeviceIp(BaseModel):
    ip_address: str
    country: str | None = None
    first_seen_at: datetime
    last_seen_at: datetime
    requests_count: int = 0


class AdminDeviceItem(AdminLogDevice):
    key: str
    #: The browser says a program drives it (Selenium, Playwright).
    automated: bool = False
    language: str | None = None
    timezone: str | None = None
    screen: str | None = None
    country: str | None = None
    first_seen_at: datetime
    last_seen_at: datetime
    last_ip: str | None = None
    requests_count: int = 0
    users_count: int = 0
    ips_count: int = 0
    #: The accounts seen on it most recently (the detail has them all).
    users: list[AdminLogUser] = []


class AdminDeviceSimilar(AdminLogDevice):
    key: str
    last_seen_at: datetime
    users: list[AdminLogUser] = []


class AdminDeviceDetail(AdminDeviceItem):
    user_agent: str | None = None
    #: What the browser reported about the machine; empty until it does.
    client: dict[str, Any] = {}
    reported_at: datetime | None = None
    users: list[AdminDeviceUser] = []
    ips: list[AdminDeviceIp] = []
    #: Other devices whose machine looks the same (a browser that forgot its id).
    similar: list[AdminDeviceSimilar] = []


class AdminDeviceListResponse(BaseModel):
    items: list[AdminDeviceItem]
    total: int
    page: int
    page_size: int
    pages: int
