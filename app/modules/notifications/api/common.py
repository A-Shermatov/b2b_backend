from fastapi import APIRouter

from app.deps import raw_db_dependency, require_buyer_ctx, require_supplier_ctx
from app.modules.notifications.schemas import NotificationSchema
from app.modules.notifications.services import NotificationService
from app.deps import BuyerContext, SupplierContext

buyer_router = APIRouter(prefix="/notifications", tags=["buyer-notifications"])
supplier_router = APIRouter(prefix="/notifications", tags=["supplier-notifications"])
