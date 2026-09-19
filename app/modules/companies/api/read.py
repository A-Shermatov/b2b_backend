from typing import Annotated

from fastapi import Depends, Query

from app.modules.companies.api.common import (
    BuyerContext,
    CompanyService,
    CompanyWithRelations,
    PublicSupplier,
    SupplierContext,
    buyer_router,
    public_router,
    require_buyer_ctx,
    require_supplier_ctx,
    supplier_router,
)


@buyer_router.get("/me", response_model=list[CompanyWithRelations])
async def buyer_my_companies(ctx: Annotated[BuyerContext, Depends(require_buyer_ctx)]):
    return await CompanyService().get_my_companies(ctx.user.id)


@supplier_router.get("/me", response_model=list[CompanyWithRelations])
async def supplier_my_companies(ctx: Annotated[SupplierContext, Depends(require_supplier_ctx)]):
    return await CompanyService().get_my_companies(ctx.user.id)


@public_router.get("/suppliers", response_model=list[PublicSupplier])
async def list_suppliers(
    q: str | None = Query(None),
    category: str | None = Query(None),
):
    return await CompanyService().list_suppliers(q, category)


@public_router.get("/suppliers/{actor_id}", response_model=PublicSupplier)
async def get_supplier(actor_id: int):
    return await CompanyService().get_public_supplier(actor_id)


@public_router.get("/companies/{company_id}", response_model=CompanyWithRelations)
async def get_company(company_id: int):
    return await CompanyService().get_company(company_id)
