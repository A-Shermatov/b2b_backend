from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ReviewCreate(BaseModel):
    contract_id: int
    target_actor_id: int
    rating: int | None = Field(default=None, ge=1, le=5)
    service_rating: int | None = Field(default=None, ge=1, le=5)
    delivery_rating: int | None = Field(default=None, ge=1, le=5)
    quality_rating: int | None = Field(default=None, ge=1, le=5)
    tags: list[str] = Field(default_factory=list, max_length=12)
    comment: str | None = None

    @model_validator(mode="after")
    def resolve_rating(self):
        axes = [
            value
            for value in (self.service_rating, self.delivery_rating, self.quality_rating)
            if value is not None
        ]
        if self.rating is None:
            if not axes:
                raise ValueError("rating or axis ratings are required")
            self.rating = max(1, min(5, round(sum(axes) / len(axes))))
        return self


class ReviewResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    contract_id: int
    reviewer_actor_id: int
    target_actor_id: int
    rating: int
    service_rating: int | None = None
    delivery_rating: int | None = None
    quality_rating: int | None = None
    tags: list[str] = []
    comment: str | None
    created_at: datetime
