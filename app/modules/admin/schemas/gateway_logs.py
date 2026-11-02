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
    external_transaction_id: str | None = None
    amount: float | None = None
    currency: str | None = None


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
    # Linked gateway transaction — for reconciliation with the bank
    external_transaction_id: str | None = None
    gateway_txn_status: str | None = None
    amount: float | None = None
    currency: str | None = None
    paid_at: datetime | None = None


class AdminGatewayLogListResponse(BaseModel):
    items: list[AdminGatewayLogItem]
    total: int
    page: int
    page_size: int
    pages: int


class AdminGatewayLogExportItem(AdminGatewayLogDetail):
    """Full log row for bank recon export (same fields as detail)."""


class AdminGatewayLogExportResponse(BaseModel):
    generated_at: datetime
    filters: dict[str, Any]
    total: int
    items: list[AdminGatewayLogExportItem]

