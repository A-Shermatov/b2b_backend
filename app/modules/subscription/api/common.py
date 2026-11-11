from fastapi import APIRouter

from app.deps import (
    BuyerContext,
    SupplierContext,
    raw_db_dependency,
    require_buyer_ctx,
    require_supplier_ctx,
)
from app.modules.subscription.schemas import (
    ActivateSubscriptionRequest,
    SubscriptionCheckoutResponse,
    SubscriptionOrderSyncResponse,
    SubscriptionPaymentConfig,
    SubscriptionResponse,
)
from app.modules.subscription.services import SubscriptionService

supplier_router = APIRouter(prefix="/subscription", tags=["supplier-subscription"])
# The same plans for the buying cabinet, bought and held separately.
buyer_router = APIRouter(prefix="/subscription", tags=["buyer-subscription"])
