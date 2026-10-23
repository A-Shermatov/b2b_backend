from app.core.exceptions import ConflictError, ValidationError
from app.modules.notifications.services import NotificationService
from app.modules.notifications.ws_manager import notification_ws_manager
from app.modules.support.schemas import SupportMessageSchema, SupportTicketSchema
from app.modules.support.services.common import (
    TICKET_COLUMNS,
    ensure_ticket_owner,
    load_message_row,
    load_messages,
    load_ticket_row,
    message_to_dict,
    message_ws_payload,
    staff_user_ids,
    ticket_admin_href,
    unread_count_for_viewer,
)


class SupportCreateMixin:
    async def create_user_ticket(self, user_id: int, side: str) -> SupportTicketSchema:
        async with self.connection() as conn:
            existing = await conn.fetchrow(
                f"""
                SELECT {TICKET_COLUMNS}
                FROM support_tickets t
                WHERE t.user_id = $1
                  AND t.side::text = $2
                  AND t.status::text = 'open'
                ORDER BY t.id DESC
                LIMIT 1
                """,
                user_id,
                side,
            )
            if existing:
                ticket = dict(existing)
            else:
                ticket_id = await conn.fetchval(
                    """
                    INSERT INTO support_tickets (user_id, side, status, created_at)
                    VALUES ($1, $2::support_side, 'open'::support_ticket_status, NOW())
                    RETURNING id
                    """,
                    user_id,
                    side,
                )
                ticket = await load_ticket_row(conn, ticket_id)
            messages = await load_messages(conn, ticket["id"])
            unread = await unread_count_for_viewer(conn, ticket["id"], user_id)
        return SupportTicketSchema(**ticket, unread_count=unread, messages=messages)

    async def _broadcast(self, user_ids: list[int], event: str, data: dict) -> None:
        ids = sorted({uid for uid in user_ids if uid})
        if not ids:
            return
        await notification_ws_manager.broadcast_to_users(ids, {"event": event, "data": data})

    async def _notify_new_message(
        self,
        *,
        recipient_ids: list[int],
        title: str,
        body: str,
        href: str | None,
    ) -> None:
        notify = NotificationService()
        for user_id in recipient_ids:
            await notify.notify_user(user_id, "support", title, body, href)

    async def add_user_message(
        self,
        ticket_id: int,
        user_id: int,
        text: str,
        *,
        file_name: str | None = None,
        file_url: str | None = None,
        file_type: str | None = None,
    ) -> SupportMessageSchema:
        text = (text or "").strip()
        if not text and not file_url:
            raise ValidationError("Message text or file is required")
        async with self.connection() as conn:
            ticket = await load_ticket_row(conn, ticket_id)
            await ensure_ticket_owner(ticket, user_id)
            if ticket["status"] != "open":
                raise ConflictError("Ticket is closed")
            message = await conn.fetchrow(
                """
                INSERT INTO support_messages (
                    ticket_id, sender_id, text, status, created_at
                )
                VALUES ($1, $2, $3, 'sent'::message_delivery_status, NOW())
                RETURNING id
                """,
                ticket_id,
                user_id,
                text,
            )
            if file_url:
                await conn.execute(
                    """
                    INSERT INTO support_message_attachments (
                        message_id, file_name, file_url, file_type
                    )
                    VALUES ($1, $2, $3, $4)
                    """,
                    message["id"],
                    file_name or "file",
                    file_url,
                    file_type or "application/octet-stream",
                )
            await conn.execute(
                """
                UPDATE support_tickets
                SET last_message_at = NOW()
                WHERE id = $1
                """,
                ticket_id,
            )
            staff_ids = await staff_user_ids(conn)
            if notification_ws_manager.is_any_online(staff_ids):
                await conn.execute(
                    """
                    UPDATE support_messages
                    SET status = 'delivered'::message_delivery_status, delivered_at = NOW()
                    WHERE id = $1
                    """,
                    message["id"],
                )
            loaded = await load_message_row(conn, ticket_id, message["id"])
            payload = message_ws_payload(loaded)
        await self._broadcast(
            staff_ids + [user_id],
            "support.message",
            {"ticket_id": ticket_id, "message": payload},
        )
        await self._notify_new_message(
            recipient_ids=staff_ids,
            title="Новое сообщение в поддержку",
            body=text[:120] if text else (file_name or "Вложение"),
            href=ticket_admin_href(),
        )
        return SupportMessageSchema.model_validate(message_to_dict(loaded))

    async def add_admin_message(
        self,
        ticket_id: int,
        admin_id: int,
        text: str,
        *,
        file_name: str | None = None,
        file_url: str | None = None,
        file_type: str | None = None,
    ) -> SupportMessageSchema:
        text = (text or "").strip()
        if not text and not file_url:
            raise ValidationError("Message text or file is required")
        async with self.connection() as conn:
            ticket = await load_ticket_row(conn, ticket_id)
            if ticket["status"] != "open":
                raise ConflictError("Ticket is closed")
            if ticket["assigned_admin_id"] is None:
                await conn.execute(
                    """
                    UPDATE support_tickets
                    SET assigned_admin_id = $1
                    WHERE id = $2 AND assigned_admin_id IS NULL
                    """,
                    admin_id,
                    ticket_id,
                )
            message = await conn.fetchrow(
                """
                INSERT INTO support_messages (
                    ticket_id, sender_id, text, status, created_at
                )
                VALUES ($1, $2, $3, 'sent'::message_delivery_status, NOW())
                RETURNING id
                """,
                ticket_id,
                admin_id,
                text,
            )
            if file_url:
                await conn.execute(
                    """
                    INSERT INTO support_message_attachments (
                        message_id, file_name, file_url, file_type
                    )
                    VALUES ($1, $2, $3, $4)
                    """,
                    message["id"],
                    file_name or "file",
                    file_url,
                    file_type or "application/octet-stream",
                )
            await conn.execute(
                """
                UPDATE support_tickets
                SET last_message_at = NOW()
                WHERE id = $1
                """,
                ticket_id,
            )
            recipient_ids = [ticket["user_id"]]
            staff_ids = await staff_user_ids(conn)
            if notification_ws_manager.is_any_online(recipient_ids):
                await conn.execute(
                    """
                    UPDATE support_messages
                    SET status = 'delivered'::message_delivery_status, delivered_at = NOW()
                    WHERE id = $1
                    """,
                    message["id"],
                )
            loaded = await load_message_row(conn, ticket_id, message["id"])
            payload = message_ws_payload(loaded)
        await self._broadcast(
            recipient_ids + staff_ids,
            "support.message",
            {"ticket_id": ticket_id, "message": payload},
        )
        await self._notify_new_message(
            recipient_ids=recipient_ids,
            title="Ответ поддержки",
            body=text[:120] if text else (file_name or "Вложение"),
            href=None,
        )
        return SupportMessageSchema.model_validate(message_to_dict(loaded))
