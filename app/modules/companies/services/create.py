from app.core.exceptions import ConflictError, NotFoundError
from app.modules.actors.services import ActorService
from app.modules.companies.schemas import (
    AddTeamMemberRequest,
    CertificateCreateRequest,
    CompanyWizardInput,
    CompanyWithRelations,
)
from app.modules.companies.services.common import (
    COMPANY_COLUMNS,
    ensure_can_manage,
    load_company_schema,
    parse_actor_types,
    user_as_dict,
)


class CompanyCreateMixin:
    async def _ensure_roles_and_actors(self, conn, user: dict, actor_types: list[str]) -> dict:
        role = user["role"]
        for side in actor_types:
            if side == "buyer" and role == "supplier":
                role = "both"
            elif side == "supplier" and role == "buyer":
                role = "both"
            elif side == "buyer" and role not in ("buyer", "both"):
                role = "buyer"
            elif side == "supplier" and role not in ("supplier", "both"):
                role = "supplier"
        if role != user["role"]:
            await conn.execute(
                """
                UPDATE users AS u
                SET role = $1::user_role,
                    role_id = CASE
                        WHEN u.role_id IS NULL OR u.role_id IN (
                            SELECT id FROM roles WHERE name IN ('buyer', 'supplier', 'both')
                        )
                        THEN (SELECT id FROM roles WHERE name = $1)
                        ELSE u.role_id
                    END,
                    updated_at = NOW()
                WHERE u.id = $2
                """,
                role,
                user["id"],
            )
            user = {**user, "role": role}
        actor_svc = ActorService(conn)
        for side in actor_types:
            await actor_svc.ensure_individual_actor(user, side)
        return user

    async def create_from_wizard(
        self, user, data: CompanyWizardInput, actor_type: str | None = None
    ) -> CompanyWithRelations:
        user_dict = user_as_dict(user)
        extra = actor_type.value if hasattr(actor_type, "value") else actor_type
        actor_types = parse_actor_types(data, extra)
        async with self.connection() as conn:
            async with conn.transaction():
                user_dict = await self._ensure_roles_and_actors(conn, user_dict, actor_types)
                primary = actor_types[0]
                company = await conn.fetchrow(
                    f"""
                    INSERT INTO companies (
                        title, actor_type, owner_id, legal_name, tax_number, website,
                        description, logo, country, city, address, verification_status,
                        rating, created_at, updated_at
                    )
                    VALUES (
                        $1, $2::actor_type, $3, $4, $5, $6,
                        $7, $8, $9, $10, $11, 'pending',
                        0, NOW(), NOW()
                    )
                    RETURNING {COMPANY_COLUMNS}
                    """,
                    data.title,
                    primary,
                    user_dict["id"],
                    data.legal_name or None,
                    data.tax_number or None,
                    data.website or None,
                    data.description or None,
                    data.logo or None,
                    data.country or None,
                    data.city or None,
                    data.address or None,
                )
                company_id = company["id"]
                founded = int(data.founded_year) if data.founded_year.isdigit() else None
                employees = int(data.employees_count) if data.employees_count.isdigit() else None
                await conn.execute(
                    """
                    INSERT INTO company_profiles (
                        company_id, founded_year, employees_count, annual_revenue_range,
                        languages, industries
                    )
                    VALUES ($1, $2, $3, $4, $5::text[], $6::text[])
                    """,
                    company_id,
                    founded,
                    employees,
                    data.annual_revenue_range or None,
                    data.languages or [],
                    data.industries or [],
                )
                await conn.execute(
                    """
                    INSERT INTO company_stats (
                        company_id, completed_contracts, active_contracts,
                        disputes_count, average_rating
                    )
                    VALUES ($1, 0, 0, 0, 0)
                    """,
                    company_id,
                )
                await conn.execute(
                    """
                    INSERT INTO company_users (company_id, user_id, role)
                    VALUES ($1, $2, 'director')
                    """,
                    company_id,
                    user_dict["id"],
                )
                for cat_id in data.category_ids:
                    await conn.execute(
                        """
                        INSERT INTO company_categories (company_id, category_id)
                        VALUES ($1, $2)
                        """,
                        company_id,
                        cat_id,
                    )
                for cert in data.certificates:
                    await conn.execute(
                        """
                        INSERT INTO company_certificates (
                            company_id, title, issuer, issue_date, expiry_date, file_url
                        )
                        VALUES ($1, $2, $3, $4, $5, $6)
                        """,
                        company_id,
                        cert.title,
                        cert.issuer,
                        cert.issue_date,
                        cert.expiry_date or None,
                        cert.file_url,
                    )
                for member in data.team:
                    member_user = await conn.fetchrow(
                        "SELECT id FROM users WHERE email = $1",
                        member.email.lower(),
                    )
                    if member_user:
                        await conn.execute(
                            """
                            INSERT INTO company_users (company_id, user_id, role)
                            VALUES ($1, $2, $3::company_role)
                            ON CONFLICT DO NOTHING
                            """,
                            company_id,
                            member_user["id"],
                            member.role,
                        )
                await ActorService(conn).sync_company_actors(
                    {
                        "id": company_id,
                        "title": data.title,
                        "verification_status": "pending",
                    },
                    actor_types,
                )
                return await load_company_schema(conn, company_id)

    async def add_certificate(
        self, company_id: int, user_id: int, data: CertificateCreateRequest
    ):
        async with self.connection() as conn:
            await ensure_can_manage(conn, company_id, user_id, "company_certificates.manage")
            row = await conn.fetchrow(
                """
                INSERT INTO company_certificates (
                    company_id, title, issuer, issue_date, expiry_date, file_url
                )
                VALUES ($1, $2, $3, $4, $5, $6)
                RETURNING id
                """,
                company_id,
                data.title,
                data.issuer,
                data.issue_date,
                data.expiry_date,
                data.file_url,
            )
        return row

    async def add_team_member(self, company_id: int, user_id: int, data: AddTeamMemberRequest):
        async with self.connection() as conn:
            await ensure_can_manage(conn, company_id, user_id, "company_team.manage")
            member = await conn.fetchrow(
                "SELECT id FROM users WHERE email = $1",
                data.email.lower(),
            )
            if not member:
                raise NotFoundError("User with this email not found")
            existing = await conn.fetchrow(
                """
                SELECT id FROM company_users
                WHERE company_id = $1 AND user_id = $2
                """,
                company_id,
                member["id"],
            )
            if existing:
                raise ConflictError("User already in team")
            row = await conn.fetchrow(
                """
                INSERT INTO company_users (company_id, user_id, role)
                VALUES ($1, $2, $3::company_role)
                RETURNING id
                """,
                company_id,
                member["id"],
                data.role,
            )
        return row
