from fastapi import APIRouter

from app.deps import require_buyer_ctx, require_supplier_ctx
from app.modules.contracts.schemas import DisputeRequest, MessageCreate, WorkSubmissionCreate
from app.modules.contracts.services import ContractService
from app.deps import BuyerContext, SupplierContext

buyer_router = APIRouter(prefix="/contracts", tags=["buyer-contracts"])
supplier_router = APIRouter(prefix="/contracts", tags=["supplier-contracts"])
