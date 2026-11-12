import re
from typing import Literal

from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.core.pagination import build_page

# Same spirit as BrendMarket-v5 RISK — off-platform payment / contacts.
CHAT_RISK_RE = re.compile(
    r"карт[уаеы]|на карту|напрямую|whats\s?app|ватсап|телеграм|telegram|"
    r"\+992\s?\d{2}\s?\d{3}|\d{4}\s?\*{4}|мимо площадк",
    re.IGNORECASE,
)

ChatKind = Literal["contract", "proposal"]
ChatFilter = Literal["all", "flag", "disp"]


def message_is_flagged(text: str | None) -> bool:
    return bool(text and CHAT_RISK_RE.search(text))



def _party_sql(actor: str) -> str:
    return f"""EXISTS (
        SELECT 1 FROM actors a
        LEFT JOIN company_users cu ON cu.company_id = a.company_id
        WHERE a.id = {actor} AND m.sender_id IN (a.user_id, cu.user_id)
    )"""


# Whose message it is ($2 is the buyer's profile, $3 the supplier's). A company
# writes through its people, so a member of the buyer's company is the buyer.
# Only the platform's own staff are «admin»; somebody who has left a company
# since is «other».
MESSAGE_SIDE_SQL = f"""CASE
    WHEN u.role::text IN ('admin', 'superadmin', 'moderator') THEN 'admin'
    WHEN {_party_sql("$2")} THEN 'buyer'
    WHEN {_party_sql("$3")} THEN 'supplier'
    ELSE 'other'
END"""

