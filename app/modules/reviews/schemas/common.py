from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ReviewCreate(BaseModel):
    contract_id: int
    target_actor_id: int
    rating: int = Field(ge=1, le=5)
    comment: str | None = None


class ReviewResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    contract_id: int
    reviewer_actor_id: int
    target_actor_id: int
    rating: int
    comment: str | None
    created_at: datetime
