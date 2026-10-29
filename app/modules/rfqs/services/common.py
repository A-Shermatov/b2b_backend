from app.core.exceptions import NotFoundError
from app.modules.notifications.services import NotificationService
from app.modules.verification.common import actor_verification_sql
from app.utils.storage import public_file_url

RFQ_COLUMNS = """
    r.id,
    r.actor_id,
    r.created_by,
    r.type::text AS type,
    r.title,
    r.description,
    r.category_id,
    r.budget_type::text AS budget_type,
    r.budget_from,
    r.budget_to,
    r.currency,
    r.deadline,
    r.visibility::text AS visibility,
    r.status::text AS status,
    r.quantity,
    r.delivery_country,
    r.delivery_city,
    r.delivery_address,
    r.delivery_date,
    r.project_duration,
    r.start_date,
    r.team_size_required,
    r.experience_required,
    r.catalog_item_id,
    r.created_at,
    r.updated_at
"""

BUYER_COLUMNS = f"""
    a.id AS buyer_id,
    a.kind::text AS buyer_kind,
    a.display_name AS buyer_display_name,
    c.rating AS buyer_rating,
    {actor_verification_sql("a", "c")} AS buyer_verification_status,
    c.city AS buyer_city,
    c.country AS buyer_country,
    c.legal_name AS buyer_legal_name,
    c.description AS buyer_description,
    c.website AS buyer_website,
    cs.completed_contracts AS buyer_completed_contracts
"""

CLOSED_RFQ_STATUSES = [
    "supplier_selected",
    "contract_created",
    "in_progress",
    "completed",
    "cancelled",
    "expired",
    "disputed",
]

BUYER_CLOSED_TAB_STATUSES = [
    "completed",
    "cancelled",
    "expired",
    "disputed",
    "archived",
]

BUYER_TAB_STATUSES = {
    "draft": ["draft"],
    "published": ["published"],
    "collecting": ["receiving_proposals"],
    "quotes": ["published", "receiving_proposals"],
    "deal": ["supplier_selected", "contract_created", "in_progress"],
    "closed": BUYER_CLOSED_TAB_STATUSES,
    "active": ["published", "receiving_proposals", "in_progress", "disputed"],
    "completed": ["completed", "cancelled", "expired"],
}

KNOWN_RFQ_STATUSES = {
    "draft",
    "published",
    "receiving_proposals",
    "supplier_selected",
    "contract_created",
    "in_progress",
    "completed",
    "cancelled",
    "expired",
    "disputed",
    "archived",
}

RFQ_UPDATABLE_COMMON = {
    "title",
    "description",
    "category_id",
    "budget_type",
    "budget_from",
    "budget_to",
    "currency",
    "deadline",
}

RFQ_UPDATABLE_PRODUCT = {
    "quantity",
    "delivery_country",
    "delivery_city",
    "delivery_address",
    "delivery_date",
}

RFQ_UPDATABLE_SERVICE = {
    "project_duration",
    "start_date",
    "team_size_required",
    "experience_required",
}


def buyer_summary_from_row(row) -> dict | None:
    if row["buyer_id"] is None:
        return None
    if row["buyer_kind"] == "company":
        return {
            "id": row["buyer_id"],
            "kind": row["buyer_kind"],
            "display_name": row["buyer_display_name"],
            "rating": float(row["buyer_rating"] or 0),
            "verification_status": row["buyer_verification_status"],
            "city": row["buyer_city"],
            "country": row["buyer_country"],
            "legal_name": row["buyer_legal_name"],
            "description": row["buyer_description"],
            "website": row["buyer_website"],
            "completed_contracts": int(row["buyer_completed_contracts"] or 0),
        }
    return {
        "id": row["buyer_id"],
        "kind": row["buyer_kind"],
        "display_name": row["buyer_display_name"],
        "rating": 0.0,
        "verification_status": None,
        "city": None,
        "country": None,
        "legal_name": None,
        "description": None,
        "website": None,
        "completed_contracts": 0,
    }


def attachment_to_dict(row) -> dict:
    return {
        "id": row["id"],
        "rfq_id": row["rfq_id"],
        "file_name": row["file_name"],
        "file_url": public_file_url(row["file_url"]),
        "file_type": row["file_type"],
    }


