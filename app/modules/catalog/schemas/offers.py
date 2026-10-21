from typing import Literal

from pydantic import BaseModel, Field


class CatalogOfferCreate(BaseModel):
    kind: Literal["buy", "negotiate"]
    price: float | None = None
    message: str | None = Field(default=None, max_length=4000)


class CatalogOfferResponse(BaseModel):
    rfq_id: str
    proposal_id: int
    kind: Literal["buy", "negotiate"]
    price: float
    currency: str
