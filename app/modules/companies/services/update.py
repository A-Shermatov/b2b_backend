import json

from app.core.exceptions import ForbiddenError, NotFoundError, ValidationError
from app.modules.actors.services import ActorService
from app.modules.companies.schemas import (
    CertificateUpdateRequest,
    CompanyCertificateSchema,
    CompanyUpdateRequest,
    CompanyWithRelations,
    UpdateTeamMemberRequest,
)
from app.modules.companies.services.common import (
    COMPANY_ROLES,
    ensure_can_manage,
    load_company_schema,
)


class CompanyUpdateMixin:
    async def update_company(
        self, company_id: int, user_id: int, data: CompanyUpdateRequest
    ) -> CompanyWithRelations:
        from app.modules.companies.services.changes import MODERATED_FIELDS

        skip_moderated = False
        async with self.connection() as conn:
            company_row = await conn.fetchrow(
                """
                SELECT verification_status::text AS verification_status,
                       title, legal_name, tax_number, address, city, country
                FROM companies WHERE id = $1
                """,
                company_id,
            )
        if not company_row:
            raise NotFoundError("Company not found")

        if company_row["verification_status"] == "verified":
            skip_moderated = True
            changed = {}
            for field in MODERATED_FIELDS:
                val = getattr(data, field, None)
                if val is None:
                    continue
                current = str(company_row[field] or "").strip()
                if str(val).strip() != current:
                    changed[field] = val
            if changed:
                await self.submit_change_request(company_id, user_id, changed)

        async with self.connection() as conn:
            async with conn.transaction():
                await load_company_schema(conn, company_id)
                await ensure_can_manage(conn, company_id, user_id, "company_profile.update")
                fields = (
                    "title",
                    "legal_name",
                    "tax_number",
                    "website",
                    "description",
                    "logo",
                    "country",
                    "city",
                    "address",
                )
                sets = []
                params: list = []
                for field in fields:
                    if skip_moderated and field in MODERATED_FIELDS:
                        continue
                    val = getattr(data, field)
                    if val is not None:
                        params.append(val)
                        sets.append(f"{field} = ${len(params)}")
                if sets:
                    params.append(company_id)
                    await conn.execute(
                        f"UPDATE companies SET {', '.join(sets)}, updated_at = NOW() WHERE id = ${len(params)}",
                        *params,
                    )
                else:
                    await conn.execute(
                        "UPDATE companies SET updated_at = NOW() WHERE id = $1",
                        company_id,
                    )

                profile_updates = []
                profile_params: list = []
                for field in (
                    "founded_year",
                    "employees_count",
                    "annual_revenue_range",
                    "languages",
                    "industries",
                    "warehouse_area",
                    "markets",
                    "brands",
                    "notable_clients",
                    "gallery",
                ):
                    val = getattr(data, field)
                    if val is not None:
                        if field == "gallery":
                            profile_params.append(json.dumps(val))
                            profile_updates.append(
                                f"{field} = ${len(profile_params)}::jsonb"
                            )
                        else:
                            profile_params.append(val)
                            cast = "::text[]" if field in {"languages", "industries"} else ""
                            profile_updates.append(
                                f"{field} = ${len(profile_params)}{cast}"
                            )
                if profile_updates:
                    profile_params.append(company_id)
                    await conn.execute(
                        f"""
                        UPDATE company_profiles
                        SET {', '.join(profile_updates)}
                        WHERE company_id = ${len(profile_params)}
                        """,
                        *profile_params,
                    )

                if data.category_ids is not None:
                    await conn.execute(
                        "DELETE FROM company_categories WHERE company_id = $1",
                        company_id,
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

                if data.actor_types is not None:
                    actor_types = list(data.actor_types)
                    if actor_types:
                        await conn.execute(
                            "UPDATE companies SET actor_type = $1::actor_type WHERE id = $2",
                            actor_types[0],
                            company_id,
                        )
                    company = await conn.fetchrow(
                        """
                        SELECT id, title, verification_status::text AS verification_status
                        FROM companies WHERE id = $1
                        """,
                        company_id,
                    )
                    await ActorService(conn).sync_company_actors(dict(company), actor_types)
                return await load_company_schema(conn, company_id)

    async def remove_team_member(self, company_id: int, user_id: int, target_user_id: int):
        async with self.connection() as conn:
            await ensure_can_manage(conn, company_id, user_id, "company_team.manage")
            membership = await conn.fetchrow(
                """
                SELECT id, role::text AS role
                FROM company_users
                WHERE company_id = $1 AND user_id = $2
                """,
                company_id,
                target_user_id,
            )
            if not membership:
                raise NotFoundError("Team member not found")
            if membership["role"] == "director":
                raise ForbiddenError("Cannot remove company director")
            await conn.execute("DELETE FROM company_users WHERE id = $1", membership["id"])

    async def update_team_member(
        self, company_id: int, user_id: int, target_user_id: int, data: UpdateTeamMemberRequest
    ):
        if data.role not in COMPANY_ROLES:
            raise ValidationError("Invalid company role")
        async with self.connection() as conn:
            await ensure_can_manage(conn, company_id, user_id, "company_team.manage")
            membership = await conn.fetchrow(
                """
                SELECT id, role::text AS role, u.email
                FROM company_users cu
                JOIN users u ON u.id = cu.user_id
                WHERE cu.company_id = $1 AND cu.user_id = $2
                """,
                company_id,
                target_user_id,
            )
            if not membership:
                raise NotFoundError("Team member not found")
            if membership["role"] == "director" and data.role != "director":
                raise ForbiddenError("Cannot change company director role")
            if data.role == "director" and membership["role"] != "director":
                raise ForbiddenError("Cannot assign director role")
            row = await conn.fetchrow(
                """
                UPDATE company_users
                SET role = $1::company_role
                WHERE id = $2
                RETURNING id, company_id, user_id, role::text AS role
                """,
                data.role,
                membership["id"],
            )
        return {
            "id": row["id"],
            "company_id": row["company_id"],
            "user_id": row["user_id"],
            "role": row["role"],
            "email": membership["email"],
        }

    async def update_certificate(
        self,
        company_id: int,
        user_id: int,
        certificate_id: int,
        data: CertificateUpdateRequest,
    ) -> CompanyCertificateSchema:
        async with self.connection() as conn:
            await ensure_can_manage(conn, company_id, user_id, "company_certificates.manage")
            current = await conn.fetchrow(
                """
                SELECT id, company_id, title, issuer, issue_date, expiry_date, file_url
                FROM company_certificates
                WHERE id = $1 AND company_id = $2
                """,
                certificate_id,
                company_id,
            )
            if not current:
                raise NotFoundError("Certificate not found")
            fields = ("title", "issuer", "issue_date", "expiry_date", "file_url")
            sets = []
            params: list = []
            for field in fields:
                val = getattr(data, field)
                if val is not None:
                    params.append(val)
                    sets.append(f"{field} = ${len(params)}")
            if sets:
                params.extend([certificate_id, company_id])
                row = await conn.fetchrow(
                    f"""
                    UPDATE company_certificates
                    SET {', '.join(sets)}
                    WHERE id = ${len(params) - 1} AND company_id = ${len(params)}
                    RETURNING id, company_id, title, issuer, issue_date, expiry_date, file_url
                    """,
                    *params,
                )
            else:
                row = current
        return CompanyCertificateSchema.model_validate(dict(row))

    async def remove_certificate(self, company_id: int, user_id: int, certificate_id: int):
        async with self.connection() as conn:
            await ensure_can_manage(conn, company_id, user_id, "company_certificates.manage")
            result = await conn.execute(
                """
                DELETE FROM company_certificates
                WHERE id = $1 AND company_id = $2
                """,
                certificate_id,
                company_id,
            )
            if result == "DELETE 0":
                raise NotFoundError("Certificate not found")