class AdminChatsMixin:
    async def count_flagged_chats(self) -> int:
        async with self.connection() as conn:
            contract_flagged = await conn.fetchval(
                """
                SELECT COUNT(DISTINCT conv.id)
                FROM conversations conv
                JOIN messages m ON m.conversation_id = conv.id
                WHERE m.deleted_at IS NULL AND m.text ~* $1
                """,
                CHAT_RISK_RE.pattern,
            )
            proposal_flagged = await conn.fetchval(
                """
                SELECT COUNT(DISTINCT p.id)
                FROM proposals p
                JOIN proposal_messages m ON m.proposal_id = p.id
                WHERE m.deleted_at IS NULL AND m.text ~* $1
                """,
                CHAT_RISK_RE.pattern,
            )
        return int(contract_flagged or 0) + int(proposal_flagged or 0)

    async def list_chats(
        self,
        *,
        filter_key: ChatFilter = "all",
        query: str | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> dict:
        async with self.connection() as conn:
            contract_rows = await conn.fetch(
                """
                SELECT
                    'contract'::text AS kind,
                    conv.id AS thread_id,
                    c.id AS ref_id,
                    COALESCE(c.title, 'Сделка') AS about,
                    ('БМ-' || c.id::text) AS ref_label,
                    COALESCE(bc.title, ba.display_name, 'Покупатель') AS party_a,
                    COALESCE(sc.title, sa.display_name, 'Поставщик') AS party_b,
                    ba.company_id AS party_a_company_id,
                    sa.company_id AS party_b_company_id,
                    lm.text AS last_text,
                    lm.created_at AS last_at,
                    EXISTS (
                        SELECT 1 FROM messages mx
                        WHERE mx.conversation_id = conv.id
                          AND mx.deleted_at IS NULL
                          AND mx.text ~* $1
                    ) AS flagged,
                    EXISTS (
                        SELECT 1 FROM disputes d
                        WHERE d.contract_id = c.id
                          AND d.status::text IN ('open', 'under_review', 'appealed')
                    ) AS has_dispute
                FROM conversations conv
                JOIN contracts c ON c.id = conv.contract_id
                JOIN actors ba ON ba.id = c.buyer_actor_id
                JOIN actors sa ON sa.id = c.supplier_actor_id
                LEFT JOIN companies bc ON bc.id = ba.company_id
                LEFT JOIN companies sc ON sc.id = sa.company_id
                JOIN LATERAL (
                    SELECT m.text, m.created_at
                    FROM messages m
                    WHERE m.conversation_id = conv.id AND m.deleted_at IS NULL
                    ORDER BY m.created_at DESC
                    LIMIT 1
                ) lm ON TRUE
                """,
                CHAT_RISK_RE.pattern,
            )
            proposal_rows = await conn.fetch(
                """
                SELECT
                    'proposal'::text AS kind,
                    p.id AS thread_id,
                    p.id AS ref_id,
                    COALESCE(r.title, 'Заявка') AS about,
                    COALESCE(r.id::text, 'З-' || p.id::text) AS ref_label,
                    COALESCE(bc.title, ba.display_name, 'Покупатель') AS party_a,
                    COALESCE(sc.title, sa.display_name, 'Поставщик') AS party_b,
                    ba.company_id AS party_a_company_id,
                    sa.company_id AS party_b_company_id,
                    lm.text AS last_text,
                    lm.created_at AS last_at,
                    EXISTS (
                        SELECT 1 FROM proposal_messages mx
                        WHERE mx.proposal_id = p.id
                          AND mx.deleted_at IS NULL
                          AND mx.text ~* $1
                    ) AS flagged,
                    FALSE AS has_dispute
                FROM proposals p
                JOIN rfqs r ON r.id = p.rfq_id
                JOIN actors ba ON ba.id = r.actor_id
                JOIN actors sa ON sa.id = p.supplier_actor_id
                LEFT JOIN companies bc ON bc.id = ba.company_id
                LEFT JOIN companies sc ON sc.id = sa.company_id
                JOIN LATERAL (
                    SELECT m.text, m.created_at
                    FROM proposal_messages m
                    WHERE m.proposal_id = p.id AND m.deleted_at IS NULL
                    ORDER BY m.created_at DESC
                    LIMIT 1
                ) lm ON TRUE
                """,
                CHAT_RISK_RE.pattern,
            )

        items = []
        for row in [*contract_rows, *proposal_rows]:
            item = {
                "id": f"{row['kind']}:{row['thread_id']}",
                "kind": row["kind"],
                "thread_id": row["thread_id"],
                "ref_id": row["ref_id"],
                "about": row["about"],
                "ref_label": row["ref_label"],
                "party_a": row["party_a"],
                "party_b": row["party_b"],
                "party_a_company_id": row["party_a_company_id"],
                "party_b_company_id": row["party_b_company_id"],
                "last_text": row["last_text"] or "",
                "last_at": row["last_at"],
                "flagged": bool(row["flagged"]),
                "has_dispute": bool(row["has_dispute"]),
            }
            if filter_key == "flag" and not item["flagged"]:
                continue
            if filter_key == "disp" and not item["has_dispute"]:
                continue
            if query:
                q = query.lower()
                hay = " ".join(
                    [
                        item["party_a"],
                        item["party_b"],
                        item["about"],
                        item["ref_label"],
                        item["last_text"],
                    ]
                ).lower()
                if q not in hay:
                    continue
            items.append(item)

        items.sort(key=lambda x: x["last_at"] or "", reverse=True)
        total = len(items)
        start = (page - 1) * page_size
        page_items = items[start : start + page_size]
        return build_page(page_items, total, page, page_size)

    async def get_chat(self, chat_id: str) -> dict:
        kind, thread_id = self._parse_chat_id(chat_id)
        async with self.connection() as conn:
            if kind == "contract":
                row = await conn.fetchrow(
                    """
                    SELECT
                        conv.id AS thread_id,
                        c.id AS ref_id,
                        COALESCE(c.title, 'Сделка') AS about,
                        ('БМ-' || c.id::text) AS ref_label,
                        COALESCE(bc.title, ba.display_name, 'Покупатель') AS party_a,
                        COALESCE(sc.title, sa.display_name, 'Поставщик') AS party_b,
                        ba.company_id AS party_a_company_id,
                        sa.company_id AS party_b_company_id,
                        ba.id AS buyer_actor_id,
                        sa.id AS supplier_actor_id,
                        EXISTS (
                            SELECT 1 FROM disputes d
                            WHERE d.contract_id = c.id
                              AND d.status::text IN ('open', 'under_review', 'appealed')
                        ) AS has_dispute
                    FROM conversations conv
                    JOIN contracts c ON c.id = conv.contract_id
                    JOIN actors ba ON ba.id = c.buyer_actor_id
                    JOIN actors sa ON sa.id = c.supplier_actor_id
                    LEFT JOIN companies bc ON bc.id = ba.company_id
                    LEFT JOIN companies sc ON sc.id = sa.company_id
                    WHERE conv.id = $1
                    """,
                    thread_id,
                )
                if not row:
                    raise NotFoundError("Chat not found")
                messages = await conn.fetch(
                    f"""
                    SELECT
                        m.id, m.sender_id, m.text, m.created_at,
                        m.status::text AS status,
                        m.deleted_at,
                        u.first_name, u.last_name, u.email,
                        {MESSAGE_SIDE_SQL} AS side
                    FROM messages m
                    LEFT JOIN users u ON u.id = m.sender_id
                    WHERE m.conversation_id = $1
                    ORDER BY m.created_at ASC, m.id ASC
                    """,
                    thread_id,
                    row["buyer_actor_id"],
                    row["supplier_actor_id"],
                )
            else:
                row = await conn.fetchrow(
                    """
                    SELECT
                        p.id AS thread_id,
                        p.id AS ref_id,
                        COALESCE(r.title, 'Заявка') AS about,
                        COALESCE(r.id::text, 'З-' || p.id::text) AS ref_label,
                        COALESCE(bc.title, ba.display_name, 'Покупатель') AS party_a,
                        COALESCE(sc.title, sa.display_name, 'Поставщик') AS party_b,
                        ba.company_id AS party_a_company_id,
                        sa.company_id AS party_b_company_id,
                        ba.id AS buyer_actor_id,
                        sa.id AS supplier_actor_id,
                        FALSE AS has_dispute
                    FROM proposals p
                    JOIN rfqs r ON r.id = p.rfq_id
                    JOIN actors ba ON ba.id = r.actor_id
                    JOIN actors sa ON sa.id = p.supplier_actor_id
                    LEFT JOIN companies bc ON bc.id = ba.company_id
                    LEFT JOIN companies sc ON sc.id = sa.company_id
                    WHERE p.id = $1
                    """,
                    thread_id,
                )
                if not row:
                    raise NotFoundError("Chat not found")
                messages = await conn.fetch(
                    f"""
                    SELECT
                        m.id, m.sender_id, m.text, m.created_at,
                        'sent'::text AS status,
                        m.deleted_at,
                        u.first_name, u.last_name, u.email,
                        {MESSAGE_SIDE_SQL} AS side
                    FROM proposal_messages m
                    LEFT JOIN users u ON u.id = m.sender_id
                    WHERE m.proposal_id = $1
                    ORDER BY m.created_at ASC, m.id ASC
                    """,
                    thread_id,
                    row["buyer_actor_id"],
                    row["supplier_actor_id"],
                )

        msg_items = []
        flagged = False
        for m in messages:
            deleted = m["deleted_at"] is not None
            text = "" if deleted else (m["text"] or "")
            is_flag = (not deleted) and message_is_flagged(text)
            if is_flag:
                flagged = True
            name_parts = [m["first_name"] or "", m["last_name"] or ""]
            sender_name = " ".join(p for p in name_parts if p).strip() or m["email"] or "Участник"
            if m["side"] == "admin":
                # The parties see «Модератор»; the panel also says which one.
                sender_name = f"Модератор · {sender_name}"
            msg_items.append(
                {
                    "id": m["id"],
                    "sender_id": m["sender_id"],
                    "sender_name": sender_name,
                    "side": m["side"],
                    "text": "[сообщение удалено]" if deleted else text,
                    "created_at": m["created_at"],
                    "flagged": is_flag,
                    "deleted": deleted,
                }
            )

        return {
            "id": f"{kind}:{thread_id}",
            "kind": kind,
            "thread_id": thread_id,
            "ref_id": row["ref_id"],
            "about": row["about"],
            "ref_label": row["ref_label"],
            "party_a": row["party_a"],
            "party_b": row["party_b"],
            "party_a_company_id": row["party_a_company_id"],
            "party_b_company_id": row["party_b_company_id"],
            "flagged": flagged,
            "has_dispute": bool(row["has_dispute"]),
            "messages": msg_items,
        }

    async def post_chat_message(self, chat_id: str, admin_user_id: int, text: str) -> dict:
        body = (text or "").strip()
        if not body:
            raise ValidationError("Message text is required")
        kind, thread_id = self._parse_chat_id(chat_id)
        async with self.connection() as conn:
            if kind == "contract":
                conversation = await conn.fetchrow(
                    "SELECT id, contract_id FROM conversations WHERE id = $1",
                    thread_id,
                )
                if not conversation:
                    raise NotFoundError("Chat not found")
                await conn.fetchrow(
                    """
                    INSERT INTO messages (
                        conversation_id, sender_id, text, status, created_at
                    )
                    VALUES ($1, $2, $3, 'sent'::message_delivery_status, NOW())
                    RETURNING id
                    """,
                    thread_id,
                    admin_user_id,
                    body,
                )
            else:
                proposal = await conn.fetchrow(
                    "SELECT id FROM proposals WHERE id = $1",
                    thread_id,
                )
                if not proposal:
                    raise NotFoundError("Chat not found")
                await conn.fetchrow(
                    """
                    INSERT INTO proposal_messages (proposal_id, sender_id, text, created_at)
                    VALUES ($1, $2, $3, NOW())
                    RETURNING id
                    """,
                    thread_id,
                    admin_user_id,
                    body,
                )
        return await self.get_chat(chat_id)

    async def delete_chat_message(
        self, chat_id: str, message_id: int, admin_user_id: int
    ) -> dict:
        kind, thread_id = self._parse_chat_id(chat_id)
        async with self.connection() as conn:
            if kind == "contract":
                row = await conn.fetchrow(
                    """
                    SELECT m.id, m.deleted_at
                    FROM messages m
                    WHERE m.id = $1 AND m.conversation_id = $2
                    """,
                    message_id,
                    thread_id,
                )
                if not row:
                    raise NotFoundError("Message not found")
                if row["deleted_at"] is not None:
                    raise ConflictError("Сообщение уже удалено")
                await conn.execute(
                    """
                    UPDATE messages
                    SET deleted_at = NOW(), deleted_by_user_id = $2,
                        text = '[сообщение удалено]'
                    WHERE id = $1
                    """,
                    message_id,
                    admin_user_id,
                )
            else:
                row = await conn.fetchrow(
                    """
                    SELECT m.id, m.deleted_at
                    FROM proposal_messages m
                    WHERE m.id = $1 AND m.proposal_id = $2
                    """,
                    message_id,
                    thread_id,
                )
                if not row:
                    raise NotFoundError("Message not found")
                if row["deleted_at"] is not None:
                    raise ConflictError("Сообщение уже удалено")
                await conn.execute(
                    """
                    UPDATE proposal_messages
                    SET deleted_at = NOW(), deleted_by_user_id = $2,
                        text = '[сообщение удалено]'
                    WHERE id = $1
                    """,
                    message_id,
                    admin_user_id,
                )
        return await self.get_chat(chat_id)

    @staticmethod
    def _parse_chat_id(chat_id: str) -> tuple[ChatKind, int]:
        try:
            kind_raw, raw_id = chat_id.split(":", 1)
            thread_id = int(raw_id)
        except (ValueError, AttributeError) as exc:
            raise ValidationError("Invalid chat id") from exc
        if kind_raw not in {"contract", "proposal"} or thread_id <= 0:
            raise ValidationError("Invalid chat id")
        return kind_raw, thread_id  # type: ignore[return-value]
