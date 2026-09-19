from fastapi import APIRouter

from app.deps import raw_db_dependency, require_buyer_ctx
from app.modules.reviews.schemas import ReviewCreate, ReviewResponse
from app.modules.reviews.services import ReviewService
from app.deps import BuyerContext
from app.modules.companies.services import CompanyService

buyer_router = APIRouter(prefix="/reviews", tags=["buyer-reviews"])
public_router = APIRouter(tags=["public"])
