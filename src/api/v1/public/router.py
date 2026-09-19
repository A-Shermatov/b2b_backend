from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.session import get_db
from src.modules.catalog.schemas import CategoryTree
from src.modules.catalog.service import CategoryService
from src.modules.companies.schemas import CompanyWithRelations, PublicSupplier
from src.modules.companies.service import CompanyService
from src.modules.catalog_items.service import CatalogItemService
from src.modules.rfqs.service import RfqService

router = APIRouter(prefix="/public", tags=["public"])


@router.get("/categories", response_model=list[CategoryTree])
async def list_categories(db: Annotated[AsyncSession, Depends(get_db)]):
    return await CategoryService(db).get_tree()


@router.get("/suppliers", response_model=list[PublicSupplier])
async def list_suppliers(
    db: Annotated[AsyncSession, Depends(get_db)],
    q: str | None = Query(None),
    category: str | None = Query(None),
):
    return await CompanyService(db).list_suppliers(q, category)


@router.get("/suppliers/{actor_id}", response_model=PublicSupplier)
async def get_supplier(
    actor_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    return await CompanyService(db).get_public_supplier(actor_id)


@router.get("/suppliers/{actor_id}/catalog")
async def supplier_catalog(
    actor_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    await CompanyService(db).get_public_supplier(actor_id)
    return await CatalogItemService(db).list_active_for_company_actor(actor_id)


@router.get("/companies/{company_id}", response_model=CompanyWithRelations)
async def get_company(
    company_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    return await CompanyService(db).get_company(company_id)


@router.get("/companies/{company_id}/catalog")
async def company_catalog(
    company_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    from sqlalchemy import select

    from src.models import Actor, ActorKind, ActorType

    result = await db.execute(
        select(Actor).where(
            Actor.company_id == company_id,
            Actor.kind == ActorKind.company,
            Actor.side == ActorType.supplier,
            Actor.is_active.is_(True),
        )
    )
    actor = result.scalar_one_or_none()
    if not actor:
        return []
    return await CatalogItemService(db).list_active_for_company_actor(actor.id)


@router.get("/catalog")
async def public_catalog(
    db: Annotated[AsyncSession, Depends(get_db)],
    q: str | None = Query(None),
    category: str | None = Query(None),
):
    return await CatalogItemService(db).list_public(q, category)


@router.get("/catalog/items/{item_id}")
async def public_catalog_item(
    item_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    return await CatalogItemService(db).get_public(item_id)


@router.get("/rfqs")
async def public_rfqs(db: Annotated[AsyncSession, Depends(get_db)]):
    return await RfqService(db).list_public_board()
