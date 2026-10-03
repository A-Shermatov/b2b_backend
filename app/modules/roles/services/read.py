from app.core.permissions import STAFF_ROLE_SCOPE
from app.modules.roles.services.common import (
    build_matrix_rows,
    fetch_staff_permissions,
    load_staff_role,
    role_from_row,
)


class RoleReadMixin:
    async def list_roles(self) -> list:
        async with self.connection() as conn:
            rows = await conn.fetch(
                """
                SELECT
                    r.id, r.name, r.description, r.is_system, r.is_active,
                    COUNT(u.id) AS users_count
                FROM roles r
                LEFT JOIN users u ON u.role_id = r.id
                WHERE r.name <> 'superadmin' AND r.is_active = TRUE AND r.scope = $1
                GROUP BY r.id
                ORDER BY r.id
                """,
                STAFF_ROLE_SCOPE,
            )
        return [role_from_row(row) for row in rows]

    async def get_role(self, role_id: int):
        async with self.connection() as conn:
            row = await load_staff_role(conn, role_id)
        return role_from_row(row)

    async def catalog(self):
        async with self.connection() as conn:
            permissions = await fetch_staff_permissions(conn)
        return build_matrix_rows(permissions, enabled_ids=None)

    async def role_matrix(self, role_id: int):
        async with self.connection() as conn:
            role = await load_staff_role(conn, role_id)
            permissions = await fetch_staff_permissions(conn)
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
            "rows": build_matrix_rows(permissions, enabled),
        }
