import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

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
    commission_amount: float = 0.0


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
    # The sender speaks for this company, in this role (director, admin, moderator, accountant).
    sender_company: str | None = None
    sender_role: str | None = None
    # Public link to the sender's photo, if they uploaded one.
    sender_avatar: str | None = None
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
    # Company title, or the person's name for an individual account.
    buyer_name: str | None = None
    supplier_name: str | None = None
    title: str
    description: str | None
    agreed_amount: float
    currency: str
    start_date: str
    due_date: str
    payment_type: str
    # "escrow": the bank holds the money until acceptance; "direct": paid out at once.
    payment_mode: Literal["escrow", "direct"] = "escrow"
    status: str
    created_at: datetime
    commission_amount: float = 0.0
    commission_percent: float | None = None
    commission_min: float | None = None


class ContractWithRelations(ContractSchema):
    payment_plan: PaymentPlanSchema | None = None
    conversation: ConversationSchema | None = None
    files: list[ContractFileSchema] = []
    submissions: list[WorkSubmissionSchema] = []


class MessageCreate(BaseModel):
    text: str = Field(min_length=1, max_length=5000)


_URL_SCHEME = re.compile(r"^\s*([a-zA-Z][a-zA-Z0-9+.-]*):")


class SubmissionAssetCreate(BaseModel):
    kind: str = Field(pattern="^(image|video|file|link)$")
    name: str = Field(min_length=1, max_length=255)
    url: str = Field(min_length=1, max_length=2000)
    file_type: str | None = None

    @field_validator("url")
    @classmethod
    def url_is_web_or_ours(cls, value: str) -> str:
        """The other party opens this link: only http(s) or a path to our own file.

        javascript:, data:, file: and similar schemes are refused.
        """
        match = _URL_SCHEME.match(value)
        if match and match.group(1).lower() not in ("http", "https"):
            raise ValueError("Ссылка должна начинаться с http:// или https://")
        return value.strip()


class WorkSubmissionCreate(BaseModel):
    type: str = "delivery"
    note: str = Field(default="", max_length=5000)
    file_names: list[str] = Field(default_factory=list, max_length=50)
    assets: list[SubmissionAssetCreate] = Field(default_factory=list, max_length=50)


class DisputeRequest(BaseModel):
    reason: str = Field(min_length=10, max_length=5000)


class DisputeStatementRequest(BaseModel):
    text: str = Field(min_length=10, max_length=5000)
