import json

from app.core.exceptions import ForbiddenError, NotFoundError
from app.core.permissions import (
    ensure_company_role_permission,
    ensure_permission,
    fetch_permission_names,
)
from app.modules.companies.schemas import (
    CompanyCategorySchema,
    CompanyCertificateSchema,
    CompanyProfileSchema,
    CompanyStatsSchema,
    CompanyUserSchema,
    CompanyWithRelations,
    PublicBuyer,
    PublicCompanyDetail,
    SideReputationSchema,
    PublicSupplier,
)

COMPANY_COLUMNS = """
    id, title, actor_type::text AS actor_type, owner_id, legal_name, tax_number,
    website, description, logo, country, city, address,
    verification_status::text AS verification_status, rating, created_at, updated_at
"""

COMPANY_ROLES = frozenset({"director", "admin", "moderator", "accountant"})


def user_as_dict(user) -> dict:
    if isinstance(user, dict):
        return user
    role = user.role
    return {
        "id": user.id,
        "email": user.email,
        "first_name": getattr(user, "first_name", ""),
        "last_name": getattr(user, "last_name", ""),
        "role": role.value if hasattr(role, "value") else str(role),
    }


def parse_actor_types(data, extra: str | None = None) -> list[str]:
    types = list(data.actor_types) if data.actor_types else []
    if not types and getattr(data, "actor_type", None):
        types = [data.actor_type]
    if not types:
        types = ["buyer"]
    if extra and extra not in types:
        types = [extra, *types]
    return types


async def ensure_company_member(conn, company_id: int, user_id: int) -> None:
    member = await conn.fetchrow(
        """
        SELECT 1
        FROM company_users
        WHERE company_id = $1 AND user_id = $2
        """,
        company_id,
        user_id,
    )
    if not member:
        raise ForbiddenError("Insufficient permissions")


async def ensure_can_manage(
    conn,
    company_id: int,
    user_id: int,
    permission: str = "company_profile.update",
) -> None:
    await ensure_company_member(conn, company_id, user_id)
    # Team, profile and certificates are managed by the company director/admin only.
    company_role = await conn.fetchval(
        "SELECT role::text FROM company_users WHERE company_id = $1 AND user_id = $2",
        company_id,
        user_id,
    )
    ensure_company_role_permission("company", company_role, permission, full_role=True)
    user = await conn.fetchrow(
        "SELECT role::text AS role, role_id FROM users WHERE id = $1",
        user_id,
    )
    if not user:
        raise ForbiddenError("Insufficient permissions")
    names = await fetch_permission_names(
        conn, role_id=user["role_id"], role_name=user["role"]
    )
    ensure_permission({"role": user["role"], "permissions": names}, permission)


