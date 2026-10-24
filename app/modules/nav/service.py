"""Attention counts for cabinet sidebars (BrendMarket-v5 cBadge semantics)."""

from app.database import pool
from app.modules.admin.services.chats import CHAT_RISK_RE
from app.modules.rfqs.services.common import OPEN_RFQ_STATUSES

# Mirrors front/lib/process/deal-stages.ts ACTION_PHASES / getDealPhase.
_ACTIVE_DISPUTE = "EXISTS (SELECT 1 FROM disputes d WHERE d.contract_id = c.id AND d.status::text <> 'resolved')"
_UNPAID_PREPAY = """
    EXISTS (
        SELECT 1 FROM payment_milestones pm
        WHERE pm.contract_id = c.id
          AND pm.trigger::text = 'contract_signed'
          AND pm.status::text IN ('pending', 'awaiting_payment')
    )
"""
_UNPAID_ANY = """
    EXISTS (
        SELECT 1 FROM payment_milestones pm
        WHERE pm.contract_id = c.id
          AND pm.status::text IN ('pending', 'awaiting_payment')
    )
"""
_FUNDED_ANY = """
    EXISTS (
        SELECT 1 FROM payment_milestones pm
        WHERE pm.contract_id = c.id
          AND pm.status::text = 'funded'
    )
"""


