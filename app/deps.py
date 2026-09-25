from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Annotated, Any

import asyncpg
from fastapi import Depends, Header, Query, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.exceptions import ForbiddenError, UnauthorizedError
from app.core.permissions import ensure_permission, fetch_permission_names
from app.core.security_core import decode_token
from app.database import pool
from app.modules.actors.services import ActorService
from app.modules.auth.services.common import load_user

security = HTTPBearer(auto_error=False)

FINANCE_ROLES = {"director", "admin", "accountant"}
FULL_COMPANY_ROLES = {"director", "admin"}
RFQ_ROLES = FULL_COMPANY_ROLES | {"moderator"}
ADMIN_ROLES = {"admin", "superadmin", "moderator"}


@dataclass
class CurrentUser:
    id: int
    email: str
    first_name: str
    last_name: str
    phone: str | None
    role: str
    status: str
    role_id: int | None = None
    permissions: frozenset[str] = frozenset()


@dataclass
class CurrentActor:
    id: int
    kind: str
    side: str
    user_id: int | None
    company_id: int | None
    display_name: str
    is_active: bool = True


@dataclass
class CurrentCompany:
    id: int
    title: str
    owner_id: int


@dataclass
class AuthContext:
    user: CurrentUser
    actor: CurrentActor | None
    company: CurrentCompany | None
    company_role: str | None


@dataclass
class BuyerContext:
    user: CurrentUser
    actor: CurrentActor
    company: CurrentCompany | None
    company_role: str | None


@dataclass
class SupplierContext:
    user: CurrentUser
    actor: CurrentActor
    company: CurrentCompany | None
    company_role: str | None


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


def user_from_row(row: dict) -> CurrentUser:
    return CurrentUser(
        id=row["id"],
        email=row["email"],
        first_name=row["first_name"],
        last_name=row["last_name"],
        phone=row.get("phone"),
        role=row["role"],
        status=row["status"],
        role_id=row.get("role_id"),
        permissions=frozenset(row.get("permissions") or ()),
    )


def actor_from_row(row: dict) -> CurrentActor:
    return CurrentActor(
        id=row["id"],
        kind=row["kind"],
        side=row["side"],
        user_id=row.get("user_id"),
        company_id=row.get("company_id"),
        display_name=row.get("display_name") or "",
        is_active=bool(row.get("is_active", True)),
    )


async def raw_db_dependency() -> AsyncGenerator[asyncpg.Connection, Any]:
    if not pool.ready:
        await pool.init()
    async with pool.acquire() as conn:
        yield conn


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)],
) -> CurrentUser:
    if not credentials:
        raise UnauthorizedError("Missing authentication token")
    try:
        payload = decode_token(credentials.credentials)
    except ValueError as exc:
        raise UnauthorizedError("Invalid or expired token") from exc
    if payload.get("type") != "access":
        raise UnauthorizedError("Invalid token type")
    user_id = int(payload["sub"])
    async with pool.connection() as conn:
        row = await load_user(conn, user_id)
        if not row:
            raise UnauthorizedError("User not found")
        if row["status"] == "blocked":
            raise ForbiddenError("Account is blocked")
        row["permissions"] = await fetch_permission_names(
            conn, role_id=row.get("role_id"), role_name=row["role"]
        )
    return user_from_row(row)


async def _load_membership(user_id: int, company_id: int) -> tuple[CurrentCompany, str] | None:
    async with pool.connection() as conn:
        row = await conn.fetchrow(
            """
            SELECT
                c.id, c.title, c.owner_id, cu.role::text AS role
            FROM company_users cu
            JOIN companies c ON c.id = cu.company_id
            WHERE cu.user_id = $1 AND cu.company_id = $2
            """,
            user_id,
            company_id,
        )
    if not row:
        return None
    return CurrentCompany(id=row["id"], title=row["title"], owner_id=row["owner_id"]), row["role"]


async def _resolve_actor_context(
    user: CurrentUser,
    x_actor_id: int | None,
    x_company_id: int | None,
    side: str | None,
    *,
    strict: bool,
) -> AuthContext:
    actor_row = await ActorService().resolve_actor_id(
        user.id,
        x_actor_id,
        x_company_id,
        side,
        strict=strict,
    )
    actor = actor_from_row(actor_row) if actor_row else None
    company = None
    company_role = None
    if actor and actor.kind == "company" and actor.company_id:
        membership = await _load_membership(user.id, actor.company_id)
        if membership:
            company, company_role = membership
    return AuthContext(user=user, actor=actor, company=company, company_role=company_role)


