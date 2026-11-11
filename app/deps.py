import re
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Annotated, Any

import asyncpg
from fastapi import Depends, Header, Query, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.exceptions import ForbiddenError, UnauthorizedError
from app.core.permissions import (
    STAFF_PLATFORM_ROLES,
    VIEWER_FORBIDDEN_MESSAGE,
    ensure_company_role_permission,
    ensure_permission,
    fetch_permission_names,
    has_permission,
)
from app.core.security_core import decode_token
from app.database import pool
from app.modules.actors.services import ActorService
from app.modules.auth.services.common import block_message, lift_expired_ban, load_user
from app.utils.side_roles import SIDE_LABELS_RU, effective_role_sql

security = HTTPBearer(auto_error=False)

FINANCE_ROLES = {"director", "admin", "accountant"}
FULL_COMPANY_ROLES = {"director", "admin"}
RFQ_ROLES = FULL_COMPANY_ROLES | {"moderator"}
ADMIN_ROLES = STAFF_PLATFORM_ROLES

# A viewer looks and changes nothing. Routes that only take the actor context
# (no buyer_perm / supplier_perm) are closed for them here, except what a person
# does for themselves: read marks, their own notifications, support, leaving the
# team, creating a company of their own.
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_VIEWER_WRITE_ALLOWED = re.compile(
    r"/(?:buyer|supplier)/(?:"
    r"notifications/.*"
    r"|nav-seen"
    r"|support/.*"
    r"|(?:.*/)?messages/read"
    r"|rfqs/subscriptions"
    r"|companies/?"
    r"|companies/\d+/team/\d+"
    r")$"
)


def ensure_viewer_may_request(request: Request, company_role: str | None) -> None:
    if company_role != "viewer" or request.method in _SAFE_METHODS:
        return
    if _VIEWER_WRITE_ALLOWED.search(request.url.path):
        return
    raise ForbiddenError(VIEWER_FORBIDDEN_MESSAGE, code="company_role_forbidden")


async def _revoked_company_title(user_id: int, x_actor_id: int | None, x_company_id: int | None):
    """The company named in the request headers, when the user is not (any longer) in it."""
    if x_actor_id is None and x_company_id is None:
        return None
    async with pool.connection() as conn:
        row = await conn.fetchrow(
            """
            SELECT c.id, c.title
            FROM companies c
            WHERE c.id = COALESCE(
                $2::int,
                (SELECT a.company_id FROM actors a
                 WHERE a.id = $1::int AND a.kind::text = 'company')
            )
              AND NOT EXISTS (
                  SELECT 1 FROM company_users cu
                  WHERE cu.company_id = c.id AND cu.user_id = $3
              )
            """,
            x_actor_id,
            x_company_id,
            user_id,
        )
    return row["title"] if row else None


async def _no_actor_error(
    user_id: int, x_actor_id: int | None, x_company_id: int | None, fallback: str
) -> ForbiddenError:
    title = await _revoked_company_title(user_id, x_actor_id, x_company_id)
    if title is not None:
        return ForbiddenError(
            f"У вас больше нет доступа к компании «{title}»",
            code="company_access_revoked",
        )
    return ForbiddenError(fallback, code="actor_required")


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
        row = await lift_expired_ban(conn, row)
        token_tv = int(payload.get("tv") or 0)
        if token_tv != int(row.get("token_version") or 0):
            raise UnauthorizedError("Session revoked")
        if row["status"] == "blocked":
            raise ForbiddenError(block_message(row))
        row["permissions"] = await fetch_permission_names(
            conn, role_id=row.get("role_id"), role_name=row["role"]
        )
    return user_from_row(row)


async def _load_membership(
    user_id: int, company_id: int, side: str | None = None
) -> tuple[CurrentCompany, str] | None:
    """The member's company and role. With a side, the role on that side.

    A side closed for the member ("none") is refused outright.
    """
    async with pool.connection() as conn:
        row = await conn.fetchrow(
            f"""
            SELECT
                c.id, c.title, c.owner_id, {effective_role_sql("cu", "$3")} AS role
            FROM company_users cu
            JOIN companies c ON c.id = cu.company_id
            WHERE cu.user_id = $1 AND cu.company_id = $2
            """,
            user_id,
            company_id,
            side or "",
        )
    if not row:
        return None
    if row["role"] == "none":
        raise ForbiddenError(
            f"У вас нет доступа к {SIDE_LABELS_RU.get(side or '', 'этой стороне')} компании",
            code="company_side_forbidden",
        )
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
        membership = await _load_membership(user.id, actor.company_id, actor.side)
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


