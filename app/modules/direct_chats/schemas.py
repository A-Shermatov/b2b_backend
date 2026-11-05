from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class DirectMessageCreate(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class DirectChatOpenBuyer(BaseModel):
    supplier_actor_id: int


class DirectChatOpenSupplier(BaseModel):
    buyer_actor_id: int


class DirectMessageSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    conversation_id: int
    sender_id: int
    sender_name: str = ""
    # The sender speaks for this company, in this role (director, admin, moderator, accountant).
    sender_company: str | None = None
    sender_role: str | None = None
    text: str
    created_at: datetime | str | None = None
    status: str = "sent"
    delivered_at: datetime | str | None = None
    viewed_at: datetime | str | None = None


class DirectChatThread(BaseModel):
    id: int
    buyer_actor_id: int
    supplier_actor_id: int
    counterpart_actor_id: int
    counterpart_name: str
    unread_count: int = 0
    last_message: DirectMessageSchema | None = None
    messages: list[DirectMessageSchema] = []
