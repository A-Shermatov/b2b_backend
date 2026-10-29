from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.modules.auth.schemas import UserPublic


class UserStatusUpdate(BaseModel):
    status: Literal["pending", "active", "blocked"]
    reason: str | None = Field(default=None, max_length=2000)
    duration_days: int | None = Field(default=None, ge=1, le=365)
    permanent: bool = False


class UserSessionsRevokeResponse(BaseModel):
    user_id: int
    token_version: int


class CompanyVipUpdate(BaseModel):
    vip: bool
    months: int | None = Field(default=None, ge=1, le=12)
    reason: str | None = Field(default=None, max_length=2000)


class CompanyVipResponse(BaseModel):
    id: int
    vip: bool
    vip_until: datetime | None = None


class AdminUserStatusCounts(BaseModel):
    all: int
    active: int
    blocked: int
    pending: int


class AdminUserTabCounts(BaseModel):
    all: int
    buyer: int
    supplier: int
    blocked: int


class AdminUserListItem(UserPublic):
    company_id: int | None = None
    company_title: str | None = None
    contracts_count: int = 0
    contracts_sum: float = 0
    banned_until: datetime | None = None
    block_reason: str | None = None


class AdminUsersResponse(BaseModel):
    items: list[AdminUserListItem]
    total: int
    page: int
    page_size: int
    pages: int
    status_counts: AdminUserStatusCounts
    tab_counts: AdminUserTabCounts


class AdminUserCompany(BaseModel):
    id: int
    title: str
    verification_status: str
    rating: float
    reviews_count: int
    vip: bool = False
    vip_until: datetime | None = None


class AdminUserStats(BaseModel):
    purchases_count: int
    sales_count: int
    contracts_sum: float
    disputes_count: int
    rating: float | None = None
    reviews_count: int = 0
    chats_count: int = 0
    flagged_chats_count: int = 0


class AdminUserContractItem(BaseModel):
    id: int
    title: str
    status: str
    agreed_amount: float
    currency: str
    role: Literal["buyer", "supplier"]
    counterparty: str
    counterparty_company_id: int | None = None
    created_at: datetime
    has_dispute: bool = False


class AdminUserHistoryItem(BaseModel):
    id: int
    action: str
    details: dict[str, Any]
    created_at: datetime
    actor: dict[str, Any] | None = None


class AdminUserChatItem(BaseModel):
    id: str
    kind: str
    about: str
    ref_label: str
    party_a: str
    party_b: str
    last_text: str
    last_at: datetime | None = None
    flagged: bool = False


class AdminUserDocumentItem(BaseModel):
    id: int | str
    title: str
    file_url: str | None = None
    kind: str = "certificate"


class AdminUserDetail(UserPublic):
    banned_until: datetime | None = None
    block_reason: str | None = None
    companies: list[AdminUserCompany]
    stats: AdminUserStats
    contracts: list[AdminUserContractItem]
    history: list[AdminUserHistoryItem]
    chats: list[AdminUserChatItem]
    documents: list[AdminUserDocumentItem]
    verification_checklist: dict[str, bool] = Field(default_factory=dict)


class AdminCompanyOwner(BaseModel):
    id: int
    email: str
    name: str


class AdminCompanyListItem(BaseModel):
    id: int
    title: str
    actor_type: str
    actor_types: list[str]
    owner: AdminCompanyOwner
    legal_name: str | None = None
    tax_number: str | None = None
    logo: str | None = None
    country: str | None = None
    city: str | None = None
    verification_status: str
    operational_status: str
    rating: float
    vip: bool = False
    vip_until: datetime | None = None
    created_at: datetime
    members_count: int = 0
    products_count: int = 0
    contracts_count: int = 0


class AdminCompanyStatusCounts(BaseModel):
    all: int
    verified: int
    pending: int
    rejected: int
    blocked: int


class AdminCompaniesResponse(BaseModel):
    items: list[AdminCompanyListItem]
    total: int
    page: int
    page_size: int
    pages: int
    status_counts: AdminCompanyStatusCounts


