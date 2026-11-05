from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError
from app.database import pool
from app.modules.notifications.services import NotificationService
from app.modules.notifications.ws_manager import notification_ws_manager
from app.utils.affiliation import (
    DIRECT_CHAT_ACTORS,
    affiliation_fields,
    sender_affiliation_columns,
)


def _sender_display_name(first_name, last_name, email, sender_id: int) -> str:
    name = f"{first_name or ''} {last_name or ''}".strip()
    if name:
        return name
    if email:
        return email
    return f"User #{sender_id}"


def _iso(value):
    return value.isoformat() if value else None


def message_to_dict(row) -> dict:
    return {
        "id": row["id"],
        "conversation_id": row["conversation_id"],
        "sender_id": row["sender_id"],
        "sender_name": _sender_display_name(
            row["first_name"], row["last_name"], row.get("email"), row["sender_id"]
        ),
        "text": row["text"] or "",
        "created_at": _iso(row["created_at"]),
        "status": row["status"] or "sent",
        "delivered_at": _iso(row["delivered_at"]),
        "viewed_at": _iso(row["viewed_at"]),
        **affiliation_fields(row),
    }


MESSAGE_SELECT = f"""
    m.id, m.conversation_id, m.sender_id, m.text, m.created_at,
    m.status::text AS status, m.delivered_at, m.viewed_at,
    u.first_name, u.last_name, u.email,
    {sender_affiliation_columns("m.sender_id", DIRECT_CHAT_ACTORS)}
"""

ACTOR_NAME = """
COALESCE(NULLIF(a.display_name, ''), NULLIF(co.title, ''), 'Участник')
"""