def rfq_to_response(
    row,
    attachments: list | None = None,
    invited_ids: list[int] | None = None,
    *,
    include_buyer: bool = False,
) -> dict:
    base = {
        "id": row["id"],
        "actor_id": str(row["actor_id"]),
        "created_by": row["created_by"],
        "title": row["title"],
        "description": row["description"],
        "category_id": row["category_id"],
        "budget_type": row["budget_type"],
        "budget_from": row["budget_from"],
        "budget_to": row["budget_to"],
        "currency": row["currency"],
        "deadline": row["deadline"],
        "visibility": row["visibility"],
        "status": row["status"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "attachments": [attachment_to_dict(item) for item in (attachments or [])],
        "invited_supplier_ids": list(invited_ids or []),
        "catalog_item_id": row.get("catalog_item_id"),
        "is_catalog_offer": row.get("catalog_item_id") is not None,
    }
    if include_buyer:
        base["buyer"] = buyer_summary_from_row(row)
    if row["type"] == "product":
        return {
            **base,
            "type": "product",
            "quantity": row["quantity"],
            "delivery_country": row["delivery_country"],
            "delivery_city": row["delivery_city"],
            "delivery_address": row["delivery_address"],
            "delivery_date": row["delivery_date"],
        }
    return {
        **base,
        "type": "service",
        "project_duration": row["project_duration"],
        "start_date": row["start_date"],
        "team_size_required": row["team_size_required"],
        "experience_required": row["experience_required"],
    }


async def fetch_attachments_map(conn, rfq_ids: list[str]) -> dict[str, list]:
    if not rfq_ids:
        return {}
    rows = await conn.fetch(
        """
        SELECT id, rfq_id, file_name, file_url, file_type
        FROM rfq_attachments
        WHERE rfq_id = ANY($1::text[])
        ORDER BY id
        """,
        rfq_ids,
    )
    mapping: dict[str, list] = {rfq_id: [] for rfq_id in rfq_ids}
    for row in rows:
        mapping[row["rfq_id"]].append(row)
    return mapping


async def fetch_invites_map(conn, rfq_ids: list[str]) -> dict[str, list[int]]:
    if not rfq_ids:
        return {}
    rows = await conn.fetch(
        """
        SELECT rfq_id, supplier_actor_id
        FROM rfq_invited_suppliers
        WHERE rfq_id = ANY($1::text[])
        ORDER BY id
        """,
        rfq_ids,
    )
    mapping: dict[str, list[int]] = {rfq_id: [] for rfq_id in rfq_ids}
    for row in rows:
        mapping[row["rfq_id"]].append(row["supplier_actor_id"])
    return mapping


OPEN_RFQ_STATUSES = ["published", "receiving_proposals"]


def deadline_passed_sql(alias: str = "r") -> str:
    deadline = f"LEFT({alias}.deadline, 10)"
    return (
        f"({deadline} ~ '^[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}$' "
        f"AND {deadline} < TO_CHAR(CURRENT_DATE, 'YYYY-MM-DD'))"
    )


async def expire_overdue_rfqs(conn) -> None:
    await conn.execute(
        f"""
        UPDATE rfqs r
        SET status = 'expired'::rfq_status, updated_at = NOW()
        WHERE r.status::text = ANY($1::text[])
          AND r.catalog_item_id IS NULL
          AND {deadline_passed_sql('r')}
          AND NOT EXISTS (SELECT 1 FROM contracts c WHERE c.rfq_id = r.id)
        """,
        OPEN_RFQ_STATUSES,
    )


async def rfq_deadline_passed(conn, rfq_id: str) -> bool:
    return bool(
        await conn.fetchval(
            f"SELECT {deadline_passed_sql('r')} FROM rfqs r WHERE r.id = $1",
            rfq_id,
        )
    )


async def validate_supplier_actor_ids(
    conn,
    supplier_ids: list[int],
    *,
    exclude_actor_id: int | None = None,
) -> list[int]:
    unique_ids = list(dict.fromkeys(supplier_ids))
    if not unique_ids:
        return []
    rows = await conn.fetch(
        """
        SELECT id FROM actors
        WHERE id = ANY($1::int[])
          AND side::text = 'supplier'
          AND is_active = TRUE
        """,
        unique_ids,
    )
    valid = {row["id"] for row in rows}
    return [sid for sid in unique_ids if sid in valid and sid != exclude_actor_id]


async def insert_invites(conn, rfq_id: str, supplier_ids: list[int]) -> list[int]:
    if not supplier_ids:
        return []
    rows = await conn.fetch(
        """
        INSERT INTO rfq_invited_suppliers (rfq_id, supplier_actor_id)
        SELECT $1, unnest($2::int[])
        ON CONFLICT (rfq_id, supplier_actor_id) DO NOTHING
        RETURNING supplier_actor_id
        """,
        rfq_id,
        supplier_ids,
    )
    return [row["supplier_actor_id"] for row in rows]


async def notify_invited_suppliers(rfq_id: str, title: str, supplier_ids: list[int]) -> None:
    service = NotificationService()
    for supplier_id in supplier_ids:
        await service.notify_actor_members(
            supplier_id,
            "rfq",
            "Персональная заявка",
            f"Заказчик отправил вам заявку «{title}»",
            f"/supplier/rfqs/{rfq_id}",
        )


def shape_for_supplier(
    rfq: dict, supplier_id: int, participation: dict | None = None
) -> dict:
    invited_ids = rfq.get("invited_supplier_ids") or []
    shaped = {
        **rfq,
        "is_invited": supplier_id in invited_ids,
        "invited_supplier_ids": [],
    }
    if participation is not None:
        shaped["my_proposal_status"] = participation.get("proposal_status")
        shaped["my_contract_id"] = participation.get("contract_id")
    return shaped


async def load_supplier_participation(conn, rfq_id: str, supplier_id: int) -> dict:
    mapping = await load_supplier_participation_map(conn, [rfq_id], supplier_id)
    return mapping.get(rfq_id, {"proposal_status": None, "contract_id": None})


async def load_supplier_participation_map(
    conn, rfq_ids: list[str], supplier_id: int
) -> dict[str, dict]:
    if not rfq_ids:
        return {}
    rows = await conn.fetch(
        """
        SELECT DISTINCT ON (p.rfq_id)
            p.rfq_id,
            p.status::text AS proposal_status,
            c.id AS contract_id
        FROM proposals p
        LEFT JOIN contracts c
          ON c.proposal_id = p.id AND c.supplier_actor_id = $2
        WHERE p.rfq_id = ANY($1::text[]) AND p.supplier_actor_id = $2
        ORDER BY p.rfq_id, p.id DESC
        """,
        rfq_ids,
        supplier_id,
    )
    return {
        row["rfq_id"]: {
            "proposal_status": row["proposal_status"],
            "contract_id": row["contract_id"],
        }
        for row in rows
    }


async def load_rfq_row(conn, rfq_id: str, *, with_buyer: bool = False):
    extra = f", {BUYER_COLUMNS}" if with_buyer else ""
    joins = """
        LEFT JOIN actors a ON a.id = r.actor_id
        LEFT JOIN companies c ON c.id = a.company_id AND a.kind::text = 'company'
        LEFT JOIN company_stats cs ON cs.company_id = c.id
    """ if with_buyer else ""
    row = await conn.fetchrow(
        f"""
        SELECT {RFQ_COLUMNS}{extra}
        FROM rfqs r
        {joins}
        WHERE r.id = $1
        """,
        rfq_id,
    )
    if not row:
        raise NotFoundError("RFQ not found")
    return row


async def load_rfq_response(conn, rfq_id: str, *, include_buyer: bool = False) -> dict:
    row = await load_rfq_row(conn, rfq_id, with_buyer=include_buyer)
    attachments = await fetch_attachments_map(conn, [rfq_id])
    invites = await fetch_invites_map(conn, [rfq_id])
    return rfq_to_response(
        row,
        attachments[rfq_id],
        invites[rfq_id],
        include_buyer=include_buyer,
    )


async def shape_rfq_rows(conn, rows, *, include_buyer: bool = False) -> list[dict]:
    rfq_ids = [row["id"] for row in rows]
    attachments = await fetch_attachments_map(conn, rfq_ids)
    invites = await fetch_invites_map(conn, rfq_ids)
    return [
        rfq_to_response(
            row,
            attachments[row["id"]],
            invites[row["id"]],
            include_buyer=include_buyer,
        )
        for row in rows
    ]
