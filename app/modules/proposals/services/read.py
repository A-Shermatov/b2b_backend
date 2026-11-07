from app.core.exceptions import ForbiddenError, NotFoundError
from app.core.pagination import build_page, fetch_page
from app.modules.proposals.services.common import (
    ACCEPTABLE_RFQ_STATUSES,
    PROPOSAL_SELECT,
    assert_chat_access,
    load_proposal_context,
    message_to_response,
    proposal_to_response,
)
from app.modules.rfqs.services.common import load_rfq_row
from app.utils.affiliation import PROPOSAL_MESSAGE_AFFILIATION


def proposal_outcome(proposal: dict, rfq: dict | None, winner: dict | None) -> dict:
    """Where a supplier's proposal stands, in the words the supplier needs.

    waiting   – the buyer has not chosen anyone yet;
    your_move – a catalog offer waits for the supplier's answer;
    won       – the buyer chose this proposal (contract_id opens the deal);
    lost      – the buyer chose another supplier (winner_name says whom);
    rejected  – the buyer declined this proposal and chose no one yet;
    withdrawn – the supplier took it back;
    closed    – the request closed without choosing anyone.
    """
    if winner is not None:
        if winner["proposal_id"] == proposal["id"]:
            return {"kind": "won", "contract_id": winner["contract_id"]}
        return {"kind": "lost", "winner_name": winner["name"]}
    status = proposal["status"]
    if status == "withdrawn":
        return {"kind": "withdrawn"}
    if status == "rejected":
        return {"kind": "rejected"}
    if status == "pending_supplier":
        return {"kind": "your_move"}
    # The buyer can still choose exactly while a proposal could be accepted.
    if status == "archived" or (rfq is not None and rfq["status"] not in ACCEPTABLE_RFQ_STATUSES):
        return {"kind": "closed"}
    return {"kind": "waiting"}


def _thread_from_row(row) -> dict:
    last_message = None
    if row["last_message_id"] is not None:
        sender_name = f"{row['last_first_name'] or ''} {row['last_last_name'] or ''}".strip()
        last_message = {
            "id": row["last_message_id"],
            "text": row["last_message_text"],
            "sender_id": row["last_sender_id"],
            "sender_name": sender_name,
            "created_at": row["last_message_at"],
        }
    return {
        "proposal_id": row["proposal_id"],
        "rfq_id": row["rfq_id"],
        "rfq_title": row["rfq_title"],
        "status": row["status"],
        "counterpart_actor_id": row["counterpart_actor_id"],
        "counterpart_name": row["counterpart_name"] or "Участник",
        "message_count": row["message_count"] or 0,
        "last_message": last_message,
    }


