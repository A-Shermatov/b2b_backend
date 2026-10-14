from app.core.exceptions import ForbiddenError, NotFoundError
from app.modules.rfqs.services.common import (
    BUYER_COLUMNS,
    BUYER_TAB_STATUSES,
    KNOWN_RFQ_STATUSES,
    OPEN_RFQ_STATUSES,
    RFQ_COLUMNS,
    load_rfq_response,
    load_rfq_row,
    load_supplier_participation,
    shape_for_supplier,
    shape_rfq_rows,
)

HIDDEN_FROM_SUPPLIERS_STATUSES = {"draft", "archived"}


class RfqReadMixin:
    async def _load(self, rfq_id: str, *, include_buyer: bool = False) -> dict:
        async with self.connection() as conn:
            return await load_rfq_response(conn, rfq_id, include_buyer=include_buyer)

    async def list_for_buyer(self, actor_id: int, tab: str | None = None) -> list:
        filters = ["r.actor_id = $1", "r.status::text <> 'archived'"]
        params: list = [actor_id]
        if tab and tab != "all":
            if tab in BUYER_TAB_STATUSES:
                params.append(BUYER_TAB_STATUSES[tab])
                filters.append(f"r.status::text = ANY(${len(params)}::text[])")
            elif tab in KNOWN_RFQ_STATUSES:
                params.append(tab)
                filters.append(f"r.status::text = ${len(params)}")
        where = " AND ".join(filters)
        async with self.connection() as conn:
            rows = await conn.fetch(
                f"""
                SELECT {RFQ_COLUMNS}
                FROM rfqs r
                WHERE {where}
                ORDER BY r.updated_at DESC
                """,
                *params,
            )
            return await shape_rfq_rows(conn, rows)

    async def list_board_for_supplier(self, supplier_id: int) -> list:
        async with self.connection() as conn:
            rows = await conn.fetch(
                f"""
                SELECT {RFQ_COLUMNS}, {BUYER_COLUMNS}
                FROM rfqs r
                LEFT JOIN actors a ON a.id = r.actor_id
                LEFT JOIN companies c ON c.id = a.company_id AND a.kind::text = 'company'
                LEFT JOIN company_stats cs ON cs.company_id = c.id
                WHERE r.status::text = ANY($1::text[])
                  AND (
                    r.visibility::text = 'public'
                    OR EXISTS (
                        SELECT 1 FROM rfq_invited_suppliers inv
                        WHERE inv.rfq_id = r.id AND inv.supplier_actor_id = $2
                    )
                  )
                ORDER BY r.updated_at DESC
                """,
                OPEN_RFQ_STATUSES,
                supplier_id,
            )
            shaped = await shape_rfq_rows(conn, rows, include_buyer=True)
            return [shape_for_supplier(rfq, supplier_id) for rfq in shaped]

    async def list_public_board(self) -> list:
        async with self.connection() as conn:
            rows = await conn.fetch(
                f"""
                SELECT {RFQ_COLUMNS}
                FROM rfqs r
                WHERE r.status::text = ANY($1::text[])
                  AND r.visibility::text = 'public'
                ORDER BY r.updated_at DESC
                LIMIT 50
                """,
                OPEN_RFQ_STATUSES,
            )
            return await shape_rfq_rows(conn, rows)

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
