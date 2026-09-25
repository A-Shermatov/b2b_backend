from app.core.exceptions import ForbiddenError, NotFoundError
from app.modules.actors.services import ActorService
from app.modules.companies.schemas import CompanyUpdateRequest, CompanyWithRelations
from app.modules.companies.services.common import ensure_can_manage, load_company_schema


class CompanyUpdateMixin:
    async def update_company(
        self, company_id: int, user_id: int, data: CompanyUpdateRequest
    ) -> CompanyWithRelations:
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
                ):
                    val = getattr(data, field)
                    if val is not None:
                        profile_params.append(val)
                        cast = "::text[]" if field in {"languages", "industries"} else ""
                        profile_updates.append(f"{field} = ${len(profile_params)}{cast}")
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
