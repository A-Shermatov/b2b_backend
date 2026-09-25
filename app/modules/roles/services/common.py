from app.core.exceptions import ForbiddenError, NotFoundError
from app.core.permissions import (
    PROTECTED_ROLE_NAMES,
    action_bucket,
    resource_alias_ru,
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
    )


def assert_role_mutable(row, *, renaming: bool = False) -> None:
    if row["name"] in PROTECTED_ROLE_NAMES and renaming:
        raise ForbiddenError("This role cannot be renamed")
    if row["name"] in PROTECTED_ROLE_NAMES and not renaming:
        raise ForbiddenError("This role cannot be deleted")


async def load_role(conn, role_id: int):
    row = await conn.fetchrow(
        """
        SELECT id, name, description, is_system, is_active
        FROM roles
        WHERE id = $1 AND is_active = TRUE
        """,
        role_id,
    )
    if not row:
        raise NotFoundError("Role not found")
    return row


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
            if enabled_ids is not None and representative is not None:
                enabled = bool(ids & enabled_ids)
            cells[bucket] = PermissionActionCell(
                permission_id=int(representative["id"]) if representative else None,
                code=representative["name"] if representative else None,
                enabled=enabled,
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


async def expand_permission_ids(conn, permission_ids: list[int]) -> list[int]:
    selected = {int(value) for value in permission_ids if int(value) > 0}
    rows = await conn.fetch(
        """
        SELECT id, resource, action
        FROM permissions
        WHERE is_active = TRUE
        """
    )
    if not selected:
        return []
    selected_buckets: set[tuple[str, str]] = set()
    for row in rows:
        if int(row["id"]) not in selected:
            continue
        bucket = action_bucket(row["action"])
        if bucket is None:
            continue
        selected_buckets.add((row["resource"], bucket))
    expanded = set(selected)
    for row in rows:
        bucket = action_bucket(row["action"])
        if bucket and (row["resource"], bucket) in selected_buckets:
            expanded.add(int(row["id"]))
    return sorted(expanded)
