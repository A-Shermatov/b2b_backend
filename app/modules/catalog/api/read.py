from typing import Annotated

import asyncpg
from fastapi import Depends, Query

from app.core.pagination import Page, build_page
from app.deps import PaginationDep, SupplierContext, raw_db_dependency, require_supplier_ctx
from app.modules.catalog.api.common import public_router, supplier_router
from app.modules.catalog.schemas import CatalogItemWithRelations, CategoryTree
from app.modules.catalog.services import CatalogItemService, CategoryService
from app.modules.companies.services import CompanyService


@public_router.get("/categories", response_model=list[CategoryTree])
async def list_categories(
    db: Annotated[asyncpg.Connection, Depends(raw_db_dependency)],
):
    return await CategoryService(db).get_tree()


@public_router.get("/suppliers/{actor_id}/catalog", response_model=Page[CatalogItemWithRelations])
async def supplier_catalog(actor_id: int, pagination: PaginationDep):
    await CompanyService().get_public_supplier(actor_id)
    return await CatalogItemService().list_active_for_company_actor(
        actor_id, pagination.page, pagination.page_size
    )


@public_router.get("/companies/{company_id}/catalog", response_model=Page[CatalogItemWithRelations])
async def company_catalog(company_id: int, pagination: PaginationDep):
    async with CatalogItemService().connection() as conn:
        actor_id = await conn.fetchval(
            """
            SELECT id FROM actors
            WHERE company_id = $1
              AND kind::text = 'company'
              AND side::text = 'supplier'
              AND is_active = TRUE
            """,
            company_id,
        )
    if not actor_id:
        return build_page([], 0, pagination.page, pagination.page_size)
    return await CatalogItemService().list_active_for_company_actor(
        actor_id, pagination.page, pagination.page_size
    )


@public_router.get("/catalog", response_model=Page[CatalogItemWithRelations])
async def public_catalog(
    pagination: PaginationDep,
    q: str | None = Query(None),
    category: str | None = Query(None),
):
    return await CatalogItemService().list_public(
        q, category, pagination.page, pagination.page_size
    )


@public_router.get("/catalog/items/{item_id}")
async def public_catalog_item(item_id: int):
    return await CatalogItemService().get_public(item_id)


@supplier_router.get("/items", response_model=Page[CatalogItemWithRelations])
async def list_items(
    ctx: Annotated[SupplierContext, Depends(require_supplier_ctx)],
    pagination: PaginationDep,
    status: str | None = Query(None),
):
    return await CatalogItemService().list_for_supplier(
        ctx.actor.id, status, pagination.page, pagination.page_size
    )


@supplier_router.get("/items/{item_id}", response_model=CatalogItemWithRelations)
async def get_item(
    item_id: int,
    ctx: Annotated[SupplierContext, Depends(require_supplier_ctx)],
):
    return await CatalogItemService().get(item_id, ctx.actor.id)
