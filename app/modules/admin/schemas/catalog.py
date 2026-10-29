from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.modules.admin.schemas.common import AdminCatalogOwner

CatalogItemStatusLiteral = Literal[
    "draft",
    "pending_review",
    "changes_requested",
    "active",
    "hidden",
    "archived",
    "deleted",
]


class AdminCatalogManageItem(BaseModel):
    id: int
    title: str
    type: str
    status: str
    category_id: int | None = None
    category_name: str | None = None
    preview_url: str | None = None
    open_reports_count: int
    reports_count: int
    owner: AdminCatalogOwner | None = None
    pricing_type: str | None = None
    currency: str | None = None
    price: float | None = None
    created_at: datetime
    updated_at: datetime | None = None
    views: int
    leads: int


class AdminCatalogStatusCounts(BaseModel):
    all: int
    draft: int
    pending_review: int
    changes_requested: int
    active: int
    hidden: int
    archived: int
    deleted: int


class AdminCatalogManageListResponse(BaseModel):
    items: list[AdminCatalogManageItem]
    total: int
    page: int
    page_size: int
    pages: int
    status_counts: AdminCatalogStatusCounts


class AdminCatalogAttributeInput(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    value: str = Field(min_length=1, max_length=500)
    value_type: str = Field(default="text", max_length=50)
    sort_order: int | None = None


class AdminCatalogPricingTierInput(BaseModel):
    min_qty: int = Field(ge=1)
    price: float = Field(ge=0)


class AdminCatalogPricingInput(BaseModel):
    pricing_type: Literal["fixed", "tiered", "hourly", "monthly"]
    currency: str = Field(default="TJS", min_length=1, max_length=10)
    fixed_price: float | None = Field(default=None, ge=0)
    hourly_rate: float | None = Field(default=None, ge=0)
    monthly_rate: float | None = Field(default=None, ge=0)
    tiers: list[AdminCatalogPricingTierInput] = Field(default_factory=list, max_length=50)


class AdminCatalogItemUpdate(BaseModel):
    type: Literal["product", "service"] | None = None
    category_id: int | None = Field(default=None, ge=1)
    title: str | None = Field(default=None, min_length=2, max_length=255)
    description: str | None = Field(default=None, max_length=20000)
    invoice: bool | None = None
    pricing: AdminCatalogPricingInput | None = None
    attributes: list[AdminCatalogAttributeInput] | None = Field(default=None, max_length=100)
    reason: str | None = Field(default=None, max_length=2000)
    notify_owner: bool = True


class AdminCatalogMediaOrder(BaseModel):
    media_ids: list[int] = Field(min_length=1, max_length=200)


class AdminCatalogBulkActionRequest(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=100)
    action: Literal["approve", "hide", "reject", "delete", "restore"]
    reason: str | None = Field(default=None, max_length=2000)


class AdminCatalogBulkFailure(BaseModel):
    id: int
    error: str


class AdminCatalogBulkActionResponse(BaseModel):
    action: str
    succeeded: list[int]
    failed: list[AdminCatalogBulkFailure]
