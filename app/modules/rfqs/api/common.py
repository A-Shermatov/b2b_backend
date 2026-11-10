from fastapi import APIRouter

from app.deps import (
    require_buyer_ctx,
    require_supplier_ctx,
)
from app.modules.rfqs.schemas import (
    InviteSuppliersRequest,
    ProductRfqCreate,
    RfqUpdate,
    ServiceRfqCreate,
)
from app.modules.rfqs.services import RfqService
from app.deps import BuyerContext, SupplierContext

buyer_router = APIRouter(prefix="/rfqs", tags=["buyer-rfqs"])
supplier_router = APIRouter(prefix="/rfqs", tags=["supplier-rfqs"])
public_router = APIRouter(tags=["public"])
