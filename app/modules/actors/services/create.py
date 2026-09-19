from app.modules.actors.services.common import (
    ACTOR_COLUMNS,
    individual_display_name,
    shape_actor_row,
    sides_for_role,
    trust_level_for_company,
)


class ActorCreateMixin:
    async def ensure_individual_actor(self, user: dict, side: str) -> dict:
        existing = await self.get_individual_actor(user["id"], side)
        if existing:
            return existing
        async with self.connection() as conn:
            row = await conn.fetchrow(
                f"""
                INSERT INTO actors (
                    kind, side, user_id, company_id, display_name,
                    trust_level, is_active, created_at, updated_at
                )
                VALUES (
                    'individual', $1::actor_side, $2, NULL, $3,
                    'basic', TRUE, NOW(), NOW()
                )
                RETURNING id
                """,
                side,
                user["id"],
                individual_display_name(user),
            )
            created = await conn.fetchrow(
                f"""
                SELECT {ACTOR_COLUMNS}
                FROM actors a
                LEFT JOIN companies c ON c.id = a.company_id
                WHERE a.id = $1
                """,
                row["id"],
            )
        return shape_actor_row(created)

    async def ensure_individual_actors_for_user(self, user: dict) -> list[dict]:
        return [
            await self.ensure_individual_actor(user, side)
            for side in sides_for_role(user["role"])
        ]

    async def ensure_company_actor(self, company: dict, side: str) -> dict:
        existing = await self.get_company_actor(company["id"], side)
        trust = trust_level_for_company(company.get("verification_status"))
        async with self.connection() as conn:
            if existing:
                await conn.execute(
                    """
                    UPDATE actors
                    SET display_name = $1,
                        trust_level = $2::trust_level,
                        is_active = TRUE,
                        updated_at = NOW()
                    WHERE id = $3
                    """,
                    company["title"],
                    trust,
                    existing["id"],
                )
                return await self.get_company_actor(company["id"], side) or existing
            row = await conn.fetchrow(
                """
                INSERT INTO actors (
                    kind, side, user_id, company_id, display_name,
                    trust_level, is_active, created_at, updated_at
                )
                VALUES (
                    'company', $1::actor_side, NULL, $2, $3,
                    $4::trust_level, TRUE, NOW(), NOW()
                )
                RETURNING id
                """,
                side,
                company["id"],
                company["title"],
                trust,
            )
            created = await conn.fetchrow(
                f"""
                SELECT {ACTOR_COLUMNS}
                FROM actors a
                LEFT JOIN companies c ON c.id = a.company_id
                WHERE a.id = $1
                """,
                row["id"],
            )
        return shape_actor_row(created)

    async def sync_company_actors(self, company: dict, actor_types: list[str]) -> list[dict]:
        actors = [await self.ensure_company_actor(company, side) for side in actor_types]
        async with self.connection() as conn:
            await conn.execute(
                """
                UPDATE actors
                SET is_active = FALSE, updated_at = NOW()
                WHERE kind = 'company'
                  AND company_id = $1
                  AND side::text <> ALL($2::text[])
                """,
                company["id"],
                actor_types,
            )
        return actors
