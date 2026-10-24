import json

from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError, ValidationError
from app.core.pagination import build_page, fetch_page
from app.modules.catalog.schemas import (
    CatalogItemInput,
    CatalogItemReportCreate,
    CatalogItemReportResponse,
    CatalogItemWithRelations,
)
from app.modules.catalog.schemas.common import (
    CatalogActorBrief,
    CategoryRefSchema,
    ItemStatsSchema,
)
from app.modules.catalog.services.quality import catalog_item_completeness_percent
from app.utils.storage import public_file_url

SUPPLIER_WRITABLE_STATUSES = {
    "draft",
    "pending_review",
    "changes_requested",
    "archived",
}
SUPPLIER_EDITABLE_STATUSES = {
    "draft",
    "changes_requested",
    "pending_review",
    "active",
    "hidden",
    "archived",
}
def _parse_tiers(value):
    if value is None:
        return []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return []
    return value or []


ITEM_COLUMNS = """
    ci.id, ci.actor_id, ci.type::text AS type, ci.category_id, ci.title,
    ci.description, ci.status::text AS status,
    COALESCE(ci.invoice, TRUE) AS invoice, ci.created_at,
    cat.id AS category_pk, cat.parent_id AS category_parent_id,
    cat.name AS category_name, cat.slug AS category_slug
"""

LIST_PRICE_EXPR = """
    COALESCE(
        ip.fixed_price,
        ip.hourly_rate,
        ip.monthly_rate,
        (
            SELECT MIN((tier_el->>'price')::double precision)
            FROM jsonb_array_elements(
                CASE
                    WHEN ip.tiers IS NULL THEN '[]'::jsonb
                    WHEN jsonb_typeof(ip.tiers) = 'array' THEN ip.tiers
                    ELSE '[]'::jsonb
                END
            ) AS tier_el
            WHERE tier_el ? 'price'
        )
    )
"""


def _item_to_schema(
    item,
    attributes,
    pricing,
    media,
    stats,
    rejection_reason: str | None = None,
) -> CatalogItemWithRelations:
    pricing_dict = (
        {
            "id": pricing["id"],
            "item_id": pricing["item_id"],
            "pricing_type": pricing["pricing_type"],
            "currency": pricing["currency"],
            "fixed_price": pricing["fixed_price"],
            "hourly_rate": pricing["hourly_rate"],
            "monthly_rate": pricing["monthly_rate"],
            "tiers": _parse_tiers(pricing["tiers"]),
        }
        if pricing
        else None
    )
    media_list = [
        {
            "id": row["id"],
            "item_id": row["item_id"],
            "file_name": row["file_name"],
            "file_url": public_file_url(row["file_url"]),
            "media_type": row["media_type"],
            "sort_order": row["sort_order"],
        }
        for row in media
    ]
    return CatalogItemWithRelations(
        id=item["id"],
        actor_id=item["actor_id"],
        type=item["type"],
        category_id=item["category_id"],
        title=item["title"],
        description=item["description"],
        status=item["status"],
        invoice=bool(item["invoice"]),
        created_at=item["created_at"],
        category=(
            CategoryRefSchema(
                id=item["category_pk"],
                parent_id=item["category_parent_id"],
                name=item["category_name"],
                slug=item["category_slug"],
            )
            if item["category_pk"]
            else None
        ),
        attributes=[dict(row) for row in attributes],
        pricing=pricing_dict,
        media=media_list,
        stats=ItemStatsSchema(
            item_id=stats["item_id"] if stats else item["id"],
            views=stats["views"] if stats else 0,
            leads=stats["leads"] if stats else 0,
        ),
        rejection_reason=rejection_reason,
        completeness_percent=catalog_item_completeness_percent(
            title=item["title"],
            description=item["description"],
            category_id=item["category_id"],
            status=item["status"],
            attributes_count=len(attributes),
            media=media_list,
            pricing=pricing_dict,
        ),
    )


