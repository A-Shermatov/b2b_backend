from app.core.exceptions import ForbiddenError, NotFoundError
from app.utils.storage import public_file_url

TICKET_COLUMNS = """
    t.id, t.user_id, t.side::text AS side, t.status::text AS status,
    t.assigned_admin_id, t.last_message_at, t.created_at, t.closed_at
"""

MESSAGE_COLUMNS = """
    m.id, m.ticket_id, m.sender_id, m.text, m.created_at,
    m.status::text AS status, m.delivered_at, m.viewed_at,
    u.first_name, u.last_name, u.email
"""


def sender_display_name(first_name, last_name, email, sender_id: int) -> str:
    name = f"{first_name or ''} {last_name or ''}".strip()
    if name:
        return name
    if email:
        return email
    return f"User #{sender_id}"


def message_to_dict(row) -> dict:
    attachment = None
    if row.get("attachment_id"):
        attachment = {
            "id": row["attachment_id"],
            "message_id": row["id"],
            "file_name": row["file_name"],
            "file_url": public_file_url(row["file_url"]),
            "file_type": row["file_type"],
        }
    return {
        "id": row["id"],
        "ticket_id": row["ticket_id"],
        "sender_id": row["sender_id"],
        "sender_name": sender_display_name(
            row["first_name"], row["last_name"], row["email"], row["sender_id"]
        ),
        "text": row["text"] or "",
        "attachment": attachment,
        "created_at": row["created_at"],
        "status": row["status"] or "sent",
        "delivered_at": row["delivered_at"],
        "viewed_at": row["viewed_at"],
    }


def message_ws_payload(row) -> dict:
    payload = message_to_dict(row)
    for key in ("created_at", "delivered_at", "viewed_at"):
        value = payload[key]
        payload[key] = value.isoformat() if value else None
    return payload


async def load_message_row(conn, ticket_id: int, message_id: int):
    row = await conn.fetchrow(
        f"""
        SELECT
            {MESSAGE_COLUMNS},
            a.id AS attachment_id, a.file_name, a.file_url, a.file_type
        FROM support_messages m
        LEFT JOIN users u ON u.id = m.sender_id
        LEFT JOIN support_message_attachments a ON a.message_id = m.id
        WHERE m.id = $1 AND m.ticket_id = $2
        """,
        message_id,
        ticket_id,
    )
    if not row:
        raise NotFoundError("Message not found")
    return row


async def load_messages(conn, ticket_id: int) -> list[dict]:
    rows = await conn.fetch(
        f"""
        SELECT
            {MESSAGE_COLUMNS},
            a.id AS attachment_id, a.file_name, a.file_url, a.file_type
        FROM support_messages m
        LEFT JOIN users u ON u.id = m.sender_id
        LEFT JOIN support_message_attachments a ON a.message_id = m.id
        WHERE m.ticket_id = $1
        ORDER BY m.created_at ASC, m.id ASC
        """,
        ticket_id,
    )
    return [message_to_dict(row) for row in rows]


async def unread_count_for_viewer(conn, ticket_id: int, viewer_id: int) -> int:
    return int(
        await conn.fetchval(
            """
            SELECT COUNT(*)
            FROM support_messages
            WHERE ticket_id = $1
              AND sender_id <> $2
              AND status::text <> 'viewed'
            """,
            ticket_id,
            viewer_id,
        )
        or 0
    )


async def ensure_ticket_owner(ticket: dict, user_id: int) -> None:
    if ticket["user_id"] != user_id:
        raise ForbiddenError("Access denied")


async def load_ticket_row(conn, ticket_id: int) -> dict:
    row = await conn.fetchrow(
        f"SELECT {TICKET_COLUMNS} FROM support_tickets t WHERE t.id = $1",
        ticket_id,
    )
    if not row:
        raise NotFoundError("Support ticket not found")
    return dict(row)


async def staff_user_ids(conn) -> list[int]:
    rows = await conn.fetch(
        """
        SELECT id
        FROM users
        WHERE role::text IN ('admin', 'superadmin', 'moderator')
          AND status::text = 'active'
        """
    )
    return [row["id"] for row in rows]


def ticket_admin_href() -> str:
    return "/admin/support"
