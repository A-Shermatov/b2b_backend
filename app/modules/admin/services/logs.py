import json
from datetime import timedelta

from app.core.exceptions import NotFoundError
from app.core.permissions import STAFF_PLATFORM_ROLES
from app.modules.admin.schemas.logs import (
    AdminLogDetail,
    AdminLogEventDetail,
    AdminLogEventItem,
    AdminLogField,
    AdminLogItem,
    AdminLogUser,
)
from app.modules.admin.services.common import normalize_query, parse_json
from app.modules.admin.services.logs_catalog import (
    EVENT_SQL,
    HTTP_SQL,
    action_kind,
    event_category,
    event_category_sql,
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
    al.id, al.action, al.details, al.ip_address, al.user_agent, al.created_at,
    al.resource_type, al.resource_id, al.actor_id, al.company_id,
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


def _row_user(row) -> AdminLogUser | None:
    if row["user_pk"] is None:
        return None
    name = f"{row['first_name'] or ''} {row['last_name'] or ''}".strip()
    return AdminLogUser(
        id=row["user_pk"],
        email=row["user_email"],
        name=name or row["user_email"],
        role=row["user_role"],
    )


def _audience(user: AdminLogUser | None) -> str:
    return "staff" if user and user.role in STAFF_PLATFORM_ROLES else "users"


def _is_http(action: str) -> bool:
    return action.startswith("http.")


def _serialize_event(row) -> dict:
    details = parse_json(row["details"])
    user = _row_user(row)
    return {
        "id": row["id"],
        "created_at": row["created_at"],
        "audience": _audience(user),
        "user": user,
        "action": row["action"],
        "category": event_category(row["action"]),
        "resource_type": row["resource_type"],
        "resource_id": row["resource_id"],
        "actor_id": row["actor_id"],
        "company_id": row["company_id"],
        "details": details if isinstance(details, dict) else ({"value": details} if details else {}),
        "ip_address": row["ip_address"],
    }


def _serialize_any(row):
    if _is_http(row["action"]):
        return AdminLogItem.model_validate(_serialize_item(row))
    return AdminLogEventItem.model_validate(_serialize_event(row))


def _serialize_item(row) -> dict:
    details = parse_json(row["details"]) or {}
    path = details.get("path")
    user = _row_user(row)
    return {
        "id": row["id"],
        "created_at": row["created_at"],
        "audience": _audience(user),
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


def _page_meta(total: int, page: int, page_size: int) -> dict:
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": max(1, (total + page_size - 1) // page_size) if total else 1,
    }


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
        source: str = "http",
        category: str | None = None,
        action: str | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
        user_id: int | None = None,
    ) -> dict:
        params: list = []
        clauses = [{"http": HTTP_SQL, "events": EVENT_SQL}.get(source, "TRUE")]
        if period in PERIOD_INTERVALS:
            params.append(PERIOD_INTERVALS[period])
            clauses.append(f"al.created_at >= NOW() - ${len(params)}::interval")
        if source == "http":
            if kind:
                clauses.append(kind_sql(kind, params))
            if section:
                clauses.append(section_sql(section, params))
            if status:
                clauses.append(status_sql(status, params))
        else:
            if category:
                clauses.append(event_category_sql(category, params))
            if action:
                params.append(action)
                clauses.append(f"al.action = ${len(params)}")
        if resource_type:
            params.append(resource_type)
            clauses.append(f"al.resource_type = ${len(params)}")
        if resource_id:
            params.append(str(resource_id))
            clauses.append(f"al.resource_id = ${len(params)}")
        if user_id is not None:
            params.append(user_id)
            clauses.append(f"al.user_id = ${len(params)}")
        normalized = normalize_query(query)
        if normalized:
            params.append(f"%{normalized}%")
            placeholder = f"${len(params)}"
            event_match = (
                ""
                if source == "http"
                else f" OR (al.action NOT LIKE 'http.%' AND al.action ILIKE {placeholder})"
            )
            clauses.append(
                f"""(
                    u.email ILIKE {placeholder}
                    OR concat_ws(' ', u.first_name, u.last_name) ILIKE {placeholder}
                    OR (u.id IS NULL AND al.details->'request_body'->>'email' ILIKE {placeholder})
                    {event_match}
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
            "items": [_serialize_any(row) for row in rows],
            **_page_meta(total, page, page_size),
            "audience_counts": audience_counts,
        }

    async def list_entity_logs(
        self,
        resource_type: str,
        resource_id: str,
        page: int = 1,
        page_size: int = 20,
    ) -> dict:
        where = f"""{EVENT_SQL} AND (
            (al.resource_type = $1 AND al.resource_id = $2)
            OR al.details ->> ($1 || '_id') = $2
        )"""
        async with self.connection() as conn:
            total = await conn.fetchval(
                f"SELECT COUNT(*) {LOG_FROM} WHERE {where}", resource_type, str(resource_id)
            )
            rows = await conn.fetch(
                f"""
                SELECT {LOG_COLUMNS}
                {LOG_FROM}
                WHERE {where}
                ORDER BY al.created_at DESC, al.id DESC
                LIMIT $3 OFFSET $4
                """,
                resource_type,
                str(resource_id),
                page_size,
                (page - 1) * page_size,
            )
        return {
            "items": [AdminLogEventItem.model_validate(_serialize_event(row)) for row in rows],
            **_page_meta(int(total or 0), page, page_size),
        }

    async def get_log(self, log_id: int) -> AdminLogDetail | AdminLogEventDetail:
        async with self.connection() as conn:
            row = await conn.fetchrow(
                f"""
                SELECT {LOG_COLUMNS}
                {LOG_FROM}
                WHERE al.id = $1
                """,
                log_id,
            )
        if not row:
            raise NotFoundError("Log entry not found")
        if not _is_http(row["action"]):
            return AdminLogEventDetail.model_validate(
                {**_serialize_event(row), "user_agent": row["user_agent"]}
            )
        details = parse_json(row["details"]) or {}
        return AdminLogDetail.model_validate({**_serialize_item(row), "fields": _fields(details)})
