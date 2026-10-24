from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.enums import PaymentMilestoneTrigger, PaymentType
from app.utils.storage import FileUrlMixin


class ProposalAttachmentSchema(FileUrlMixin):
    model_config = ConfigDict(from_attributes=True)

    id: int
    proposal_id: int
    file_name: str
    file_url: str
    file_type: str


class ProposalSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    rfq_id: str
    supplier_actor_id: int
    price: float
    currency: str
    delivery_time: str | None
    message: str | None
    status: str
    created_at: datetime


class ProposalActorBrief(BaseModel):
    id: int
    name: str
    rating: float = 0
    photo: str | None = None


class ProposalItemBrief(BaseModel):
    name: str
    description: str | None = None
    photo: str | None = None


class ProposalWithRelations(ProposalSchema):
    attachment: ProposalAttachmentSchema | None = None
    actor: ProposalActorBrief | None = None
    item: ProposalItemBrief | None = None


class ProposalCreate(BaseModel):
    price: float = Field(gt=0)
    currency: str = "TJS"
    delivery_time: str | None = None
    message: str | None = None


class ProposalUpdate(BaseModel):
    price: float | None = Field(default=None, gt=0)
    currency: str | None = None
    delivery_time: str | None = None
    message: str | None = None


class MilestoneInput(BaseModel):
    title: str = Field(min_length=1)
    percentage: float = Field(gt=0, le=100)
    trigger: PaymentMilestoneTrigger


class ProposalMessageSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    proposal_id: int
    sender_id: int
    sender_name: str = ""
    text: str
    created_at: datetime


class ProposalMessageCreate(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class ProposalMessageThreadLastMessage(BaseModel):
    id: int
    text: str
    sender_id: int
    sender_name: str = ""
    created_at: datetime


class ProposalMessageThread(BaseModel):
    proposal_id: int
    rfq_id: str
    rfq_title: str
    status: str
    counterpart_actor_id: int
    counterpart_name: str
    message_count: int = 0
    last_message: ProposalMessageThreadLastMessage | None = None


class ProposalAcceptRequest(BaseModel):
    payment_type: PaymentType
    milestones: list[MilestoneInput] | None = None

    @model_validator(mode="after")
    def _validate_milestones(self) -> "ProposalAcceptRequest":
        if self.payment_type == PaymentType.milestone:
            if not self.milestones:
                raise ValueError("Milestones are required for milestone payment type")
            total = sum(m.percentage for m in self.milestones)
            if abs(total - 100) > 0.5:
                raise ValueError("Milestone percentages must sum to 100")
        else:
            self.milestones = None
        return self
