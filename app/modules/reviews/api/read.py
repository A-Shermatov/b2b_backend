from typing import Annotated

import asyncpg
from fastapi import Depends

from app.core.pagination import Page
from app.deps import PaginationDep
from app.deps import SupplierContext, require_supplier_ctx
from app.modules.reviews.api.common import (
    BuyerContext,
    CompanyService,
    ReviewResponse,
    ReviewService,
    buyer_router,
    public_router,
    raw_db_dependency,
    require_buyer_ctx,
    supplier_router,
)


@buyer_router.get("/", response_model=Page[ReviewResponse])
async def list_my_buyer_reviews(
    ctx: Annotated[BuyerContext, Depends(require_buyer_ctx)],
    db: Annotated[asyncpg.Connection, Depends(raw_db_dependency)],
    pagination: PaginationDep,
):
    return await ReviewService(db).list_for_reviewer(
        ctx.actor.id, pagination.page, pagination.page_size
    )


@buyer_router.get("/received", response_model=Page[ReviewResponse])
async def list_buyer_received_reviews(
    ctx: Annotated[BuyerContext, Depends(require_buyer_ctx)],
    db: Annotated[asyncpg.Connection, Depends(raw_db_dependency)],
    pagination: PaginationDep,
):
    return await ReviewService(db).list_for_actor(
        ctx.actor.id, pagination.page, pagination.page_size
    )


@supplier_router.get("/", response_model=Page[ReviewResponse])
async def list_my_supplier_reviews(
    ctx: Annotated[SupplierContext, Depends(require_supplier_ctx)],
    db: Annotated[asyncpg.Connection, Depends(raw_db_dependency)],
    pagination: PaginationDep,
):
    return await ReviewService(db).list_for_reviewer(
        ctx.actor.id, pagination.page, pagination.page_size
    )


@supplier_router.get("/received", response_model=Page[ReviewResponse])
async def list_supplier_received_reviews(
    ctx: Annotated[SupplierContext, Depends(require_supplier_ctx)],
    db: Annotated[asyncpg.Connection, Depends(raw_db_dependency)],
    pagination: PaginationDep,
):
    return await ReviewService(db).list_for_actor(
        ctx.actor.id, pagination.page, pagination.page_size
    )


@public_router.get("/suppliers/{actor_id}/reviews", response_model=Page[ReviewResponse])
async def supplier_reviews(
    actor_id: int,
    db: Annotated[asyncpg.Connection, Depends(raw_db_dependency)],
    pagination: PaginationDep,
):
    await CompanyService().get_public_supplier(actor_id)
    return await ReviewService(db).list_for_actor(actor_id, pagination.page, pagination.page_size)


@public_router.get("/buyers/{actor_id}/reviews", response_model=Page[ReviewResponse])
async def buyer_reviews(
    actor_id: int,
    db: Annotated[asyncpg.Connection, Depends(raw_db_dependency)],
    pagination: PaginationDep,
):
    await CompanyService().get_public_buyer(actor_id)
    return await ReviewService(db).list_for_actor(actor_id, pagination.page, pagination.page_size)


@public_router.get("/companies/{company_id}/reviews", response_model=Page[ReviewResponse])
async def company_reviews(
    company_id: int,
    db: Annotated[asyncpg.Connection, Depends(raw_db_dependency)],
    pagination: PaginationDep,
):
    return await ReviewService(db).list_for_company(
        company_id, pagination.page, pagination.page_size
    )
