from typing import Annotated

import asyncpg
from fastapi import Depends

from app.modules.reviews.api.common import (
    BuyerContext,
    ReviewCreate,
    ReviewResponse,
    ReviewService,
    buyer_router,
    raw_db_dependency,
    require_buyer_ctx,
)


@buyer_router.post("/", response_model=ReviewResponse)
async def create_review(
    data: ReviewCreate,
    ctx: Annotated[BuyerContext, Depends(require_buyer_ctx)],
    db: Annotated[asyncpg.Connection, Depends(raw_db_dependency)],
):
    return await ReviewService(db).create(ctx.actor.id, data)
