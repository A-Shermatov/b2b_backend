from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel

AdminGatewayLogPeriod = Literal["24h", "7d", "30d", "all"]
AdminGatewayLogDirection = Literal["outbound", "inbound"]
AdminGatewayPaymentKind = Literal["milestone", "subscription", "unknown"]


class AdminGatewayLogItem(BaseModel):
    id: int
    created_at: datetime
    responded_at: datetime | None = None
    gateway: str
    direction: AdminGatewayLogDirection
    operation: str
    order_id: str | None = None
    method: str | None = None
    url: str | None = None
    response_status: int | None = None
    error: str | None = None


class AdminGatewayLogDetail(AdminGatewayLogItem):
    request_headers: Any = None
    request_body: Any = None
    response_body: Any = None
    response_raw: str | None = None
    payment_kind: AdminGatewayPaymentKind = "unknown"
    milestone_id: int | None = None
    contract_id: int | None = None
    subscription_user_id: int | None = None
    plan: str | None = None


class AdminGatewayLogListResponse(BaseModel):
    items: list[AdminGatewayLogItem]
    total: int
    page: int
    page_size: int
    pages: int
