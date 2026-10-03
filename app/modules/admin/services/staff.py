import asyncpg

from app.core.exceptions import ConflictError, NotFoundError
from app.core.permissions import STAFF_PLATFORM_ROLES
from app.core.security_core import hash_password
from app.modules.admin.schemas.staff import AdminStaffMember
from app.modules.admin.services.common import actor_id_of, log_admin, normalize_query
from app.modules.roles.services.common import (
    assign_staff_role,
    ensure_can_manage_staff_user,
)

STAFF_COLUMNS = """
    u.id, u.email, u.first_name, u.last_name, u.phone,
    u.role::text AS platform_role, u.status::text AS status,
    u.role_id, r.name AS role_name, r.description AS role_description,
    u.created_at, u.updated_at
"""

STAFF_ROLES_SQL = ", ".join(f"'{role}'" for role in sorted(STAFF_PLATFORM_ROLES))


async def _load_staff_member(conn, user_id: int) -> AdminStaffMember:
    row = await conn.fetchrow(
        f"""
        SELECT {STAFF_COLUMNS}
        FROM users u
        LEFT JOIN roles r ON r.id = u.role_id
        WHERE u.id = $1 AND u.role::text IN ({STAFF_ROLES_SQL})
        """,
        user_id,
    )
    if not row:
        raise NotFoundError("Staff user not found")
    return AdminStaffMember.model_validate(dict(row))


class AdminStaffMixin:
    async def list_staff(self, query: str | None = None) -> dict:
        normalized = normalize_query(query)
        params: list = []
        search_sql = ""
        if normalized:
            params.append(f"%{normalized}%")
            search_sql = """
                AND (
                    u.email ILIKE $1
                    OR (u.first_name || ' ' || u.last_name) ILIKE $1
                    OR r.name ILIKE $1
                )
            """
        async with self.connection() as conn:
            rows = await conn.fetch(
                f"""
                SELECT {STAFF_COLUMNS}
                FROM users u
                LEFT JOIN roles r ON r.id = u.role_id
                WHERE u.role::text IN ({STAFF_ROLES_SQL})
                {search_sql}
                ORDER BY u.created_at DESC, u.id DESC
                """,
                *params,
            )
        items = [AdminStaffMember.model_validate(dict(row)) for row in rows]
        return {"items": items, "total": len(items)}

    async def create_staff(self, data, current_user) -> AdminStaffMember:
        email = data.email.lower()
        async with self.connection() as conn:
            async with conn.transaction():
                try:
                    row = await conn.fetchrow(
                        """
                        INSERT INTO users (
                            email, password_hash, first_name, last_name, phone,
                            role, status, created_at, updated_at
                        )
                        VALUES ($1, $2, $3, $4, $5, 'moderator', 'active', NOW(), NOW())
                        RETURNING id, role::text AS role
                        """,
                        email,
                        hash_password(data.password),
                        data.first_name.strip(),
                        data.last_name.strip(),
                        data.phone,
                    )
                except asyncpg.UniqueViolationError as exc:
                    raise ConflictError("Email already registered") from exc
                role = await assign_staff_role(conn, current_user, dict(row), data.role_id)
                await log_admin(
                    conn,
                    actor_id_of(current_user),
                    "admin.staff.create",
                    "user",
                    row["id"],
                    {"email": email, "role_id": role["id"], "role_name": role["name"]},
                )
            return await _load_staff_member(conn, row["id"])

    async def update_staff(self, user_id: int, data, current_user) -> AdminStaffMember:
        async with self.connection() as conn:
            target = await _load_staff_member(conn, user_id)
            target_row = {"id": target.id, "role": target.platform_role}
            ensure_can_manage_staff_user(current_user, target_row)
            details: dict = {}
            async with conn.transaction():
                if data.role_id is not None and data.role_id != target.role_id:
                    role = await assign_staff_role(conn, current_user, target_row, data.role_id)
                    details["role_id"] = role["id"]
                    details["role_name"] = role["name"]
                profile = data.model_dump(include={"first_name", "last_name", "phone"}, exclude_unset=True)
                if profile:
                    await conn.execute(
                        """
                        UPDATE users
                        SET first_name = COALESCE($2, first_name),
                            last_name = COALESCE($3, last_name),
                            phone = CASE WHEN $5 THEN $4 ELSE phone END,
                            updated_at = NOW()
                        WHERE id = $1
                        """,
                        user_id,
                        profile.get("first_name"),
                        profile.get("last_name"),
                        profile.get("phone"),
                        "phone" in profile,
                    )
                    details["profile"] = sorted(profile)
                if data.status is not None and data.status != target.status:
                    await conn.execute(
                        "UPDATE users SET status = $2::user_status, updated_at = NOW() WHERE id = $1",
                        user_id,
                        data.status,
                    )
                    details["status"] = data.status
                if data.password:
                    await conn.execute(
                        "UPDATE users SET password_hash = $2, updated_at = NOW() WHERE id = $1",
                        user_id,
                        hash_password(data.password),
                    )
                    details["password_changed"] = True
                if details:
                    await log_admin(
                        conn,
                        actor_id_of(current_user),
                        "admin.staff.update",
                        "user",
                        user_id,
                        details,
                    )
            return await _load_staff_member(conn, user_id)
