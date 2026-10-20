from typing import Annotated

from fastapi import Depends, File, UploadFile

from app.modules.companies.api.common import (
    AddTeamMemberRequest,
    BuyerContext,
    CertificateCreateRequest,
    CompanyService,
    CompanyWizardInput,
    CompanyWithRelations,
    SupplierContext,
    buyer_router,
    require_buyer_ctx,
    require_supplier_ctx,
    supplier_router,
)
from app.modules.companies.schemas import CompanyLogoUploadResponse
from app.modules.subscription.services import SubscriptionService
from app.utils.storage import storage_service


@buyer_router.post("/", response_model=CompanyWithRelations)
async def buyer_create_company(
    data: CompanyWizardInput,
    ctx: Annotated[BuyerContext, Depends(require_buyer_ctx)],
):
    return await CompanyService().create_from_wizard(ctx.user, data, "buyer")


@supplier_router.post("/", response_model=CompanyWithRelations)
async def supplier_create_company(
    data: CompanyWizardInput,
    ctx: Annotated[SupplierContext, Depends(require_supplier_ctx)],
):
    await SubscriptionService().check_company_limit(ctx.user)
    return await CompanyService().create_from_wizard(ctx.user, data, "supplier")


@buyer_router.post("/logo", response_model=CompanyLogoUploadResponse)
async def buyer_upload_logo(
    ctx: Annotated[BuyerContext, Depends(require_buyer_ctx)],
    file: UploadFile = File(...),
):
    key, _ = await storage_service.upload(file, f"logos/{ctx.user.id}")
    return CompanyLogoUploadResponse(logo=key)


@supplier_router.post("/logo", response_model=CompanyLogoUploadResponse)
async def supplier_upload_logo(
    ctx: Annotated[SupplierContext, Depends(require_supplier_ctx)],
    file: UploadFile = File(...),
):
    key, _ = await storage_service.upload(file, f"logos/{ctx.user.id}")
    return CompanyLogoUploadResponse(logo=key)


@buyer_router.post("/{company_id}/certificates")
async def buyer_add_certificate(
    company_id: int,
    data: CertificateCreateRequest,
    ctx: Annotated[BuyerContext, Depends(require_buyer_ctx)],
):
    cert = await CompanyService().add_certificate(company_id, ctx.user.id, data)
    return {"id": cert["id"]}


@supplier_router.post("/{company_id}/certificates")
async def supplier_add_certificate(
    company_id: int,
    data: CertificateCreateRequest,
    ctx: Annotated[SupplierContext, Depends(require_supplier_ctx)],
):
    cert = await CompanyService().add_certificate(company_id, ctx.user.id, data)
    return {"id": cert["id"]}


@buyer_router.post("/{company_id}/team")
async def buyer_add_team_member(
    company_id: int,
    data: AddTeamMemberRequest,
    ctx: Annotated[BuyerContext, Depends(require_buyer_ctx)],
):
    member = await CompanyService().add_team_member(company_id, ctx.user.id, data)
    return {"id": member["id"]}


@supplier_router.post("/{company_id}/team")
async def supplier_add_team_member(
    company_id: int,
    data: AddTeamMemberRequest,
    ctx: Annotated[SupplierContext, Depends(require_supplier_ctx)],
):
    member = await CompanyService().add_team_member(company_id, ctx.user.id, data)
    return {"id": member["id"]}
