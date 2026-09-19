from datetime import datetime

from pydantic import BaseModel, ConfigDict


class NotificationSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    type: str
    title: str
    body: str
    href: str | None
    read: bool
    created_at: datetime
