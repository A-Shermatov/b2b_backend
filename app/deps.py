from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Annotated, Any

import asyncpg
from fastapi import Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import pool
from src.core.deps import (
    FINANCE_ROLES,
    FULL_COMPANY_ROLES,
    RFQ_ROLES,
    AuthContext,
    BuyerContext,
    SupplierContext,
    get_auth_context,
    get_client_ip,
    get_current_user,
    require_actor,
    require_admin,
    require_buyer_company_roles,
    require_buyer_ctx,
    require_company_roles,
    require_supplier_ctx,
    security,
)
from src.db.session import get_db


@dataclass(frozen=True)
class Pagination:
    page: int
    page_size: int

    @property
    def offset(self) -> int:
        return max(0, (self.page - 1) * self.page_size)


def pagination_dependency(
    page: int = Query(1, ge=1, description="Номер страницы (с 1)"),
    page_size: int = Query(20, ge=1, le=100, description="Элементов на странице"),
) -> Pagination:
    return Pagination(page=page, page_size=page_size)


PaginationDep = Annotated[Pagination, Depends(pagination_dependency)]


async def db_dependency() -> AsyncGenerator[AsyncSession, Any]:
    async for session in get_db():
        yield session


async def raw_db_dependency() -> AsyncGenerator[asyncpg.Connection, Any]:
    if not pool.ready:
        await pool.init()
    async with pool.acquire() as conn:
        yield conn


__all__ = [
    "AuthContext",
    "BuyerContext",
    "SupplierContext",
    "FINANCE_ROLES",
    "FULL_COMPANY_ROLES",
    "RFQ_ROLES",
    "security",
    "get_current_user",
    "get_auth_context",
    "require_actor",
    "require_company_roles",
    "require_buyer_ctx",
    "require_buyer_company_roles",
    "require_supplier_ctx",
    "require_admin",
    "get_client_ip",
    "Pagination",
    "PaginationDep",
    "pagination_dependency",
    "db_dependency",
    "raw_db_dependency",
]
