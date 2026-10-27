from fastapi import APIRouter

from app.deps import BuyerContext, raw_db_dependency, require_buyer_ctx
from app.modules.reviews.schemas import ReviewCreate, ReviewResponse
from app.modules.reviews.services import ReviewService
from app.modules.companies.services import CompanyService

buyer_router = APIRouter(prefix="/reviews", tags=["buyer-reviews"])
supplier_router = APIRouter(prefix="/reviews", tags=["supplier-reviews"])
public_router = APIRouter(tags=["public"])
