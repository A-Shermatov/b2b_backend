from fastapi import APIRouter

from app.deps import SupplierContext, raw_db_dependency, require_supplier_ctx
from app.modules.subscription.schemas import (
    ActivateSubscriptionRequest,
    SubscriptionCheckoutResponse,
    SubscriptionOrderSyncResponse,
    SubscriptionPaymentConfig,
    SubscriptionResponse,
)
from app.modules.subscription.services import SubscriptionService

supplier_router = APIRouter(prefix="/subscription", tags=["supplier-subscription"])
