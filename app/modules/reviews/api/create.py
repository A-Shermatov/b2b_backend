from typing import Annotated

import asyncpg
from fastapi import Depends, File, UploadFile

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
from app.modules.reviews.schemas import ReviewMediaUploadResponse
from app.utils.storage import storage_service


@buyer_router.post("/media", response_model=ReviewMediaUploadResponse)
async def upload_buyer_review_media(
    ctx: Annotated[BuyerContext, Depends(buyer_perm("reviews.create"))],
    file: UploadFile = File(...),
):
    key, _ = await storage_service.upload(file, f"reviews/{ctx.actor.id}")
    return ReviewMediaUploadResponse(
        file_name=file.filename or "photo.jpg",
        file_url=key,
    )


@supplier_router.post("/media", response_model=ReviewMediaUploadResponse)
async def upload_supplier_review_media(
    ctx: Annotated[SupplierContext, Depends(supplier_perm("reviews.create"))],
    file: UploadFile = File(...),
):
    key, _ = await storage_service.upload(file, f"reviews/{ctx.actor.id}")
    return ReviewMediaUploadResponse(
        file_name=file.filename or "photo.jpg",
        file_url=key,
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


@buyer_router.post("/{review_id}/helpful", response_model=ReviewResponse)
async def buyer_vote_review_helpful(
    review_id: int,
    ctx: Annotated[BuyerContext, Depends(buyer_perm("reviews.view"))],
    db: Annotated[asyncpg.Connection, Depends(raw_db_dependency)],
):
    return await ReviewService(db).vote_helpful(review_id, ctx.actor.id)


@supplier_router.post("/{review_id}/helpful", response_model=ReviewResponse)
async def supplier_vote_review_helpful(
    review_id: int,
    ctx: Annotated[SupplierContext, Depends(supplier_perm("reviews.view"))],
    db: Annotated[asyncpg.Connection, Depends(raw_db_dependency)],
):
    return await ReviewService(db).vote_helpful(review_id, ctx.actor.id)
