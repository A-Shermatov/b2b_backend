from typing import Annotated

from fastapi import Depends

from app.modules.companies.api.common import (
    BuyerContext,
    CompanyService,
    CompanyUpdateRequest,
    CompanyWithRelations,
    SupplierContext,
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
