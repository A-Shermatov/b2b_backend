from app.core.exceptions import NotFoundError
from app.modules.companies.schemas import CompanyWithRelations, PublicSupplier
from app.modules.companies.services.common import load_company_schema, row_to_public_supplier

SUPPLIER_SELECT = """
    SELECT
        a.id,
        a.kind::text AS kind,
        a.display_name,
        a.company_id,
        a.trust_level::text AS trust_level,
        a.user_id,
        c.title,
        c.city,
        c.country,
        c.description,
        c.website,
        c.rating,
        c.verification_status::text AS verification_status,
        cs.average_rating,
        cp.industries,
        u.first_name,
        u.last_name,
        (SELECT COUNT(*) FROM reviews r WHERE r.target_actor_id = a.id) AS reviews_count,
        (
            SELECT COUNT(*) FROM catalog_items ci
            WHERE ci.actor_id = a.id AND ci.status::text = 'active'
        ) AS active_catalog_count,
        (
            SELECT COUNT(*) FROM contracts ct
            WHERE ct.supplier_actor_id = a.id AND ct.status::text = 'completed'
        ) AS completed_contracts,
        (
            SELECT COUNT(*) FROM contracts ct
            WHERE ct.supplier_actor_id = a.id
              AND ct.status::text IN ('active', 'delivered', 'pending_payment')
        ) AS active_contracts,
        (
            SELECT COUNT(*) FROM contracts ct
            WHERE ct.supplier_actor_id = a.id AND ct.status::text = 'disputed'
        ) AS disputed_contracts
    FROM actors a
    LEFT JOIN companies c ON c.id = a.company_id
    LEFT JOIN company_stats cs ON cs.company_id = c.id
    LEFT JOIN company_profiles cp ON cp.company_id = c.id
    LEFT JOIN users u ON u.id = a.user_id
    WHERE a.side::text = 'supplier' AND a.is_active = TRUE
"""


class CompanyReadMixin:
    async def get_company(self, company_id: int) -> CompanyWithRelations:
        async with self.connection() as conn:
            return await load_company_schema(conn, company_id)

    async def get_my_companies(self, user_id: int) -> list[CompanyWithRelations]:
        async with self.connection() as conn:
            rows = await conn.fetch(
                """
                SELECT c.id
                FROM companies c
                JOIN company_users cu ON cu.company_id = c.id
                WHERE cu.user_id = $1
                ORDER BY c.id
                """,
                user_id,
            )
            return [await load_company_schema(conn, row["id"]) for row in rows]

    async def list_suppliers(
        self, query: str | None = None, category_slug: str | None = None
    ) -> list[PublicSupplier]:
        filters = []
        params: list = []
        if query:
            params.append(f"%{query}%")
            filters.append(
                f"""
                (
                    a.display_name ILIKE ${len(params)}
                    OR c.title ILIKE ${len(params)}
                    OR c.description ILIKE ${len(params)}
                    OR u.first_name ILIKE ${len(params)}
                    OR u.last_name ILIKE ${len(params)}
                )
                """
            )
        if category_slug:
            params.append(category_slug)
            filters.append(
                f"""
                a.kind = 'company'
                AND EXISTS (
                    SELECT 1
                    FROM company_categories cc
                    JOIN categories cat ON cat.id = cc.category_id
                    WHERE cc.company_id = a.company_id AND cat.slug = ${len(params)}
                )
                """
            )
        where = (" AND " + " AND ".join(filters)) if filters else ""
        sql = SUPPLIER_SELECT + where + " ORDER BY a.display_name ASC"
        async with self.connection() as conn:
            rows = await conn.fetch(sql, *params)
        return [row_to_public_supplier(row) for row in rows]

    async def get_public_supplier(self, actor_id: int) -> PublicSupplier:
        async with self.connection() as conn:
            row = await conn.fetchrow(
                SUPPLIER_SELECT + " AND a.id = $1",
                actor_id,
            )
        if not row:
            raise NotFoundError("Supplier not found")
        return row_to_public_supplier(row)
