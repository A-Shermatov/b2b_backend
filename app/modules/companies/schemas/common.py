from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

from app.core.exceptions import ValidationError as AppValidationError
from app.utils.storage import FileUrlMixin, public_file_url, stored_file_key


class CompanyProfileSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    company_id: int
    founded_year: int | None
    employees_count: int | None
    annual_revenue_range: str | None
    languages: list[str]
    industries: list[str]
    warehouse_area: str | None = None
    markets: str | None = None
    brands: str | None = None
    notable_clients: str | None = None
    gallery: list[dict] = []


class CompanyCategorySchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    company_id: int
    category_id: int


class CompanyStatsSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    company_id: int
    completed_contracts: int
    active_contracts: int
    disputes_count: int
    average_rating: float


class CompanyCertificateSchema(FileUrlMixin):
    model_config = ConfigDict(from_attributes=True)

    id: int
    company_id: int
    title: str
    issuer: str
    issue_date: str
    expiry_date: str | None
    file_url: str


class CompanyUserSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    company_id: int
    user_id: int
    role: str
    email: str | None = None
    name: str | None = None
    # Role on purchases / sales when it differs from the company role; "none" closes the side.
    buyer_role: str | None = None
    supplier_role: str | None = None


class ReviewSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    contract_id: int
    reviewer_actor_id: int
    target_actor_id: int
    rating: int
    comment: str | None
    created_at: datetime


class CompanySchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    actor_type: str
    actor_types: list[str] = []
    owner_id: int
    team_members: list[int] = []
    legal_name: str | None
    tax_number: str | None
    website: str | None
    description: str | None
    logo: str | None
    # The wide picture at the top of the company page.
    cover: str | None = None
    country: str | None
    city: str | None
    address: str | None
    verification_status: str
    rating: float
    created_at: datetime
    updated_at: datetime

    @field_serializer("logo", "cover")
    def serialize_logo(self, value: str | None) -> str | None:
        if not value:
            return value
        return public_file_url(value)


class SideReputationSchema(BaseModel):
    """How the company does in one role: as a supplier or as a buyer.

    Kept apart on purpose: a supplier is judged on quality and deadlines, a buyer
    on paying on time and accepting fairly, so one averaged number would mislead.
    """

    side: str
    rating: float | None = None
    reviews_count: int = 0
    completed_deals: int = 0


class CompanyWithRelations(CompanySchema):
    profile: CompanyProfileSchema | None = None
    categories: list[CompanyCategorySchema] = []
    stats: CompanyStatsSchema | None = None
    certificates: list[CompanyCertificateSchema] = []
    reputation: list[SideReputationSchema] = []
    reviews: list[ReviewSchema] = []
    company_users: list[CompanyUserSchema] = []
    # Fields of the profile waiting for an admin (see company_change_requests).
    pending_changes: list[str] = []


class PublicCompanyDetail(BaseModel):
    id: int
    title: str
    actor_type: str
    actor_types: list[str] = []
    legal_name: str | None = None
    has_tax_number: bool = False
    website: str | None = None
    description: str | None = None
    logo: str | None = None
    cover: str | None = None
    country: str | None = None
    city: str | None = None
    verification_status: str
    rating: float
    created_at: datetime
    updated_at: datetime
    profile: CompanyProfileSchema | None = None
    categories: list[CompanyCategorySchema] = []
    stats: CompanyStatsSchema | None = None
    certificates: list[CompanyCertificateSchema] = []
    reputation: list[SideReputationSchema] = []

    @field_serializer("logo", "cover")
    def serialize_logo(self, value: str | None) -> str | None:
        if not value:
            return value
        return public_file_url(value)


class CompanyLogoUploadResponse(BaseModel):
    logo: str

    @field_serializer("logo")
    def serialize_logo(self, value: str) -> str:
        return public_file_url(value)


class CompanyCertificateUploadResponse(BaseModel):
    file_url: str

    @field_serializer("file_url")
    def serialize_file_url(self, value: str) -> str:
        return public_file_url(value)


class CompanyWizardCertificate(BaseModel):
    title: str
    issuer: str
    issue_date: str
    expiry_date: str = ""
    file_url: str


class CompanyWizardTeamMember(BaseModel):
    email: str
    role: str


def _own_logo(value: str | None) -> str | None:
    """The logo must be a file uploaded to our storage (see stored_file_key)."""
    try:
        return stored_file_key(value)
    except AppValidationError as exc:
        raise ValueError("Загрузите логотип через форму загрузки") from exc


class CompanyWizardInput(BaseModel):
    title: str = Field(min_length=2)
    legal_name: str = ""
    tax_number: str = ""
    description: str = ""
    logo: str = ""
    country: str = ""
    city: str = ""
    address: str = ""
    website: str = ""
    founded_year: str = ""
    employees_count: str = ""
    annual_revenue_range: str = ""
    languages: list[str] = []
    industries: list[str] = []
    category_ids: list[int] = []
    certificates: list[CompanyWizardCertificate] = []
    team: list[CompanyWizardTeamMember] = []
    actor_type: str = "buyer"
    actor_types: list[str] = []

    @field_validator("logo")
    @classmethod
    def logo_is_ours(cls, value: str) -> str:
        return _own_logo(value) or ""


