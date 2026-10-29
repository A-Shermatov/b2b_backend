from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

IndividualVerificationStatus = Literal["none", "pending", "needs_documents", "verified", "rejected"]
IndividualDocumentType = Literal["passport", "selfie", "patent", "other"]


class IndividualDocument(BaseModel):
    id: int
    doc_type: str
    doc_type_label: str
    file_name: str | None = None
    file_url: str | None = None
    status: str
    uploaded_at: datetime
    uploaded_by_admin: bool = False


class IndividualVerificationState(BaseModel):
    user_id: int
    status: IndividualVerificationStatus
    status_label: str
    reason: str | None = None
    submitted_at: datetime | None = None
    verified_at: datetime | None = None
    sides: list[str] = Field(default_factory=list)
    is_individual: bool = False
    documents: list[IndividualDocument] = Field(default_factory=list)
    can_edit_documents: bool = False
    can_upload: bool = False
    can_submit: bool = False
