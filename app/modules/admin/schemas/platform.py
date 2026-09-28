from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class AdminCategory(BaseModel):
    id: int
    parent_id: int | None
    name: str
    slug: str
    catalog_items_count: int
    companies_count: int
    children_count: int


class AdminCategoryListResponse(BaseModel):
    items: list[AdminCategory]


class AdminCategoryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    slug: str = Field(min_length=1, max_length=100)
    parent_id: int | None = None


class AdminCategoryUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    slug: str | None = Field(default=None, min_length=1, max_length=100)
    parent_id: int | None = None


class AdminCategoryDeleteResponse(BaseModel):
    id: int
    deleted: bool


class AdminEscrowCurrencySummary(BaseModel):
    currency: str
    held: float
    disputed: float
    awaiting: float
    expected_commission: float


class AdminEscrowViewCounts(BaseModel):
    held: int
    disputed: int
    awaiting: int


class AdminEscrowContractItem(BaseModel):
    id: int
    title: str
    status: str
    currency: str
    agreed_amount: float
    amount: float
    commission: float
    milestones_count: int
    buyer_name: str | None = None
    supplier_name: str | None = None
    created_at: datetime


class AdminEscrowResponse(BaseModel):
    summary: list[AdminEscrowCurrencySummary]
    view_counts: AdminEscrowViewCounts
    items: list[AdminEscrowContractItem]
    total: int
    page: int
    page_size: int
    pages: int


class AdminAnalyticsMetrics(BaseModel):
    users: float
    companies: float
    rfqs: float
    contracts: float
    contract_volume: float
    released: float
    commission: float


class AdminAnalyticsPoint(AdminAnalyticsMetrics):
    bucket: datetime


class AdminAnalyticsResponse(BaseModel):
    period: Literal["30d", "90d", "12m"]
    bucket: Literal["day", "week", "month"]
    currency: str
    totals: AdminAnalyticsMetrics
    previous_totals: AdminAnalyticsMetrics
    series: list[AdminAnalyticsPoint]
