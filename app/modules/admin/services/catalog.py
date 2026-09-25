from app.core.exceptions import ForbiddenError, NotFoundError, ValidationError
from app.core.permissions import ensure_permission
from app.modules.admin.services.common import (
    actor_id_of,
    actor_role_of,
    catalog_view_sql,
    file_url,
    load_audit_history,
    load_owners,
    log_admin,
    normalize_query,
    notify_user,
    resolve_owner_user_id,
    with_reason,
)


def _catalog_search_sql(normalized: str, params: list) -> str:
    clauses = ["ci.status::text != 'deleted'"]
    if not normalized:
        return " AND ".join(clauses)
    params.append(f"%{normalized}%")
    idx = len(params)
    search = f"""
        (
            ci.title ILIKE ${idx}
            OR ci.description ILIKE ${idx}
            OR ci.id::text ILIKE ${idx}
    """
    if normalized.isdigit():
        params.append(int(normalized))
        search += f" OR ci.id = ${len(params)}"
    search += ")"
    clauses.append(search)
    return " AND ".join(clauses)


def _serialize_catalog_list_item(item, owner) -> dict:
    return {
        "id": item["id"],
        "title": item["title"],
        "type": item["type"],
        "status": item["status"],
        "category_name": item["category_name"],
        "preview_url": file_url(item["preview_url"]) if item["preview_url"] else item["preview_url"],
        "open_reports_count": item["open_reports_count"],
        "owner": owner,
        "created_at": item["created_at"],
        "views": item["views"] or 0,
        "leads": item["leads"] or 0,
    }


def _catalog_action_notification(action: str, item_title: str, reason: str | None):
    messages = {
        "approve": ("Позиция одобрена", f"Позиция «{item_title}» опубликована в каталоге."),
        "hide": ("Позиция скрыта", f"Позиция «{item_title}» скрыта модератором."),
        "request_changes": (
            "Нужны изменения",
            f"Для позиции «{item_title}» запрошены правки перед публикацией.",
        ),
        "delete": ("Позиция удалена", f"Позиция «{item_title}» удалена администратором."),
    }
    title, body = messages[action]
    return title, with_reason(body, reason)


CATALOG_LIST_SQL = """
    SELECT
        ci.id, ci.title, ci.type::text AS type, ci.status::text AS status,
        ci.actor_id, ci.created_at, cat.name AS category_name,
        COALESCE(st.views, 0) AS views,
        COALESCE(st.leads, 0) AS leads,
        (
            SELECT COUNT(*) FROM catalog_item_reports r
            WHERE r.item_id = ci.id AND r.status::text = 'open'
        ) AS open_reports_count,
        (
            SELECT m.file_url FROM item_media m
            WHERE m.item_id = ci.id AND m.media_type::text = 'image'
            ORDER BY m.sort_order
            LIMIT 1
        ) AS preview_url
    FROM catalog_items ci
    LEFT JOIN categories cat ON cat.id = ci.category_id
    LEFT JOIN item_stats st ON st.item_id = ci.id
"""


