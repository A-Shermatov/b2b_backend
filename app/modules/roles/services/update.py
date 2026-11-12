from app.core.exceptions import ConflictError, ForbiddenError, ValidationError
from app.core.permissions import PROTECTED_ROLE_NAMES
from app.modules.admin.services.common import log_admin
from app.modules.roles.services.common import (
    ensure_can_manage_role,
    ensure_permissions_subset,
    fetch_staff_permissions,
    keep_what_a_box_held,
    load_staff_role,
    role_from_row,
)
from app.modules.roles.services.read import RoleReadMixin


async def _assigned_users_count(conn, role_id: int) -> int:
    return int(await conn.fetchval("SELECT COUNT(*) FROM users WHERE role_id = $1", role_id))


class RoleUpdateMixin(RoleReadMixin):
    async def update_role(self, role_id: int, data, actor):
        async with self.connection() as conn:
            row = await load_staff_role(conn, role_id)
            ensure_can_manage_role(actor, row)
            name = data.name.strip() if data.name is not None else None
            if name and name != row["name"]:
                if row["is_system"]:
                    raise ForbiddenError("System role cannot be renamed")
                if name.lower() in PROTECTED_ROLE_NAMES:
                    raise ConflictError("This role name is reserved")
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
            if data.is_active is False:
                if row["is_system"]:
                    raise ForbiddenError("System role cannot be deactivated")
                if await _assigned_users_count(conn, role_id):
                    raise ConflictError("Role is assigned to users")
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
            await log_admin(
                conn,
                actor.id,
                "admin.role.update",
                "role",
                role_id,
                data.model_dump(exclude_none=True),
            )
        return role_from_row(updated)

    async def replace_matrix(self, role_id: int, permission_ids: list[int], actor):
        async with self.connection() as conn:
            row = await load_staff_role(conn, role_id)
            ensure_can_manage_role(actor, row)
            if actor.role_id is not None and int(actor.role_id) == int(role_id):
                raise ForbiddenError("You cannot change your own role matrix")

            staff_permissions = await fetch_staff_permissions(conn)
            by_id = {int(item["id"]): item for item in staff_permissions}
            requested = {int(value) for value in permission_ids if int(value) > 0}
            if requested - by_id.keys():
                raise ValidationError("Unknown or non-staff permission")

            current = {
                int(record["permission_id"])
                for record in await conn.fetch(
                    "SELECT permission_id FROM role_permissions WHERE role_id = $1 AND is_active = TRUE",
                    role_id,
                )
            }
            expanded = keep_what_a_box_held(requested, current, by_id)
            # Nobody hands out more than they hold; what the role already had is not theirs to judge.
            ensure_permissions_subset(actor, {by_id[pid]["name"] for pid in expanded - current})

            async with conn.transaction():
                await conn.execute(
                    "UPDATE role_permissions SET is_active = FALSE WHERE role_id = $1",
                    role_id,
                )
                for permission_id in sorted(expanded):
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
                await log_admin(
                    conn,
                    actor.id,
                    "admin.role.matrix",
                    "role",
                    role_id,
                    {"permissions": sorted(by_id[pid]["name"] for pid in expanded)},
                )
        return await self.role_matrix(role_id)

    async def delete_role(self, role_id: int, actor) -> None:
        async with self.connection() as conn:
            row = await load_staff_role(conn, role_id)
            ensure_can_manage_role(actor, row)
            if row["is_system"]:
                raise ForbiddenError("System role cannot be deleted")
            if await _assigned_users_count(conn, role_id):
                raise ConflictError("Role is assigned to users")
            await conn.execute("UPDATE roles SET is_active = FALSE WHERE id = $1", role_id)
            await log_admin(conn, actor.id, "admin.role.delete", "role", role_id, {"name": row["name"]})