async def load_company_schema(conn, company_id: int) -> CompanyWithRelations:
    company = await conn.fetchrow(
        f"SELECT {COMPANY_COLUMNS} FROM companies WHERE id = $1",
        company_id,
    )
    if not company:
        raise NotFoundError("Company not found")
    profile = await conn.fetchrow(
        """
        SELECT company_id, founded_year, employees_count, annual_revenue_range,
               languages, industries,
               warehouse_area, markets, brands, notable_clients, gallery
        FROM company_profiles WHERE company_id = $1
        """,
        company_id,
    )
    stats = await conn.fetchrow(
        """
        SELECT company_id, completed_contracts, active_contracts,
               disputes_count, average_rating
        FROM company_stats WHERE company_id = $1
        """,
        company_id,
    )
    categories = await conn.fetch(
        "SELECT id, company_id, category_id FROM company_categories WHERE company_id = $1",
        company_id,
    )
    certificates = await conn.fetch(
        """
        SELECT id, company_id, title, issuer, issue_date, expiry_date, file_url
        FROM company_certificates WHERE company_id = $1
        """,
        company_id,
    )
    members = await conn.fetch(
        """
        SELECT
            cu.id, cu.company_id, cu.user_id, cu.role::text AS role, u.email,
            NULLIF(TRIM(CONCAT_WS(' ', u.first_name, u.last_name)), '') AS name,
            (SELECT sr.role FROM company_user_side_roles sr
             WHERE sr.company_user_id = cu.id AND sr.side = 'buyer') AS buyer_role,
            (SELECT sr.role FROM company_user_side_roles sr
             WHERE sr.company_user_id = cu.id AND sr.side = 'supplier') AS supplier_role
        FROM company_users cu
        JOIN users u ON u.id = cu.user_id
        WHERE cu.company_id = $1
        ORDER BY CASE cu.role::text WHEN 'director' THEN 0 WHEN 'admin' THEN 1 ELSE 2 END, cu.id
        """,
        company_id,
    )
    # Reputation per role: reviews about the company as a supplier / as a buyer,
    # and deals it completed in that role.
    reputation_rows = await conn.fetch(
        """
        SELECT
            a.side::text AS side,
            (SELECT ROUND(AVG(r.rating)::numeric, 2)::float FROM reviews r
             WHERE r.target_actor_id = a.id) AS rating,
            (SELECT COUNT(*)::int FROM reviews r WHERE r.target_actor_id = a.id) AS reviews_count,
            (SELECT COUNT(*)::int FROM contracts ct
             WHERE (ct.buyer_actor_id = a.id OR ct.supplier_actor_id = a.id)
               AND ct.status::text = 'completed') AS completed_deals
        FROM actors a
        WHERE a.company_id = $1 AND a.kind::text = 'company' AND a.is_active = TRUE
        ORDER BY CASE a.side::text WHEN 'supplier' THEN 0 ELSE 1 END
        """,
        company_id,
    )
    actors = await conn.fetch(
        "SELECT side::text AS side FROM actors WHERE company_id = $1 AND is_active = TRUE",
        company_id,
    )
    actor_types = [row["side"] for row in actors] or [company["actor_type"]]
    profile_schema = None
    if profile:
        profile_data = dict(profile)
        gallery = profile_data.get("gallery")
        if isinstance(gallery, str):
            try:
                gallery = json.loads(gallery)
            except json.JSONDecodeError:
                gallery = []
        if gallery is None:
            gallery = []
        profile_data["gallery"] = list(gallery)
        profile_schema = CompanyProfileSchema.model_validate(profile_data)
    return CompanyWithRelations(
        id=company["id"],
        title=company["title"],
        actor_type=company["actor_type"],
        actor_types=actor_types,
        owner_id=company["owner_id"],
        team_members=[row["user_id"] for row in members],
        legal_name=company["legal_name"],
        tax_number=company["tax_number"],
        website=company["website"],
        description=company["description"],
        logo=company["logo"],
        country=company["country"],
        city=company["city"],
        address=company["address"],
        verification_status=company["verification_status"],
        rating=company["rating"],
        created_at=company["created_at"],
        updated_at=company["updated_at"],
        profile=profile_schema,
        categories=[CompanyCategorySchema.model_validate(dict(row)) for row in categories],
        stats=CompanyStatsSchema.model_validate(dict(stats)) if stats else None,
        certificates=[
            CompanyCertificateSchema.model_validate(dict(row)) for row in certificates
        ],
        reputation=[SideReputationSchema.model_validate(dict(row)) for row in reputation_rows],
        reviews=[],
        company_users=[CompanyUserSchema.model_validate(dict(row)) for row in members],
    )


def to_public_company_detail(company: CompanyWithRelations) -> PublicCompanyDetail:
    return PublicCompanyDetail(
        id=company.id,
        title=company.title,
        actor_type=company.actor_type,
        actor_types=company.actor_types,
        legal_name=company.legal_name,
        has_tax_number=bool((company.tax_number or "").strip()),
        website=company.website,
        description=company.description,
        logo=company.logo,
        country=company.country,
        city=company.city,
        verification_status=company.verification_status,
        rating=company.rating,
        created_at=company.created_at,
        updated_at=company.updated_at,
        profile=company.profile,
        categories=company.categories,
        stats=company.stats,
        certificates=company.certificates,
        reputation=company.reputation,
    )


def _clean_completed(row, completed: int) -> int:
    try:
        value = row["clean_completed_contracts"]
    except (KeyError, IndexError):
        return completed
    return int(value or 0)


def compute_success_rate(completed: int, disputed: int) -> float | None:
    total = completed + disputed
    if total <= 0:
        return None
    return round(completed / total * 100, 1)


def _reputation_tier(
    *,
    verification_status: str | None,
    completed: int,
    rating: float,
    success_rate: float | None,
) -> str:
    from app.modules.trust.levels import resolve_reputation_tier

    return resolve_reputation_tier(
        verification_status=verification_status,
        completed_contracts=completed,
        rating=rating,
        success_rate=success_rate,
    )


def _public_supplier_rating(row) -> float:
    reviews_avg = None
    try:
        reviews_avg = row["reviews_avg"]
    except (KeyError, IndexError):
        pass
    if reviews_avg is not None and float(reviews_avg) > 0:
        return round(float(reviews_avg), 2)
    return round(float(row["average_rating"] or row["rating"] or 0), 2)


def apply_reputation_override(row, computed: str) -> str:
    """An admin-set mark (or an admin-removed one) replaces the computed tier."""
    from app.modules.trust.levels import TIER_LABELS_RU

    try:
        override = row["reputation_override"]
    except (KeyError, IndexError):
        return computed
    return override if override in TIER_LABELS_RU else computed


