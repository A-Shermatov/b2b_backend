from app.core.exceptions import ConflictError, ForbiddenError
from app.core.permissions import PROTECTED_ROLE_NAMES
from app.modules.roles.services.common import (
    assert_role_mutable,
    expand_permission_ids,
    load_role,
    role_from_row,
)
from app.modules.roles.services.read import RoleReadMixin


class RoleUpdateMixin(RoleReadMixin):
    async def update_role(self, role_id: int, data):
        async with self.connection() as conn:
            row = await load_role(conn, role_id)
            if row["name"] in PROTECTED_ROLE_NAMES:
                raise ForbiddenError("This role cannot be changed")
            name = data.name.strip() if data.name is not None else None
            if name and name.lower() in PROTECTED_ROLE_NAMES:
                raise ConflictError("This role name is reserved")
            if name and name != row["name"]:
                taken = await conn.fetchrow(
                    """
                    SELECT id FROM roles
                    WHERE name = $1 AND id <> $2 AND is_active = TRUE
                    """,
                    name,
                    role_id,
                )
                if taken:
                    raise ConflictError("Role already exists")
            updated = await conn.fetchrow(
                """
                UPDATE roles
                SET name = COALESCE($2, name),
                    description = COALESCE($3, description),
                    is_active = COALESCE($4, is_active)
                WHERE id = $1
                RETURNING id, name, description, is_system, is_active
                """,
                role_id,
                name,
                data.description,
                data.is_active,
            )
        return role_from_row(updated)

    async def replace_matrix(self, role_id: int, permission_ids: list[int], actor_role_id: int | None):
        async with self.connection() as conn:
            row = await load_role(conn, role_id)
            if row["name"] in PROTECTED_ROLE_NAMES:
                raise ForbiddenError("This role matrix cannot be changed")
            if actor_role_id is not None and int(actor_role_id) == int(role_id):
                raise ForbiddenError("You cannot change your own role matrix")
            expanded = await expand_permission_ids(conn, permission_ids)
            async with conn.transaction():
                await conn.execute(
                    "UPDATE role_permissions SET is_active = FALSE WHERE role_id = $1",
                    role_id,
                )
                for permission_id in expanded:
                    await conn.execute(
                        """
                        INSERT INTO role_permissions (role_id, permission_id, is_active)
                        VALUES ($1, $2, TRUE)
                        ON CONFLICT (role_id, permission_id)
                        DO UPDATE SET is_active = TRUE
                        """,
                        role_id,
                        permission_id,
                    )
        return await self.role_matrix(role_id)

    async def delete_role(self, role_id: int) -> None:
        async with self.connection() as conn:
            row = await load_role(conn, role_id)
            assert_role_mutable(row)
            if row["is_system"]:
                raise ForbiddenError("System role cannot be deleted")
            await conn.execute("UPDATE roles SET is_active = FALSE WHERE id = $1", role_id)
