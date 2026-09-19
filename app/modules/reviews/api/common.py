from typing import Annotated

import asyncpg
from fastapi import APIRouter, Depends

from app.deps import raw_db_dependency, require_buyer_ctx
from app.modules.reviews.schemas import ReviewCreate, ReviewResponse
from app.modules.reviews.services import ReviewService
from src.core.deps import BuyerContext
from src.db.session import get_db
from src.modules.companies.service import CompanyService

buyer_router = APIRouter(prefix="/reviews", tags=["buyer-reviews"])
public_router = APIRouter(tags=["public"])
