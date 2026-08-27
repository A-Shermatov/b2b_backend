from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.deps import require_admin
from src.db.session import get_db
from src.models import User, UserStatus
from src.modules.admin.service import AdminService
from src.modules.auth.schemas import UserPublic

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])


class UserStatusUpdate(BaseModel):
    status: Literal["pending", "active", "blocked"]


class AdminUserStatusCounts(BaseModel):
    all: int
    active: int
    blocked: int
    pending: int


class AdminUsersResponse(BaseModel):
    items: list[UserPublic]
    total: int
    page: int
    page_size: int
    pages: int
    status_counts: AdminUserStatusCounts


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
    created_at: datetime


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
    certificates: list[dict[str, Any]]
    products: list[dict[str, Any]]
    services: list[dict[str, Any]]
    contracts: list[dict[str, Any]]
    reviews: list[dict[str, Any]]
    verification_checklist: dict[str, bool]


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
    category: dict[str, Any] | None = None
    attributes: list[dict[str, Any]]
    pricing: dict[str, Any] | None = None
    media: list[dict[str, Any]]
    stats: dict[str, Any]
    reports: list[dict[str, Any]]
    history: list[dict[str, Any]]


class CatalogAdminActionRequest(BaseModel):
    action: Literal["approve", "hide", "request_changes", "delete"]
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


class AdminActivityItem(BaseModel):
    id: str
    type: Literal["registration", "contract", "payment", "dispute"]
    title: str
    description: str
    happened_at: datetime


class AdminDashboardResponse(BaseModel):
    metrics: AdminDashboardMetrics
    recent_activity: list[AdminActivityItem]


@router.get("/dashboard", response_model=AdminDashboardResponse)
async def admin_dashboard(
    db: Annotated[AsyncSession, Depends(get_db)],
):
    return await AdminService(db).get_dashboard()


@router.get("/users", response_model=AdminUsersResponse)
async def admin_list_users(
    db: Annotated[AsyncSession, Depends(get_db)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    status: Literal["active", "blocked", "pending"] | None = None,
    query: Annotated[str | None, Query(max_length=255)] = None,
):
    return await AdminService(db).list_users(
        page=page,
        page_size=page_size,
        status=UserStatus(status) if status else None,
        query=query,
    )


@router.patch("/users/{user_id}/status", response_model=UserPublic)
async def admin_update_user_status(
    user_id: int,
    data: UserStatusUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(require_admin)],
):
    return await AdminService(db).update_user_status(
        user_id,
        data.status,
        current_user,
    )


@router.get("/companies", response_model=AdminCompaniesResponse)
async def admin_list_companies(
    db: Annotated[AsyncSession, Depends(get_db)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    status: Literal["all", "verified", "pending", "rejected", "blocked"] = "all",
    query: Annotated[str | None, Query(max_length=200)] = None,
):
    return await AdminService(db).list_companies(
        page=page,
        page_size=page_size,
        status=status,
        query=query,
    )


@router.get("/companies/pending-verification")
async def admin_pending_verification(
    db: Annotated[AsyncSession, Depends(get_db)],
):
    return await AdminService(db).list_pending_verification()


@router.get("/companies/{company_id}", response_model=AdminCompanyDetail)
async def admin_get_company(
    company_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    return await AdminService(db).get_company_detail(company_id)


@router.post(
    "/companies/{company_id}/action",
    response_model=CompanyAdminActionResponse,
)
async def admin_apply_company_action(
    company_id: int,
    data: CompanyAdminActionRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(require_admin)],
):
    return await AdminService(db).apply_company_action(
        company_id=company_id,
        action=data.action,
        current_user=current_user,
        reason=data.reason,
    )


@router.post("/companies/{company_id}/verify")
async def admin_verify_company(
    company_id: int,
    data: VerifyCompanyRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(require_admin)],
):
    return await AdminService(db).apply_company_action(
        company_id=company_id,
        action="approve" if data.approved else "reject",
        current_user=current_user,
        reason=None if data.approved else "Legacy verification endpoint",
    )


