from app.core.exceptions import ForbiddenError
from app.core.pagination import build_page, fetch_page
from app.modules.proposals.services.common import (
    PROPOSAL_SELECT,
    assert_chat_access,
    load_proposal_context,
    message_to_response,
    proposal_to_response,
)
from app.modules.rfqs.services.common import load_rfq_row


class ProposalReadMixin:
    async def _page_proposals(self, conn, where_sql: str, params: list, page: int, page_size: int):
        total, id_rows = await fetch_page(
            conn,
            f"SELECT COUNT(*) FROM proposals p WHERE {where_sql}",
            f"""
            SELECT p.id
            FROM proposals p
            WHERE {where_sql}
            ORDER BY p.created_at DESC
            """,
            params,
            page,
            page_size,
        )
        if not id_rows:
            return build_page([], total, page, page_size)
        rows = await conn.fetch(
            PROPOSAL_SELECT + " WHERE p.id = ANY($1::int[]) ORDER BY p.created_at DESC",
            [row["id"] for row in id_rows],
        )
        return build_page([proposal_to_response(row) for row in rows], total, page, page_size)

    async def list_for_rfq(
        self, rfq_id: str, buyer_actor_id: int, page: int = 1, page_size: int = 20
    ) -> dict:
        async with self.connection() as conn:
            rfq = await load_rfq_row(conn, rfq_id)
            if rfq["actor_id"] != buyer_actor_id:
                raise ForbiddenError("Access denied")
            await conn.execute(
                """
                UPDATE proposals
                SET status = 'viewed'::proposal_status
                WHERE rfq_id = $1 AND status::text = 'submitted'
                """,
                rfq_id,
            )
            return await self._page_proposals(conn, "p.rfq_id = $1", [rfq_id], page, page_size)

    async def list_for_supplier(self, supplier_id: int, page: int = 1, page_size: int = 20) -> dict:
        async with self.connection() as conn:
            return await self._page_proposals(
                conn,
                """
                p.supplier_actor_id = $1
                  AND p.status::text NOT IN ('accepted', 'archived')
                """,
                [supplier_id],
                page,
                page_size,
            )

    async def list_messages(self, proposal_id: int, actor_id: int, as_buyer: bool) -> list:
        async with self.connection() as conn:
            row = await load_proposal_context(conn, proposal_id)
            assert_chat_access(row, actor_id, as_buyer)
            messages = await conn.fetch(
                """
                SELECT
                    m.id, m.proposal_id, m.sender_id, m.text, m.created_at,
                    u.first_name, u.last_name
                FROM proposal_messages m
                LEFT JOIN users u ON u.id = m.sender_id
                WHERE m.proposal_id = $1
                ORDER BY m.created_at ASC
                """,
                proposal_id,
            )
        return [message_to_response(item) for item in messages]
