from app.core.exceptions import ForbiddenError, NotFoundError
from app.core.permissions import (
    PROTECTED_ROLE_NAMES,
    STAFF_ROLE_SCOPE,
    action_alias_ru,
    action_bucket,
    is_staff_resource,
    is_superadmin,
    permission_names,
    resource_alias_ru,
    staff_platform_role_for,
    staff_role_rank,
    user_staff_rank,
)
from app.modules.roles.schemas.common import (
    PermissionActionCell,
    PermissionMatrixRow,
    RoleResponse,
)

_BUCKET_REPRESENTATIVE = {
    "read": ("view",),
    "write": (
        "update",
        "create",
        "manage",
        "publish",
        "approve",
        "accept",
        "fund",
        "invite",
        "close",
        "block",
        "force_complete",
        "suspend",
        "export",
    ),
    "delete": ("delete",),
}


def role_from_row(row) -> RoleResponse:
    return RoleResponse(
        id=row["id"],
        name=row["name"],
        description=row["description"],
        is_system=row["is_system"],
        is_active=row["is_active"],
        users_count=int(row.get("users_count") or 0),
    )


async def load_staff_role(conn, role_id: int):
    row = await conn.fetchrow(
        """
        SELECT id, name, description, is_system, is_active, scope
        FROM roles
        WHERE id = $1 AND is_active = TRUE
        """,
        role_id,
    )
    if not row or row["scope"] != STAFF_ROLE_SCOPE or row["name"] in PROTECTED_ROLE_NAMES:
        raise NotFoundError("Role not found")
    return row


async def fetch_staff_permissions(conn) -> list[dict]:
    rows = await conn.fetch(
        """
        SELECT id, name, resource, action
        FROM permissions
        WHERE is_active = TRUE
        ORDER BY resource, action
        """
    )
    return [dict(row) for row in rows if is_staff_resource(row["resource"])]


async def role_permission_names(conn, role_id: int) -> set[str]:
    rows = await conn.fetch(
        """
        SELECT p.name
        FROM role_permissions rp
        JOIN permissions p ON p.id = rp.permission_id
        WHERE rp.role_id = $1 AND rp.is_active = TRUE AND p.is_active = TRUE
        """,
        role_id,
    )
    return {row["name"] for row in rows}


def ensure_can_manage_role(actor, role_row) -> None:
    """Only staff with a strictly higher rank may change or grant a role."""
    if is_superadmin(actor):
        return
    if staff_role_rank(role_row["name"]) >= user_staff_rank(actor):
        raise ForbiddenError("You cannot manage this role")


def ensure_permissions_subset(actor, names: set[str]) -> None:
    """Staff cannot grant permissions they do not hold themselves."""
    if is_superadmin(actor):
        return
    missing = names - permission_names(actor)
    if missing:
        raise ForbiddenError("You cannot grant permissions you do not have")


def ensure_can_manage_staff_user(actor, target) -> None:
    actor_id = actor["id"] if isinstance(actor, dict) else actor.id
    if int(target["id"]) == int(actor_id):
        raise ForbiddenError("You cannot change your own staff account")
    if user_staff_rank(target) >= user_staff_rank(actor):
        raise ForbiddenError("You cannot change this staff account")


async def assign_staff_role(conn, actor, target, role_id: int):
    """Set users.role_id and the matching users.role for a staff account."""
    ensure_can_manage_staff_user(actor, target)
    role = await load_staff_role(conn, role_id)
    ensure_can_manage_role(actor, role)
    ensure_permissions_subset(actor, await role_permission_names(conn, role_id))
    await conn.execute(
        """
        UPDATE users
        SET role = $1::user_role, role_id = $2, updated_at = NOW()
        WHERE id = $3
        """,
        staff_platform_role_for(role["name"]),
        role_id,
        target["id"],
    )
    return role


def _pick_representative(rows: list[dict], bucket: str) -> dict | None:
    by_action = {(row["action"] or "").lower(): row for row in rows}
    for action in _BUCKET_REPRESENTATIVE.get(bucket, ()):
        if action in by_action:
            return by_action[action]
    return rows[0] if rows else None


def build_matrix_rows(permission_rows: list[dict], enabled_ids: set[int] | None) -> list[PermissionMatrixRow]:
    grouped: dict[str, dict[str, list[dict]]] = {}
    for row in permission_rows:
        bucket = action_bucket(row["action"])
        if bucket is None:
            continue
        grouped.setdefault(row["resource"], {}).setdefault(bucket, []).append(dict(row))

    result: list[PermissionMatrixRow] = []
    for resource in sorted(grouped):
        cells = {}
        for bucket in ("read", "write", "delete"):
            bucket_rows = grouped[resource].get(bucket) or []
            representative = _pick_representative(bucket_rows, bucket)
            ids = {int(item["id"]) for item in bucket_rows}
            enabled = False
            missing: list[str] = []
            if enabled_ids is not None and representative is not None:
                enabled = bool(ids & enabled_ids)
                if enabled:
                    missing = sorted(
                        action_alias_ru(item["action"])
                        for item in bucket_rows
                        if int(item["id"]) not in enabled_ids
                    )
            cells[bucket] = PermissionActionCell(
                permission_id=int(representative["id"]) if representative else None,
                code=representative["name"] if representative else None,
                enabled=enabled,
                missing=missing,
            )
        result.append(
            PermissionMatrixRow(
                resource=resource,
                alias_ru=resource_alias_ru(resource),
                read=cells["read"],
                write=cells["write"],
                delete=cells["delete"],
            )
        )
    return result


def keep_what_a_box_held(
    requested: set[int],
    current: set[int],
    permissions: dict[int, dict],
) -> set[int]:
    """The rights a role gets when its table is saved.

    A box of the table («Изменение» of a section) stands for several actions. A
    box ticked for the first time gives all of them. A box that was ticked
    before and still is keeps exactly what it held: a built-in role may hold
    only some actions of a box (a moderator changes companies but does not block
    them), and saving the table for another reason must not hand it the rest.
    """
    bucket_of = {
        pid: (item["resource"], action_bucket(item["action"])) for pid, item in permissions.items()
    }
    ticked = {bucket_of[pid] for pid in requested if bucket_of[pid][1]}
    # Anything allowed in a section lets its pages be opened.
    ticked |= {(resource, "read") for resource, _bucket in ticked}
    held = {bucket_of[pid] for pid in current if pid in bucket_of}
    granted: set[int] = set()
    for pid, box in bucket_of.items():
        if box not in ticked:
            continue
        if box in held and pid not in current:
            continue
        granted.add(pid)
    return granted
