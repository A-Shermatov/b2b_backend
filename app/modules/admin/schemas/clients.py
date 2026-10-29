from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.modules.verification.schemas import IndividualDocument, IndividualVerificationStatus

AdminIndividualAction = Literal["approve", "reject", "request_documents", "set"]


class AdminIndividualListItem(BaseModel):
    id: int
    email: str
    name: str
    phone: str | None = None
    account_status: str
    verification_status: IndividualVerificationStatus
    verification_reason: str | None = None
    submitted_at: datetime | None = None
    verified_at: datetime | None = None
    created_at: datetime
    sides: list[str] = Field(default_factory=list)
    documents_count: int = 0


class AdminIndividualsResponse(BaseModel):
    items: list[AdminIndividualListItem]
    total: int
    page: int
    page_size: int
    pages: int
    status_counts: dict[str, int]


class AdminIndividualVerifier(BaseModel):
    email: str
    name: str | None = None


class AdminIndividualHistoryActor(BaseModel):
    id: int
    email: str
    name: str


class AdminIndividualHistoryItem(BaseModel):
    id: int
    action: str
    details: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    actor: AdminIndividualHistoryActor | None = None


class AdminIndividualDetail(BaseModel):
    id: int
    email: str
    name: str
    phone: str | None = None
    role: str
    account_status: str
    created_at: datetime
    verification_status: IndividualVerificationStatus
    verification_status_label: str
    verification_reason: str | None = None
    submitted_at: datetime | None = None
    verified_at: datetime | None = None
    verified_by: AdminIndividualVerifier | None = None
    sides: list[str] = Field(default_factory=list)
    companies_count: int = 0
    documents: list[IndividualDocument] = Field(default_factory=list)
    history: list[AdminIndividualHistoryItem] = Field(default_factory=list)


class AdminIndividualVerificationSet(BaseModel):
    action: AdminIndividualAction
    status: IndividualVerificationStatus | None = None
    reason: str | None = Field(default=None, max_length=2000)


class AdminVerificationCounts(BaseModel):
    supplier_companies: int = 0
    buyer_companies: int = 0
    individuals: int = 0
    changes: int = 0


class AdminDossierUser(BaseModel):
    id: int
    email: str
    name: str
    phone: str | None = None
    role: str
    status: str
    banned_until: datetime | None = None
    block_reason: str | None = None
    created_at: datetime
    account_age_days: int
    totp_enabled: bool
    last_login_at: datetime | None = None
    last_login_ip: str | None = None


class AdminDossierIdentity(BaseModel):
    status: IndividualVerificationStatus
    status_label: str
    reason: str | None = None
    submitted_at: datetime | None = None
    verified_at: datetime | None = None
    sides: list[str] = Field(default_factory=list)
    documents: list[IndividualDocument] = Field(default_factory=list)


class AdminDossierCompany(BaseModel):
    id: int
    title: str
    role: str
    is_owner: bool
    actor_type: str
    sides: list[str] = Field(default_factory=list)
    verification_status: str
    operational_status: str


class AdminDossierSignals(BaseModel):
    disputes_total: int
    disputes_opened_by: int
    disputes_against: int
    disputes_active: int
    disputes_lost: int
    contracts_total: int
    contracts_cancelled: int
    contracts_completed: int
    contracts_active: int
    complaints_total: int
    complaints_open: int
    complaints_catalog: int
    complaints_rfq: int
    complaints_proposal: int
    complaints_filed: int
    blocks_count: int
    failed_logins_30d: int
    distinct_ips_30d: int
    account_age_days: int


class AdminDossierMoney(BaseModel):
    currency: str
    buyer_turnover: float
    supplier_turnover: float
    contracts_as_buyer: int
    contracts_as_supplier: int
    escrow_held_as_buyer: float
    escrow_held_as_supplier: float
    pending_withdrawals_count: int
    pending_withdrawals_amount: float


class AdminDossierReviewStats(BaseModel):
    count: int = 0
    avg: float | None = None


class AdminDossierBlock(BaseModel):
    id: int
    action: str
    status: str
    reason: str | None = None
    created_at: datetime
    admin: str | None = None


class AdminDossierRiskReason(BaseModel):
    code: str
    weight: int
    label: str


class AdminDossierRisk(BaseModel):
    level: Literal["low", "medium", "high"]
    label: str
    score: int
    reasons: list[AdminDossierRiskReason] = Field(default_factory=list)


class AdminUserDossier(BaseModel):
    user: AdminDossierUser
    identity: AdminDossierIdentity
    companies: list[AdminDossierCompany]
    signals: AdminDossierSignals
    money: AdminDossierMoney
    reviews: dict[str, AdminDossierReviewStats]
    blocks: list[AdminDossierBlock]
    risk: AdminDossierRisk
