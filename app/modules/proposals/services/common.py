from app.core.exceptions import ForbiddenError, NotFoundError
from app.utils.storage import public_file_url

PROPOSAL_COLUMNS = """
    p.id,
    p.rfq_id,
    p.supplier_actor_id,
    p.price,
    p.currency::text AS currency,
    p.delivery_time,
    p.message,
    p.status::text AS status,
    p.created_at
"""

OPEN_CHAT_STATUSES = ("submitted", "viewed", "shortlisted", "pending_supplier")
FINAL_PROPOSAL_STATUSES = ("accepted", "rejected", "withdrawn", "archived")


def attachment_from_row(row) -> dict | None:
    if not row or row["attachment_id"] is None:
        return None
    return {
        "id": row["attachment_id"],
        "proposal_id": row["id"],
        "file_name": row["attachment_file_name"],
        "file_url": public_file_url(row["attachment_file_url"]),
        "file_type": row["attachment_file_type"],
    }


def _supplier_from_row(row) -> dict | None:
    keys = row.keys()
    if "supplier_id" not in keys:
        return None
    photo_raw = row["supplier_photo"]
    return {
        "id": row["supplier_id"],
        "name": row["supplier_name"] or "Исполнитель",
        "rating": float(row["supplier_rating"] or 0),
        "photo": public_file_url(photo_raw) if photo_raw else None,
    }


def proposal_to_response(row) -> dict:
    payload = {
        "id": row["id"],
        "rfq_id": row["rfq_id"],
        "supplier_actor_id": row["supplier_actor_id"],
        "price": row["price"],
        "currency": row["currency"],
        "delivery_time": row["delivery_time"],
        "message": row["message"],
        "status": row["status"],
        "created_at": row["created_at"],
        "attachment": attachment_from_row(row),
    }
    supplier = _supplier_from_row(row)
    if supplier is not None:
        payload["supplier"] = supplier
    return payload


def message_to_response(row) -> dict:
    sender_name = f"{row['first_name'] or ''} {row['last_name'] or ''}".strip()
    return {
        "id": row["id"],
        "proposal_id": row["proposal_id"],
        "sender_id": row["sender_id"],
        "sender_name": sender_name,
        "text": row["text"],
        "created_at": row["created_at"],
    }


PROPOSAL_SELECT = f"""
    SELECT
        {PROPOSAL_COLUMNS},
        pa.id AS attachment_id,
        pa.file_name AS attachment_file_name,
        pa.file_url AS attachment_file_url,
        pa.file_type AS attachment_file_type,
        sa.id AS supplier_id,
        COALESCE(
            NULLIF(sc.title, ''),
            NULLIF(sa.display_name, ''),
            'Исполнитель'
        ) AS supplier_name,
        COALESCE(cs.average_rating, sc.rating, 0) AS supplier_rating,
        sc.logo AS supplier_photo
    FROM proposals p
    LEFT JOIN proposal_attachments pa ON pa.proposal_id = p.id
    JOIN actors sa ON sa.id = p.supplier_actor_id
    LEFT JOIN companies sc ON sc.id = sa.company_id
    LEFT JOIN company_stats cs ON cs.company_id = sc.id
"""


async def load_proposal_row(conn, proposal_id: int):
    row = await conn.fetchrow(
        PROPOSAL_SELECT + " WHERE p.id = $1",
        proposal_id,
    )
    if not row:
        raise NotFoundError("Proposal not found")
    return row


async def load_proposal_context(conn, proposal_id: int):
    row = await conn.fetchrow(
        f"""
        SELECT
            {PROPOSAL_COLUMNS},
            pa.id AS attachment_id,
            pa.file_name AS attachment_file_name,
            pa.file_url AS attachment_file_url,
            pa.file_type AS attachment_file_type,
            r.actor_id AS rfq_actor_id,
            r.title AS rfq_title,
            r.description AS rfq_description,
            r.deadline AS rfq_deadline,
            r.status::text AS rfq_status,
            r.visibility::text AS rfq_visibility
        FROM proposals p
        JOIN rfqs r ON r.id = p.rfq_id
        LEFT JOIN proposal_attachments pa ON pa.proposal_id = p.id
        WHERE p.id = $1
        """,
        proposal_id,
    )
    if not row:
        raise NotFoundError("Proposal not found")
    return row


def assert_chat_access(row, actor_id: int, as_buyer: bool) -> None:
    if as_buyer:
        if row["rfq_actor_id"] != actor_id:
            raise ForbiddenError("Access denied")
        return
    if row["supplier_actor_id"] != actor_id:
        raise ForbiddenError("Access denied")