class DirectChatService:
    def connection(self):
        return pool.connection()

    async def open_for_buyer(self, buyer_actor_id: int, supplier_actor_id: int, user_id: int):
        if supplier_actor_id == buyer_actor_id:
            raise ConflictError("Cannot chat with yourself")
        async with self.connection() as conn:
            await self._assert_side(conn, buyer_actor_id, "buyer")
            await self._assert_side(conn, supplier_actor_id, "supplier")
            chat_id = await self._get_or_create(conn, buyer_actor_id, supplier_actor_id)
            return await self._load_thread(conn, chat_id, user_id, as_buyer=True)

    async def open_for_supplier(self, supplier_actor_id: int, buyer_actor_id: int, user_id: int):
        if supplier_actor_id == buyer_actor_id:
            raise ConflictError("Cannot chat with yourself")
        async with self.connection() as conn:
            await self._assert_side(conn, buyer_actor_id, "buyer")
            await self._assert_side(conn, supplier_actor_id, "supplier")
            chat_id = await self._get_or_create(conn, buyer_actor_id, supplier_actor_id)
            return await self._load_thread(conn, chat_id, user_id, as_buyer=False)

    async def list_for_actor(self, actor_id: int, user_id: int, as_buyer: bool) -> list[dict]:
        col = "buyer_actor_id" if as_buyer else "supplier_actor_id"
        counterpart_col = "supplier_actor_id" if as_buyer else "buyer_actor_id"
        fallback = "Исполнитель" if as_buyer else "Заказчик"
        async with self.connection() as conn:
            rows = await conn.fetch(
                f"""
                SELECT
                    d.id, d.buyer_actor_id, d.supplier_actor_id,
                    d.{counterpart_col} AS counterpart_actor_id,
                    COALESCE(
                        NULLIF(a.display_name, ''),
                        NULLIF(co.title, ''),
                        $2
                    ) AS counterpart_name
                FROM direct_conversations d
                JOIN actors a ON a.id = d.{counterpart_col}
                LEFT JOIN companies co ON co.id = a.company_id
                WHERE d.{col} = $1
                ORDER BY d.id DESC
                """,
                actor_id,
                fallback,
            )
            threads = []
            for row in rows:
                threads.append(
                    await self._shape_thread(
                        conn, dict(row), user_id, include_messages=False
                    )
                )
            return threads

    async def get(self, chat_id: int, actor_id: int, user_id: int, as_buyer: bool) -> dict:
        async with self.connection() as conn:
            row = await self._load_row(conn, chat_id)
            self._ensure_party(row, actor_id)
            return await self._load_thread(conn, chat_id, user_id, as_buyer=as_buyer)

    async def add_message(
        self, chat_id: int, actor_id: int, user_id: int, text: str, as_buyer: bool
    ) -> dict:
        cleaned = text.strip()
        if not cleaned:
            raise ConflictError("Message cannot be empty")
        async with self.connection() as conn:
            row = await self._load_row(conn, chat_id)
            self._ensure_party(row, actor_id)
            saved = await conn.fetchrow(
                """
                INSERT INTO direct_messages (
                    conversation_id, sender_id, text, status, created_at
                )
                VALUES ($1, $2, $3, 'sent'::message_delivery_status, NOW())
                RETURNING id
                """,
                chat_id,
                user_id,
                cleaned,
            )
            recipient_actor_id = (
                row["supplier_actor_id"] if as_buyer else row["buyer_actor_id"]
            )
            recipient_user_ids = await self._actor_user_ids(conn, recipient_actor_id)
            if notification_ws_manager.is_any_online(recipient_user_ids):
                await conn.execute(
                    """
                    UPDATE direct_messages
                    SET status = 'delivered'::message_delivery_status, delivered_at = NOW()
                    WHERE id = $1
                    """,
                    saved["id"],
                )
            loaded = await conn.fetchrow(
                f"""
                SELECT {MESSAGE_SELECT}
                FROM direct_messages m
                LEFT JOIN users u ON u.id = m.sender_id
                WHERE m.id = $1
                """,
                saved["id"],
            )
        payload = message_to_dict(loaded)
        await notification_ws_manager.broadcast_to_users(
            recipient_user_ids,
            {
                "event": "direct.message",
                "data": {"conversation_id": chat_id, "message": payload},
            },
        )
        href = (
            f"/supplier/messages?buyer={row['buyer_actor_id']}"
            if as_buyer
            else f"/customer/messages?supplier={row['supplier_actor_id']}"
        )
        await NotificationService().notify_actor_members(
            recipient_actor_id,
            "contract",
            "Новое сообщение",
            cleaned[:140],
            href,
        )
        async with self.connection() as conn:
            return await self._load_thread(conn, chat_id, user_id, as_buyer=as_buyer)

    async def mark_read(self, chat_id: int, actor_id: int, user_id: int) -> dict:
        async with self.connection() as conn:
            row = await self._load_row(conn, chat_id)
            self._ensure_party(row, actor_id)
            updated = await conn.fetch(
                """
                UPDATE direct_messages
                SET
                    delivered_at = COALESCE(delivered_at, NOW()),
                    status = 'viewed'::message_delivery_status,
                    viewed_at = NOW()
                WHERE conversation_id = $1
                  AND sender_id <> $2
                  AND status::text <> 'viewed'
                RETURNING id
                """,
                chat_id,
                user_id,
            )
            payloads = []
            for item in updated:
                msg = await conn.fetchrow(
                    f"""
                    SELECT {MESSAGE_SELECT}
                    FROM direct_messages m
                    LEFT JOIN users u ON u.id = m.sender_id
                    WHERE m.id = $1
                    """,
                    item["id"],
                )
                payloads.append(message_to_dict(msg))
        counterpart_actor = (
            row["supplier_actor_id"]
            if actor_id == row["buyer_actor_id"]
            else row["buyer_actor_id"]
        )
        counterpart_users = await self._actor_user_ids_outer(counterpart_actor)
        if payloads:
            await notification_ws_manager.broadcast_to_users(
                counterpart_users,
                {
                    "event": "direct.message.status",
                    "data": {
                        "conversation_id": chat_id,
                        "messages": payloads,
                    },
                },
            )
        return {"message_ids": [item["id"] for item in updated], "messages": payloads}

    async def _actor_user_ids_outer(self, actor_id: int) -> list[int]:
        async with self.connection() as conn:
            return await self._actor_user_ids(conn, actor_id)

    async def _get_or_create(self, conn, buyer_actor_id: int, supplier_actor_id: int) -> int:
        chat_id = await conn.fetchval(
            """
            INSERT INTO direct_conversations (buyer_actor_id, supplier_actor_id, created_at)
            VALUES ($1, $2, NOW())
            ON CONFLICT (buyer_actor_id, supplier_actor_id)
            DO UPDATE SET buyer_actor_id = EXCLUDED.buyer_actor_id
            RETURNING id
            """,
            buyer_actor_id,
            supplier_actor_id,
        )
        return int(chat_id)

    async def _assert_side(self, conn, actor_id: int, side: str) -> None:
        row = await conn.fetchrow(
            "SELECT id, side::text AS side FROM actors WHERE id = $1",
            actor_id,
        )
        if not row:
            raise NotFoundError("Actor not found")
        if row["side"] != side:
            raise ForbiddenError("Invalid chat participant")

    async def _load_row(self, conn, chat_id: int):
        row = await conn.fetchrow(
            """
            SELECT id, buyer_actor_id, supplier_actor_id
            FROM direct_conversations
            WHERE id = $1
            """,
            chat_id,
        )
        if not row:
            raise NotFoundError("Chat not found")
        return row

    def _ensure_party(self, row, actor_id: int) -> None:
        if actor_id not in (row["buyer_actor_id"], row["supplier_actor_id"]):
            raise ForbiddenError("Access denied")

    async def _load_thread(self, conn, chat_id: int, user_id: int, as_buyer: bool) -> dict:
        counterpart_col = "supplier_actor_id" if as_buyer else "buyer_actor_id"
        fallback = "Исполнитель" if as_buyer else "Заказчик"
        row = await conn.fetchrow(
            f"""
            SELECT
                d.id, d.buyer_actor_id, d.supplier_actor_id,
                d.{counterpart_col} AS counterpart_actor_id,
                COALESCE(
                    NULLIF(a.display_name, ''),
                    NULLIF(co.title, ''),
                    $2
                ) AS counterpart_name
            FROM direct_conversations d
            JOIN actors a ON a.id = d.{counterpart_col}
            LEFT JOIN companies co ON co.id = a.company_id
            WHERE d.id = $1
            """,
            chat_id,
            fallback,
        )
        return await self._shape_thread(conn, dict(row), user_id, include_messages=True)

    async def _shape_thread(
        self, conn, row: dict, user_id: int, include_messages: bool
    ) -> dict:
        messages: list[dict] = []
        if include_messages:
            msg_rows = await conn.fetch(
                f"""
                SELECT {MESSAGE_SELECT}
                FROM direct_messages m
                LEFT JOIN users u ON u.id = m.sender_id
                WHERE m.conversation_id = $1
                ORDER BY m.created_at ASC, m.id ASC
                """,
                row["id"],
            )
            messages = [message_to_dict(item) for item in msg_rows]
        last_row = await conn.fetchrow(
            f"""
            SELECT {MESSAGE_SELECT}
            FROM direct_messages m
            LEFT JOIN users u ON u.id = m.sender_id
            WHERE m.conversation_id = $1
            ORDER BY m.created_at DESC, m.id DESC
            LIMIT 1
            """,
            row["id"],
        )
        unread = int(
            await conn.fetchval(
                """
                SELECT COUNT(*)
                FROM direct_messages
                WHERE conversation_id = $1
                  AND sender_id <> $2
                  AND status::text <> 'viewed'
                """,
                row["id"],
                user_id,
            )
            or 0
        )
        return {
            "id": row["id"],
            "buyer_actor_id": row["buyer_actor_id"],
            "supplier_actor_id": row["supplier_actor_id"],
            "counterpart_actor_id": row["counterpart_actor_id"],
            "counterpart_name": row["counterpart_name"],
            "unread_count": unread,
            "last_message": message_to_dict(last_row) if last_row else None,
            "messages": messages,
        }

    async def _actor_user_ids(self, conn, actor_id: int) -> list[int]:
        actor = await conn.fetchrow(
            "SELECT kind::text AS kind, user_id, company_id FROM actors WHERE id = $1",
            actor_id,
        )
        if not actor:
            return []
        if actor["kind"] == "individual":
            return [actor["user_id"]] if actor["user_id"] else []
        if actor["company_id"]:
            rows = await conn.fetch(
                "SELECT user_id FROM company_users WHERE company_id = $1",
                actor["company_id"],
            )
            return [item["user_id"] for item in rows]
        return []
