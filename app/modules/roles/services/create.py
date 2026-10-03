from app.core.exceptions import ConflictError, NotFoundError
from app.core.permissions import PROTECTED_ROLE_NAMES, STAFF_RANK, STAFF_ROLE_SCOPE
from app.modules.admin.services.common import log_admin
from app.modules.roles.services.common import assign_staff_role, role_from_row


class RoleCreateMixin:
    async def create_role(self, name: str, description: str | None, actor):
        cleaned = name.strip()
        if cleaned.lower() in PROTECTED_ROLE_NAMES:
            raise ConflictError("This role name is reserved")
        async with self.connection() as conn:
            existing = await conn.fetchrow(
                "SELECT id FROM roles WHERE name = $1 AND is_active = TRUE",
                cleaned,
            )
            if existing:
                raise ConflictError("Role already exists")
            row = await conn.fetchrow(
                """
                INSERT INTO roles (name, description, is_system, is_active, scope)
                VALUES ($1, $2, FALSE, TRUE, $3)
                RETURNING id, name, description, is_system, is_active
                """,
                cleaned,
                description,
                STAFF_ROLE_SCOPE,
            )
            await log_admin(conn, actor.id, "admin.role.create", "role", row["id"], {"name": cleaned})
        return role_from_row(row)

    async def assign_role(self, user_id: int, role_id: int, actor) -> None:
        async with self.connection() as conn:
            user = await conn.fetchrow(
                "SELECT id, role::text AS role FROM users WHERE id = $1",
                user_id,
            )
            if not user or user["role"] not in STAFF_RANK:
                raise NotFoundError("Staff user not found")
            await assign_staff_role(conn, actor, dict(user), role_id)
            await log_admin(conn, actor.id, "admin.role.assign", "user", user_id, {"role_id": role_id})
