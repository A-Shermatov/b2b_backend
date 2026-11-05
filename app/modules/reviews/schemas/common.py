from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ReviewPhotoInput(BaseModel):
    file_name: str
    file_url: str


class ReviewMediaSchema(BaseModel):
    id: int | None = None
    file_name: str
    file_url: str
    sort_order: int = 0


class ReviewMediaUploadResponse(BaseModel):
    file_name: str
    file_url: str


class ReviewCreate(BaseModel):
    contract_id: int
    target_actor_id: int
    rating: int | None = Field(default=None, ge=1, le=5)
    service_rating: int | None = Field(default=None, ge=1, le=5)
    delivery_rating: int | None = Field(default=None, ge=1, le=5)
    quality_rating: int | None = Field(default=None, ge=1, le=5)
    tags: list[str] = Field(default_factory=list, max_length=12)
    comment: str | None = None
    photos: list[ReviewPhotoInput] = Field(default_factory=list, max_length=5)

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


class ReviewPartySchema(BaseModel):
    actor_id: int
    kind: str
    display_name: str
    company_title: str | None = None
    user_name: str | None = None


class ReviewAuthorSchema(BaseModel):
    """The person who wrote the review on behalf of the reviewing company."""

    user_id: int
    name: str | None = None
    role: str | None = None
    company_title: str | None = None


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
    photos: list[ReviewMediaSchema] = []
    helpful_count: int = 0
    viewer_voted: bool = False
    reviewer: ReviewPartySchema | None = None
    target: ReviewPartySchema | None = None
    author: ReviewAuthorSchema | None = None
