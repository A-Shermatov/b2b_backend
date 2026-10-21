from typing import Annotated

from fastapi import Depends

from app.deps import BuyerContext, buyer_perm
from app.modules.catalog.api.common import buyer_router
from app.modules.catalog.schemas import CatalogOfferCreate
from app.modules.catalog.services import CatalogItemService


@buyer_router.post("/items/{item_id}/offers")
async def create_catalog_offer(
    item_id: int,
    data: CatalogOfferCreate,
    ctx: Annotated[BuyerContext, Depends(buyer_perm("rfqs.create"))],
):
    return await CatalogItemService().create_offer(
        item_id,
        ctx.actor.id,
        str(ctx.user.id),
        data,
    )
