from app.core.exceptions import ConflictError, ForbiddenError
from app.modules.notifications.ws_manager import notification_ws_manager
from app.modules.support.schemas import SupportMessageSchema, SupportTicketSchema
from app.modules.support.services.common import (
    ensure_ticket_owner,
    load_message_row,
    load_messages,
    load_ticket_row,
    message_to_dict,
    message_ws_payload,
    staff_user_ids,
    unread_count_for_viewer,
)


class SupportUpdateMixin:
    async def _broadcast(self, user_ids: list[int], event: str, data: dict) -> None:
        ids = sorted({uid for uid in user_ids if uid})
        if not ids:
            return
        await notification_ws_manager.broadcast_to_users(ids, {"event": event, "data": data})

    async def mark_message_delivered(
        self,
        ticket_id: int,
        message_id: int,
        viewer_id: int,
        *,
        as_admin: bool = False,
    ) -> SupportMessageSchema:
        async with self.connection() as conn:
            ticket = await load_ticket_row(conn, ticket_id)
            if not as_admin:
                await ensure_ticket_owner(ticket, viewer_id)
            msg = await load_message_row(conn, ticket_id, message_id)
            if msg["sender_id"] == viewer_id:
                raise ForbiddenError("Cannot acknowledge own message")
            if msg["status"] == "sent":
                await conn.execute(
                    """
                    UPDATE support_messages
                    SET status = 'delivered'::message_delivery_status, delivered_at = NOW()
                    WHERE id = $1
                    """,
                    message_id,
                )
                msg = await load_message_row(conn, ticket_id, message_id)
                staff_ids = await staff_user_ids(conn)
                await self._broadcast(
                    [ticket["user_id"], *staff_ids],
                    "support.message.status",
                    {
                        "ticket_id": ticket_id,
                        "message": message_ws_payload(msg),
                    },
                )
            return SupportMessageSchema.model_validate(message_to_dict(msg))

    async def mark_messages_read(
        self, ticket_id: int, viewer_id: int, *, as_admin: bool = False
    ) -> dict:
        async with self.connection() as conn:
            ticket = await load_ticket_row(conn, ticket_id)
            if not as_admin:
                await ensure_ticket_owner(ticket, viewer_id)
            updated_ids = await conn.fetch(
                """
                UPDATE support_messages
                SET
                    delivered_at = COALESCE(delivered_at, NOW()),
                    status = 'viewed'::message_delivery_status,
                    viewed_at = NOW()
                WHERE ticket_id = $1
                  AND sender_id <> $2
                  AND status::text <> 'viewed'
                RETURNING id
                """,
                ticket_id,
                viewer_id,
            )
            if not updated_ids:
                return {"message_ids": []}
            payloads = []
            for item in updated_ids:
                msg = await load_message_row(conn, ticket_id, item["id"])
                payloads.append(message_ws_payload(msg))
            staff_ids = await staff_user_ids(conn)
            await self._broadcast(
                [ticket["user_id"], *staff_ids],
                "support.message.status",
                {
                    "ticket_id": ticket_id,
                    "messages": payloads,
                    "message": payloads[0],
                },
            )
            return {
                "message_ids": [item["id"] for item in updated_ids],
                "messages": payloads,
            }

    async def close_user_ticket(self, ticket_id: int, user_id: int) -> SupportTicketSchema:
        async with self.connection() as conn:
            ticket = await load_ticket_row(conn, ticket_id)
            await ensure_ticket_owner(ticket, user_id)
            if ticket["status"] == "closed":
                raise ConflictError("Ticket already closed")
            row = await conn.fetchrow(
                """
                UPDATE support_tickets
                SET status = 'closed'::support_ticket_status, closed_at = NOW()
                WHERE id = $1
                RETURNING
                    id, user_id, side::text AS side, status::text AS status,
                    assigned_admin_id, last_message_at, created_at, closed_at
                """,
                ticket_id,
            )
            messages = await load_messages(conn, ticket_id)
            unread = await unread_count_for_viewer(conn, ticket_id, user_id)
            staff_ids = await staff_user_ids(conn)
        await self._broadcast(
            [user_id, *staff_ids],
            "support.ticket.closed",
            {"ticket_id": ticket_id, "status": "closed"},
        )
        return SupportTicketSchema(**dict(row), unread_count=unread, messages=messages)

    async def close_admin_ticket(self, ticket_id: int, admin_id: int) -> SupportTicketSchema:
        async with self.connection() as conn:
            ticket = await load_ticket_row(conn, ticket_id)
            if ticket["status"] == "closed":
                raise ConflictError("Ticket already closed")
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
            row = await conn.fetchrow(
                """
                UPDATE support_tickets
                SET status = 'closed'::support_ticket_status, closed_at = NOW()
                WHERE id = $1
                RETURNING
                    id, user_id, side::text AS side, status::text AS status,
                    assigned_admin_id, last_message_at, created_at, closed_at
                """,
                ticket_id,
            )
            messages = await load_messages(conn, ticket_id)
            unread = await unread_count_for_viewer(conn, ticket_id, admin_id)
            staff_ids = await staff_user_ids(conn)
        await self._broadcast(
            [row["user_id"], *staff_ids],
            "support.ticket.closed",
            {"ticket_id": ticket_id, "status": "closed"},
        )
        return SupportTicketSchema(**dict(row), unread_count=unread, messages=messages)
