from app.core.exceptions import ForbiddenError, NotFoundError
from app.core.pagination import build_page, fetch_page
from app.db.sql import like_contains
from app.modules.rfqs.services.common import (
    BUYER_COLUMNS,
    BUYER_TAB_STATUSES,
    KNOWN_RFQ_STATUSES,
    OPEN_RFQ_STATUSES,
    RFQ_COLUMNS,
    expire_overdue_rfqs,
    load_rfq_response,
    load_rfq_row,
    load_supplier_participation,
    load_supplier_participation_map,
    shape_for_supplier,
    shape_rfq_rows,
)

HIDDEN_FROM_SUPPLIERS_STATUSES = {"draft", "archived"}

# Board filter: requests of the supplier's own company (same company, same ИНН,
# or the same person on an individual account). Uses $2 = supplier actor id and
# the board's "a" (buyer actor) and "c" (buyer company) aliases.
_TAX = "upper(regexp_replace(COALESCE({col}, ''), '[^0-9A-Za-z]', '', 'g'))"
OWN_COMPANY_EXCLUSION = f"""NOT EXISTS (
    SELECT 1 FROM actors me
    LEFT JOIN companies mc ON mc.id = me.company_id
    WHERE me.id = $2 AND (
        (me.company_id IS NOT NULL AND me.company_id = a.company_id)
        OR (me.user_id IS NOT NULL AND me.user_id = a.user_id)
        OR (
            {_TAX.format(col="mc.tax_number")} <> ''
            AND {_TAX.format(col="mc.tax_number")} = {_TAX.format(col="c.tax_number")}
        )
    )
)"""


def parse_category_ids(*values: str | None) -> list[int]:
    """Category ids from "3" or "1,4,6"; anything that is not a number is ignored."""
    ids: list[int] = []
    for value in values:
        for part in (value or "").split(","):
            part = part.strip()
            if part.isdigit() and int(part) not in ids:
                ids.append(int(part))
    return ids[:50]