class AdminCompanyDetail(AdminCompanyListItem):
    website: str | None = None
    description: str | None = None
    address: str | None = None
    updated_at: datetime
    profile: dict[str, Any] | None = None
    stats: dict[str, Any] | None = None
    members: list[dict[str, Any]]
    categories: list[dict[str, Any]] = Field(default_factory=list)
    certificates: list[dict[str, Any]]
    products: list[dict[str, Any]]
    services: list[dict[str, Any]]
    contracts: list[dict[str, Any]]
    reviews: list[dict[str, Any]]
    verification_checklist: dict[str, bool]


AdminCompanyMemberRole = Literal["admin", "moderator", "accountant"]


class AdminCompanyUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    legal_name: str | None = Field(default=None, max_length=255)
    tax_number: str | None = Field(default=None, max_length=50)
    website: str | None = Field(default=None, max_length=500)
    description: str | None = Field(default=None, max_length=10000)
    country: str | None = Field(default=None, max_length=100)
    city: str | None = Field(default=None, max_length=100)
    address: str | None = Field(default=None, max_length=500)
    logo: str | None = Field(default=None, max_length=500)
    founded_year: int | None = Field(default=None, ge=1800, le=2100)
    employees_count: int | None = Field(default=None, ge=0)
    annual_revenue_range: str | None = Field(default=None, max_length=100)
    languages: list[str] | None = None
    industries: list[str] | None = None


class AdminCompanyLogoResponse(BaseModel):
    logo: str
    logo_url: str | None = None


