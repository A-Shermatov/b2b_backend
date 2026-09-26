from app.core.exceptions import ForbiddenError
from app.modules.proposals.services.common import (
    PROPOSAL_SELECT,
    assert_chat_access,
    load_proposal_context,
    message_to_response,
    proposal_to_response,
)
from app.modules.rfqs.services.common import load_rfq_row


class ProposalReadMixin:
    async def list_for_rfq(self, rfq_id: str, buyer_actor_id: int) -> list:
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
            rows = await conn.fetch(
                PROPOSAL_SELECT + " WHERE p.rfq_id = $1 ORDER BY p.created_at DESC",
                rfq_id,
            )
        return [proposal_to_response(row) for row in rows]

    async def list_for_supplier(self, supplier_id: int) -> list:
        async with self.connection() as conn:
            rows = await conn.fetch(
                PROPOSAL_SELECT
                + """
                WHERE p.supplier_actor_id = $1
                  AND p.status::text NOT IN ('accepted', 'archived')
                ORDER BY p.created_at DESC
                """,
                supplier_id,
            )
        return [proposal_to_response(row) for row in rows]

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
