from app.core.exceptions import ForbiddenError, NotFoundError
from app.core.pagination import build_page, fetch_page
from app.modules.rfqs.services.common import (
    BUYER_COLUMNS,
    BUYER_TAB_STATUSES,
    KNOWN_RFQ_STATUSES,
    OPEN_RFQ_STATUSES,
    RFQ_COLUMNS,
    load_rfq_response,
    load_rfq_row,
    load_supplier_participation,
    load_supplier_participation_map,
    shape_for_supplier,
    shape_rfq_rows,
)

HIDDEN_FROM_SUPPLIERS_STATUSES = {"draft", "archived"}


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
        filters = ["r.actor_id = $1", "r.status::text <> 'archived'"]
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
            params.append(f"%{query}%")
            n = len(params)
            filters.append(f"(r.title ILIKE ${n} OR r.id::text ILIKE ${n})")
        where = " AND ".join(filters)
        async with self.connection() as conn:
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
        if source == "catalog":
            filters.append("r.catalog_item_id IS NOT NULL")
        elif source == "marketplace":
            filters.append("r.catalog_item_id IS NULL")
        query = (q or "").strip()
        if query:
            params.append(f"%{query}%")
            n = len(params)
            filters.append(
                f"(r.title ILIKE ${n} OR r.id::text ILIKE ${n} "
                f"OR COALESCE(c.name, '') ILIKE ${n})"
            )
        board_where = " AND ".join(filters)
        board_from = """
            FROM rfqs r
            LEFT JOIN actors a ON a.id = r.actor_id
            LEFT JOIN companies c ON c.id = a.company_id AND a.kind::text = 'company'
            LEFT JOIN company_stats cs ON cs.company_id = c.id
        """
        async with self.connection() as conn:
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
