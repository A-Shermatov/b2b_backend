import json
from datetime import timedelta

from app.core.exceptions import NotFoundError
from app.core.journal import journal
from app.core.permissions import STAFF_PLATFORM_ROLES
from app.modules.admin.schemas.logs import (
    AdminLogDetail,
    AdminLogEventDetail,
    AdminLogEventItem,
    AdminLogField,
    AdminLogItem,
    AdminLogPageDetail,
    AdminLogPageItem,
    AdminLogUser,
)
from app.modules.admin.services.common import normalize_query, parse_json
from app.modules.admin.services.logs_catalog import (
    EVENT_SQL,
    HTTP_SQL,
    PAGE_SQL,
    action_kind,
    event_category,
    event_category_sql,
    kind_sql,
    method_from_action,
    object_id_for,
    row_source,
    section_for,
    section_sql,
    status_group,
    status_sql,
)
from app.utils.client_info import is_identified, parse_user_agent

PERIOD_INTERVALS = {
    "1h": timedelta(hours=1),
    "24h": timedelta(days=1),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
}
REDACTED_VALUE = "***"
FIELD_VALUE_LIMIT = 300
RELATED_LIMIT = 20
_STAFF_ROLES = sorted(STAFF_PLATFORM_ROLES)

LOG_COLUMNS = """
    al.id, al.action, al.details, al.ip_address, al.user_agent, al.created_at,
    al.resource_type, al.resource_id, al.actor_id, al.company_id,
    al.method, al.path, al.status_code, al.duration_ms, al.request_id,
    al.device_key, al.session_key, al.country,
    u.id AS user_pk, u.email AS user_email, u.first_name, u.last_name,
    u.role::text AS user_role,
    d.device_key AS known_device, d.os_name, d.os_version, d.browser_name,
    d.browser_version, d.device_type, d.device_model, d.is_bot
"""
LOG_FROM = """
    FROM audit_logs al
    LEFT JOIN users u ON u.id = al.user_id
    LEFT JOIN client_devices d ON d.device_key = al.device_key
"""
SOURCE_SQL = {"http": HTTP_SQL, "events": EVENT_SQL, "pages": PAGE_SQL}


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


def joined(name: str | None, version: str | None) -> str | None:
    """«Windows» + «11» → «Windows 11»."""
    return " ".join(part for part in (name, version) if part) or None


def device_summary(row) -> dict | None:
    """The device of a journal row, short enough for a table."""
    if row["known_device"]:
        return {
            "key": row["device_key"],
            "identified": is_identified(row["device_key"]),
            "type": row["device_type"] or "other",
            "os": joined(row["os_name"], row["os_version"]),
            "browser": joined(row["browser_name"], row["browser_version"]),
            "model": row["device_model"],
            "is_bot": bool(row["is_bot"]),
        }
    if not row["user_agent"]:
        return None
    # A row from before devices were kept, or from something the registry does
    # not hold: its User-Agent still says what it was. No key: there is no card
    # to open.
    traits = parse_user_agent(row["user_agent"])
    return {
        "key": None,
        "identified": False,
        "type": traits.device_type,
        "os": joined(traits.os_name, traits.os_version),
        "browser": joined(traits.browser_name, traits.browser_version),
        "model": traits.device_model,
        "is_bot": traits.is_bot,
    }


def _details(row) -> dict:
    details = parse_json(row["details"])
    return details if isinstance(details, dict) else ({"value": details} if details else {})


def _path(row, details: dict) -> str | None:
    return row["path"] or details.get("path")


def _status_code(row, details: dict):
    return row["status_code"] if row["status_code"] is not None else details.get("status_code")


def _common(row, user: AdminLogUser | None) -> dict:
    return {
        "id": row["id"],
        "created_at": row["created_at"],
        "audience": _audience(user),
        "user": user,
        "ip_address": row["ip_address"],
        "country": row["country"],
        "device": device_summary(row),
    }


def _serialize_event(row) -> dict:
    user = _row_user(row)
    return {
        **_common(row, user),
        "action": row["action"],
        "category": event_category(row["action"]),
        "resource_type": row["resource_type"],
        "resource_id": row["resource_id"],
        "actor_id": row["actor_id"],
        "company_id": row["company_id"],
        "details": _details(row),
    }


def _serialize_item(row) -> dict:
    details = _details(row)
    path = _path(row, details)
    user = _row_user(row)
    return {
        **_common(row, user),
        "guest_email": None if user else _guest_email(details),
        "kind": action_kind(method_from_action(row["action"]), path),
        "section": section_for(path),
        "status": status_group(_status_code(row, details)),
        "object_id": object_id_for(path),
        "path": path,
        "repeats": int(details.get("repeats") or 0),
        "repeated_until": details.get("repeated_until"),
    }


