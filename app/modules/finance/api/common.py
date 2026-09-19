from fastapi import APIRouter

from app.deps import require_supplier_ctx
from app.modules.finance.schemas import (
    InvoiceSchema,
    WithdrawalCreate,
    WithdrawalDestinationCreate,
    WithdrawalDestinationSchema,
    WithdrawalSchema,
)
from app.modules.finance.services import SupplierFinanceService
from app.deps import SupplierContext

supplier_router = APIRouter(prefix="/finance", tags=["supplier-finance"])