class RfqReadMixin:
    async def _load(self, rfq_id: str, *, include_buyer: bool = False) -> dict:
        async with self.connection() as conn:
            return await load_rfq_response(conn, rfq_id, include_buyer=include_buyer)

    async def list_for_buyer(
        self,
        actor_id: int,
        tab: str | None = None,
        page: int = 1,
        page_size: int = 20,
        *,
        q: str | None = None,
        rfq_type: str | None = None,
    ) -> dict:
        filters = ["r.actor_id = $1"]
        if tab not in ("closed", "archived"):
            filters.append("r.status::text <> 'archived'")
        params: list = [actor_id]
        if tab and tab != "all":
            if tab in BUYER_TAB_STATUSES:
                params.append(BUYER_TAB_STATUSES[tab])
                filters.append(f"r.status::text = ANY(${len(params)}::text[])")
            elif tab in KNOWN_RFQ_STATUSES:
                params.append(tab)
                filters.append(f"r.status::text = ${len(params)}")
        if rfq_type in ("product", "service"):
            params.append(rfq_type)
            filters.append(f"r.type::text = ${len(params)}")
        query = (q or "").strip()
        if query:
            params.append(like_contains(query))
            n = len(params)
            filters.append(f"(r.title ILIKE ${n} OR r.id::text ILIKE ${n})")
        where = " AND ".join(filters)
        async with self.connection() as conn:
            await expire_overdue_rfqs(conn)
            total, rows = await fetch_page(
                conn,
                f"SELECT COUNT(*) FROM rfqs r WHERE {where}",
                f"""
                SELECT {RFQ_COLUMNS}
                FROM rfqs r
                WHERE {where}
                ORDER BY r.updated_at DESC
                """,
                params,
                page,
                page_size,
            )
            items = await shape_rfq_rows(conn, rows)
        return build_page(items, total, page, page_size)

    async def list_board_for_supplier(
        self,
        supplier_id: int,
        page: int = 1,
        page_size: int = 20,
        *,
        q: str | None = None,
        source: str | None = None,
        rfq_type: str | None = None,
        category_id: str | None = None,
        category_ids: str | None = None,
    ) -> dict:
        filters = [
            "r.status::text = ANY($1::text[])",
            """(
                r.visibility::text = 'public'
                OR EXISTS (
                    SELECT 1 FROM rfq_invited_suppliers inv
                    WHERE inv.rfq_id = r.id AND inv.supplier_actor_id = $2
                )
            )""",
        ]
        params: list = [OPEN_RFQ_STATUSES, supplier_id]
        # A supplier never sees its own company's requests: it could not answer them anyway.
        filters.append(OWN_COMPANY_EXCLUSION)
        if source == "catalog":
            filters.append("r.catalog_item_id IS NOT NULL")
        elif source == "marketplace":
            filters.append("r.catalog_item_id IS NULL")
        if rfq_type in ("product", "service"):
            params.append(rfq_type)
            filters.append(f"r.type::text = ${len(params)}")
        wanted = parse_category_ids(category_id, category_ids)
        if wanted:
            # A parent category also matches its subcategories.
            params.append(wanted)
            n = len(params)
            # Requests keep the category as an id ("2") or a slug ("web"): match both.
            filters.append(
                f"""r.category_id IN (
                    SELECT v.key FROM categories cat
                    CROSS JOIN LATERAL (VALUES (cat.id::text), (cat.slug)) v(key)
                    WHERE cat.id = ANY(${n}::int[]) OR cat.parent_id = ANY(${n}::int[])
                )"""
            )
        query = (q or "").strip()
        if query:
            params.append(like_contains(query))
            n = len(params)
            filters.append(
                f"(r.title ILIKE ${n} OR r.id::text ILIKE ${n} "
                f"OR COALESCE(c.title, '') ILIKE ${n})"
            )
        board_where = " AND ".join(filters)
        board_from = """
            FROM rfqs r
            LEFT JOIN actors a ON a.id = r.actor_id
            LEFT JOIN companies c ON c.id = a.company_id AND a.kind::text = 'company'
            LEFT JOIN company_stats cs ON cs.company_id = c.id
        """
        async with self.connection() as conn:
            await expire_overdue_rfqs(conn)
            total, rows = await fetch_page(
                conn,
                f"SELECT COUNT(*) {board_from} WHERE {board_where}",
                f"""
                SELECT {RFQ_COLUMNS}, {BUYER_COLUMNS}
                {board_from}
                WHERE {board_where}
                ORDER BY r.updated_at DESC
                """,
                params,
                page,
                page_size,
            )
            items = await shape_rfq_rows(conn, rows, include_buyer=True)
            participation_map = await load_supplier_participation_map(
                conn, [rfq["id"] for rfq in items], supplier_id
            )
        shaped = [
            shape_for_supplier(
                rfq,
                supplier_id,
                participation_map.get(rfq["id"]),
            )
            for rfq in items
        ]
        return build_page(shaped, total, page, page_size)

    async def list_public_board(self, page: int = 1, page_size: int = 20) -> dict:
        public_where = """
            r.status::text = ANY($1::text[])
            AND r.visibility::text = 'public'
        """
        params = [OPEN_RFQ_STATUSES]
        async with self.connection() as conn:
            await expire_overdue_rfqs(conn)
            total, rows = await fetch_page(
                conn,
                f"SELECT COUNT(*) FROM rfqs r WHERE {public_where}",
                f"""
                SELECT {RFQ_COLUMNS}
                FROM rfqs r
                WHERE {public_where}
                ORDER BY r.updated_at DESC
                """,
                params,
                page,
                page_size,
            )
            items = await shape_rfq_rows(conn, rows)
        return build_page(items, total, page, page_size)

    async def get(
        self,
        rfq_id: str,
        actor_id: int | None = None,
        supplier_id: int | None = None,
    ):
        async with self.connection() as conn:
            row = await load_rfq_row(conn, rfq_id)
            if supplier_id:
                await self._ensure_supplier_access(conn, row, supplier_id)
                response = await load_rfq_response(conn, rfq_id, include_buyer=True)
                participation = await load_supplier_participation(
                    conn, row["id"], supplier_id
                )
                return shape_for_supplier(response, supplier_id, participation)
            if actor_id and row["actor_id"] != actor_id:
                raise ForbiddenError("Access denied")
            return await load_rfq_response(conn, rfq_id)

    async def _ensure_supplier_access(self, conn, row, supplier_id: int) -> None:
        rfq_id = row["id"]
        has_link = await conn.fetchval(
            """
            SELECT EXISTS (
                SELECT 1 FROM rfq_invited_suppliers
                WHERE rfq_id = $1 AND supplier_actor_id = $2
            ) OR EXISTS (
                SELECT 1 FROM proposals
                WHERE rfq_id = $1 AND supplier_actor_id = $2
            )
            """,
            rfq_id,
            supplier_id,
        )
        if has_link:
            return
        if row["status"] in HIDDEN_FROM_SUPPLIERS_STATUSES:
            raise NotFoundError("RFQ not found")
        if row["visibility"] == "invited_only":
            raise ForbiddenError("RFQ is invite-only")
