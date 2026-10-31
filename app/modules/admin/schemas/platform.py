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


class AdminMerchantCard(BaseModel):
    role: Literal["supplier_pool", "platform_commission", "platform_subscriptions"]
    source: Literal["deals", "subscriptions"]
    label: str
    hint: str = ""
    terminal_id: str | None = None
    terminal_masked: str | None = None
    held: float
    released: float
    refunded: float
    net_released: float
    currency: str
    configured: bool


class AdminMerchantTotals(BaseModel):
    held: float
    released: float
    refunded: float
    supplier_available: float
    withdrawn: float
    deals_commission_released: float = 0
    subscriptions_released: float = 0
    currency: str


class AdminSupplierBalanceItem(BaseModel):
    actor_id: int
    display_name: str | None = None
    company_id: int | None = None
    company_title: str | None = None
    owner_email: str | None = None
    available: float
    pending: float
    escrow_locked: float
    released: float
    withdrawn: float
    currency: str


class AdminSupplierBalancesPage(BaseModel):
    items: list[AdminSupplierBalanceItem]
    total: int
    page: int
    page_size: int
    total_pages: int


class AdminMerchantBalancesResponse(BaseModel):
    source: Literal["ledger"]
    note: str
    gateway_configured: bool
    marketplace_enabled: bool
    currency: str
    merchants: list[AdminMerchantCard]
    totals: AdminMerchantTotals
    suppliers: AdminSupplierBalancesPage


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