async def get_auth_context(
    user: Annotated[CurrentUser, Depends(get_current_user)],
    x_actor_id: Annotated[int | None, Header(alias="X-Actor-Id")] = None,
    x_company_id: Annotated[int | None, Header(alias="X-Company-Id")] = None,
) -> AuthContext:
    return await _resolve_actor_context(
        user, x_actor_id, x_company_id, side=None, strict=False
    )


async def require_actor(
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> AuthContext:
    if not ctx.actor:
        raise ForbiddenError("X-Actor-Id header required", code="actor_required")
    return ctx


def buyer_perm(code: str):
    async def _check(
        ctx: Annotated[BuyerContext, Depends(require_buyer_ctx)],
    ) -> BuyerContext:
        ensure_permission(ctx.user, code)
        return ctx

    return _check


def supplier_perm(code: str):
    async def _check(
        ctx: Annotated[SupplierContext, Depends(require_supplier_ctx)],
    ) -> SupplierContext:
        ensure_permission(ctx.user, code)
        return ctx

    return _check


def require_company_roles(*roles: str):
    allowed = {role.value if hasattr(role, "value") else str(role) for role in roles}

    async def _check(ctx: Annotated[AuthContext, Depends(require_actor)]) -> AuthContext:
        if ctx.actor and ctx.actor.kind == "individual":
            return ctx
        if not ctx.company_role or ctx.company_role not in allowed:
            raise ForbiddenError("Insufficient company permissions")
        return ctx

    return _check


async def require_admin(
    user: Annotated[CurrentUser, Depends(get_current_user)],
) -> CurrentUser:
    if user.role not in ADMIN_ROLES:
        raise ForbiddenError("Admin access required")
    return user


async def require_buyer_ctx(
    user: Annotated[CurrentUser, Depends(get_current_user)],
    x_actor_id: Annotated[int | None, Header(alias="X-Actor-Id")] = None,
    x_company_id: Annotated[int | None, Header(alias="X-Company-Id")] = None,
) -> BuyerContext:
    actor_row = await ActorService().resolve_actor_id(
        user.id, x_actor_id, x_company_id, "buyer", strict=True
    )
    if not actor_row:
        raise ForbiddenError("Buyer actor required", code="actor_required")
    actor = actor_from_row(actor_row)
    if actor.side != "buyer":
        raise ForbiddenError("Buyer actor required")
    if user.role not in ("buyer", "both"):
        raise ForbiddenError("Buyer platform role required")

    company = None
    company_role = None
    if actor.company_id:
        membership = await _load_membership(user.id, actor.company_id)
        if not membership:
            raise ForbiddenError("Not a member of this company")
        company, company_role = membership

    return BuyerContext(
        user=user,
        actor=actor,
        company=company,
        company_role=company_role or "director",
    )


def require_buyer_company_roles(*roles: str):
    allowed = {role.value if hasattr(role, "value") else str(role) for role in roles}

    async def _check(
        ctx: Annotated[BuyerContext, Depends(require_buyer_ctx)],
    ) -> BuyerContext:
        if ctx.actor.kind == "individual":
            return ctx
        if not ctx.company_role or ctx.company_role not in allowed:
            raise ForbiddenError("Insufficient company permissions")
        return ctx

    return _check


async def require_supplier_ctx(
    user: Annotated[CurrentUser, Depends(get_current_user)],
    x_actor_id: Annotated[int | None, Header(alias="X-Actor-Id")] = None,
    x_company_id: Annotated[int | None, Header(alias="X-Company-Id")] = None,
) -> SupplierContext:
    actor_row = await ActorService().resolve_actor_id(
        user.id, x_actor_id, x_company_id, "supplier", strict=True
    )
    if not actor_row:
        raise ForbiddenError("Supplier actor required", code="actor_required")
    actor = actor_from_row(actor_row)
    if actor.side != "supplier":
        raise ForbiddenError("Supplier actor required")
    if user.role not in ("supplier", "both"):
        raise ForbiddenError("Supplier platform role required")

    company = None
    company_role = None
    if actor.company_id:
        membership = await _load_membership(user.id, actor.company_id)
        if not membership:
            raise ForbiddenError("Not a member of this company")
        company, company_role = membership

    return SupplierContext(
        user=user,
        actor=actor,
        company=company,
        company_role=company_role or "director",
    )


async def get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


__all__ = [
    "AuthContext",
    "BuyerContext",
    "SupplierContext",
    "CurrentUser",
    "CurrentActor",
    "CurrentCompany",
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
    "buyer_perm",
    "supplier_perm",
    "require_supplier_ctx",
    "require_admin",
    "get_client_ip",
    "Pagination",
    "PaginationDep",
    "pagination_dependency",
    "raw_db_dependency",
]
