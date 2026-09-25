from app.core.exceptions import ValidationError
from app.modules.actors.services import ActorService
from app.modules.auth.schemas import ActivateRoleRequest, MeResponse, UserPublic, UserUpdateRequest
from app.modules.auth.services.common import USER_COLUMNS, load_user, shape_user_public


class AuthUpdateMixin:
    async def update_me(self, user_id: int, data: UserUpdateRequest) -> UserPublic:
        async with self.connection() as conn:
            user = await load_user(conn, user_id)
            if not user:
                from app.core.exceptions import UnauthorizedError

                raise UnauthorizedError("User not found")
            first_name = data.first_name if data.first_name is not None else user["first_name"]
            last_name = data.last_name if data.last_name is not None else user["last_name"]
            phone = data.phone if data.phone is not None else user["phone"]
            row = await conn.fetchrow(
                f"""
                UPDATE users
                SET first_name = $1, last_name = $2, phone = $3, updated_at = NOW()
                WHERE id = $4
                RETURNING {USER_COLUMNS}
                """,
                first_name,
                last_name,
                phone,
                user_id,
            )
        return shape_user_public(dict(row))

    async def switch_actor(self, user_id: int, actor_id: int) -> MeResponse:
        actor_svc = ActorService(self.conn)
        actor = await actor_svc.get_by_id(actor_id)
        if not actor or not await actor_svc.user_owns_actor(user_id, actor):
            raise ValidationError("Actor not linked to user")
        return await self.get_me(user_id, active_actor_id=actor_id)

    async def switch_company(self, user_id: int, company_id: int) -> MeResponse:
        async with self.connection() as conn:
            membership = await conn.fetchrow(
                """
                SELECT id FROM company_users
                WHERE user_id = $1 AND company_id = $2
                """,
                user_id,
                company_id,
            )
            if not membership:
                raise ValidationError("Company not linked to user")
            company = await conn.fetchrow(
                "SELECT id, actor_type::text AS actor_type FROM companies WHERE id = $1",
                company_id,
            )
        if not company:
            raise ValidationError("Company not found")
        actor = await ActorService(self.conn).get_company_actor(
            company_id, company["actor_type"]
        )
        if not actor:
            raise ValidationError("Company actor not found")
        return await self.get_me(user_id, active_actor_id=actor["id"])

    async def activate_role(self, user_id: int, data: ActivateRoleRequest) -> MeResponse:
        side = data.side
        async with self.connection() as conn:
            user = await load_user(conn, user_id)
            if not user:
                from app.core.exceptions import UnauthorizedError

                raise UnauthorizedError("User not found")
            role = user["role"]
            if side == "buyer" and role == "supplier":
                role = "both"
            elif side == "supplier" and role == "buyer":
                role = "both"
            elif side == "buyer" and role not in ("buyer", "both"):
                role = "buyer"
            elif side == "supplier" and role not in ("supplier", "both"):
                role = "supplier"
            row = await conn.fetchrow(
                f"""
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
                RETURNING {USER_COLUMNS}
                """,
                role,
                user_id,
            )
            user = dict(row)
            actor = await ActorService(conn).ensure_individual_actor(user, side)
        return await self.get_me(user_id, active_actor_id=actor["id"])
