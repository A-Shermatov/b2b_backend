from app.core.exceptions import ForbiddenError, NotFoundError
from app.modules.companies.schemas import (
    CompanyCategorySchema,
    CompanyCertificateSchema,
    CompanyProfileSchema,
    CompanyStatsSchema,
    CompanyUserSchema,
    CompanyWithRelations,
    PublicSupplier,
)

COMPANY_COLUMNS = """
    id, title, actor_type::text AS actor_type, owner_id, legal_name, tax_number,
    website, description, logo, country, city, address,
    verification_status::text AS verification_status, rating, created_at, updated_at
"""


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


async def ensure_can_manage(conn, company_id: int, user_id: int) -> None:
    row = await conn.fetchrow(
        """
        SELECT role::text AS role
        FROM company_users
        WHERE company_id = $1 AND user_id = $2
        """,
        company_id,
        user_id,
    )
    if not row or row["role"] not in ("director", "admin"):
        raise ForbiddenError("Insufficient permissions")


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
               languages, industries
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
        SELECT cu.id, cu.company_id, cu.user_id, cu.role::text AS role, u.email
        FROM company_users cu
        JOIN users u ON u.id = cu.user_id
        WHERE cu.company_id = $1
        """,
        company_id,
    )
    actors = await conn.fetch(
        "SELECT side::text AS side FROM actors WHERE company_id = $1 AND is_active = TRUE",
        company_id,
    )
    actor_types = [row["side"] for row in actors] or [company["actor_type"]]
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
        profile=CompanyProfileSchema.model_validate(dict(profile)) if profile else None,
        categories=[CompanyCategorySchema.model_validate(dict(row)) for row in categories],
        stats=CompanyStatsSchema.model_validate(dict(stats)) if stats else None,
        certificates=[
            CompanyCertificateSchema.model_validate(dict(row)) for row in certificates
        ],
        reviews=[],
        company_users=[CompanyUserSchema.model_validate(dict(row)) for row in members],
    )


def row_to_public_supplier(row) -> PublicSupplier:
    if row["kind"] == "company" and row["company_id"]:
        rating = float(row["average_rating"] or row["rating"] or 0)
        return PublicSupplier(
            actor_id=row["id"],
            kind=row["kind"],
            display_name=row["display_name"] or row["title"] or f"Исполнитель #{row['id']}",
            company_id=row["company_id"],
            city=row["city"],
            country=row["country"],
            description=row["description"],
            website=row["website"],
            rating=rating,
            verification_status=row["verification_status"] or "pending",
            reviews_count=int(row["reviews_count"] or 0),
            industries=list(row["industries"] or []),
            active_catalog_count=int(row["active_catalog_count"] or 0),
            trust_level=row["trust_level"],
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
        rating=0.0,
        verification_status="pending",
        reviews_count=int(row["reviews_count"] or 0),
        industries=[],
        active_catalog_count=int(row["active_catalog_count"] or 0),
        trust_level=row["trust_level"],
    )