class AdminCatalogMixin:
    async def list_catalog_items(
        self,
        page: int = 1,
        page_size: int = 20,
        view: str = "all",
        query: str | None = None,
    ) -> dict:
        normalized = normalize_query(query)
        search_params: list = []
        search_sql = _catalog_search_sql(normalized, search_params)

        async def _count(conn, extra_sql: str | None):
            clauses = [search_sql] if search_sql else []
            if extra_sql:
                clauses.append(extra_sql)
            where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
            return int(
                await conn.fetchval(
                    f"SELECT COUNT(*) FROM catalog_items ci {where}",
                    *search_params,
                )
            )

        async with self.connection() as conn:
            view_sql = catalog_view_sql(view)
            total = await _count(conn, view_sql)
            count_values = {}
            for count_view in ("all", "products", "services", "draft", "reported", "hidden"):
                count_values[count_view] = await _count(conn, catalog_view_sql(count_view))
            params = list(search_params)
            clauses = [search_sql] if search_sql else []
            if view_sql:
                clauses.append(view_sql)
            where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
            params.extend([page_size, (page - 1) * page_size])
            items = await conn.fetch(
                f"""
                {CATALOG_LIST_SQL}
                {where}
                ORDER BY ci.created_at DESC, ci.id DESC
                LIMIT ${len(params) - 1} OFFSET ${len(params)}
                """,
                *params,
            )
            owners = await load_owners(conn, [item["actor_id"] for item in items])

        return {
            "items": [
                _serialize_catalog_list_item(item, owners.get(item["actor_id"]))
                for item in items
            ],
            "total": total,
            "page": page,
            "page_size": page_size,
            "pages": max(1, (total + page_size - 1) // page_size),
            "view_counts": count_values,
        }

    async def get_catalog_item_detail(self, item_id: int) -> dict:
        async with self.connection() as conn:
            item = await conn.fetchrow(
                f"{CATALOG_LIST_SQL} WHERE ci.id = $1",
                item_id,
            )
            if not item or item["status"] == "deleted":
                raise NotFoundError("Catalog item not found")
            detail = await conn.fetchrow(
                """
                SELECT ci.description, ci.created_at,
                       cat.id AS category_id, cat.name AS category_name, cat.slug AS category_slug,
                       ip.pricing_type::text AS pricing_type, ip.currency,
                       ip.fixed_price, ip.hourly_rate, ip.monthly_rate, ip.tiers
                FROM catalog_items ci
                LEFT JOIN categories cat ON cat.id = ci.category_id
                LEFT JOIN item_pricing ip ON ip.item_id = ci.id
                WHERE ci.id = $1
                """,
                item_id,
            )
            attributes = await conn.fetch(
                """
                SELECT id, name, value, value_type, sort_order
                FROM item_attributes WHERE item_id = $1
                ORDER BY sort_order
                """,
                item_id,
            )
            media = await conn.fetch(
                """
                SELECT id, file_name, file_url, media_type::text AS media_type, sort_order
                FROM item_media WHERE item_id = $1
                ORDER BY sort_order
                """,
                item_id,
            )
            reports = await conn.fetch(
                """
                SELECT r.id, r.reason::text AS reason, r.details, r.status::text AS status,
                       r.created_at, r.resolved_at,
                       u.id AS reporter_id, u.email, u.first_name, u.last_name
                FROM catalog_item_reports r
                JOIN users u ON u.id = r.reporter_user_id
                WHERE r.item_id = $1
                ORDER BY r.created_at DESC
                """,
                item_id,
            )
            owners = await load_owners(conn, [item["actor_id"]])
            owner = owners.get(item["actor_id"])
            history = await load_audit_history(conn, "catalog_item", str(item_id))

        payload = _serialize_catalog_list_item(item, owner)
        payload.update(
            {
                "description": detail["description"],
                "updated_at": detail["created_at"],
                "category": (
                    {
                        "id": detail["category_id"],
                        "name": detail["category_name"],
                        "slug": detail["category_slug"],
                    }
                    if detail["category_id"]
                    else None
                ),
                "attributes": [dict(row) for row in attributes],
                "pricing": (
                    {
                        "pricing_type": detail["pricing_type"],
                        "currency": detail["currency"],
                        "fixed_price": detail["fixed_price"],
                        "hourly_rate": detail["hourly_rate"],
                        "monthly_rate": detail["monthly_rate"],
                        "tiers": detail["tiers"] or [],
                    }
                    if detail["pricing_type"]
                    else None
                ),
                "media": [
                    {
                        "id": row["id"],
                        "file_name": row["file_name"],
                        "file_url": file_url(row["file_url"]),
                        "media_type": row["media_type"],
                        "sort_order": row["sort_order"],
                    }
                    for row in media
                ],
                "stats": {"views": item["views"] or 0, "leads": item["leads"] or 0},
                "owner": owner,
                "reports": [
                    {
                        "id": report["id"],
                        "reason": report["reason"],
                        "details": report["details"],
                        "status": report["status"],
                        "created_at": report["created_at"],
                        "resolved_at": report["resolved_at"],
                        "reporter": {
                            "id": report["reporter_id"],
                            "email": report["email"],
                            "name": f"{report['first_name']} {report['last_name']}".strip(),
                        },
                    }
                    for report in reports
                ],
                "history": history,
            }
        )
        return payload

    async def apply_catalog_action(
        self,
        item_id: int,
        action: str,
        current_user,
        reason: str | None = None,
    ) -> dict:
        allowed = {"approve", "hide", "request_changes", "delete"}
        if action not in allowed:
            raise ValidationError("Unsupported catalog action")
        ensure_permission(
            current_user,
            "admin_catalog.delete" if action == "delete" else "admin_catalog.update",
        )
        if action in {"hide", "request_changes", "delete"} and not (reason and reason.strip()):
            raise ValidationError("Reason is required for this action")

        async with self.connection() as conn:
            item = await conn.fetchrow(
                """
                SELECT id, title, actor_id, status::text AS status
                FROM catalog_items WHERE id = $1
                """,
                item_id,
            )
            if not item or item["status"] == "deleted":
                raise NotFoundError("Catalog item not found")
            previous_status = item["status"]
            if action == "approve":
                status = "active"
                report_status = "dismissed"
            elif action == "hide":
                status = "hidden"
                report_status = "resolved"
            elif action == "request_changes":
                status = "changes_requested"
                report_status = None
            else:
                status = "deleted"
                report_status = "resolved"

            await conn.execute(
                "UPDATE catalog_items SET status = $1::item_status WHERE id = $2",
                status,
                item_id,
            )
            if report_status:
                await conn.execute(
                    """
                    UPDATE catalog_item_reports
                    SET status = $1::catalog_item_report_status,
                        resolved_at = NOW(),
                        resolved_by_id = $2
                    WHERE item_id = $3 AND status::text = 'open'
                    """,
                    report_status,
                    actor_id_of(current_user),
                    item_id,
                )
            owner_user_id = await resolve_owner_user_id(conn, item["actor_id"])
            if owner_user_id:
                title, body = _catalog_action_notification(action, item["title"], reason)
                await notify_user(
                    conn,
                    owner_user_id,
                    title,
                    body,
                    href=f"/supplier/catalog/{item_id}",
                )
            await log_admin(
                conn,
                actor_id_of(current_user),
                f"admin.catalog.{action}",
                "catalog_item",
                item_id,
                {
                    "reason": reason.strip() if reason else None,
                    "previous_status": previous_status,
                    "status": status,
                },
            )
        return {"id": item_id, "action": action, "status": status}
