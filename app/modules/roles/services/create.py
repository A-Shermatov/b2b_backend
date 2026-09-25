from app.core.exceptions import ConflictError, NotFoundError
from app.core.permissions import PROTECTED_ROLE_NAMES
from app.modules.roles.services.common import role_from_row


class RoleCreateMixin:
    async def create_role(self, name: str, description: str | None):
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
                INSERT INTO roles (name, description, is_system, is_active)
                VALUES ($1, $2, FALSE, TRUE)
                RETURNING id, name, description, is_system, is_active
                """,
                cleaned,
                description,
            )
        return role_from_row(row)

    async def assign_role(self, user_id: int, role_id: int) -> None:
        async with self.connection() as conn:
            role = await conn.fetchrow(
                "SELECT id, name, is_active FROM roles WHERE id = $1",
                role_id,
            )
            if not role or not role["is_active"] or role["name"] in PROTECTED_ROLE_NAMES:
                raise NotFoundError("Role not found")
            user = await conn.fetchrow("SELECT id FROM users WHERE id = $1", user_id)
            if not user:
                raise NotFoundError("User not found")
            await conn.execute(
                "UPDATE users SET role_id = $1, updated_at = NOW() WHERE id = $2",
                role_id,
                user_id,
            )
