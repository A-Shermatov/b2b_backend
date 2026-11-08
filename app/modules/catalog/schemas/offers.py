from typing import Literal

from pydantic import BaseModel, Field


class CatalogOfferCreate(BaseModel):
    # buy: at the published price; negotiate: the buyer names a price for the
    # whole order; quote: the supplier is asked to name the price.
    kind: Literal["buy", "negotiate", "quote"]
    price: float | None = None
    message: str | None = Field(default=None, max_length=4000)
    qty: int = Field(default=1, ge=1, le=99999)
    payment_mode: Literal["escrow", "direct"] = "escrow"


class CatalogOfferResponse(BaseModel):
    rfq_id: str
    # A price request has no offer yet: the supplier answers with one.
    proposal_id: int | None
    kind: Literal["buy", "negotiate", "quote"]
    price: float | None
    currency: str
    qty: int = 1
    payment_mode: Literal["escrow", "direct"] = "escrow"
