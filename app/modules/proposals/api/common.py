from fastapi import APIRouter

from app.deps import require_buyer_ctx, require_supplier_ctx
from app.modules.proposals.schemas import (
    ProposalAcceptRequest,
    ProposalMessageCreate,
    ProposalUpdate,
)
from app.modules.proposals.services import ProposalService
from app.deps import BuyerContext, SupplierContext

buyer_router = APIRouter(tags=["buyer-proposals"])
supplier_router = APIRouter(prefix="/proposals", tags=["supplier-proposals"])
