from fastapi import APIRouter

from app.deps import BuyerContext, CurrentUser, SupplierContext
from app.modules.support.schemas import (
    SupportMessageSchema,
    SupportTicketDetail,
    SupportTicketListItem,
    SupportTicketResponse,
    SupportTicketSchema,
    UnreadCountResponse,
)
from app.modules.support.services import SupportService

buyer_router = APIRouter(prefix="/support", tags=["buyer-support"])
supplier_router = APIRouter(prefix="/support", tags=["supplier-support"])
admin_router = APIRouter(prefix="/support", tags=["admin-support"])
