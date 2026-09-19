from app.core.exceptions import ForbiddenError
from app.modules.rfqs.services.common import (
    BUYER_COLUMNS,
    BUYER_TAB_STATUSES,
    KNOWN_RFQ_STATUSES,
    RFQ_COLUMNS,
    load_rfq_response,
    load_rfq_row,
    shape_rfq_rows,
)


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
                ["published", "receiving_proposals"],
                supplier_id,
            )
            return await shape_rfq_rows(conn, rows, include_buyer=True)

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
                ["published", "receiving_proposals"],
            )
            return await shape_rfq_rows(conn, rows)

    async def get(
        self,
        rfq_id: str,
        actor_id: int | None = None,
        supplier_id: int | None = None,
    ):
        async with self.connection() as conn:
            row = await load_rfq_row(conn, rfq_id, with_buyer=bool(supplier_id))
            if actor_id and row["actor_id"] != actor_id:
                if supplier_id:
                    if row["visibility"] == "invited_only":
                        invited = await conn.fetchval(
                            """
                            SELECT 1 FROM rfq_invited_suppliers
                            WHERE rfq_id = $1 AND supplier_actor_id = $2
                            """,
                            rfq_id,
                            supplier_id,
                        )
                        if not invited:
                            raise ForbiddenError("RFQ is invite-only")
                else:
                    raise ForbiddenError("Access denied")
            return await load_rfq_response(
                conn,
                rfq_id,
                include_buyer=bool(supplier_id),
            )
