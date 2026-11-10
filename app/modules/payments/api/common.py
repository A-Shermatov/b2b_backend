from fastapi import APIRouter

from app.deps import require_buyer_ctx, require_supplier_ctx
from app.modules.payments.services import PaymentService
from app.deps import BuyerContext, SupplierContext

buyer_router = APIRouter(prefix="/payments", tags=["buyer-payments"])
supplier_router = APIRouter(prefix="/payments", tags=["supplier-payments"])
public_router = APIRouter(prefix="/payments", tags=["payment-callbacks"])