class NavService:
    def connection(self):
        return pool.connection()

    async def buyer_counts(self, *, actor_id: int, user_id: int) -> dict:
        async with self.connection() as conn:
            rfqs = int(
                await conn.fetchval(
                    """
                    SELECT COUNT(*)
                    FROM rfqs r
                    WHERE r.actor_id = $1
                      AND r.status::text = ANY($2::text[])
                    """,
                    actor_id,
                    list(OPEN_RFQ_STATUSES),
                )
                or 0
            )
            deals = int(
                await conn.fetchval(
                    f"""
                    SELECT COUNT(*)
                    FROM contracts c
                    WHERE c.buyer_actor_id = $1
                      AND c.status::text <> 'disputed'
                      AND NOT {_ACTIVE_DISPUTE}
                      AND (
                        (c.status::text = 'pending_payment' AND {_UNPAID_PREPAY})
                        OR c.status::text = 'delivered'
                        OR (
                            c.status::text = 'completed'
                            AND {_UNPAID_ANY}
                        )
                        OR (
                            c.status::text = 'completed'
                            AND {_FUNDED_ANY}
                            AND NOT {_UNPAID_ANY}
                        )
                      )
                    """,
                    actor_id,
                )
                or 0
            )
            messages = await self._unread_contract_messages(
                conn, actor_col="buyer_actor_id", actor_id=actor_id, user_id=user_id
            )
            company_id = await conn.fetchval(
                "SELECT company_id FROM actors WHERE id = $1",
                actor_id,
            )
            approvals = 0
            if company_id:
                approvals = int(
                    await conn.fetchval(
                        """
                        SELECT COUNT(*)
                        FROM purchase_approvals
                        WHERE company_id = $1 AND status::text = 'wait'
                        """,
                        company_id,
                    )
                    or 0
                )
        return {
            "rfqs": rfqs,
            "deals": deals,
            "messages": messages,
            "approvals": approvals,
        }

    async def supplier_counts(self, *, actor_id: int, user_id: int) -> dict:
        async with self.connection() as conn:
            rfqs = int(
                await conn.fetchval(
                    """
                    SELECT COUNT(*)
                    FROM rfqs r
                    WHERE r.status::text = ANY($1::text[])
                      AND (
                        r.visibility::text = 'public'
                        OR EXISTS (
                            SELECT 1 FROM rfq_invited_suppliers inv
                            WHERE inv.rfq_id = r.id AND inv.supplier_actor_id = $2
                        )
                      )
                      AND NOT EXISTS (
                          SELECT 1 FROM proposals p
                          WHERE p.rfq_id = r.id AND p.supplier_actor_id = $2
                      )
                    """,
                    list(OPEN_RFQ_STATUSES),
                    actor_id,
                )
                or 0
            )
            deals = int(
                await conn.fetchval(
                    f"""
                    SELECT COUNT(*)
                    FROM contracts c
                    WHERE c.supplier_actor_id = $1
                      AND c.status::text NOT IN ('cancelled', 'disputed', 'delivered', 'completed')
                      AND NOT {_ACTIVE_DISPUTE}
                      AND NOT (
                          c.status::text = 'pending_payment' AND {_UNPAID_PREPAY}
                      )
                    """,
                    actor_id,
                )
                or 0
            )
            messages = await self._unread_contract_messages(
                conn, actor_col="supplier_actor_id", actor_id=actor_id, user_id=user_id
            )
            catalog = int(
                await conn.fetchval(
                    """
                    SELECT COUNT(*)
                    FROM catalog_items
                    WHERE actor_id = $1
                      AND status::text IN ('pending_review', 'changes_requested')
                    """,
                    actor_id,
                )
                or 0
            )
        return {
            "rfqs": rfqs,
            "deals": deals,
            "messages": messages,
            "catalog": catalog,
        }

    async def admin_counts(self) -> dict:
        async with self.connection() as conn:
            pending_verifications = int(
                await conn.fetchval(
                    """
                    SELECT COUNT(*) FROM companies
                    WHERE verification_status::text IN ('pending', 'needs_documents')
                    """
                )
                or 0
            )
            pending_catalog = int(
                await conn.fetchval(
                    """
                    SELECT COUNT(*) FROM catalog_items
                    WHERE status::text = 'pending_review'
                    """
                )
                or 0
            )
            open_reports = int(
                await conn.fetchval(
                    """
                    SELECT
                      (SELECT COUNT(*) FROM catalog_item_reports WHERE status::text = 'open')
                    + (SELECT COUNT(*) FROM rfq_reports WHERE status::text = 'open')
                    + (SELECT COUNT(*) FROM proposal_reports WHERE status::text = 'open')
                    """
                )
                or 0
            )
            support = int(
                await conn.fetchval(
                    """
                    SELECT COUNT(*)
                    FROM support_messages m
                    JOIN support_tickets t ON t.id = m.ticket_id
                    WHERE t.status::text = 'open'
                      AND m.sender_id = t.user_id
                      AND m.status::text <> 'viewed'
                    """
                )
                or 0
            )
            contract_flagged = int(
                await conn.fetchval(
                    """
                    SELECT COUNT(DISTINCT conv.id)
                    FROM conversations conv
                    JOIN messages m ON m.conversation_id = conv.id
                    WHERE m.text ~* $1
                    """,
                    CHAT_RISK_RE.pattern,
                )
                or 0
            )
            proposal_flagged = int(
                await conn.fetchval(
                    """
                    SELECT COUNT(DISTINCT p.id)
                    FROM proposals p
                    JOIN proposal_messages m ON m.proposal_id = p.id
                    WHERE m.text ~* $1
                    """,
                    CHAT_RISK_RE.pattern,
                )
                or 0
            )
            disputes = int(
                await conn.fetchval(
                    """
                    SELECT COUNT(*) FROM disputes
                    WHERE status::text IN ('open', 'under_review', 'appealed')
                    """
                )
                or 0
            )
            finance = int(
                await conn.fetchval(
                    """
                    SELECT COUNT(*) FROM withdrawals
                    WHERE status::text IN ('pending', 'processing')
                    """
                )
                or 0
            )
        return {
            "companies": pending_verifications,
            "moderation": pending_catalog + open_reports,
            "support": support,
            "chats": contract_flagged + proposal_flagged,
            "disputes": disputes,
            "finance": finance,
        }

    async def _unread_contract_messages(
        self,
        conn,
        *,
        actor_col: str,
        actor_id: int,
        user_id: int,
    ) -> int:
        if actor_col not in ("buyer_actor_id", "supplier_actor_id"):
            raise ValueError("invalid actor column")
        return int(
            await conn.fetchval(
                f"""
                SELECT COUNT(*)
                FROM messages m
                JOIN conversations conv ON conv.id = m.conversation_id
                JOIN contracts c ON c.id = conv.contract_id
                WHERE c.{actor_col} = $1
                  AND m.sender_id <> $2
                  AND m.status::text <> 'viewed'
                """,
                actor_id,
                user_id,
            )
            or 0
        )
