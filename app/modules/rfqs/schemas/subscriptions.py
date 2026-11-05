from pydantic import BaseModel, Field


class RfqCategorySubscriptions(BaseModel):
    """Categories in which an employee wants to hear about new requests."""

    category_ids: list[int] = Field(default_factory=list, max_length=100)