class ProposalReadMixin:
    async def _page_proposals(
        self,
        conn,
        where_sql: str,
        params: list,
        page: int,
        page_size: int,
        *,
        actor_role: str = "supplier",
    ):
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
        return build_page(
            [proposal_to_response(row, actor_role=actor_role) for row in rows],
            total,
            page,
            page_size,
        )

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
            return await self._page_proposals(
                conn,
                """
                p.rfq_id = $1
                AND NOT EXISTS (
                    SELECT 1
                    FROM actors sa
                    LEFT JOIN companies sc ON sc.id = sa.company_id
                    LEFT JOIN users su ON su.id = COALESCE(sc.owner_id, sa.user_id)
                    WHERE sa.id = p.supplier_actor_id
                      AND (
                        sa.is_active = FALSE
                        OR su.status::text = 'blocked'
                        OR COALESCE(sc.operational_status::text, 'active') = 'blocked'
                      )
                )
                """,
                [rfq_id],
                page,
                page_size,
                actor_role="supplier",
            )

    async def list_for_supplier(
        self,
        supplier_id: int,
        page: int = 1,
        page_size: int = 20,
        status: str | None = None,
    ) -> dict:
        # Every proposal the supplier sent, won and lost included: closing a
        # request archives the others, and they must not just disappear.
        filters = ["p.supplier_actor_id = $1"]
        params: list = [supplier_id]
        if status:
            params.append(status)
            filters.append(f"p.status::text = ${len(params)}")
        async with self.connection() as conn:
            result = await self._page_proposals(
                conn,
                " AND ".join(filters),
                params,
                page,
                page_size,
                actor_role="buyer",
            )
            await self._attach_outcomes(conn, result["items"])
            return result

    async def _attach_outcomes(self, conn, items: list[dict]) -> None:
        """Adds the request (title, status, deadline) and the outcome to each proposal."""
        rfq_ids = list({item["rfq_id"] for item in items})
        if not rfq_ids:
            return
        rfqs = {
            row["id"]: dict(row)
            for row in await conn.fetch(
                """
                SELECT id, title, status::text AS status, deadline
                FROM rfqs
                WHERE id = ANY($1::varchar[])
                """,
                rfq_ids,
            )
        }
        # The chosen proposal of each request: its supplier's public name and the deal.
        winners = {
            row["rfq_id"]: dict(row)
            for row in await conn.fetch(
                """
                SELECT DISTINCT ON (p.rfq_id)
                    p.rfq_id,
                    p.id AS proposal_id,
                    COALESCE(
                        NULLIF(sc.title, ''),
                        NULLIF(sa.display_name, ''),
                        'Исполнитель'
                    ) AS name,
                    c.id AS contract_id
                FROM proposals p
                JOIN actors sa ON sa.id = p.supplier_actor_id
                LEFT JOIN companies sc ON sc.id = sa.company_id
                LEFT JOIN contracts c ON c.proposal_id = p.id
                WHERE p.rfq_id = ANY($1::varchar[])
                  AND p.status::text = 'accepted'
                ORDER BY p.rfq_id, p.id
                """,
                rfq_ids,
            )
        }
        for item in items:
            rfq = rfqs.get(item["rfq_id"])
            item["rfq_summary"] = (
                {
                    "id": rfq["id"],
                    "title": rfq["title"],
                    "status": rfq["status"],
                    "deadline": rfq["deadline"],
                }
                if rfq
                else None
            )
            item["outcome"] = proposal_outcome(item, rfq, winners.get(item["rfq_id"]))

    async def list_messages(self, proposal_id: int, actor_id: int, as_buyer: bool) -> list:
        async with self.connection() as conn:
            row = await load_proposal_context(conn, proposal_id)
            assert_chat_access(row, actor_id, as_buyer)
            messages = await conn.fetch(
                f"""
                SELECT
                    m.id, m.proposal_id, m.sender_id, m.text, m.created_at,
                    u.first_name, u.last_name,
                {PROPOSAL_MESSAGE_AFFILIATION}
                FROM proposal_messages m
                LEFT JOIN users u ON u.id = m.sender_id
                WHERE m.proposal_id = $1
                  AND m.deleted_at IS NULL
                ORDER BY m.created_at ASC
                """,
                proposal_id,
            )
        return [message_to_response(item) for item in messages]

    async def list_message_threads(self, actor_id: int, as_buyer: bool) -> list:
        if as_buyer:
            sql = """
                SELECT
                    p.id AS proposal_id,
                    p.rfq_id,
                    r.title AS rfq_title,
                    p.status::text AS status,
                    p.supplier_actor_id AS counterpart_actor_id,
                    COALESCE(
                        NULLIF(sc.title, ''),
                        NULLIF(sa.display_name, ''),
                        'Исполнитель'
                    ) AS counterpart_name,
                    stats.message_count,
                    lm.id AS last_message_id,
                    lm.text AS last_message_text,
                    lm.sender_id AS last_sender_id,
                    lm.created_at AS last_message_at,
                    u.first_name AS last_first_name,
                    u.last_name AS last_last_name
                FROM proposals p
                JOIN rfqs r ON r.id = p.rfq_id
                JOIN actors sa ON sa.id = p.supplier_actor_id
                LEFT JOIN companies sc ON sc.id = sa.company_id
                JOIN LATERAL (
                    SELECT COUNT(*)::int AS message_count, MAX(m.created_at) AS last_at
                    FROM proposal_messages m
                    WHERE m.proposal_id = p.id AND m.deleted_at IS NULL
                ) stats ON stats.message_count > 0
                JOIN LATERAL (
                    SELECT m.id, m.text, m.sender_id, m.created_at
                    FROM proposal_messages m
                    WHERE m.proposal_id = p.id AND m.deleted_at IS NULL
                    ORDER BY m.created_at DESC, m.id DESC
                    LIMIT 1
                ) lm ON TRUE
                LEFT JOIN users u ON u.id = lm.sender_id
                WHERE r.actor_id = $1
                ORDER BY stats.last_at DESC, p.id DESC
            """
        else:
            sql = """
                SELECT
                    p.id AS proposal_id,
                    p.rfq_id,
                    r.title AS rfq_title,
                    p.status::text AS status,
                    r.actor_id AS counterpart_actor_id,
                    COALESCE(
                        NULLIF(ba.display_name, ''),
                        NULLIF(bc.title, ''),
                        'Заказчик'
                    ) AS counterpart_name,
                    stats.message_count,
                    lm.id AS last_message_id,
                    lm.text AS last_message_text,
                    lm.sender_id AS last_sender_id,
                    lm.created_at AS last_message_at,
                    u.first_name AS last_first_name,
                    u.last_name AS last_last_name
                FROM proposals p
                JOIN rfqs r ON r.id = p.rfq_id
                JOIN actors ba ON ba.id = r.actor_id
                LEFT JOIN companies bc ON bc.id = ba.company_id
                JOIN LATERAL (
                    SELECT COUNT(*)::int AS message_count, MAX(m.created_at) AS last_at
                    FROM proposal_messages m
                    WHERE m.proposal_id = p.id AND m.deleted_at IS NULL
                ) stats ON stats.message_count > 0
                JOIN LATERAL (
                    SELECT m.id, m.text, m.sender_id, m.created_at
                    FROM proposal_messages m
                    WHERE m.proposal_id = p.id AND m.deleted_at IS NULL
                    ORDER BY m.created_at DESC, m.id DESC
                    LIMIT 1
                ) lm ON TRUE
                LEFT JOIN users u ON u.id = lm.sender_id
                WHERE p.supplier_actor_id = $1
                ORDER BY stats.last_at DESC, p.id DESC
            """
        async with self.connection() as conn:
            rows = await conn.fetch(sql, actor_id)
        return [_thread_from_row(row) for row in rows]

    async def get_message_thread(
        self, proposal_id: int, actor_id: int, as_buyer: bool
    ) -> dict:
        async with self.connection() as conn:
            row = await load_proposal_context(conn, proposal_id)
            assert_chat_access(row, actor_id, as_buyer)
            if as_buyer:
                meta = await conn.fetchrow(
                    """
                    SELECT
                        p.id AS proposal_id,
                        p.rfq_id,
                        r.title AS rfq_title,
                        p.status::text AS status,
                        p.supplier_actor_id AS counterpart_actor_id,
                        COALESCE(
                            NULLIF(sc.title, ''),
                            NULLIF(sa.display_name, ''),
                            'Исполнитель'
                        ) AS counterpart_name,
                        (
                            SELECT COUNT(*)::int FROM proposal_messages m
                            WHERE m.proposal_id = p.id AND m.deleted_at IS NULL
                        ) AS message_count,
                        lm.id AS last_message_id,
                        lm.text AS last_message_text,
                        lm.sender_id AS last_sender_id,
                        lm.created_at AS last_message_at,
                        u.first_name AS last_first_name,
                        u.last_name AS last_last_name
                    FROM proposals p
                    JOIN rfqs r ON r.id = p.rfq_id
                    JOIN actors sa ON sa.id = p.supplier_actor_id
                    LEFT JOIN companies sc ON sc.id = sa.company_id
                    LEFT JOIN LATERAL (
                        SELECT m.id, m.text, m.sender_id, m.created_at
                        FROM proposal_messages m
                        WHERE m.proposal_id = p.id AND m.deleted_at IS NULL
                        ORDER BY m.created_at DESC, m.id DESC
                        LIMIT 1
                    ) lm ON TRUE
                    LEFT JOIN users u ON u.id = lm.sender_id
                    WHERE p.id = $1
                    """,
                    proposal_id,
                )
            else:
                meta = await conn.fetchrow(
                    """
                    SELECT
                        p.id AS proposal_id,
                        p.rfq_id,
                        r.title AS rfq_title,
                        p.status::text AS status,
                        r.actor_id AS counterpart_actor_id,
                        COALESCE(
                            NULLIF(ba.display_name, ''),
                            NULLIF(bc.title, ''),
                            'Заказчик'
                        ) AS counterpart_name,
                        (
                            SELECT COUNT(*)::int FROM proposal_messages m
                            WHERE m.proposal_id = p.id AND m.deleted_at IS NULL
                        ) AS message_count,
                        lm.id AS last_message_id,
                        lm.text AS last_message_text,
                        lm.sender_id AS last_sender_id,
                        lm.created_at AS last_message_at,
                        u.first_name AS last_first_name,
                        u.last_name AS last_last_name
                    FROM proposals p
                    JOIN rfqs r ON r.id = p.rfq_id
                    JOIN actors ba ON ba.id = r.actor_id
                    LEFT JOIN companies bc ON bc.id = ba.company_id
                    LEFT JOIN LATERAL (
                        SELECT m.id, m.text, m.sender_id, m.created_at
                        FROM proposal_messages m
                        WHERE m.proposal_id = p.id AND m.deleted_at IS NULL
                        ORDER BY m.created_at DESC, m.id DESC
                        LIMIT 1
                    ) lm ON TRUE
                    LEFT JOIN users u ON u.id = lm.sender_id
                    WHERE p.id = $1
                    """,
                    proposal_id,
                )
        if not meta:
            raise NotFoundError("Proposal not found")
        return _thread_from_row(meta)