def row_to_public_supplier(row) -> PublicSupplier:
    completed = int(row["completed_contracts"] or 0)
    active = int(row["active_contracts"] or 0)
    disputed = int(row["disputed_contracts"] or 0)
    # Deals with a dispute (open, or resolved by refunding the buyer) are not a
    # success even after the dispute is closed and the contract becomes completed.
    success_rate = compute_success_rate(_clean_completed(row, completed), disputed)
    rating = _public_supplier_rating(row)
    vip = bool(row.get("vip")) if "vip" in row.keys() else False
    plan = row.get("subscription_plan") if "subscription_plan" in row.keys() else None
    verification = row["verification_status"] or (
        "pending" if row["kind"] == "company" and row["company_id"] else "unverified"
    )
    reputation = apply_reputation_override(
        row,
        _reputation_tier(
            verification_status=verification,
            completed=completed,
            rating=rating,
            success_rate=success_rate,
        ),
    )
    if row["kind"] == "company" and row["company_id"]:
        return PublicSupplier(
            actor_id=row["id"],
            kind=row["kind"],
            display_name=row["display_name"] or row["title"] or f"Исполнитель #{row['id']}",
            company_id=row["company_id"],
            city=row["city"],
            country=row["country"],
            description=row["description"],
            website=row["website"],
            logo=row["logo"],
            rating=rating,
            verification_status=verification,
            reviews_count=int(row["reviews_count"] or 0),
            industries=list(row["industries"] or []),
            active_catalog_count=int(row["active_catalog_count"] or 0),
            trust_level=row["trust_level"],
            reputation_tier=reputation,
            completed_contracts=completed,
            active_contracts=active,
            disputed_contracts=disputed,
            success_rate=success_rate,
            vip=vip,
            promoted=plan is not None,
            subscription_plan=plan,
        )
    display = row["display_name"]
    if not display:
        name = f"{row['first_name'] or ''} {row['last_name'] or ''}".strip()
        display = name or f"Исполнитель #{row['id']}"
    return PublicSupplier(
        actor_id=row["id"],
        kind=row["kind"],
        display_name=display,
        company_id=None,
        city=None,
        country=None,
        description=None,
        website=None,
        rating=rating,
        verification_status=verification,
        reviews_count=int(row["reviews_count"] or 0),
        industries=[],
        active_catalog_count=int(row["active_catalog_count"] or 0),
        trust_level=row["trust_level"],
        reputation_tier=reputation,
        completed_contracts=completed,
        active_contracts=active,
        disputed_contracts=disputed,
        vip=vip,
        promoted=plan is not None,
        subscription_plan=plan,
        success_rate=success_rate,
    )


def row_to_public_buyer(row) -> PublicBuyer:
    completed = int(row["completed_contracts"] or 0)
    active = int(row["active_contracts"] or 0)
    disputed = int(row["disputed_contracts"] or 0)
    # Deals with a dispute (open, or resolved by refunding the buyer) are not a
    # success even after the dispute is closed and the contract becomes completed.
    success_rate = compute_success_rate(_clean_completed(row, completed), disputed)
    rating = _public_supplier_rating(row)
    verification = row["verification_status"] or (
        "pending" if row["kind"] == "company" and row["company_id"] else "unverified"
    )
    reputation = _reputation_tier(
        verification_status=verification,
        completed=completed,
        rating=rating,
        success_rate=success_rate,
    )
    if row["kind"] == "company" and row["company_id"]:
        return PublicBuyer(
            actor_id=row["id"],
            kind=row["kind"],
            display_name=row["display_name"] or row["title"] or f"Заказчик #{row['id']}",
            company_id=row["company_id"],
            city=row["city"],
            country=row["country"],
            description=row["description"],
            website=row["website"],
            legal_name=row.get("legal_name"),
            rating=rating,
            verification_status=verification,
            reviews_count=int(row["reviews_count"] or 0),
            trust_level=row["trust_level"],
            reputation_tier=reputation,
            completed_contracts=completed,
            active_contracts=active,
            disputed_contracts=disputed,
            success_rate=success_rate,
        )
    display = row["display_name"]
    if not display:
        name = f"{row['first_name'] or ''} {row['last_name'] or ''}".strip()
        display = name or f"Заказчик #{row['id']}"
    return PublicBuyer(
        actor_id=row["id"],
        kind=row["kind"],
        display_name=display,
        company_id=None,
        city=None,
        country=None,
        description=None,
        website=None,
        legal_name=None,
        rating=rating,
        verification_status=verification,
        reviews_count=int(row["reviews_count"] or 0),
        trust_level=row["trust_level"],
        reputation_tier=reputation,
        completed_contracts=completed,
        active_contracts=active,
        disputed_contracts=disputed,
        success_rate=success_rate,
    )