class AdminCompanyMemberAdd(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    role: AdminCompanyMemberRole = "admin"


class AdminCompanyMemberUpdate(BaseModel):
    role: AdminCompanyMemberRole


class AdminCompanyOwnerChange(BaseModel):
    user_id: int | None = Field(default=None, ge=1)
    email: str | None = Field(default=None, max_length=255)
    reason: str | None = Field(default=None, max_length=2000)


class AdminCompanyCategoriesUpdate(BaseModel):
    category_ids: list[int] = Field(default_factory=list, max_length=100)


class AdminCompanyVerificationSet(BaseModel):
    status: Literal["verified", "pending", "needs_documents", "rejected"]
    reason: str | None = Field(default=None, max_length=2000)


class CompanyAdminActionRequest(BaseModel):
    action: Literal[
        "approve",
        "reject",
        "request_documents",
        "block",
        "deactivate",
        "reactivate",
    ]
    reason: str | None = Field(default=None, max_length=2000)


class CompanyChangeRequestAction(BaseModel):
    action: Literal["approve", "reject"]
    reason: str | None = Field(default=None, max_length=2000)


class CompanyChangeRequestAdminSchema(BaseModel):
    id: int
    company_id: int
    company_title: str | None = None
    requested_by_user_id: int
    status: str
    proposed: dict
    current_snapshot: dict
    reason_from_user: str | None = None
    admin_reason: str | None = None
    reviewed_by_user_id: int | None = None
    reviewed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    field_labels: dict[str, str] = {}


class CompanyAdminActionResponse(BaseModel):
    id: int
    action: str
    verification_status: str
    operational_status: str


class VerifyCompanyRequest(BaseModel):
    approved: bool = True


class AdminDisputeParty(BaseModel):
    actor_id: int
    actor_kind: str
    display_name: str
    company_id: int | None = None
    company_title: str | None = None
    user_id: int | None = None
    email: str | None = None
    name: str


class AdminDisputeListItem(BaseModel):
    id: int
    status: str
    contract_id: int
    contract_title: str | None = None
    contract_amount: float | None = None
    currency: str | None = None
    opened_by_actor_id: int | None = None
    opened_by: AdminDisputeParty | None = None
    buyer: AdminDisputeParty | None = None
    supplier: AdminDisputeParty | None = None
    created_at: datetime
    updated_at: datetime


class AdminDisputeViewCounts(BaseModel):
    open: int
    under_review: int
    resolved: int
    appealed: int


class AdminDisputeListResponse(BaseModel):
    items: list[AdminDisputeListItem]
    total: int
    page: int
    page_size: int
    pages: int
    view_counts: AdminDisputeViewCounts


class AdminDisputeDetail(AdminDisputeListItem):
    buyer_statement: str | None = None
    supplier_statement: str | None = None
    admin_instructions: str | None = None
    resolution: str | None = None
    resolution_note: str | None = None
    partial_buyer_amount: float | None = None
    resolved_at: datetime | None = None
    buyer: AdminDisputeParty | None = None
    supplier: AdminDisputeParty | None = None
    contract: dict[str, Any]
    evidence: list[dict[str, Any]]
    files: list[dict[str, Any]]
    messages: list[dict[str, Any]]
    escrow: dict[str, Any]
    timeline: list[dict[str, Any]]
    chat_id: str | None = None
    conversation_id: int | None = None


class DisputeAdminActionRequest(BaseModel):
    action: Literal[
        "release_funds",
        "refund_buyer",
        "partial_refund",
        "request_evidence",
        "close_case",
    ]
    reason: str | None = Field(default=None, max_length=2000)
    partial_buyer_amount: float | None = Field(default=None, gt=0)


class DisputeAdminActionResponse(BaseModel):
    id: int
    action: str
    status: str
    resolution: str | None = None
    contract_status: str
    partial_buyer_amount: float | None = None


class AdminFinanceParty(BaseModel):
    actor_id: int
    actor_kind: str
    display_name: str
    company_id: int | None = None
    company_title: str | None = None
    user_id: int | None = None
    email: str | None = None
    name: str


class AdminFinanceListItem(BaseModel):
    id: int
    type: str
    status: str
    gateway: str
    amount: float
    commission: float
    currency: str
    title: str
    invoice_id: int | None = None
    withdrawal_id: int | None = None
    contract_id: int | None = None
    actor: AdminFinanceParty | None = None
    created_at: datetime
    updated_at: datetime


class AdminFinanceViewCounts(BaseModel):
    platform_revenue: int
    subscriptions: int
    commission: int
    refunds: int
    payouts: int


class AdminFinanceListResponse(BaseModel):
    items: list[AdminFinanceListItem]
    total: int
    page: int
    page_size: int
    pages: int
    view_counts: AdminFinanceViewCounts


class AdminFinanceDetail(AdminFinanceListItem):
    description: str | None = None
    external_id: str | None = None
    paid_at: datetime | None = None
    failed_at: datetime | None = None
    refunded_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    actor: AdminFinanceParty | None = None
    invoice: dict[str, Any] | None = None
    withdrawal: dict[str, Any] | None = None
    contract: dict[str, Any] | None = None
    subscription_user_id: int | None = None
    history: list[dict[str, Any]]


class FinanceAdminActionRequest(BaseModel):
    action: Literal["refund", "retry", "mark_paid"]
    reason: str | None = Field(default=None, max_length=2000)


class FinanceAdminActionResponse(BaseModel):
    id: int
    action: str
    status: str
    type: str


class AdminCatalogOwner(BaseModel):
    actor_id: int
    actor_kind: str
    display_name: str
    company_id: int | None = None
    company_title: str | None = None
    user_id: int | None = None
    email: str | None = None
    name: str


class AdminCatalogListItem(BaseModel):
    id: int
    title: str
    type: str
    status: str
    category_name: str | None = None
    preview_url: str | None = None
    open_reports_count: int
    owner: AdminCatalogOwner | None = None
    created_at: datetime
    views: int
    leads: int


class AdminCatalogViewCounts(BaseModel):
    all: int
    products: int
    services: int
    draft: int
    reported: int
    hidden: int


class AdminCatalogListResponse(BaseModel):
    items: list[AdminCatalogListItem]
    total: int
    page: int
    page_size: int
    pages: int
    view_counts: AdminCatalogViewCounts


class AdminCatalogDetail(AdminCatalogListItem):
    description: str | None = None
    updated_at: datetime
    invoice: bool = True
    category: dict[str, Any] | None = None
    attributes: list[dict[str, Any]]
    pricing: dict[str, Any] | None = None
    media: list[dict[str, Any]]
    stats: dict[str, Any]
    reports: list[dict[str, Any]]
    history: list[dict[str, Any]]


class CatalogAdminActionRequest(BaseModel):
    action: Literal["approve", "hide", "reject", "delete", "restore"]
    reason: str | None = Field(default=None, max_length=2000)


class CatalogAdminActionResponse(BaseModel):
    id: int
    action: str
    status: str


class AdminRfqBuyer(BaseModel):
    actor_id: int
    actor_kind: str
    display_name: str
    company_id: int | None = None
    company_title: str | None = None
    user_id: int | None = None
    email: str | None = None
    name: str


class AdminRfqListItem(BaseModel):
    id: str
    title: str
    type: str
    status: str
    category_id: str
    currency: str
    budget_from: float | None = None
    budget_to: float | None = None
    deadline: str
    proposals_count: int
    open_reports_count: int
    buyer: AdminRfqBuyer | None = None
    created_at: datetime
    updated_at: datetime


class AdminRfqViewCounts(BaseModel):
    published: int
    closed: int
    draft: int
    reported: int
    archived: int


class AdminRfqListResponse(BaseModel):
    items: list[AdminRfqListItem]
    total: int
    page: int
    page_size: int
    pages: int
    view_counts: AdminRfqViewCounts


class AdminRfqDetail(AdminRfqListItem):
    description: str | None = None
    requirements: dict[str, Any]
    buyer: AdminRfqBuyer | None = None
    proposals: list[dict[str, Any]]
    messages: list[dict[str, Any]]
    reports: list[dict[str, Any]]
    history: list[dict[str, Any]]


class RfqAdminActionRequest(BaseModel):
    action: Literal["hide", "close", "delete", "warn_buyer"]
    reason: str | None = Field(default=None, max_length=2000)


class RfqAdminActionResponse(BaseModel):
    id: str
    action: str
    status: str


class AdminProposalParty(BaseModel):
    actor_id: int
    actor_kind: str
    display_name: str
    company_id: int | None = None
    company_title: str | None = None
    user_id: int | None = None
    email: str | None = None
    name: str


class AdminProposalListItem(BaseModel):
    id: int
    rfq_id: str
    rfq_title: str | None = None
    price: float
    currency: str
    status: str
    open_reports_count: int
    has_contract: bool
    supplier: AdminProposalParty | None = None
    buyer: AdminProposalParty | None = None
    created_at: datetime


class AdminProposalViewCounts(BaseModel):
    all: int
    pending: int
    accepted: int
    rejected: int
    reported: int


class AdminProposalListResponse(BaseModel):
    items: list[AdminProposalListItem]
    total: int
    page: int
    page_size: int
    pages: int
    view_counts: AdminProposalViewCounts


class AdminProposalDetail(AdminProposalListItem):
    delivery_time: str | None = None
    message: str | None = None
    attachment: dict[str, Any] | None = None
    supplier: AdminProposalParty | None = None
    buyer: AdminProposalParty | None = None
    rfq: dict[str, Any] | None = None
    contract: dict[str, Any] | None = None
    messages: list[dict[str, Any]]
    reports: list[dict[str, Any]]
    history: list[dict[str, Any]]


class ProposalAdminActionRequest(BaseModel):
    action: Literal["delete", "investigate", "block_supplier"]
    reason: str | None = Field(default=None, max_length=2000)


class ProposalAdminActionResponse(BaseModel):
    id: int
    action: str
    status: str
    blocked_company_id: int | None = None


class AdminContractParty(BaseModel):
    actor_id: int
    actor_kind: str
    display_name: str
    company_id: int | None = None
    company_title: str | None = None
    user_id: int | None = None
    email: str | None = None
    name: str


class AdminContractListItem(BaseModel):
    id: int
    title: str
    status: str
    agreed_amount: float
    currency: str
    payment_type: str
    rfq_id: str
    proposal_id: int
    buyer: AdminContractParty | None = None
    supplier: AdminContractParty | None = None
    escrow_held: float
    created_at: datetime


class AdminContractViewCounts(BaseModel):
    active: int
    completed: int
    cancelled: int
    disputed: int


class AdminContractListResponse(BaseModel):
    items: list[AdminContractListItem]
    total: int
    page: int
    page_size: int
    pages: int
    view_counts: AdminContractViewCounts


class AdminContractDetail(AdminContractListItem):
    description: str | None = None
    start_date: str
    due_date: str
    payment_type: str
    buyer: AdminContractParty | None = None
    supplier: AdminContractParty | None = None
    rfq: dict[str, Any] | None = None
    proposal: dict[str, Any] | None = None
    payment_plan: dict[str, Any] | None = None
    milestones: list[dict[str, Any]]
    files: list[dict[str, Any]]
    messages: list[dict[str, Any]]
    escrow: dict[str, Any]
    history: list[dict[str, Any]]


class ContractAdminActionRequest(BaseModel):
    action: Literal["freeze", "cancel", "force_complete", "open_investigation"]
    reason: str | None = Field(default=None, max_length=2000)


class ContractAdminActionResponse(BaseModel):
    id: int
    action: str
    status: str


class AdminDashboardMetrics(BaseModel):
    total_users: int
    total_companies: int
    catalog_items: int
    active_rfqs: int
    active_contracts: int
    escrow_balance: float
    open_disputes: int
    monthly_revenue: float
    pending_verifications: int
    pending_catalog: int = 0
    open_reports: int = 0
    pending_payouts: int = 0
    flagged_chats: int = 0
    pending_company_changes: int = 0
    users_without_2fa: int = 0
    failed_logins_24h: int = 0


class AdminActivityItem(BaseModel):
    id: str
    type: Literal["registration", "contract", "payment", "dispute"]
    title: str
    description: str
    happened_at: datetime


class AdminDashboardResponse(BaseModel):
    metrics: AdminDashboardMetrics
    recent_activity: list[AdminActivityItem]


class AdminReportReporter(BaseModel):
    id: int
    email: str
    name: str


class AdminReportedObject(BaseModel):
    type: str
    id: str
    title: str
    href: str
    status: str | None = None
    owner: dict[str, Any] | None = None


class AdminReportListItem(BaseModel):
    id: int
    target_type: str
    reason: str
    status: str
    details_preview: str | None = None
    created_at: datetime
    reporter: AdminReportReporter
    reported_object: AdminReportedObject


class AdminReportViewCounts(BaseModel):
    all: int
    spam: int
    fraud: int
    counterfeit: int
    abuse: int
    other: int


class AdminReportListResponse(BaseModel):
    items: list[AdminReportListItem]
    total: int
    page: int
    page_size: int
    pages: int
    view_counts: AdminReportViewCounts


class AdminReportDetail(BaseModel):
    id: int
    target_type: str
    reason: str
    status: str
    details: str | None = None
    created_at: datetime
    resolved_at: datetime | None = None
    reporter: AdminReportReporter
    reported_object: AdminReportedObject
    owner: dict[str, Any] | None = None
    evidence: dict[str, Any]
    history: list[dict[str, Any]]


class ReportAdminActionRequest(BaseModel):
    action: Literal["dismiss", "warn", "suspend", "delete"]
    reason: str | None = Field(default=None, max_length=2000)


class ReportAdminActionResponse(BaseModel):
    id: int
    target_type: str
    action: str
    status: str


class AdminChatListItem(BaseModel):
    id: str
    kind: Literal["contract", "proposal"]
    thread_id: int
    ref_id: int | str
    about: str
    ref_label: str
    party_a: str
    party_b: str
    party_a_company_id: int | None = None
    party_b_company_id: int | None = None
    last_text: str
    last_at: datetime | None = None
    flagged: bool = False
    has_dispute: bool = False


class AdminChatListResponse(BaseModel):
    items: list[AdminChatListItem]
    total: int
    page: int
    page_size: int
    total_pages: int = 0


class AdminChatMessage(BaseModel):
    id: int
    sender_id: int | None = None
    sender_name: str
    side: Literal["buyer", "supplier", "admin"]
    text: str
    created_at: datetime
    flagged: bool = False
    deleted: bool = False


class AdminChatDetail(BaseModel):
    id: str
    kind: Literal["contract", "proposal"]
    thread_id: int
    ref_id: int | str
    about: str
    ref_label: str
    party_a: str
    party_b: str
    party_a_company_id: int | None = None
    party_b_company_id: int | None = None
    flagged: bool = False
    has_dispute: bool = False
    messages: list[AdminChatMessage] = []


class AdminChatMessageCreate(BaseModel):
    text: str = Field(min_length=1, max_length=5000)