def _seconds(row, details: dict) -> int | None:
    if row["duration_ms"] is not None:
        return int(row["duration_ms"] // 1000)
    seconds = details.get("seconds")
    return int(seconds) if isinstance(seconds, (int, float)) else None


def _serialize_page(row) -> dict:
    details = _details(row)
    path = row["path"] or "/"
    query = details.get("query")
    return {
        **_common(row, _row_user(row)),
        "path": path,
        "title": details.get("title"),
        "query": query if isinstance(query, dict) else {},
        "referrer": details.get("referrer"),
        "seconds": _seconds(row, details),
        "active_seconds": details.get("active_seconds"),
        "object_id": object_id_for(path),
    }


def _serialize_any(row):
    source = row_source(row["action"])
    if source == "http":
        return AdminLogItem.model_validate(_serialize_item(row))
    if source == "page":
        return AdminLogPageItem.model_validate(_serialize_page(row))
    return AdminLogEventItem.model_validate(_serialize_event(row))


def _technical(row, details: dict) -> dict:
    query = details.get("query")
    is_page = row_source(row["action"]) == "page"
    return {
        "method": row["method"] or (method_from_action(row["action"]) if _is_http(row["action"]) else None),
        "path": _path(row, details),
        "query": query if isinstance(query, dict) else {},
        "status_code": _status_code(row, details),
        # For a page this column holds the time spent on it, shown on its own.
        "duration_ms": None if is_page else row["duration_ms"],
        "request_id": str(row["request_id"]) if row["request_id"] else None,
        "visit": row["session_key"],
        "user_agent": row["user_agent"],
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
        ip: str | None = None,
        device: str | None = None,
        visit: str | None = None,
    ) -> dict:
        # Views wait in memory for about a second; whoever opens the journal
        # sees them at once.
        await journal.flush()
        params: list = []
        clauses = [SOURCE_SQL.get(source, "TRUE")]
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
        elif source != "pages":
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
        for column, value in (
            ("al.user_id", user_id),
            ("al.ip_address", (ip or "").strip() or None),
            ("al.device_key", (device or "").strip() or None),
            ("al.session_key", (visit or "").strip() or None),
        ):
            if value is not None:
                params.append(value)
                clauses.append(f"{column} = ${len(params)}")
        normalized = normalize_query(query)
        if normalized:
            params.append(f"%{normalized}%")
            placeholder = f"${len(params)}"
            # Besides the person: the address they came from, and what they
            # opened or (among events) what happened.
            extra = (
                f" OR ({EVENT_SQL} AND al.action ILIKE {placeholder})"
                if source in {"events", "all"}
                else f" OR al.path ILIKE {placeholder}"
            )
            clauses.append(
                f"""(
                    u.email ILIKE {placeholder}
                    OR concat_ws(' ', u.first_name, u.last_name) ILIKE {placeholder}
                    OR (u.id IS NULL AND al.details->'request_body'->>'email' ILIKE {placeholder})
                    OR al.ip_address ILIKE {placeholder}
                    {extra}
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

    async def get_log(self, log_id: int) -> AdminLogDetail | AdminLogEventDetail | AdminLogPageDetail:
        await journal.flush()
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
            related = []
            if row["request_id"]:
                # Everything written while the same request was served: the
                # request itself and the events it caused.
                related = await conn.fetch(
                    """
                    SELECT id, action, created_at FROM audit_logs
                    WHERE request_id = $1 AND id <> $2
                    ORDER BY id
                    LIMIT $3
                    """,
                    row["request_id"],
                    log_id,
                    RELATED_LIMIT,
                )
            device = device_summary(row)
            if device and row["known_device"]:
                more = await conn.fetchrow(
                    """
                    SELECT d.language, d.timezone, d.screen, d.first_seen_at, d.last_seen_at,
                           (SELECT COUNT(*) FROM client_device_users du
                            WHERE du.device_key = d.device_key) AS users_count
                    FROM client_devices d
                    WHERE d.device_key = $1
                    """,
                    row["device_key"],
                )
                if more:
                    device = {**device, **dict(more)}

        details = _details(row)
        extra = {
            "device": device,
            "technical": _technical(row, details),
            "related": [
                {
                    "id": item["id"],
                    "source": row_source(item["action"]),
                    "action": item["action"],
                    "created_at": item["created_at"],
                }
                for item in related
            ],
        }
        source = row_source(row["action"])
        if source == "event":
            return AdminLogEventDetail.model_validate(
                {**_serialize_event(row), "user_agent": row["user_agent"], **extra}
            )
        if source == "page":
            return AdminLogPageDetail.model_validate({**_serialize_page(row), **extra})
        return AdminLogDetail.model_validate(
            {**_serialize_item(row), "fields": _fields(details), **extra}
        )
