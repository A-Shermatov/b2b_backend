from fastapi import APIRouter

from app.deps import raw_db_dependency, require_supplier_ctx
from app.modules.subscription.schemas import ActivateSubscriptionRequest, SubscriptionResponse
from app.modules.subscription.services import SubscriptionService
from app.deps import SupplierContext

supplier_router = APIRouter(prefix="/subscription", tags=["supplier-subscription"])
