from typing import Annotated

import asyncpg
from fastapi import Depends

from app.deps import SupplierContext, buyer_perm, supplier_perm
from app.modules.reviews.api.common import (
    BuyerContext,
    ReviewCreate,
    ReviewResponse,
    ReviewService,
    buyer_router,
    raw_db_dependency,
    supplier_router,
)


@buyer_router.post("/", response_model=ReviewResponse)
async def create_buyer_review(
    data: ReviewCreate,
    ctx: Annotated[BuyerContext, Depends(buyer_perm("reviews.create"))],
    db: Annotated[asyncpg.Connection, Depends(raw_db_dependency)],
):
    return await ReviewService(db).create(ctx.actor.id, data)


@supplier_router.post("/", response_model=ReviewResponse)
async def create_supplier_review(
    data: ReviewCreate,
    ctx: Annotated[SupplierContext, Depends(supplier_perm("reviews.create"))],
    db: Annotated[asyncpg.Connection, Depends(raw_db_dependency)],
):
    return await ReviewService(db).create(ctx.actor.id, data)
