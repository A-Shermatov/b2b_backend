"""Attention counts for cabinet sidebars (BrendMarket-v5 cBadge semantics)."""

from datetime import datetime, timezone

from app.core.exceptions import ValidationError
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

BUYER_SECTIONS = frozenset({"rfqs", "deals", "approvals"})
SUPPLIER_SECTIONS = frozenset({"rfqs", "deals", "catalog"})
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


class NavService:
    def connection(self):
        return pool.connection()

    async def buyer_counts(self, *, actor_id: int, user_id: int) -> dict:
        async with self.connection() as conn:
            seen_rfqs = await self._section_seen_at(conn, user_id, actor_id, "rfqs")
            seen_deals = await self._section_seen_at(conn, user_id, actor_id, "deals")
            seen_approvals = await self._section_seen_at(
                conn, user_id, actor_id, "approvals"
            )
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
            rfqs_new = int(
                await conn.fetchval(
                    """
                    SELECT COUNT(*)
                    FROM rfqs r
                    WHERE r.actor_id = $1
                      AND r.status::text = ANY($2::text[])
                      AND r.created_at > $3
                    """,
                    actor_id,
                    list(OPEN_RFQ_STATUSES),
                    seen_rfqs,
                )
                or 0
            )
            deals_sql = f"""
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
            """
            deals = int(await conn.fetchval(deals_sql, actor_id) or 0)
            deals_new = int(
                await conn.fetchval(
                    deals_sql + " AND c.created_at > $2",
                    actor_id,
                    seen_deals,
                )
                or 0
            )
            messages = await self._unread_contract_messages(
                conn, actor_col="buyer_actor_id", actor_id=actor_id, user_id=user_id
            )
            messages += await self._unread_direct_messages(
                conn, actor_col="buyer_actor_id", actor_id=actor_id, user_id=user_id
            )
            company_id = await conn.fetchval(
                "SELECT company_id FROM actors WHERE id = $1",
                actor_id,
            )
            approvals = 0
            approvals_new = 0
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
                approvals_new = int(
                    await conn.fetchval(
                        """
                        SELECT COUNT(*)
                        FROM purchase_approvals
                        WHERE company_id = $1
                          AND status::text = 'wait'
                          AND created_at > $2
                        """,
                        company_id,
                        seen_approvals,
                    )
                    or 0
                )
        return {
            "rfqs": rfqs,
            "rfqs_new": rfqs_new,
            "deals": deals,
            "deals_new": deals_new,
            "messages": messages,
            "approvals": approvals,
            "approvals_new": approvals_new,
        }

    async def supplier_counts(self, *, actor_id: int, user_id: int) -> dict:
        async with self.connection() as conn:
            seen_rfqs = await self._section_seen_at(conn, user_id, actor_id, "rfqs")
            seen_deals = await self._section_seen_at(conn, user_id, actor_id, "deals")
            seen_catalog = await self._section_seen_at(
                conn, user_id, actor_id, "catalog"
            )
            rfqs_sql = """
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
            """
            rfqs = int(
                await conn.fetchval(rfqs_sql, list(OPEN_RFQ_STATUSES), actor_id) or 0
            )
            rfqs_new = int(
                await conn.fetchval(
                    rfqs_sql + " AND r.created_at > $3",
                    list(OPEN_RFQ_STATUSES),
                    actor_id,
                    seen_rfqs,
                )
                or 0
            )
            deals_sql = f"""
                SELECT COUNT(*)
                FROM contracts c
                WHERE c.supplier_actor_id = $1
                  AND c.status::text NOT IN ('cancelled', 'disputed', 'delivered', 'completed')
                  AND NOT {_ACTIVE_DISPUTE}
                  AND NOT (
                      c.status::text = 'pending_payment' AND {_UNPAID_PREPAY}
                  )
            """
            deals = int(await conn.fetchval(deals_sql, actor_id) or 0)
            deals_new = int(
                await conn.fetchval(
                    deals_sql + " AND c.created_at > $2",
                    actor_id,
                    seen_deals,
                )
                or 0
            )
            messages = await self._unread_contract_messages(
                conn, actor_col="supplier_actor_id", actor_id=actor_id, user_id=user_id
            )
            messages += await self._unread_direct_messages(
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
            catalog_new = int(
                await conn.fetchval(
                    """
                    SELECT COUNT(*)
                    FROM catalog_items
                    WHERE actor_id = $1
                      AND status::text IN ('pending_review', 'changes_requested')
                      AND created_at > $2
                    """,
                    actor_id,
                    seen_catalog,
                )
                or 0
            )
        return {
            "rfqs": rfqs,
            "rfqs_new": rfqs_new,
            "deals": deals,
            "deals_new": deals_new,
            "messages": messages,
            "catalog": catalog,
            "catalog_new": catalog_new,
        }

    async def mark_seen(
        self, *, user_id: int, actor_id: int, section: str, allowed: frozenset[str]
    ) -> None:
        if section not in allowed:
            raise ValidationError("Unknown nav section")
        async with self.connection() as conn:
            await conn.execute(
                """
                INSERT INTO nav_section_seen (user_id, actor_id, section, seen_at)
                VALUES ($1, $2, $3, NOW())
                ON CONFLICT (user_id, actor_id, section)
                DO UPDATE SET seen_at = NOW()
                """,
                user_id,
                actor_id,
                section,
            )

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
                    WHERE status::text IN ('pending_review', 'changes_requested')
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

    async def _section_seen_at(self, conn, user_id: int, actor_id: int, section: str):
        value = await conn.fetchval(
            """
            SELECT seen_at FROM nav_section_seen
            WHERE user_id = $1 AND actor_id = $2 AND section = $3
            """,
            user_id,
            actor_id,
            section,
        )
        return value or _EPOCH

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

    async def _unread_direct_messages(
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
                FROM direct_messages m
                JOIN direct_conversations d ON d.id = m.conversation_id
                WHERE d.{actor_col} = $1
                  AND m.sender_id <> $2
                  AND m.status::text <> 'viewed'
                """,
                actor_id,
                user_id,
            )
            or 0
        )
