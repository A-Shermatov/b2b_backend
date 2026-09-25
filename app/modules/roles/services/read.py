from app.core.exceptions import NotFoundError
from app.modules.roles.services.common import build_matrix_rows, load_role, role_from_row


class RoleReadMixin:
    async def list_roles(self) -> list:
        async with self.connection() as conn:
            rows = await conn.fetch(
                """
                SELECT id, name, description, is_system, is_active
                FROM roles
                WHERE name <> 'superadmin' AND is_active = TRUE
                ORDER BY id
                """
            )
        return [role_from_row(row) for row in rows]

    async def get_role(self, role_id: int):
        async with self.connection() as conn:
            row = await load_role(conn, role_id)
        if row["name"] == "superadmin":
            raise NotFoundError("Role not found")
        return role_from_row(row)

    async def catalog(self):
        async with self.connection() as conn:
            rows = await conn.fetch(
                """
                SELECT id, name, resource, action
                FROM permissions
                WHERE is_active = TRUE
                ORDER BY resource, action
                """
            )
        return build_matrix_rows([dict(row) for row in rows], enabled_ids=None)

    async def role_matrix(self, role_id: int):
        async with self.connection() as conn:
            role = await load_role(conn, role_id)
            if role["name"] == "superadmin":
                raise NotFoundError("Role not found")
            permissions = await conn.fetch(
                """
                SELECT id, name, resource, action
                FROM permissions
                WHERE is_active = TRUE
                ORDER BY resource, action
                """
            )
            granted = await conn.fetch(
                """
                SELECT permission_id
                FROM role_permissions
                WHERE role_id = $1 AND is_active = TRUE
                """,
                role_id,
            )
        enabled = {int(row["permission_id"]) for row in granted}
        return {
            "role_id": role["id"],
            "role_name": role["name"],
            "rows": build_matrix_rows([dict(row) for row in permissions], enabled),
        }
