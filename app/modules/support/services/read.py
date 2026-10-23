from app.core.pagination import build_page, fetch_page
from app.modules.support.schemas import (
    SupportTicketDetail,
    SupportTicketListItem,
    SupportTicketResponse,
    SupportTicketSchema,
)
from app.modules.support.services.common import (
    TICKET_COLUMNS,
    ensure_ticket_owner,
    load_messages,
    load_ticket_row,
    unread_count_for_viewer,
)


class SupportReadMixin:
    async def get_user_ticket(self, user_id: int, side: str) -> SupportTicketResponse:
        async with self.connection() as conn:
            row = await conn.fetchrow(
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
            if not row:
                return SupportTicketResponse(ticket=None)
            ticket = dict(row)
            messages = await load_messages(conn, ticket["id"])
            unread = await unread_count_for_viewer(conn, ticket["id"], user_id)
        return SupportTicketResponse(
            ticket=SupportTicketSchema(
                **ticket,
                unread_count=unread,
                messages=messages,
            )
        )

    async def user_unread_count(self, user_id: int, side: str) -> dict:
        async with self.connection() as conn:
            count = int(
                await conn.fetchval(
                    """
                    SELECT COUNT(*)
                    FROM support_messages m
                    JOIN support_tickets t ON t.id = m.ticket_id
                    WHERE t.user_id = $1
                      AND t.side::text = $2
                      AND t.status::text = 'open'
                      AND m.sender_id <> $1
                      AND m.status::text <> 'viewed'
                    """,
                    user_id,
                    side,
                )
                or 0
            )
        return {"count": count}

    async def admin_unread_count(self) -> dict:
        async with self.connection() as conn:
            count = int(
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
        return {"count": count}

    async def get_member_ticket(self, ticket_id: int, user_id: int) -> SupportTicketSchema:
        async with self.connection() as conn:
            ticket = await load_ticket_row(conn, ticket_id)
            await ensure_ticket_owner(ticket, user_id)
            messages = await load_messages(conn, ticket_id)
            unread = await unread_count_for_viewer(conn, ticket_id, user_id)
        return SupportTicketSchema(**ticket, unread_count=unread, messages=messages)

    async def list_admin_tickets(
        self,
        *,
        status: str = "open",
        page: int = 1,
        page_size: int = 20,
        viewer_id: int,
    ) -> dict:
        params: list = []
        filters = []
        if status and status != "all":
            params.append(status)
            filters.append(f"t.status::text = ${len(params)}")
        where = (" WHERE " + " AND ".join(filters)) if filters else ""
        count_sql = f"SELECT COUNT(*) FROM support_tickets t{where}"
        rows_sql = f"""
            SELECT
                {TICKET_COLUMNS},
                u.email AS user_email,
                u.first_name,
                u.last_name,
                COALESCE(
                    (
                        SELECT m.text
                        FROM support_messages m
                        WHERE m.ticket_id = t.id
                        ORDER BY m.created_at DESC, m.id DESC
                        LIMIT 1
                    ),
                    ''
                ) AS last_message_preview,
                (
                    SELECT COUNT(*)
                    FROM support_messages m
                    WHERE m.ticket_id = t.id
                      AND m.sender_id = t.user_id
                      AND m.status::text <> 'viewed'
                ) AS unread_count
            FROM support_tickets t
            JOIN users u ON u.id = t.user_id
            {where}
            ORDER BY t.last_message_at DESC NULLS LAST, t.id DESC
        """
        async with self.connection() as conn:
            total, rows = await fetch_page(conn, count_sql, rows_sql, params, page, page_size)
            items = []
            for row in rows:
                name = f"{row['first_name'] or ''} {row['last_name'] or ''}".strip()
                items.append(
                    SupportTicketListItem(
                        id=row["id"],
                        user_id=row["user_id"],
                        user_name=name or row["user_email"],
                        user_email=row["user_email"],
                        side=row["side"],
                        status=row["status"],
                        assigned_admin_id=row["assigned_admin_id"],
                        last_message_at=row["last_message_at"],
                        created_at=row["created_at"],
                        closed_at=row["closed_at"],
                        unread_count=int(row["unread_count"] or 0),
                        last_message_preview=(row["last_message_preview"] or "")[:200],
                    )
                )
        return build_page(items, total, page, page_size)

    async def get_admin_ticket(self, ticket_id: int, viewer_id: int) -> SupportTicketDetail:
        async with self.connection() as conn:
            row = await conn.fetchrow(
                f"""
                SELECT
                    {TICKET_COLUMNS},
                    u.email AS user_email,
                    u.first_name,
                    u.last_name
                FROM support_tickets t
                JOIN users u ON u.id = t.user_id
                WHERE t.id = $1
                """,
                ticket_id,
            )
            if not row:
                from app.core.exceptions import NotFoundError

                raise NotFoundError("Support ticket not found")
            messages = await load_messages(conn, ticket_id)
            unread = await unread_count_for_viewer(conn, ticket_id, viewer_id)
            name = f"{row['first_name'] or ''} {row['last_name'] or ''}".strip()
            preview = messages[-1]["text"] if messages else ""
        return SupportTicketDetail(
            id=row["id"],
            user_id=row["user_id"],
            user_name=name or row["user_email"],
            user_email=row["user_email"],
            side=row["side"],
            status=row["status"],
            assigned_admin_id=row["assigned_admin_id"],
            last_message_at=row["last_message_at"],
            created_at=row["created_at"],
            closed_at=row["closed_at"],
            unread_count=unread,
            last_message_preview=(preview or "")[:200],
            messages=messages,
        )
