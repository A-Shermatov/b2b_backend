from fastapi import APIRouter

from app.deps import require_buyer_ctx, require_supplier_ctx
from app.modules.companies.schemas import (
    AddTeamMemberRequest,
    CertificateCreateRequest,
    CertificateUpdateRequest,
    CompanyCertificateSchema,
    CompanyChangeRequestCreate,
    CompanyChangeRequestSchema,
    CompanyUpdateRequest,
    CompanyWizardInput,
    CompanyWithRelations,
    PublicBuyer,
    PublicCompanyDetail,
    PublicSupplier,
    UpdateTeamMemberRequest,
)
from app.modules.companies.services import CompanyService
from app.deps import BuyerContext, SupplierContext

buyer_router = APIRouter(prefix="/companies", tags=["buyer-companies"])
supplier_router = APIRouter(prefix="/companies", tags=["supplier-companies"])
public_router = APIRouter(tags=["public"])
