from app.modules.actors.services.common import ACTOR_COLUMNS, shape_actor_row


class ActorReadMixin:
    async def get_by_id(self, actor_id: int) -> dict | None:
        async with self.connection() as conn:
            row = await conn.fetchrow(
                f"""
                SELECT {ACTOR_COLUMNS}
                FROM actors a
                LEFT JOIN companies c ON c.id = a.company_id
                WHERE a.id = $1 AND a.is_active = TRUE
                """,
                actor_id,
            )
        return shape_actor_row(row) if row else None

    async def list_for_user(self, user_id: int) -> list[dict]:
        async with self.connection() as conn:
            rows = await conn.fetch(
                f"""
                SELECT {ACTOR_COLUMNS}
                FROM actors a
                LEFT JOIN companies c ON c.id = a.company_id
                WHERE a.is_active = TRUE
                  AND (
                    a.user_id = $1
                    OR a.company_id IN (
                        SELECT company_id FROM company_users WHERE user_id = $1
                    )
                  )
                ORDER BY a.kind, a.side, a.id
                """,
                user_id,
            )
        return [shape_actor_row(row) for row in rows]

    async def user_owns_actor(self, user_id: int, actor: dict) -> bool:
        if actor["kind"] == "individual":
            return actor["user_id"] == user_id
        if actor["company_id"] is None:
            return False
        async with self.connection() as conn:
            row = await conn.fetchrow(
                """
                SELECT id FROM company_users
                WHERE user_id = $1 AND company_id = $2
                """,
                user_id,
                actor["company_id"],
            )
        return row is not None

    async def get_individual_actor(self, user_id: int, side: str) -> dict | None:
        async with self.connection() as conn:
            row = await conn.fetchrow(
                f"""
                SELECT {ACTOR_COLUMNS}
                FROM actors a
                LEFT JOIN companies c ON c.id = a.company_id
                WHERE a.kind = 'individual'
                  AND a.user_id = $1
                  AND a.side::text = $2
                  AND a.is_active = TRUE
                """,
                user_id,
                side,
            )
        return shape_actor_row(row) if row else None

    async def get_company_actor(self, company_id: int, side: str) -> dict | None:
        async with self.connection() as conn:
            row = await conn.fetchrow(
                f"""
                SELECT {ACTOR_COLUMNS}
                FROM actors a
                LEFT JOIN companies c ON c.id = a.company_id
                WHERE a.kind = 'company'
                  AND a.company_id = $1
                  AND a.side::text = $2
                  AND a.is_active = TRUE
                """,
                company_id,
                side,
            )
        return shape_actor_row(row) if row else None

    async def resolve_actor_id(
        self,
        user_id: int,
        x_actor_id: int | None,
        x_company_id: int | None,
        side: str | None = None,
        *,
        strict: bool = False,
    ) -> dict | None:
        if x_actor_id is not None:
            actor = await self.get_by_id(x_actor_id)
            if not actor:
                return None
            if not await self.user_owns_actor(user_id, actor):
                return None
            if side and actor["side"] != side:
                return None
            return actor

        if x_company_id is not None and side is not None:
            async with self.connection() as conn:
                membership = await conn.fetchrow(
                    """
                    SELECT id FROM company_users
                    WHERE user_id = $1 AND company_id = $2
                    """,
                    user_id,
                    x_company_id,
                )
            if not membership:
                return None
            return await self.get_company_actor(x_company_id, side)

        if strict:
            if side is None:
                return None
            return await self.get_individual_actor(user_id, side)

        actors = await self.list_for_user(user_id)
        if side:
            actors = [a for a in actors if a["side"] == side]
        return actors[0] if actors else None
