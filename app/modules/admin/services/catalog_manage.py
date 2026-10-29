import json

from app.core.exceptions import AppError, NotFoundError, ValidationError
from app.core.permissions import ensure_permission
from app.modules.admin.services.common import (
    actor_id_of,
    file_url,
    load_owners,
    log_admin,
    normalize_query,
    notify_user,
    pages_for,
    parse_json,
    resolve_owner_user_id,
    with_reason,
)
from app.modules.catalog.services.items import LIST_PRICE_EXPR

CATALOG_STATUSES = (
    "draft",
    "pending_review",
    "changes_requested",
    "active",
    "hidden",
    "archived",
    "deleted",
)

MAX_ITEM_MEDIA = 30

SORT_COLUMNS = {
    "created_at": "ci.created_at",
    "updated_at": "COALESCE(ci.updated_at, ci.created_at)",
    "price": f"({LIST_PRICE_EXPR})",
    "title": "LOWER(ci.title)",
    "views": "COALESCE(st.views, 0)",
    "reports": "open_reports_count",
}

FIELD_LABELS = {
    "type": "тип",
    "category_id": "категория",
    "title": "название",
    "description": "описание",
    "invoice": "работа по счёту",
    "pricing": "цена",
    "attributes": "характеристики",
}

MANAGE_FROM_SQL = """
    FROM catalog_items ci
    JOIN actors a ON a.id = ci.actor_id
    LEFT JOIN companies c ON c.id = a.company_id
    LEFT JOIN users ou ON ou.id = c.owner_id
    LEFT JOIN users iu ON iu.id = a.user_id
    LEFT JOIN categories cat ON cat.id = ci.category_id
    LEFT JOIN item_stats st ON st.item_id = ci.id
    LEFT JOIN item_pricing ip ON ip.item_id = ci.id
"""

MANAGE_COLUMNS_SQL = f"""
    ci.id, ci.title, ci.type::text AS type, ci.status::text AS status,
    ci.actor_id, ci.category_id, ci.created_at,
    COALESCE(ci.updated_at, ci.created_at) AS updated_at,
    cat.name AS category_name,
    COALESCE(st.views, 0) AS views,
    COALESCE(st.leads, 0) AS leads,
    ip.pricing_type::text AS pricing_type, ip.currency,
    ({LIST_PRICE_EXPR}) AS list_price,
    (
        SELECT COUNT(*) FROM catalog_item_reports r
        WHERE r.item_id = ci.id AND r.status::text = 'open'
    ) AS open_reports_count,
    (
        SELECT COUNT(*) FROM catalog_item_reports r WHERE r.item_id = ci.id
    ) AS reports_count,
    (
        SELECT m.file_url FROM item_media m
        WHERE m.item_id = ci.id AND m.media_type::text = 'image'
        ORDER BY m.sort_order, m.id
        LIMIT 1
    ) AS preview_url
"""


def _media_type_from_content_type(content_type: str | None) -> str:
    if not content_type:
        return "document"
    if content_type.startswith("image/"):
        return "image"
    if content_type.startswith("video/"):
        return "video"
    return "document"


def _pricing_snapshot(row) -> dict | None:
    if not row:
        return None
    tiers = parse_json(row["tiers"]) if row["tiers"] is not None else []
    return {
        "pricing_type": row["pricing_type"],
        "currency": row["currency"],
        "fixed_price": row["fixed_price"],
        "hourly_rate": row["hourly_rate"],
        "monthly_rate": row["monthly_rate"],
        "tiers": tiers if isinstance(tiers, list) else [],
    }


def _normalize_pricing(pricing) -> dict:
    data = {
        "pricing_type": pricing.pricing_type,
        "currency": pricing.currency.strip().upper(),
        "fixed_price": pricing.fixed_price,
        "hourly_rate": pricing.hourly_rate,
        "monthly_rate": pricing.monthly_rate,
        "tiers": sorted(
            ({"min_qty": tier.min_qty, "price": tier.price} for tier in pricing.tiers),
            key=lambda tier: tier["min_qty"],
        ),
    }
    required = {
        "fixed": "fixed_price",
        "hourly": "hourly_rate",
        "monthly": "monthly_rate",
    }.get(data["pricing_type"])
    if required and data[required] is None:
        raise ValidationError(f"Field {required} is required for {data['pricing_type']} pricing")
    if data["pricing_type"] == "tiered" and not data["tiers"]:
        raise ValidationError("Tiered pricing requires at least one tier")
    return data


