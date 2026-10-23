from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.utils.storage import FileUrlMixin


class SupportAttachmentSchema(FileUrlMixin):
    model_config = ConfigDict(from_attributes=True)

    id: int
    message_id: int
    file_name: str
    file_url: str
    file_type: str


class SupportMessageSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    ticket_id: int
    sender_id: int
    sender_name: str | None = None
    text: str
    attachment: SupportAttachmentSchema | None = None
    created_at: datetime | None = None
    status: str | None = "sent"
    delivered_at: datetime | None = None
    viewed_at: datetime | None = None


class SupportTicketSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    side: str
    status: str
    assigned_admin_id: int | None
    last_message_at: datetime | None
    created_at: datetime
    closed_at: datetime | None
    unread_count: int = 0
    messages: list[SupportMessageSchema] = []


class SupportTicketResponse(BaseModel):
    ticket: SupportTicketSchema | None


class SupportTicketListItem(BaseModel):
    id: int
    user_id: int
    user_name: str
    user_email: str
    side: str
    status: str
    assigned_admin_id: int | None
    last_message_at: datetime | None
    created_at: datetime
    closed_at: datetime | None
    unread_count: int = 0
    last_message_preview: str = ""


class SupportTicketDetail(SupportTicketListItem):
    messages: list[SupportMessageSchema] = []


class UnreadCountResponse(BaseModel):
    count: int