def buyer_perm(code: str, *, full_role: bool = False):
    """Platform permission + the member's company role (see COMPANY_ROLE_PERMISSIONS).

    full_role=True reserves the action for company director/admin.
    """

    async def _check(
        ctx: Annotated[BuyerContext, Depends(require_buyer_ctx)],
    ) -> BuyerContext:
        ensure_permission(ctx.user, code)
        ensure_company_role_permission(
            ctx.actor.kind, ctx.company_role, code, full_role=full_role
        )
        return ctx

    return _check


def supplier_perm(code: str, *, full_role: bool = False):
    async def _check(
        ctx: Annotated[SupplierContext, Depends(require_supplier_ctx)],
    ) -> SupplierContext:
        ensure_permission(ctx.user, code)
        ensure_company_role_permission(
            ctx.actor.kind, ctx.company_role, code, full_role=full_role
        )
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


def admin_perm(*codes: str):
    async def _check(
        user: Annotated[CurrentUser, Depends(require_admin)],
    ) -> CurrentUser:
        if not any(has_permission(user, code) for code in codes):
            raise ForbiddenError("Insufficient permissions")
        return user

    return _check


async def require_buyer_ctx(
    request: Request,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    x_actor_id: Annotated[int | None, Header(alias="X-Actor-Id")] = None,
    x_company_id: Annotated[int | None, Header(alias="X-Company-Id")] = None,
) -> BuyerContext:
    actor_row = await ActorService().resolve_actor_id(
        user.id, x_actor_id, x_company_id, "buyer", strict=True
    )
    if not actor_row:
        raise await _no_actor_error(user.id, x_actor_id, x_company_id, "Buyer actor required")
    actor = actor_from_row(actor_row)
    if actor.side != "buyer":
        raise ForbiddenError("Buyer actor required")
    if user.role not in ("buyer", "both"):
        raise ForbiddenError("Buyer platform role required")

    company = None
    company_role = None
    if actor.company_id:
        membership = await _load_membership(user.id, actor.company_id, actor.side)
        if not membership:
            raise ForbiddenError("Not a member of this company")
        company, company_role = membership
        if not company_role:
            raise ForbiddenError("Company role required")
        ensure_viewer_may_request(request, company_role)

    return BuyerContext(
        user=user,
        actor=actor,
        company=company,
        company_role=company_role,
    )


def require_buyer_company_roles(*roles: str):
    """Require a company buyer actor with one of the given company_users roles.

    Individual actors are never elevated — company-scoped actions must use a
    company actor (fixes cross-tenant decide via personal buyer actor).
    """
    allowed = {role.value if hasattr(role, "value") else str(role) for role in roles}

    async def _check(
        ctx: Annotated[BuyerContext, Depends(require_buyer_ctx)],
    ) -> BuyerContext:
        if ctx.actor.kind != "company" or not ctx.company:
            raise ForbiddenError("Company actor required")
        if not ctx.company_role or ctx.company_role not in allowed:
            raise ForbiddenError("Insufficient company permissions")
        return ctx

    return _check


async def require_supplier_ctx(
    request: Request,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    x_actor_id: Annotated[int | None, Header(alias="X-Actor-Id")] = None,
    x_company_id: Annotated[int | None, Header(alias="X-Company-Id")] = None,
) -> SupplierContext:
    actor_row = await ActorService().resolve_actor_id(
        user.id, x_actor_id, x_company_id, "supplier", strict=True
    )
    if not actor_row:
        raise await _no_actor_error(user.id, x_actor_id, x_company_id, "Supplier actor required")
    actor = actor_from_row(actor_row)
    if actor.side != "supplier":
        raise ForbiddenError("Supplier actor required")
    if user.role not in ("supplier", "both"):
        raise ForbiddenError("Supplier platform role required")

    company = None
    company_role = None
    if actor.company_id:
        membership = await _load_membership(user.id, actor.company_id, actor.side)
        if not membership:
            raise ForbiddenError("Not a member of this company")
        company, company_role = membership
        if not company_role:
            raise ForbiddenError("Company role required")
        ensure_viewer_may_request(request, company_role)

    return SupplierContext(
        user=user,
        actor=actor,
        company=company,
        company_role=company_role,
    )


async def get_client_ip(request: Request) -> str:
    from app.utils.request_meta import client_ip

    ip = client_ip(request)
    return "unknown" if ip == "-" else ip


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
    "admin_perm",
    "get_client_ip",
    "Pagination",
    "PaginationDep",
    "pagination_dependency",
    "raw_db_dependency",
]
