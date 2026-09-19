from datetime import datetime
from typing import Literal, Union

from pydantic import BaseModel, ConfigDict, Field

from app.utils.storage import FileUrlMixin


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
    team_size_required: int | None
    experience_required: str | None


class ProductRfqResponse(ProductRfqSchema):
    attachments: list[RfqAttachmentSchema] = []
    invited_supplier_ids: list[int] = []


class ServiceRfqResponse(ServiceRfqSchema):
    attachments: list[RfqAttachmentSchema] = []
    invited_supplier_ids: list[int] = []


RfqResponse = Union[ProductRfqResponse, ServiceRfqResponse]


class ProductRfqCreate(BaseModel):
    type: Literal["product"] = "product"
    title: str = Field(min_length=5)
    description: str | None = None
    category_id: str
    budget_type: str
    budget_from: float | None = None
    budget_to: float | None = None
    currency: str = "TJS"
    deadline: str
    visibility: str = "public"
    status: str | None = "draft"
    quantity: int = Field(ge=1)
    delivery_country: str
    delivery_city: str
    delivery_address: str | None = None
    delivery_date: str


class ServiceRfqCreate(BaseModel):
    type: Literal["service"] = "service"
    title: str = Field(min_length=5)
    description: str | None = None
    category_id: str
    budget_type: str
    budget_from: float | None = None
    budget_to: float | None = None
    currency: str = "TJS"
    deadline: str
    visibility: str = "public"
    status: str | None = "draft"
    project_duration: str
    start_date: str
    team_size_required: int | None = None
    experience_required: str | None = None


RfqCreate = Union[ProductRfqCreate, ServiceRfqCreate]


class InviteSuppliersRequest(BaseModel):
    supplier_ids: list[int]
