import re
from datetime import date, datetime, time
from typing import Literal, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.utils.storage import FileUrlMixin


# A request date as the buyer set it: a day, or a day with hours and minutes.
_REQUEST_DATE = re.compile(r"^(\d{4}-\d{2}-\d{2})(?:[T ](\d{2}:\d{2})(?::\d{2})?)?$")
REQUEST_DATE_HINT = "Дата: ГГГГ-ММ-ДД или ГГГГ-ММ-ДДTЧЧ:ММ"
TITLE_MAX_LENGTH = 500


def clean_request_date(value: str | None) -> str | None:
    """'2026-11-20' stays as is; '2026-11-20 18:30:00' becomes '2026-11-20T18:30'.

    The time is wall-clock time of the platform's market (see
    settings.business_timezone), so the value carries no zone. A day without a
    time means "through the end of that day".
    """
    if value is None:
        return None
    match = _REQUEST_DATE.match(str(value).strip())
    if not match:
        raise ValueError(REQUEST_DATE_HINT)
    day, clock = match.groups()
    try:
        date.fromisoformat(day)
        if clock:
            time.fromisoformat(clock)
    except ValueError as exc:
        raise ValueError(REQUEST_DATE_HINT) from exc
    return f"{day}T{clock}" if clock else day


def clean_title(value: str | None) -> str | None:
    """Any length the buyer likes, as long as there is something to read."""
    if value is None:
        return None
    title = " ".join(str(value).split())
    if not title:
        raise ValueError("Напишите название заявки")
    if len(title) > TITLE_MAX_LENGTH:
        raise ValueError(f"Название длиннее {TITLE_MAX_LENGTH} символов")
    return title


class _RequestFieldsMixin(BaseModel):
    """Shared cleaning for what the buyer types into a request."""

    @field_validator("title", mode="before", check_fields=False)
    @classmethod
    def _title(cls, value):
        return clean_title(value)

    @field_validator("deadline", "delivery_date", "start_date", mode="before", check_fields=False)
    @classmethod
    def _dates(cls, value):
        return clean_request_date(value)


class RfqAttachmentSchema(FileUrlMixin):
    model_config = ConfigDict(from_attributes=True)

    id: str
    rfq_id: str
    file_name: str
    file_url: str
    file_type: str


class RfqBaseSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    actor_id: str
    created_by: str
    # Role of the person who wrote the request in the buying company, if any.
    contact_role: str | None = None
    title: str
    description: str | None
    category_id: str
    budget_type: str
    budget_from: float | None
    budget_to: float | None
    currency: str
    deadline: str
    visibility: str
    status: str
    payment_mode: Literal["escrow", "direct"] = "escrow"
    # Set when the buyer moved the request to the archive.
    archived_at: datetime | None = None
    # Why the buyer cancelled the request, if they said.
    cancel_reason: str | None = None
    # Set when a moderator hid or closed the RFQ.
    moderation_reason: str | None = None
    moderated_at: datetime | None = None
    # The buyer holds a paid plan: the request stands first in lists and is highlighted.
    promoted: bool = False
    promotion_plan: str | None = None
    created_at: datetime
    updated_at: datetime


class ProductRfqSchema(RfqBaseSchema):
    type: Literal["product"]
    quantity: int
    delivery_country: str
    delivery_city: str
    delivery_address: str | None
    delivery_date: str


class ServiceRfqSchema(RfqBaseSchema):
    type: Literal["service"]
    project_duration: str
    start_date: str
    # When the work must be finished; requests made before this field have none.
    delivery_date: str | None = None
    team_size_required: int | None
    experience_required: str | None


class ProductRfqResponse(ProductRfqSchema):
    attachments: list[RfqAttachmentSchema] = []
    invited_supplier_ids: list[int] = []


class ServiceRfqResponse(ServiceRfqSchema):
    attachments: list[RfqAttachmentSchema] = []
    invited_supplier_ids: list[int] = []


RfqResponse = Union[ProductRfqResponse, ServiceRfqResponse]


class ProductRfqCreate(_RequestFieldsMixin):
    type: Literal["product"] = "product"
    title: str
    description: str | None = None
    category_id: str
    budget_type: str
    budget_from: float | None = None
    budget_to: float | None = None
    currency: str = "TJS"
    deadline: str
    visibility: str = "public"
    status: str | None = "draft"
    invited_supplier_ids: list[int] = Field(default_factory=list, max_length=50)
    payment_mode: Literal["escrow", "direct"] = "escrow"
    quantity: int = Field(ge=1)
    delivery_country: str
    delivery_city: str
    delivery_address: str | None = None
    delivery_date: str


class ServiceRfqCreate(_RequestFieldsMixin):
    type: Literal["service"] = "service"
    title: str
    description: str | None = None
    category_id: str
    budget_type: str
    budget_from: float | None = None
    budget_to: float | None = None
    currency: str = "TJS"
    deadline: str
    visibility: str = "public"
    status: str | None = "draft"
    invited_supplier_ids: list[int] = Field(default_factory=list, max_length=50)
    payment_mode: Literal["escrow", "direct"] = "escrow"
    project_duration: str
    start_date: str
    # The work deadline: when the result must be ready.
    delivery_date: str | None = None
    team_size_required: int | None = None
    experience_required: str | None = None


RfqCreate = Union[ProductRfqCreate, ServiceRfqCreate]


class RfqUpdate(_RequestFieldsMixin):
    title: str | None = None
    description: str | None = None
    category_id: str | None = None
    budget_type: str | None = None
    budget_from: float | None = None
    budget_to: float | None = None
    currency: str | None = None
    deadline: str | None = None
    visibility: Literal["public", "invited_only"] | None = None
    payment_mode: Literal["escrow", "direct"] | None = None
    quantity: int | None = Field(default=None, ge=1)
    delivery_country: str | None = None
    delivery_city: str | None = None
    delivery_address: str | None = None
    delivery_date: str | None = None
    project_duration: str | None = None
    start_date: str | None = None
    team_size_required: int | None = None
    experience_required: str | None = None


class RfqCloseRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


class InviteSuppliersRequest(BaseModel):
    supplier_ids: list[int] = Field(min_length=1, max_length=50)
