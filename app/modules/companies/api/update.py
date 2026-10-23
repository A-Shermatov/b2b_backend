from typing import Annotated

from fastapi import Depends

from app.modules.companies.api.common import (
    BuyerContext,
    CertificateUpdateRequest,
    CompanyCertificateSchema,
    CompanyService,
    CompanyUpdateRequest,
    CompanyWithRelations,
    SupplierContext,
    UpdateTeamMemberRequest,
    buyer_router,
    require_buyer_ctx,
    require_supplier_ctx,
    supplier_router,
)


@buyer_router.patch("/{company_id}", response_model=CompanyWithRelations)
async def buyer_update_company(
    company_id: int,
    data: CompanyUpdateRequest,
    ctx: Annotated[BuyerContext, Depends(require_buyer_ctx)],
):
    return await CompanyService().update_company(company_id, ctx.user.id, data)


@supplier_router.patch("/{company_id}", response_model=CompanyWithRelations)
async def supplier_update_company(
    company_id: int,
    data: CompanyUpdateRequest,
    ctx: Annotated[SupplierContext, Depends(require_supplier_ctx)],
):
    return await CompanyService().update_company(company_id, ctx.user.id, data)


@buyer_router.patch(
    "/{company_id}/certificates/{certificate_id}",
    response_model=CompanyCertificateSchema,
)
async def buyer_update_certificate(
    company_id: int,
    certificate_id: int,
    data: CertificateUpdateRequest,
    ctx: Annotated[BuyerContext, Depends(require_buyer_ctx)],
):
    return await CompanyService().update_certificate(
        company_id, ctx.user.id, certificate_id, data
    )


@supplier_router.patch(
    "/{company_id}/certificates/{certificate_id}",
    response_model=CompanyCertificateSchema,
)
async def supplier_update_certificate(
    company_id: int,
    certificate_id: int,
    data: CertificateUpdateRequest,
    ctx: Annotated[SupplierContext, Depends(require_supplier_ctx)],
):
    return await CompanyService().update_certificate(
        company_id, ctx.user.id, certificate_id, data
    )


@buyer_router.delete("/{company_id}/certificates/{certificate_id}", status_code=204)
async def buyer_remove_certificate(
    company_id: int,
    certificate_id: int,
    ctx: Annotated[BuyerContext, Depends(require_buyer_ctx)],
):
    await CompanyService().remove_certificate(company_id, ctx.user.id, certificate_id)


@supplier_router.delete("/{company_id}/certificates/{certificate_id}", status_code=204)
async def supplier_remove_certificate(
    company_id: int,
    certificate_id: int,
    ctx: Annotated[SupplierContext, Depends(require_supplier_ctx)],
):
    await CompanyService().remove_certificate(company_id, ctx.user.id, certificate_id)


@buyer_router.patch("/{company_id}/team/{target_user_id}")
async def buyer_update_team_member(
    company_id: int,
    target_user_id: int,
    data: UpdateTeamMemberRequest,
    ctx: Annotated[BuyerContext, Depends(require_buyer_ctx)],
):
    return await CompanyService().update_team_member(
        company_id, ctx.user.id, target_user_id, data
    )


@supplier_router.patch("/{company_id}/team/{target_user_id}")
async def supplier_update_team_member(
    company_id: int,
    target_user_id: int,
    data: UpdateTeamMemberRequest,
    ctx: Annotated[SupplierContext, Depends(require_supplier_ctx)],
):
    return await CompanyService().update_team_member(
        company_id, ctx.user.id, target_user_id, data
    )


@buyer_router.delete("/{company_id}/team/{target_user_id}", status_code=204)
async def buyer_remove_team_member(
    company_id: int,
    target_user_id: int,
    ctx: Annotated[BuyerContext, Depends(require_buyer_ctx)],
):
    await CompanyService().remove_team_member(company_id, ctx.user.id, target_user_id)


@supplier_router.delete("/{company_id}/team/{target_user_id}", status_code=204)
async def supplier_remove_team_member(
    company_id: int,
    target_user_id: int,
    ctx: Annotated[SupplierContext, Depends(require_supplier_ctx)],
):
    await CompanyService().remove_team_member(company_id, ctx.user.id, target_user_id)
