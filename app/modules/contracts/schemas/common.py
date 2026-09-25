from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer

from app.utils.storage import FileUrlMixin, public_file_url


class PaymentMilestoneSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    contract_id: int
    title: str
    percentage: float
    amount: float
    trigger: str
    status: str


class PaymentPlanSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    contract_id: int
    payment_type: str
    milestones: list[PaymentMilestoneSchema] = []


class MessageAttachmentSchema(FileUrlMixin):
    model_config = ConfigDict(from_attributes=True)

    id: int
    message_id: int
    file_name: str
    file_url: str
    file_type: str


class MessageSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    conversation_id: int
    sender_id: int
    sender_name: str = ""
    text: str
    attachment: MessageAttachmentSchema | None = None
    created_at: datetime | None = None
    status: str = "sent"
    delivered_at: datetime | None = None
    viewed_at: datetime | None = None


class ConversationSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    contract_id: int
    messages: list[MessageSchema] = []


class ContractFileSchema(FileUrlMixin):
    model_config = ConfigDict(from_attributes=True)

    id: int
    contract_id: int
    file_name: str
    file_url: str
    file_type: str
    uploaded_by: int
    uploaded_by_name: str
    uploaded_by_side: Literal["buyer", "supplier"] | None = None
    created_at: datetime


class SubmissionAssetSchema(BaseModel):
    kind: str
    name: str
    url: str
    file_type: str | None = None

    @field_serializer("url")
    def serialize_url(self, value: str) -> str:
        return public_file_url(value)


class WorkSubmissionSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    contract_id: int
    type: str
    note: str
    status: str
    submitted_at: datetime
    file_names: list[str]
    assets: list[SubmissionAssetSchema] = []


class ContractSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    rfq_id: str
    proposal_id: int
    buyer_actor_id: int
    supplier_actor_id: int
    title: str
    description: str | None
    agreed_amount: float
    currency: str
    start_date: str
    due_date: str
    payment_type: str
    status: str
    created_at: datetime


class ContractWithRelations(ContractSchema):
    payment_plan: PaymentPlanSchema | None = None
    conversation: ConversationSchema | None = None
    files: list[ContractFileSchema] = []
    submissions: list[WorkSubmissionSchema] = []


class MessageCreate(BaseModel):
    text: str = Field(min_length=1)


class SubmissionAssetCreate(BaseModel):
    kind: str = Field(pattern="^(image|video|file|link)$")
    name: str = Field(min_length=1)
    url: str = Field(min_length=1)
    file_type: str | None = None


class WorkSubmissionCreate(BaseModel):
    type: str = "delivery"
    note: str = ""
    file_names: list[str] = []
    assets: list[SubmissionAssetCreate] = []


class DisputeRequest(BaseModel):
    reason: str = Field(min_length=10)
