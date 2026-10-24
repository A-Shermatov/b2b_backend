from pydantic import BaseModel


class BuyerNavCounts(BaseModel):
    rfqs: int = 0
    deals: int = 0
    messages: int = 0
    approvals: int = 0


class SupplierNavCounts(BaseModel):
    rfqs: int = 0
    deals: int = 0
    messages: int = 0
    catalog: int = 0


class AdminNavCounts(BaseModel):
    companies: int = 0
    moderation: int = 0
    support: int = 0
    chats: int = 0
    disputes: int = 0
    finance: int = 0