class CatalogItemMixin:
    async def _load_item_schema(self, conn, item_id: int) -> CatalogItemWithRelations:
        item = await conn.fetchrow(
            f"""
            SELECT {ITEM_COLUMNS}
            FROM catalog_items ci
            LEFT JOIN categories cat ON cat.id = ci.category_id
            WHERE ci.id = $1 AND ci.status::text != 'deleted'
            """,
            item_id,
        )
        if not item:
            raise NotFoundError("Catalog item not found")
        attributes = await conn.fetch(
            """
            SELECT id, item_id, name, value, value_type, sort_order
            FROM item_attributes WHERE item_id = $1 ORDER BY sort_order, id
            """,
            item_id,
        )
        pricing = await conn.fetchrow(
            """
            SELECT id, item_id, pricing_type::text AS pricing_type, currency,
                   fixed_price, hourly_rate, monthly_rate, tiers
            FROM item_pricing WHERE item_id = $1
            """,
            item_id,
        )
        media = await conn.fetch(
            """
            SELECT id, item_id, file_name, file_url, media_type::text AS media_type, sort_order
            FROM item_media WHERE item_id = $1 ORDER BY sort_order, id
            """,
            item_id,
        )
        stats = await conn.fetchrow(
            "SELECT item_id, views, leads FROM item_stats WHERE item_id = $1",
            item_id,
        )
        rejection_reason = None
        if item["status"] == "changes_requested":
            audit = await conn.fetchrow(
                """
                SELECT details
                FROM audit_logs
                WHERE resource_type = 'catalog_item'
                  AND resource_id = $1
                  AND action = 'admin.catalog.reject'
                ORDER BY id DESC
                LIMIT 1
                """,
                str(item_id),
            )
            if audit and audit["details"]:
                details = audit["details"]
                if isinstance(details, str):
                    try:
                        details = json.loads(details)
                    except json.JSONDecodeError:
                        details = {}
                if isinstance(details, dict):
                    reason = details.get("reason")
                    if isinstance(reason, str) and reason.strip():
                        rejection_reason = reason.strip()
        return _item_to_schema(
            item,
            attributes,
            pricing,
            media,
            stats,
            rejection_reason=rejection_reason,
        )

    async def _load_many(self, conn, items) -> list[CatalogItemWithRelations]:
        return [await self._load_item_schema(conn, item["id"]) for item in items]

    def _normalize_supplier_status(self, requested: str, current: str | None = None) -> str:
        status = requested
        if status not in {
            "draft",
            "pending_review",
            "changes_requested",
            "archived",
            "active",
            "hidden",
            "deleted",
        }:
            raise ValidationError("Unsupported catalog item status")
        if status in {"active", "hidden", "deleted"}:
            raise ForbiddenError("Supplier cannot set this status")
        if status == "pending_review":
            return "pending_review"
        if current == "changes_requested" and status == "draft":
            return "draft"
        if status not in SUPPLIER_WRITABLE_STATUSES:
            raise ForbiddenError("Supplier cannot set this status")
        return status

    async def _replace_relations(self, conn, item_id: int, data: CatalogItemInput) -> None:
        await conn.execute("DELETE FROM item_attributes WHERE item_id = $1", item_id)
        for idx, attr in enumerate(data.attributes):
            await conn.execute(
                """
                INSERT INTO item_attributes (item_id, name, value, value_type, sort_order)
                VALUES ($1, $2, $3, $4, $5)
                """,
                item_id,
                attr.name,
                attr.value,
                attr.value_type,
                attr.sort_order or idx,
            )
        await conn.execute("DELETE FROM item_pricing WHERE item_id = $1", item_id)
        await conn.execute(
            """
            INSERT INTO item_pricing (
                item_id, pricing_type, currency, fixed_price, hourly_rate, monthly_rate, tiers
            )
            VALUES ($1, $2::pricing_type, $3, $4, $5, $6, $7::jsonb)
            """,
            item_id,
            data.pricing.pricing_type,
            data.pricing.currency,
            data.pricing.fixed_price,
            data.pricing.hourly_rate,
            data.pricing.monthly_rate,
            json.dumps([t.model_dump() for t in data.pricing.tiers]),
        )
        await conn.execute("DELETE FROM item_media WHERE item_id = $1", item_id)
        for idx, media in enumerate(data.media):
            await conn.execute(
                """
                INSERT INTO item_media (item_id, file_name, file_url, media_type, sort_order)
                VALUES ($1, $2, $3, $4::item_media_type, $5)
                """,
                item_id,
                media.file_name,
                media.file_url,
                media.media_type,
                media.sort_order or idx,
            )

    async def _page_item_ids(
        self,
        conn,
        count_sql: str,
        rows_sql: str,
        params: list,
        page: int,
        page_size: int,
    ) -> dict:
        total, rows = await fetch_page(conn, count_sql, rows_sql, params, page, page_size)
        items = await self._load_many(conn, rows)
        return build_page(items, total, page, page_size)

    async def list_for_supplier(
        self, actor_id: int, status: str | None = None, page: int = 1, page_size: int = 20
    ):
        async with self.connection() as conn:
            if status:
                return await self._page_item_ids(
                    conn,
                    """
                    SELECT COUNT(*) FROM catalog_items
                    WHERE actor_id = $1 AND status::text != 'deleted' AND status::text = $2
                    """,
                    """
                    SELECT id FROM catalog_items
                    WHERE actor_id = $1 AND status::text != 'deleted' AND status::text = $2
                    ORDER BY created_at DESC
                    """,
                    [actor_id, status],
                    page,
                    page_size,
                )
            return await self._page_item_ids(
                conn,
                """
                SELECT COUNT(*) FROM catalog_items
                WHERE actor_id = $1 AND status::text != 'deleted'
                """,
                """
                SELECT id FROM catalog_items
                WHERE actor_id = $1 AND status::text != 'deleted'
                ORDER BY created_at DESC
                """,
                [actor_id],
                page,
                page_size,
            )

    async def list_active_for_company_actor(self, actor_id: int, page: int = 1, page_size: int = 20):
        async with self.connection() as conn:
            return await self._page_item_ids(
                conn,
                """
                SELECT COUNT(*) FROM catalog_items
                WHERE actor_id = $1 AND status::text = 'active'
                """,
                """
                SELECT id FROM catalog_items
                WHERE actor_id = $1 AND status::text = 'active'
                ORDER BY created_at DESC
                """,
                [actor_id],
                page,
                page_size,
            )

    async def list_public(
        self,
        query: str | None = None,
        category_slug: str | None = None,
        page: int = 1,
        page_size: int = 20,
        *,
        category_id: int | None = None,
        catalog_type: str | None = None,
        verified: bool = False,
        invoice: bool = False,
        sort: str = "pop",
    ):
        clauses = ["ci.status::text = 'active'"]
        params: list = []
        if query:
            params.append(f"%{query}%")
            clauses.append(f"(ci.title ILIKE ${len(params)} OR ci.description ILIKE ${len(params)})")
        if category_slug:
            params.append(category_slug)
            clauses.append(f"cat.slug = ${len(params)}")
        if category_id is not None:
            params.append(category_id)
            clauses.append(f"ci.category_id = ${len(params)}")
        if catalog_type:
            params.append(catalog_type)
            clauses.append(f"ci.type::text = ${len(params)}")
        if invoice:
            clauses.append("COALESCE(ci.invoice, TRUE) = TRUE")
        if verified:
            clauses.append("c.verification_status::text = 'verified'")
        where = " AND ".join(clauses)

        joins = """
            FROM catalog_items ci
            JOIN categories cat ON cat.id = ci.category_id
            JOIN actors a ON a.id = ci.actor_id
            LEFT JOIN companies c ON c.id = a.company_id
            LEFT JOIN item_stats ist ON ist.item_id = ci.id
            LEFT JOIN item_pricing ip ON ip.item_id = ci.id
            LEFT JOIN company_stats cs ON cs.company_id = c.id
        """

        sort_key = sort if sort in {"pop", "cheap", "rating"} else "pop"
        if sort_key == "cheap":
            order_sql = f"ORDER BY {LIST_PRICE_EXPR} ASC NULLS LAST, ci.id DESC"
        elif sort_key == "rating":
            order_sql = """
                ORDER BY COALESCE(cs.average_rating, c.rating, 0) DESC,
                         ci.id DESC
            """
        else:
            order_sql = """
                ORDER BY (COALESCE(ist.views, 0) + COALESCE(ist.leads, 0)) DESC,
                         ci.created_at DESC
            """

        async with self.connection() as conn:
            return await self._page_item_ids(
                conn,
                f"SELECT COUNT(*) {joins} WHERE {where}",
                f"""
                SELECT ci.id
                {joins}
                WHERE {where}
                {order_sql}
                """,
                params,
                page,
                page_size,
            )

    async def get_public(self, item_id: int):
        async with self.connection() as conn:
            schema = await self._load_item_schema(conn, item_id)
            if schema.status != "active":
                raise NotFoundError("Catalog item not found")
            actor_row = await conn.fetchrow(
                """
                SELECT
                    a.id,
                    COALESCE(
                        NULLIF(c.title, ''),
                        NULLIF(a.display_name, ''),
                        'Исполнитель'
                    ) AS name,
                    COALESCE(cs.average_rating, c.rating, 0) AS rating,
                    c.logo AS photo
                FROM actors a
                LEFT JOIN companies c ON c.id = a.company_id
                LEFT JOIN company_stats cs ON cs.company_id = c.id
                WHERE a.id = $1
                """,
                schema.actor_id,
            )
        actor = None
        if actor_row:
            photo_raw = actor_row["photo"]
            actor = CatalogActorBrief(
                id=actor_row["id"],
                name=actor_row["name"] or "Исполнитель",
                rating=float(actor_row["rating"] or 0),
                photo=public_file_url(photo_raw) if photo_raw else None,
            )
        return schema.model_copy(update={"actor": actor})

    async def get(self, item_id: int, actor_id: int):
        async with self.connection() as conn:
            schema = await self._load_item_schema(conn, item_id)
        if schema.actor_id != actor_id:
            raise ForbiddenError("Access denied")
        return schema

    async def create(self, actor_id: int, data: CatalogItemInput):
        status = self._normalize_supplier_status(data.status)
        async with self.connection() as conn:
            item_id = await conn.fetchval(
                """
                INSERT INTO catalog_items (
                    actor_id, type, category_id, title, description, status, invoice, created_at
                )
                VALUES ($1, $2::catalog_item_type, $3, $4, $5, $6::item_status, $7, NOW())
                RETURNING id
                """,
                actor_id,
                data.type,
                data.category_id,
                data.title,
                data.description or None,
                status,
                bool(data.invoice),
            )
            await self._replace_relations(conn, item_id, data)
            await conn.execute(
                "INSERT INTO item_stats (item_id, views, leads) VALUES ($1, 0, 0)",
                item_id,
            )
            return await self._load_item_schema(conn, item_id)

    async def update(self, item_id: int, actor_id: int, data: CatalogItemInput):
        async with self.connection() as conn:
            item = await conn.fetchrow(
                """
                SELECT id, actor_id, status::text AS status
                FROM catalog_items
                WHERE id = $1 AND status::text != 'deleted'
                """,
                item_id,
            )
            if not item:
                raise NotFoundError("Catalog item not found")
            if item["actor_id"] != actor_id:
                raise ForbiddenError("Access denied")
            if item["status"] not in SUPPLIER_EDITABLE_STATUSES:
                raise ForbiddenError("Catalog item cannot be edited in current status")

            requested = self._normalize_supplier_status(data.status, current=item["status"])
            if item["status"] == "changes_requested" and requested == "draft":
                next_status = "draft"
            elif requested == "pending_review":
                next_status = "pending_review"
            elif item["status"] in {"active", "hidden", "pending_review"} and requested == "draft":
                next_status = "draft"
            elif requested == "archived":
                next_status = "archived"
            else:
                next_status = requested if requested in SUPPLIER_WRITABLE_STATUSES else "draft"

            await conn.execute(
                """
                UPDATE catalog_items
                SET type = $1::catalog_item_type,
                    category_id = $2,
                    title = $3,
                    description = $4,
                    status = $5::item_status,
                    invoice = $6
                WHERE id = $7
                """,
                data.type,
                data.category_id,
                data.title,
                data.description or None,
                next_status,
                bool(data.invoice),
                item_id,
            )
            await self._replace_relations(conn, item_id, data)
            return await self._load_item_schema(conn, item_id)

    async def set_status(self, item_id: int, actor_id: int, status: str):
        status_value = status.value if hasattr(status, "value") else status
        async with self.connection() as conn:
            item = await conn.fetchrow(
                """
                SELECT id, actor_id FROM catalog_items
                WHERE id = $1 AND status::text != 'deleted'
                """,
                item_id,
            )
            if not item:
                raise NotFoundError("Catalog item not found")
            if item["actor_id"] != actor_id:
                raise ForbiddenError("Access denied")
            if status_value == "active":
                next_status = "pending_review"
            elif status_value in {"pending_review", "archived", "draft"}:
                next_status = status_value
            else:
                raise ForbiddenError("Supplier cannot set this status")
            await conn.execute(
                "UPDATE catalog_items SET status = $1::item_status WHERE id = $2",
                next_status,
                item_id,
            )
            return await self._load_item_schema(conn, item_id)

    async def delete(self, item_id: int, actor_id: int) -> None:
        async with self.connection() as conn:
            item = await conn.fetchrow(
                """
                SELECT id, actor_id FROM catalog_items
                WHERE id = $1 AND status::text != 'deleted'
                """,
                item_id,
            )
            if not item:
                raise NotFoundError("Catalog item not found")
            if item["actor_id"] != actor_id:
                raise ForbiddenError("Access denied")
            await conn.execute(
                "UPDATE catalog_items SET status = 'deleted'::item_status WHERE id = $1",
                item_id,
            )

    async def create_report(self, item_id: int, reporter_id: int, data: CatalogItemReportCreate):
        async with self.connection() as conn:
            item = await conn.fetchrow(
                """
                SELECT id, actor_id, status::text AS status
                FROM catalog_items
                WHERE id = $1 AND status::text != 'deleted'
                """,
                item_id,
            )
            if not item:
                raise NotFoundError("Catalog item not found")
            if item["status"] != "active":
                raise ValidationError("Only active catalog items can be reported")

            actor = await conn.fetchrow(
                """
                SELECT kind::text AS kind, user_id, company_id
                FROM actors WHERE id = $1
                """,
                item["actor_id"],
            )
            if actor:
                if actor["kind"] == "individual" and actor["user_id"] == reporter_id:
                    raise ForbiddenError("Cannot report your own catalog item")
                if actor["kind"] == "company" and actor["company_id"]:
                    owner_id = await conn.fetchval(
                        "SELECT owner_id FROM companies WHERE id = $1",
                        actor["company_id"],
                    )
                    if owner_id == reporter_id:
                        raise ForbiddenError("Cannot report your own catalog item")

            existing = await conn.fetchval(
                """
                SELECT id FROM catalog_item_reports
                WHERE item_id = $1 AND reporter_user_id = $2 AND status::text = 'open'
                """,
                item_id,
                reporter_id,
            )
            if existing:
                raise ConflictError("You already have an open report for this item")

            report = await conn.fetchrow(
                """
                INSERT INTO catalog_item_reports (
                    item_id, reporter_user_id, reason, details, status, created_at
                )
                VALUES (
                    $1, $2, $3::catalog_item_report_reason, $4,
                    'open'::catalog_item_report_status, NOW()
                )
                RETURNING id, item_id, reason::text AS reason, details,
                          status::text AS status, created_at
                """,
                item_id,
                reporter_id,
                data.reason,
                data.details.strip() if data.details else None,
            )
        return CatalogItemReportResponse.model_validate(dict(report))
