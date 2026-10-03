import json
from datetime import timedelta

from app.core.exceptions import NotFoundError
from app.core.permissions import STAFF_PLATFORM_ROLES
from app.modules.admin.schemas.logs import (
    AdminLogDetail,
    AdminLogField,
    AdminLogItem,
    AdminLogUser,
)
from app.modules.admin.services.common import normalize_query, parse_json
from app.modules.admin.services.logs_catalog import (
    action_kind,
    kind_sql,
    method_from_action,
    object_id_for,
    section_for,
    section_sql,
    status_group,
    status_sql,
)

PERIOD_INTERVALS = {"24h": timedelta(days=1), "7d": timedelta(days=7), "30d": timedelta(days=30)}
REDACTED_VALUE = "***"
FIELD_VALUE_LIMIT = 300
_STAFF_ROLES = sorted(STAFF_PLATFORM_ROLES)

LOG_COLUMNS = """
    al.id, al.action, al.details, al.ip_address, al.created_at,
    u.id AS user_pk, u.email AS user_email, u.first_name, u.last_name,
    u.role::text AS user_role
"""
LOG_FROM = """
    FROM audit_logs al
    LEFT JOIN users u ON u.id = al.user_id
"""


def _is_staff_sql(params: list) -> str:
    params.append(_STAFF_ROLES)
    return f"(u.id IS NOT NULL AND u.role::text = ANY(${len(params)}::text[]))"


def _request_body(details: dict) -> dict:
    body = details.get("request_body")
    return body if isinstance(body, dict) else {}


def _guest_email(details: dict) -> str | None:
    email = _request_body(details).get("email")
    return email if isinstance(email, str) and email and email != REDACTED_VALUE else None


def _serialize_item(row) -> dict:
    details = parse_json(row["details"]) or {}
    path = details.get("path")
    user = None
    if row["user_pk"] is not None:
        name = f"{row['first_name'] or ''} {row['last_name'] or ''}".strip()
        user = AdminLogUser(
            id=row["user_pk"],
            email=row["user_email"],
            name=name or row["user_email"],
            role=row["user_role"],
        )
    return {
        "id": row["id"],
        "created_at": row["created_at"],
        "audience": "staff" if user and user.role in STAFF_PLATFORM_ROLES else "users",
        "user": user,
        "guest_email": None if user else _guest_email(details),
        "kind": action_kind(method_from_action(row["action"]), path),
        "section": section_for(path),
        "status": status_group(details.get("status_code")),
        "object_id": object_id_for(path),
        "ip_address": row["ip_address"],
    }


def _field_value(value) -> str:
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False, default=str)
    elif value is None:
        text = "—"
    elif isinstance(value, bool):
        text = "Да" if value else "Нет"
    else:
        text = str(value)
    return text if len(text) <= FIELD_VALUE_LIMIT else f"{text[:FIELD_VALUE_LIMIT]}…"


def _fields(details: dict) -> list[AdminLogField]:
    return [
        AdminLogField(
            key=str(key),
            value="" if value == REDACTED_VALUE else _field_value(value),
            hidden=value == REDACTED_VALUE,
        )
        for key, value in _request_body(details).items()
    ]


class AdminLogsMixin:
    async def list_logs(
        self,
        audience: str = "users",
        kind: str | None = None,
        section: str | None = None,
        status: str | None = None,
        query: str | None = None,
        period: str = "7d",
        page: int = 1,
        page_size: int = 20,
    ) -> dict:
        params: list = []
        clauses = ["al.action LIKE 'http.%'"]
        if period in PERIOD_INTERVALS:
            params.append(PERIOD_INTERVALS[period])
            clauses.append(f"al.created_at >= NOW() - ${len(params)}::interval")
        if kind:
            clauses.append(kind_sql(kind, params))
        if section:
            clauses.append(section_sql(section, params))
        if status:
            clauses.append(status_sql(status, params))
        normalized = normalize_query(query)
        if normalized:
            params.append(f"%{normalized}%")
            placeholder = f"${len(params)}"
            clauses.append(
                f"""(
                    u.email ILIKE {placeholder}
                    OR concat_ws(' ', u.first_name, u.last_name) ILIKE {placeholder}
                    OR (u.id IS NULL AND al.details->'request_body'->>'email' ILIKE {placeholder})
                )"""
            )
        base_where = " AND ".join(clauses)
        staff_sql = _is_staff_sql(params)

        async with self.connection() as conn:
            counts = await conn.fetchrow(
                f"""
                SELECT
                    COUNT(*) FILTER (WHERE {staff_sql}) AS staff,
                    COUNT(*) FILTER (WHERE NOT {staff_sql}) AS users
                {LOG_FROM}
                WHERE {base_where}
                """,
                *params,
            )
            audience_sql = staff_sql if audience == "staff" else f"NOT {staff_sql}"
            list_params = [*params, page_size, (page - 1) * page_size]
            rows = await conn.fetch(
                f"""
                SELECT {LOG_COLUMNS}
                {LOG_FROM}
                WHERE {base_where} AND {audience_sql}
                ORDER BY al.created_at DESC, al.id DESC
                LIMIT ${len(list_params) - 1} OFFSET ${len(list_params)}
                """,
                *list_params,
            )

        audience_counts = {"staff": int(counts["staff"]), "users": int(counts["users"])}
        total = audience_counts["staff" if audience == "staff" else "users"]
        return {
            "items": [AdminLogItem.model_validate(_serialize_item(row)) for row in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
            "pages": max(1, (total + page_size - 1) // page_size) if total else 1,
            "audience_counts": audience_counts,
        }

    async def get_log(self, log_id: int) -> AdminLogDetail:
        async with self.connection() as conn:
            row = await conn.fetchrow(
                f"""
                SELECT {LOG_COLUMNS}
                {LOG_FROM}
                WHERE al.id = $1 AND al.action LIKE 'http.%'
                """,
                log_id,
            )
        if not row:
            raise NotFoundError("Log entry not found")
        details = parse_json(row["details"]) or {}
        return AdminLogDetail.model_validate({**_serialize_item(row), "fields": _fields(details)})