@router.get("/catalog", response_model=AdminCatalogListResponse)
async def admin_list_catalog(
    db: Annotated[AsyncSession, Depends(get_db)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    view: Literal["all", "products", "services", "draft", "reported", "hidden"] = "all",
    query: Annotated[str | None, Query(max_length=200)] = None,
):
    return await AdminService(db).list_catalog_items(
        page=page,
        page_size=page_size,
        view=view,
        query=query,
    )


@router.get("/catalog/{item_id}", response_model=AdminCatalogDetail)
async def admin_get_catalog_item(
    item_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    return await AdminService(db).get_catalog_item_detail(item_id)


@router.post(
    "/catalog/{item_id}/action",
    response_model=CatalogAdminActionResponse,
)
async def admin_apply_catalog_action(
    item_id: int,
    data: CatalogAdminActionRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(require_admin)],
):
    return await AdminService(db).apply_catalog_action(
        item_id=item_id,
        action=data.action,
        current_user=current_user,
        reason=data.reason,
    )


@router.get("/rfqs", response_model=AdminRfqListResponse)
async def admin_list_rfqs(
    db: Annotated[AsyncSession, Depends(get_db)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    view: Literal["published", "closed", "draft", "reported", "archived"] = "published",
    query: Annotated[str | None, Query(max_length=200)] = None,
):
    return await AdminService(db).list_rfqs(
        page=page,
        page_size=page_size,
        view=view,
        query=query,
    )


@router.get("/rfqs/{rfq_id}", response_model=AdminRfqDetail)
async def admin_get_rfq(
    rfq_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    return await AdminService(db).get_rfq_detail(rfq_id)


@router.post("/rfqs/{rfq_id}/action", response_model=RfqAdminActionResponse)
async def admin_apply_rfq_action(
    rfq_id: str,
    data: RfqAdminActionRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(require_admin)],
):
    return await AdminService(db).apply_rfq_action(
        rfq_id=rfq_id,
        action=data.action,
        current_user=current_user,
        reason=data.reason,
    )


@router.get("/proposals", response_model=AdminProposalListResponse)
async def admin_list_proposals(
    db: Annotated[AsyncSession, Depends(get_db)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    view: Literal["all", "pending", "accepted", "rejected", "reported"] = "all",
    query: Annotated[str | None, Query(max_length=200)] = None,
):
    return await AdminService(db).list_proposals(
        page=page,
        page_size=page_size,
        view=view,
        query=query,
    )


@router.get("/proposals/{proposal_id}", response_model=AdminProposalDetail)
async def admin_get_proposal(
    proposal_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    return await AdminService(db).get_proposal_detail(proposal_id)


@router.post(
    "/proposals/{proposal_id}/action",
    response_model=ProposalAdminActionResponse,
)
async def admin_apply_proposal_action(
    proposal_id: int,
    data: ProposalAdminActionRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(require_admin)],
):
    return await AdminService(db).apply_proposal_action(
        proposal_id=proposal_id,
        action=data.action,
        current_user=current_user,
        reason=data.reason,
    )


@router.get("/contracts", response_model=AdminContractListResponse)
async def admin_list_contracts(
    db: Annotated[AsyncSession, Depends(get_db)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    view: Literal["active", "completed", "cancelled", "disputed"] = "active",
    query: Annotated[str | None, Query(max_length=255)] = None,
):
    return await AdminService(db).list_contracts(
        page=page,
        page_size=page_size,
        view=view,
        query=query,
    )


@router.get("/contracts/{contract_id}", response_model=AdminContractDetail)
async def admin_get_contract(
    contract_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    return await AdminService(db).get_contract_detail(contract_id)


@router.post(
    "/contracts/{contract_id}/action",
    response_model=ContractAdminActionResponse,
)
async def admin_apply_contract_action(
    contract_id: int,
    data: ContractAdminActionRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(require_admin)],
):
    return await AdminService(db).apply_contract_action(
        contract_id=contract_id,
        action=data.action,
        current_user=current_user,
        reason=data.reason,
    )


@router.get("/disputes", response_model=AdminDisputeListResponse)
async def admin_list_disputes(
    db: Annotated[AsyncSession, Depends(get_db)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    view: Literal["open", "under_review", "resolved", "appealed"] = "open",
    query: Annotated[str | None, Query(max_length=255)] = None,
):
    return await AdminService(db).list_disputes(
        page=page,
        page_size=page_size,
        view=view,
        query=query,
    )


@router.get("/disputes/{dispute_id}", response_model=AdminDisputeDetail)
async def admin_get_dispute(
    dispute_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    return await AdminService(db).get_dispute_detail(dispute_id)


@router.post(
    "/disputes/{dispute_id}/action",
    response_model=DisputeAdminActionResponse,
)
async def admin_apply_dispute_action(
    dispute_id: int,
    data: DisputeAdminActionRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(require_admin)],
):
    return await AdminService(db).apply_dispute_action(
        dispute_id=dispute_id,
        action=data.action,
        current_user=current_user,
        reason=data.reason,
        partial_buyer_amount=data.partial_buyer_amount,
    )


@router.get("/finance", response_model=AdminFinanceListResponse)
async def admin_list_finance(
    db: Annotated[AsyncSession, Depends(get_db)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    view: Literal[
        "platform_revenue",
        "subscriptions",
        "commission",
        "refunds",
        "payouts",
    ] = "platform_revenue",
    query: Annotated[str | None, Query(max_length=255)] = None,
):
    return await AdminService(db).list_finance(
        page=page,
        page_size=page_size,
        view=view,
        query=query,
    )


@router.get("/finance/export")
async def admin_export_finance(
    db: Annotated[AsyncSession, Depends(get_db)],
    view: Literal[
        "platform_revenue",
        "subscriptions",
        "commission",
        "refunds",
        "payouts",
    ] = "platform_revenue",
    query: Annotated[str | None, Query(max_length=255)] = None,
    payment_id: Annotated[int | None, Query(ge=1)] = None,
):
    csv_body = await AdminService(db).export_finance_csv(
        view=view,
        query=query,
        payment_id=payment_id,
    )
    filename = (
        f"finance-{payment_id}.csv" if payment_id else f"finance-{view}.csv"
    )
    return Response(
        content=csv_body,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/finance/{payment_id}", response_model=AdminFinanceDetail)
async def admin_get_finance(
    payment_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    return await AdminService(db).get_finance_detail(payment_id)


@router.post(
    "/finance/{payment_id}/action",
    response_model=FinanceAdminActionResponse,
)
async def admin_apply_finance_action(
    payment_id: int,
    data: FinanceAdminActionRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(require_admin)],
):
    return await AdminService(db).apply_finance_action(
        payment_id=payment_id,
        action=data.action,
        current_user=current_user,
        reason=data.reason,
    )


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


@router.get("/reports", response_model=AdminReportListResponse)
async def admin_list_reports(
    db: Annotated[AsyncSession, Depends(get_db)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    view: Literal["all", "spam", "fraud", "counterfeit", "abuse", "other"] = "all",
    query: Annotated[str | None, Query(max_length=255)] = None,
):
    return await AdminService(db).list_reports(
        page=page,
        page_size=page_size,
        view=view,
        query=query,
    )


@router.get(
    "/reports/{target_type}/{report_id}",
    response_model=AdminReportDetail,
)
async def admin_get_report(
    target_type: Literal["catalog", "rfq", "proposal"],
    report_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    return await AdminService(db).get_report_detail(target_type, report_id)


@router.post(
    "/reports/{target_type}/{report_id}/action",
    response_model=ReportAdminActionResponse,
)
async def admin_apply_report_action(
    target_type: Literal["catalog", "rfq", "proposal"],
    report_id: int,
    data: ReportAdminActionRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(require_admin)],
):
    return await AdminService(db).apply_report_action(
        target_type=target_type,
        report_id=report_id,
        action=data.action,
        current_user=current_user,
        reason=data.reason,
    )