def _attributes_snapshot(rows) -> list[dict]:
    return [
        {"name": row["name"], "value": row["value"], "value_type": row["value_type"]}
        for row in rows
    ]


class AdminCatalogManageMixin:
    async def list_catalog_manage(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        item_type: str | None = None,
        statuses: list[str] | None = None,
        company_id: int | None = None,
        actor_id: int | None = None,
        supplier: str | None = None,
        category_id: int | None = None,
        price_min: float | None = None,
        price_max: float | None = None,
        has_reports: bool | None = None,
        query: str | None = None,
        sort: str = "created_at",
        order: str = "desc",
        include_deleted: bool = False,
    ) -> dict:
        params: list = []
        clauses: list[str] = []

        def add_param(value) -> str:
            params.append(value)
            return f"${len(params)}"

        if item_type:
            clauses.append(f"ci.type::text = {add_param(item_type)}")
        if company_id is not None:
            clauses.append(f"a.company_id = {add_param(company_id)}")
        if actor_id is not None:
            clauses.append(f"ci.actor_id = {add_param(actor_id)}")
        supplier_text = normalize_query(supplier)
        if supplier_text:
            ref = add_param(f"%{supplier_text}%")
            clauses.append(
                f"""
                (
                    a.display_name ILIKE {ref}
                    OR c.title ILIKE {ref}
                    OR ou.email ILIKE {ref}
                    OR iu.email ILIKE {ref}
                    OR TRIM(COALESCE(iu.first_name, '') || ' ' || COALESCE(iu.last_name, '')) ILIKE {ref}
                )
                """
            )
        if category_id is not None:
            ref = add_param(category_id)
            clauses.append(f"(ci.category_id = {ref} OR cat.parent_id = {ref})")
        if price_min is not None:
            clauses.append(f"({LIST_PRICE_EXPR}) >= {add_param(price_min)}")
        if price_max is not None:
            clauses.append(f"({LIST_PRICE_EXPR}) <= {add_param(price_max)}")
        if has_reports is not None:
            exists_sql = """
                EXISTS (
                    SELECT 1 FROM catalog_item_reports r
                    WHERE r.item_id = ci.id AND r.status::text = 'open'
                )
            """
            clauses.append(exists_sql if has_reports else f"NOT {exists_sql}")
        normalized = normalize_query(query)
        if normalized:
            ref = add_param(f"%{normalized}%")
            search = f"(ci.title ILIKE {ref} OR ci.description ILIKE {ref}"
            if normalized.isdigit():
                search += f" OR ci.id = {add_param(int(normalized))}"
            clauses.append(search + ")")

        base_where = " AND ".join(clauses) if clauses else "TRUE"
        list_clauses = [base_where]
        list_params = list(params)
        if statuses:
            list_params.append(list(statuses))
            list_clauses.append(f"ci.status::text = ANY(${len(list_params)}::text[])")
        elif not include_deleted:
            list_clauses.append("ci.status::text <> 'deleted'")
        list_where = " AND ".join(list_clauses)

        sort_sql = SORT_COLUMNS.get(sort, SORT_COLUMNS["created_at"])
        direction = "ASC" if order == "asc" else "DESC"
        nulls = " NULLS LAST" if sort == "price" else ""

        async with self.connection() as conn:
            count_rows = await conn.fetch(
                f"""
                SELECT ci.status::text AS status, COUNT(*) AS cnt
                {MANAGE_FROM_SQL}
                WHERE {base_where}
                GROUP BY ci.status
                """,
                *params,
            )
            total = int(
                await conn.fetchval(
                    f"SELECT COUNT(*) {MANAGE_FROM_SQL} WHERE {list_where}",
                    *list_params,
                )
            )
            list_params.extend([page_size, (page - 1) * page_size])
            rows = await conn.fetch(
                f"""
                SELECT {MANAGE_COLUMNS_SQL}
                {MANAGE_FROM_SQL}
                WHERE {list_where}
                ORDER BY {sort_sql} {direction}{nulls}, ci.id {direction}
                LIMIT ${len(list_params) - 1} OFFSET ${len(list_params)}
                """,
                *list_params,
            )
            owners = await load_owners(conn, [row["actor_id"] for row in rows])

        counts = {status: 0 for status in CATALOG_STATUSES}
        for row in count_rows:
            counts[row["status"]] = int(row["cnt"])
        counts["all"] = sum(value for key, value in counts.items() if key != "deleted")

        return {
            "items": [
                {
                    "id": row["id"],
                    "title": row["title"],
                    "type": row["type"],
                    "status": row["status"],
                    "category_id": row["category_id"],
                    "category_name": row["category_name"],
                    "preview_url": file_url(row["preview_url"]),
                    "open_reports_count": row["open_reports_count"],
                    "reports_count": row["reports_count"],
                    "owner": owners.get(row["actor_id"]),
                    "pricing_type": row["pricing_type"],
                    "currency": row["currency"],
                    "price": row["list_price"],
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                    "views": row["views"],
                    "leads": row["leads"],
                }
                for row in rows
            ],
            "total": total,
            "page": page,
            "page_size": page_size,
            "pages": pages_for(total, page_size),
            "status_counts": counts,
        }

    async def _editable_catalog_item(self, conn, item_id: int):
        item = await conn.fetchrow(
            """
            SELECT id, actor_id, title, description, type::text AS type, category_id,
                   COALESCE(invoice, TRUE) AS invoice, status::text AS status
            FROM catalog_items WHERE id = $1
            """,
            item_id,
        )
        if not item:
            raise NotFoundError("Catalog item not found")
        if item["status"] == "deleted":
            raise ValidationError("Catalog item is deleted; restore it first")
        return item

    async def get_editable_catalog_item_actor(self, item_id: int) -> int:
        async with self.connection() as conn:
            item = await self._editable_catalog_item(conn, item_id)
        return item["actor_id"]

    async def _notify_catalog_owner(self, conn, actor_id: int, item_id: int, title: str, body: str):
        owner_user_id = await resolve_owner_user_id(conn, actor_id)
        if owner_user_id:
            await notify_user(
                conn,
                owner_user_id,
                title,
                body,
                href=f"/supplier/catalog/{item_id}",
            )

    async def update_catalog_item_admin(self, item_id: int, data, current_user) -> dict:
        ensure_permission(current_user, "admin_catalog.update")
        fields = data.model_fields_set - {"reason", "notify_owner"}
        async with self.connection() as conn, conn.transaction():
            item = await self._editable_catalog_item(conn, item_id)
            changes: dict[str, dict] = {}

            for field in ("type", "category_id", "title", "invoice"):
                value = getattr(data, field)
                if field in fields and value is not None:
                    if field == "title":
                        value = value.strip()
                        if len(value) < 2:
                            raise ValidationError("Title is too short")
                    if value != item[field]:
                        changes[field] = {"before": item[field], "after": value}
            if "description" in fields:
                value = (data.description or "").strip() or None
                if value != item["description"]:
                    changes["description"] = {"before": item["description"], "after": value}

            if "category_id" in changes:
                exists = await conn.fetchval(
                    "SELECT 1 FROM categories WHERE id = $1",
                    changes["category_id"]["after"],
                )
                if not exists:
                    raise ValidationError("Category not found")

            pricing_after = None
            if "pricing" in fields and data.pricing is not None:
                pricing_row = await conn.fetchrow(
                    """
                    SELECT pricing_type::text AS pricing_type, currency,
                           fixed_price, hourly_rate, monthly_rate, tiers
                    FROM item_pricing WHERE item_id = $1
                    """,
                    item_id,
                )
                pricing_before = _pricing_snapshot(pricing_row)
                pricing_after = _normalize_pricing(data.pricing)
                if pricing_after != pricing_before:
                    changes["pricing"] = {"before": pricing_before, "after": pricing_after}

            attributes_after = None
            if "attributes" in fields and data.attributes is not None:
                attribute_rows = await conn.fetch(
                    """
                    SELECT name, value, value_type FROM item_attributes
                    WHERE item_id = $1 ORDER BY sort_order, id
                    """,
                    item_id,
                )
                attributes_before = _attributes_snapshot(attribute_rows)
                attributes_after = [
                    {
                        "name": attr.name.strip(),
                        "value": attr.value.strip(),
                        "value_type": attr.value_type or "text",
                    }
                    for attr in data.attributes
                ]
                if attributes_after != attributes_before:
                    changes["attributes"] = {
                        "before": attributes_before,
                        "after": attributes_after,
                    }

            if changes:
                scalar = {
                    key: changes[key]["after"]
                    for key in ("type", "category_id", "title", "description", "invoice")
                    if key in changes
                }
                if scalar:
                    assignments = []
                    values = []
                    for key, value in scalar.items():
                        values.append(value)
                        cast = "::catalog_item_type" if key == "type" else ""
                        assignments.append(f"{key} = ${len(values)}{cast}")
                    values.append(item_id)
                    await conn.execute(
                        f"UPDATE catalog_items SET {', '.join(assignments)} WHERE id = ${len(values)}",
                        *values,
                    )
                else:
                    await conn.execute(
                        "UPDATE catalog_items SET updated_at = NOW() WHERE id = $1",
                        item_id,
                    )
                if "pricing" in changes and pricing_after is not None:
                    await conn.execute(
                        """
                        INSERT INTO item_pricing (
                            item_id, pricing_type, currency,
                            fixed_price, hourly_rate, monthly_rate, tiers
                        )
                        VALUES ($1, $2::pricing_type, $3, $4, $5, $6, $7::jsonb)
                        ON CONFLICT (item_id) DO UPDATE SET
                            pricing_type = EXCLUDED.pricing_type,
                            currency = EXCLUDED.currency,
                            fixed_price = EXCLUDED.fixed_price,
                            hourly_rate = EXCLUDED.hourly_rate,
                            monthly_rate = EXCLUDED.monthly_rate,
                            tiers = EXCLUDED.tiers
                        """,
                        item_id,
                        pricing_after["pricing_type"],
                        pricing_after["currency"],
                        pricing_after["fixed_price"],
                        pricing_after["hourly_rate"],
                        pricing_after["monthly_rate"],
                        json.dumps(pricing_after["tiers"]),
                    )
                if "attributes" in changes and attributes_after is not None:
                    await conn.execute("DELETE FROM item_attributes WHERE item_id = $1", item_id)
                    for idx, attr in enumerate(attributes_after):
                        await conn.execute(
                            """
                            INSERT INTO item_attributes (
                                item_id, name, value, value_type, sort_order
                            )
                            VALUES ($1, $2, $3, $4, $5)
                            """,
                            item_id,
                            attr["name"],
                            attr["value"],
                            attr["value_type"],
                            idx,
                        )
                reason = data.reason.strip() if data.reason else None
                await log_admin(
                    conn,
                    actor_id_of(current_user),
                    "admin.catalog.update",
                    "catalog_item",
                    item_id,
                    {
                        "reason": reason,
                        "status": item["status"],
                        "fields": sorted(changes),
                        "changes": changes,
                    },
                )
                if data.notify_owner:
                    labels = ", ".join(FIELD_LABELS[key] for key in sorted(changes))
                    title = scalar.get("title") or item["title"]
                    await self._notify_catalog_owner(
                        conn,
                        item["actor_id"],
                        item_id,
                        "Позиция изменена администратором",
                        with_reason(
                            f"Администратор изменил позицию «{title}»: {labels}.",
                            reason,
                        ),
                    )
        return await self.get_catalog_item_detail(item_id)

    async def add_catalog_media_admin(
        self,
        item_id: int,
        current_user,
        *,
        file_name: str,
        key: str,
        content_type: str | None,
    ) -> dict:
        ensure_permission(current_user, "admin_catalog.update")
        media_type = _media_type_from_content_type(content_type)
        async with self.connection() as conn, conn.transaction():
            await self._editable_catalog_item(conn, item_id)
            count = await conn.fetchval(
                "SELECT COUNT(*) FROM item_media WHERE item_id = $1",
                item_id,
            )
            if count >= MAX_ITEM_MEDIA:
                raise ValidationError(f"Too many media files (max {MAX_ITEM_MEDIA})")
            media_id = await conn.fetchval(
                """
                INSERT INTO item_media (item_id, file_name, file_url, media_type, sort_order)
                VALUES (
                    $1, $2, $3, $4::item_media_type,
                    COALESCE((SELECT MAX(sort_order) + 1 FROM item_media WHERE item_id = $1), 0)
                )
                RETURNING id
                """,
                item_id,
                file_name[:255],
                key,
                media_type,
            )
            await conn.execute(
                "UPDATE catalog_items SET updated_at = NOW() WHERE id = $1",
                item_id,
            )
            await log_admin(
                conn,
                actor_id_of(current_user),
                "admin.catalog.media.add",
                "catalog_item",
                item_id,
                {
                    "media_id": media_id,
                    "file_name": file_name,
                    "file_url": key,
                    "media_type": media_type,
                },
            )
        return await self.get_catalog_item_detail(item_id)

    async def delete_catalog_media_admin(self, item_id: int, media_id: int, current_user) -> dict:
        ensure_permission(current_user, "admin_catalog.update")
        async with self.connection() as conn, conn.transaction():
            await self._editable_catalog_item(conn, item_id)
            media = await conn.fetchrow(
                """
                DELETE FROM item_media WHERE id = $1 AND item_id = $2
                RETURNING id, file_name, file_url, media_type::text AS media_type
                """,
                media_id,
                item_id,
            )
            if not media:
                raise NotFoundError("Media not found")
            await conn.execute(
                """
                UPDATE item_media m SET sort_order = ordered.rn - 1
                FROM (
                    SELECT id, ROW_NUMBER() OVER (ORDER BY sort_order, id) AS rn
                    FROM item_media WHERE item_id = $1
                ) ordered
                WHERE m.id = ordered.id
                """,
                item_id,
            )
            await conn.execute(
                "UPDATE catalog_items SET updated_at = NOW() WHERE id = $1",
                item_id,
            )
            await log_admin(
                conn,
                actor_id_of(current_user),
                "admin.catalog.media.delete",
                "catalog_item",
                item_id,
                {
                    "media_id": media["id"],
                    "file_name": media["file_name"],
                    "file_url": media["file_url"],
                    "media_type": media["media_type"],
                },
            )
        return await self.get_catalog_item_detail(item_id)

    async def reorder_catalog_media_admin(self, item_id: int, media_ids: list[int], current_user) -> dict:
        ensure_permission(current_user, "admin_catalog.update")
        async with self.connection() as conn, conn.transaction():
            await self._editable_catalog_item(conn, item_id)
            rows = await conn.fetch(
                "SELECT id FROM item_media WHERE item_id = $1 ORDER BY sort_order, id",
                item_id,
            )
            before = [row["id"] for row in rows]
            if len(media_ids) != len(set(media_ids)) or set(media_ids) != set(before):
                raise ValidationError("media_ids must list every media file of the item exactly once")
            if media_ids != before:
                await conn.execute(
                    """
                    UPDATE item_media m SET sort_order = o.idx - 1
                    FROM unnest($2::int[]) WITH ORDINALITY AS o(id, idx)
                    WHERE m.id = o.id AND m.item_id = $1
                    """,
                    item_id,
                    media_ids,
                )
                await conn.execute(
                    "UPDATE catalog_items SET updated_at = NOW() WHERE id = $1",
                    item_id,
                )
                await log_admin(
                    conn,
                    actor_id_of(current_user),
                    "admin.catalog.media.reorder",
                    "catalog_item",
                    item_id,
                    {"before": before, "after": media_ids},
                )
        return await self.get_catalog_item_detail(item_id)

    async def bulk_catalog_action(
        self,
        ids: list[int],
        action: str,
        current_user,
        reason: str | None = None,
    ) -> dict:
        ensure_permission(
            current_user,
            "admin_catalog.delete" if action == "delete" else "admin_catalog.update",
        )
        if action in {"hide", "reject", "delete"} and not (reason and reason.strip()):
            raise ValidationError("Reason is required for this action")
        unique_ids = list(dict.fromkeys(ids))
        succeeded: list[int] = []
        failed: list[dict] = []
        for item_id in unique_ids:
            try:
                await self.apply_catalog_action(
                    item_id=item_id,
                    action=action,
                    current_user=current_user,
                    reason=reason,
                    extra_details={"bulk": True, "bulk_size": len(unique_ids)},
                )
            except AppError as exc:
                failed.append({"id": item_id, "error": str(exc.detail)})
            else:
                succeeded.append(item_id)
        return {"action": action, "succeeded": succeeded, "failed": failed}