class CompanyUpdateRequest(BaseModel):
    title: str | None = None
    legal_name: str | None = None
    tax_number: str | None = None
    website: str | None = None
    description: str | None = None
    logo: str | None = None
    cover: str | None = None
    country: str | None = None
    city: str | None = None
    address: str | None = None
    founded_year: int | None = None
    employees_count: int | None = None
    annual_revenue_range: str | None = None
    languages: list[str] | None = None
    industries: list[str] | None = None
    category_ids: list[int] | None = None
    actor_types: list[str] | None = None
    warehouse_area: str | None = None
    markets: str | None = None
    brands: str | None = None
    notable_clients: str | None = None
    gallery: list[dict] | None = None

    @field_validator("logo", "cover")
    @classmethod
    def logo_is_ours(cls, value: str | None) -> str | None:
        return _own_logo(value)


class AddTeamMemberRequest(BaseModel):
    email: str
    role: str


class CompanyInvitationSchema(BaseModel):
    """An invitation as the company's director or admin sees it."""

    id: int
    company_id: int
    email: str
    role: str
    # pending, expired, declined (accepted ones are team members already)
    status: str
    invited_by_name: str | None = None
    created_at: datetime
    expires_at: datetime
    responded_at: datetime | None = None
    # Only in the answer to "invite" and "send again": the link to hand over.
    invite_url: str | None = None
    # True when the platform's mail is set up and the letter was queued.
    email_sent: bool = False


class MyInvitationSchema(BaseModel):
    """An invitation as the invited person sees it in their cabinet."""

    id: int
    company_id: int
    company_title: str
    company_logo: str | None = None
    role: str
    invited_by_name: str | None = None
    created_at: datetime
    expires_at: datetime


class InvitationPreview(BaseModel):
    """What the invitation link shows before the person signs in."""

    status: str
    company_title: str
    company_logo: str | None = None
    role: str
    email: str
    invited_by_name: str | None = None
    expires_at: datetime


class InvitationTokenRequest(BaseModel):
    token: str = Field(min_length=10, max_length=200)


class InvitationAccepted(BaseModel):
    company_id: int


class UpdateTeamMemberRequest(BaseModel):
    """Change the company role and/or the role on one side.

    buyer_role / supplier_role: admin, moderator, accountant, viewer or none (no access);
    null removes the override, so the company role applies again. A field that is
    not sent stays as it is.
    """

    role: str | None = None
    buyer_role: str | None = None
    supplier_role: str | None = None


class CertificateCreateRequest(BaseModel):
    title: str
    issuer: str
    issue_date: str
    expiry_date: str | None = None
    file_url: str


class CertificateUpdateRequest(BaseModel):
    title: str | None = None
    issuer: str | None = None
    issue_date: str | None = None
    expiry_date: str | None = None
    file_url: str | None = None


class CompanyChangeRequestCreate(BaseModel):
    title: str | None = None
    legal_name: str | None = None
    tax_number: str | None = None
    address: str | None = None
    city: str | None = None
    country: str | None = None
    reason: str | None = None


class CompanyChangeRequestSchema(BaseModel):
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
    # Pictures to compare: {"logo": {"before": url, "after": url}}.
    media: dict = {}


class PublicSupplier(BaseModel):
    actor_id: int
    kind: str
    display_name: str
    company_id: int | None = None
    city: str | None = None
    country: str | None = None
    description: str | None = None
    website: str | None = None
    logo: str | None = None
    rating: float = 0.0
    verification_status: str = "pending"
    reviews_count: int = 0
    industries: list[str] = []
    active_catalog_count: int = 0
    trust_level: str = "basic"
    reputation_tier: str = "none"
    completed_contracts: int = 0
    active_contracts: int = 0
    disputed_contracts: int = 0
    success_rate: float | None = None
    vip: bool = False
    promoted: bool = False
    # Active paid plan (start / pro / business) shown as a mark next to the name.
    subscription_plan: str | None = None
    # Company: the wide picture at the top of its page.
    cover: str | None = None
    # A private person's page (kind == "individual"); logo is then their photo.
    headline: str | None = None
    skills: list[str] = []
    rate_amount: float | None = None
    rate_currency: str | None = None
    rate_unit: str | None = None
    portfolio: list[dict] = []

    @field_serializer("logo", "cover")
    def serialize_logo(self, value: str | None) -> str | None:
        if not value:
            return value
        return public_file_url(value)

    @field_serializer("portfolio")
    def serialize_portfolio(self, value: list[dict]) -> list[dict]:
        return [
            {**item, "image": public_file_url(item["image"]) if item.get("image") else None}
            for item in value
        ]


class PublicBuyer(BaseModel):
    actor_id: int
    kind: str
    display_name: str
    company_id: int | None = None
    city: str | None = None
    country: str | None = None
    description: str | None = None
    website: str | None = None
    legal_name: str | None = None
    rating: float = 0.0
    verification_status: str = "pending"
    reviews_count: int = 0
    trust_level: str = "basic"
    reputation_tier: str = "none"
    completed_contracts: int = 0
    active_contracts: int = 0
    disputed_contracts: int = 0
    success_rate: float | None = None
    # Active paid plan of the owner, shown next to the name on both profiles.
    subscription_plan: str | None = None
