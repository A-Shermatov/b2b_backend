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


def _actor_brief(
    *,
    actor_id,
    name: str | None,
    rating,
    photo_raw,
    default_name: str,
) -> dict:
    return {
        "id": actor_id,
        "name": name or default_name,
        "rating": float(rating or 0),
        "photo": public_file_url(photo_raw) if photo_raw else None,
    }


def _actor_from_row(row, *, role: str = "supplier") -> dict | None:
    keys = row.keys()
    if role == "buyer":
        if "buyer_id" not in keys:
            return None
        return _actor_brief(
            actor_id=row["buyer_id"],
            name=row["buyer_name"],
            rating=row["buyer_rating"],
            photo_raw=row["buyer_photo"],
            default_name="Заказчик",
        )
    if "supplier_id" not in keys:
        return None
    return _actor_brief(
        actor_id=row["supplier_id"],
        name=row["supplier_name"],
        rating=row["supplier_rating"],
        photo_raw=row["supplier_photo"],
        default_name="Исполнитель",
    )


def _item_from_row(row) -> dict | None:
    keys = row.keys()
    if "item_name" not in keys:
        return None
    photo_raw = row["item_photo"]
    return {
        "name": row["item_name"] or "",
        "description": row["item_description"],
        "photo": public_file_url(photo_raw) if photo_raw else None,
    }


def proposal_to_response(row, *, actor_role: str = "supplier") -> dict:
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
    actor = _actor_from_row(row, role=actor_role)
    if actor is not None:
        payload["actor"] = actor
    item = _item_from_row(row)
    if item is not None:
        payload["item"] = item
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
        COALESCE(scs.average_rating, sc.rating, 0) AS supplier_rating,
        sc.logo AS supplier_photo,
        ba.id AS buyer_id,
        COALESCE(
            NULLIF(bc.title, ''),
            NULLIF(ba.display_name, ''),
            'Заказчик'
        ) AS buyer_name,
        COALESCE(bcs.average_rating, bc.rating, 0) AS buyer_rating,
        bc.logo AS buyer_photo,
        COALESCE(NULLIF(ci.title, ''), r.title) AS item_name,
        COALESCE(NULLIF(ci.description, ''), r.description) AS item_description,
        (
            SELECT im.file_url
            FROM item_media im
            WHERE im.item_id = r.catalog_item_id
            ORDER BY im.sort_order ASC, im.id ASC
            LIMIT 1
        ) AS item_photo
    FROM proposals p
    LEFT JOIN proposal_attachments pa ON pa.proposal_id = p.id
    JOIN actors sa ON sa.id = p.supplier_actor_id
    LEFT JOIN companies sc ON sc.id = sa.company_id
    LEFT JOIN company_stats scs ON scs.company_id = sc.id
    JOIN rfqs r ON r.id = p.rfq_id
    JOIN actors ba ON ba.id = r.actor_id
    LEFT JOIN companies bc ON bc.id = ba.company_id
    LEFT JOIN company_stats bcs ON bcs.company_id = bc.id
    LEFT JOIN catalog_items ci ON ci.id = r.catalog_item_id
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
