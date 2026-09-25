from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_serializer

from app.utils.storage import FileUrlMixin, public_file_url


class CompanyProfileSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    company_id: int
    founded_year: int | None
    employees_count: int | None
    annual_revenue_range: str | None
    languages: list[str]
    industries: list[str]


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
    country: str | None
    city: str | None
    address: str | None
    verification_status: str
    rating: float
    created_at: datetime
    updated_at: datetime

    @field_serializer("logo")
    def serialize_logo(self, value: str | None) -> str | None:
        if not value:
            return value
        return public_file_url(value)


class CompanyWithRelations(CompanySchema):
    profile: CompanyProfileSchema | None = None
    categories: list[CompanyCategorySchema] = []
    stats: CompanyStatsSchema | None = None
    certificates: list[CompanyCertificateSchema] = []
    reviews: list[ReviewSchema] = []
    company_users: list[CompanyUserSchema] = []


class CompanyWizardCertificate(BaseModel):
    title: str
    issuer: str
    issue_date: str
    expiry_date: str = ""
    file_url: str


class CompanyWizardTeamMember(BaseModel):
    email: str
    role: str


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


class CompanyUpdateRequest(BaseModel):
    title: str | None = None
    legal_name: str | None = None
    tax_number: str | None = None
    website: str | None = None
    description: str | None = None
    logo: str | None = None
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


class AddTeamMemberRequest(BaseModel):
    email: str
    role: str


class CertificateCreateRequest(BaseModel):
    title: str
    issuer: str
    issue_date: str
    expiry_date: str | None = None
    file_url: str


class PublicSupplier(BaseModel):
    actor_id: int
    kind: str
    display_name: str
    company_id: int | None = None
    city: str | None = None
    country: str | None = None
    description: str | None = None
    website: str | None = None
    rating: float = 0.0
    verification_status: str = "pending"
    reviews_count: int = 0
    industries: list[str] = []
    active_catalog_count: int = 0
    trust_level: str = "basic"
    completed_contracts: int = 0
    active_contracts: int = 0
    disputed_contracts: int = 0
    success_rate: float | None = None