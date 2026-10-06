import json
import re
from datetime import datetime, timedelta
from types import SimpleNamespace

from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError
from app.enums import PaymentMilestoneStatus, PaymentMilestoneTrigger, PaymentType
from app.utils.storage import public_file_url

ACTIVE_DISPUTE_STATUSES = ("open", "under_review", "appealed")
_DELIVERY_DAYS_RE = re.compile(r"(\d+)")

CONTRACT_COLUMNS = """
    c.id, c.rfq_id, c.proposal_id, c.buyer_actor_id, c.supplier_actor_id,
    c.title, c.description, c.agreed_amount, c.currency::text AS currency,
    c.start_date, c.due_date, c.payment_type::text AS payment_type,
    c.status::text AS status, c.created_at,
    c.commission_amount, c.commission_percent, c.commission_min
"""


def enum_value(value) -> str:
    return value.value if hasattr(value, "value") else str(value)


def _agreed_amount(contract) -> float:
    if hasattr(contract, "agreed_amount") and not hasattr(contract, "keys"):
        return float(contract.agreed_amount)
    return float(contract["agreed_amount"])


def _default_milestones(contract, payment_type) -> list[dict]:
    amount = _agreed_amount(contract)
    ptype = enum_value(payment_type)
    if ptype == PaymentType.full_prepayment.value:
        return [
            {
                "title": "Полная предоплата",
                "percentage": 100,
                "amount": amount,
                "trigger": PaymentMilestoneTrigger.contract_signed.value,
                "status": PaymentMilestoneStatus.awaiting_payment,
            }
        ]
    if ptype == PaymentType.full_postpayment.value:
        return [
            {
                "title": "Оплата после приёмки",
                "percentage": 100,
                "amount": amount,
                "trigger": PaymentMilestoneTrigger.delivery_accepted.value,
                "status": PaymentMilestoneStatus.pending,
            }
        ]
    if ptype == PaymentType.split_payment.value:
        return [
            {
                "title": "Предоплата 50%",
                "percentage": 50,
                "amount": round(amount * 0.5, 2),
                "trigger": PaymentMilestoneTrigger.contract_signed.value,
                "status": PaymentMilestoneStatus.awaiting_payment,
            },
            {
                "title": "Оплата 50% после приёмки",
                "percentage": 50,
                "amount": round(amount * 0.5, 2),
                "trigger": PaymentMilestoneTrigger.delivery_accepted.value,
                "status": PaymentMilestoneStatus.pending,
            },
        ]
    return [
        {
            "title": "Этап 1 — подписание",
            "percentage": 30,
            "amount": round(amount * 0.3, 2),
            "trigger": PaymentMilestoneTrigger.contract_signed.value,
            "status": PaymentMilestoneStatus.awaiting_payment,
        },
        {
            "title": "Этап 2 — промежуточная сдача",
            "percentage": 40,
            "amount": round(amount * 0.4, 2),
            "trigger": PaymentMilestoneTrigger.delivery_accepted.value,
            "status": PaymentMilestoneStatus.pending,
        },
        {
            "title": "Этап 3 — финальная приёмка",
            "percentage": 30,
            "amount": round(amount * 0.3, 2),
            "trigger": PaymentMilestoneTrigger.delivery_accepted.value,
            "status": PaymentMilestoneStatus.pending,
        },
    ]


def _custom_milestones(contract, milestones: list[dict]) -> list[dict]:
    amount = _agreed_amount(contract)
    result: list[dict] = []
    allocated = 0.0
    last_index = len(milestones) - 1
    for index, item in enumerate(milestones):
        trigger_value = enum_value(item["trigger"])
        if index == last_index:
            stage_amount = round(amount - allocated, 2)
        else:
            stage_amount = round(item["percentage"] / 100 * amount, 2)
            allocated += stage_amount
        status = (
            PaymentMilestoneStatus.awaiting_payment
            if trigger_value == PaymentMilestoneTrigger.contract_signed.value
            else PaymentMilestoneStatus.pending
        )
        result.append(
            {
                "title": item["title"],
                "percentage": item["percentage"],
                "amount": stage_amount,
                "trigger": trigger_value,
                "status": status,
            }
        )
    return result


def parse_delivery_days(value) -> int | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    match = _DELIVERY_DAYS_RE.search(text)
    if not match:
        return None
    days = int(match.group(1))
    return days if days > 0 else None


def resolve_contract_due_date(
    start_date: str,
    delivery_time,
    fallback: str | None,
) -> str:
    days = parse_delivery_days(delivery_time)
    if days is not None:
        start = datetime.strptime(start_date[:10], "%Y-%m-%d")
        return (start + timedelta(days=days)).strftime("%Y-%m-%d")
    if fallback:
        return str(fallback)[:10]
    return start_date[:10]


def proposal_fields(proposal) -> dict:
    if isinstance(proposal, dict):
        rfq = proposal.get("rfq") or {}
        currency = proposal.get("currency")
        return {
            "id": proposal["id"],
            "supplier_actor_id": proposal["supplier_actor_id"],
            "price": proposal["price"],
            "currency": enum_value(currency),
            "delivery_time": proposal.get("delivery_time"),
            "rfq_id": proposal.get("rfq_id") or rfq.get("id"),
            "rfq_title": rfq.get("title") or proposal.get("title"),
            "rfq_description": rfq.get("description") or proposal.get("description"),
            "rfq_deadline": rfq.get("deadline") or proposal.get("deadline"),
        }
    rfq = proposal.rfq
    return {
        "id": proposal.id,
        "supplier_actor_id": proposal.supplier_actor_id,
        "price": proposal.price,
        "currency": enum_value(proposal.currency),
        "delivery_time": getattr(proposal, "delivery_time", None),
        "rfq_id": rfq.id,
        "rfq_title": rfq.title,
        "rfq_description": rfq.description,
        "rfq_deadline": rfq.deadline,
    }


def sender_display_name(first_name, last_name, email, user_id) -> str:
    name = f"{first_name or ''} {last_name or ''}".strip()
    return name or email or f"User #{user_id}"


def file_uploader_side(
    uploaded_by: int | None,
    buyer_user_ids: set[int],
    supplier_user_ids: set[int],
) -> str | None:
    if uploaded_by is None:
        return None
    in_buyer = uploaded_by in buyer_user_ids
    in_supplier = uploaded_by in supplier_user_ids
    if in_buyer and not in_supplier:
        return "buyer"
    if in_supplier and not in_buyer:
        return "supplier"
    return None


def shape_contract_file(
    item,
    *,
    buyer_user_ids: set[int],
    supplier_user_ids: set[int],
    include_contract_id: bool = True,
) -> dict:
    uploaded_by = item["uploaded_by"]
    payload = {
        "id": item["id"],
        "file_name": item["file_name"],
        "file_url": public_file_url(item["file_url"]),
        "file_type": item["file_type"],
        "uploaded_by": uploaded_by,
        "uploaded_by_name": sender_display_name(
            item["first_name"],
            item["last_name"],
            item["email"],
            uploaded_by,
        ),
        "uploaded_by_side": file_uploader_side(
            uploaded_by, buyer_user_ids, supplier_user_ids
        ),
        "created_at": item["created_at"],
    }
    if include_contract_id:
        payload["contract_id"] = item["contract_id"]
    return payload


def message_to_dict(row) -> dict:
    return {
        "id": row["id"],
        "conversation_id": row["conversation_id"],
        "sender_id": row["sender_id"],
        "sender_name": sender_display_name(
            row["first_name"], row["last_name"], row["email"], row["sender_id"]
        ),
        "text": row["text"],
        "attachment": None,
        "created_at": row["created_at"],
        "status": row["status"] or "sent",
        "delivered_at": row["delivered_at"],
        "viewed_at": row["viewed_at"],
    }


def message_ws_payload(row) -> dict:
    payload = message_to_dict(row)
    created = payload["created_at"]
    delivered = payload["delivered_at"]
    viewed = payload["viewed_at"]
    payload["created_at"] = created.isoformat() if created else None
    payload["delivered_at"] = delivered.isoformat() if delivered else None
    payload["viewed_at"] = viewed.isoformat() if viewed else None
    return payload


def submission_assets(file_names, raw_assets) -> list[dict]:
    raw = raw_assets or []
    if isinstance(raw, str):
        raw = json.loads(raw)
    result: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        kind = item.get("kind") or "file"
        name = item.get("name") or ""
        url = item.get("url") or ""
        if not name or not url:
            continue
        result.append(
            {
                "kind": kind,
                "name": name,
                "url": url if kind == "link" else public_file_url(url),
                "file_type": item.get("file_type"),
            }
        )
    if result:
        return result
    return [
        {"kind": "file", "name": name, "url": "#", "file_type": None}
        for name in (file_names or [])
        if name
    ]


async def load_contract_row(conn, contract_id: int):
    row = await conn.fetchrow(
        f"SELECT {CONTRACT_COLUMNS} FROM contracts c WHERE c.id = $1",
        contract_id,
    )
    if not row:
        raise NotFoundError("Contract not found")
    return row


async def ensure_party(row, actor_id: int) -> None:
    if row["buyer_actor_id"] != actor_id and row["supplier_actor_id"] != actor_id:
        raise ForbiddenError("Access denied")


async def has_active_dispute(conn, contract_id: int) -> bool:
    found = await conn.fetchval(
        """
        SELECT 1 FROM disputes
        WHERE contract_id = $1 AND status::text = ANY($2::text[])
        LIMIT 1
        """,
        contract_id,
        list(ACTIVE_DISPUTE_STATUSES),
    )
    return bool(found)


async def ensure_not_under_dispute(conn, row) -> None:
    if row["status"] == "disputed" or await has_active_dispute(conn, row["id"]):
        raise ConflictError("Contract is under dispute")


async def resolve_actor_user_ids(conn, actor_id: int) -> list[int]:
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


async def load_contract_schema(conn, contract_id: int) -> dict:
    contract = await load_contract_row(conn, contract_id)
    plan = await conn.fetchrow(
        """
        SELECT id, contract_id, payment_type::text AS payment_type
        FROM payment_plans WHERE contract_id = $1
        """,
        contract_id,
    )
    milestones = []
    payment_plan = None
    if plan:
        milestone_rows = await conn.fetch(
            """
            SELECT id, contract_id, title, percentage, amount, trigger,
                   status::text AS status, commission_amount
            FROM payment_milestones
            WHERE payment_plan_id = $1
            ORDER BY id
            """,
            plan["id"],
        )
        milestones = [dict(item) for item in milestone_rows]
        payment_plan = {
            "id": plan["id"],
            "contract_id": plan["contract_id"],
            "payment_type": plan["payment_type"],
            "milestones": milestones,
        }

    conversation_row = await conn.fetchrow(
        "SELECT id, contract_id FROM conversations WHERE contract_id = $1",
        contract_id,
    )
    conversation = None
    if conversation_row:
        messages = await conn.fetch(
            """
            SELECT
                m.id, m.conversation_id, m.sender_id, m.text, m.created_at,
                m.status::text AS status, m.delivered_at, m.viewed_at,
                u.first_name, u.last_name, u.email
            FROM messages m
            LEFT JOIN users u ON u.id = m.sender_id
            WHERE m.conversation_id = $1
            ORDER BY m.created_at ASC, m.id ASC
            """,
            conversation_row["id"],
        )
        conversation = {
            "id": conversation_row["id"],
            "contract_id": conversation_row["contract_id"],
            "messages": [message_to_dict(item) for item in messages],
        }

    files = await conn.fetch(
        """
        SELECT
            cf.id, cf.contract_id, cf.file_name, cf.file_url, cf.file_type,
            cf.uploaded_by, cf.created_at,
            u.first_name, u.last_name, u.email
        FROM contract_files cf
        LEFT JOIN users u ON u.id = cf.uploaded_by
        WHERE cf.contract_id = $1
        ORDER BY cf.id
        """,
        contract_id,
    )
    buyer_user_ids = set(await resolve_actor_user_ids(conn, contract["buyer_actor_id"]))
    supplier_user_ids = set(
        await resolve_actor_user_ids(conn, contract["supplier_actor_id"])
    )
    submissions = await conn.fetch(
        """
        SELECT id, contract_id, type::text AS type, note, status::text AS status,
               submitted_at, file_names, assets
        FROM work_submissions WHERE contract_id = $1 ORDER BY id
        """,
        contract_id,
    )
    dispute_row = await conn.fetchrow(
        """
        SELECT id, status::text AS status, opened_by_actor_id, buyer_statement,
               supplier_statement, admin_instructions, created_at, updated_at
        FROM disputes
        WHERE contract_id = $1 AND status::text = ANY($2::text[])
        ORDER BY id DESC
        LIMIT 1
        """,
        contract_id,
        list(ACTIVE_DISPUTE_STATUSES),
    )
    dispute = None
    if dispute_row:
        evidence = await conn.fetch(
            """
            SELECT id, file_name, file_url, file_type, note,
                   uploaded_by_actor_id, created_at
            FROM dispute_evidence
            WHERE dispute_id = $1
            ORDER BY created_at DESC
            """,
            dispute_row["id"],
        )
        dispute = {
            "id": dispute_row["id"],
            "status": dispute_row["status"],
            "opened_by_actor_id": dispute_row["opened_by_actor_id"],
            "buyer_statement": dispute_row["buyer_statement"],
            "supplier_statement": dispute_row["supplier_statement"],
            "admin_instructions": dispute_row["admin_instructions"],
            "created_at": dispute_row["created_at"],
            "updated_at": dispute_row["updated_at"],
            "evidence": [
                {
                    "id": item["id"],
                    "file_name": item["file_name"],
                    "file_url": public_file_url(item["file_url"]),
                    "file_type": item["file_type"],
                    "note": item["note"],
                    "uploaded_by_actor_id": item["uploaded_by_actor_id"],
                    "created_at": item["created_at"],
                }
                for item in evidence
            ],
        }

    return {
        "id": contract["id"],
        "rfq_id": contract["rfq_id"],
        "proposal_id": contract["proposal_id"],
        "buyer_actor_id": contract["buyer_actor_id"],
        "supplier_actor_id": contract["supplier_actor_id"],
        "title": contract["title"],
        "description": contract["description"],
        "agreed_amount": contract["agreed_amount"],
        "currency": contract["currency"],
        "start_date": contract["start_date"],
        "due_date": contract["due_date"],
        "payment_type": contract["payment_type"],
        "status": contract["status"],
        "created_at": contract["created_at"],
        "commission_amount": contract["commission_amount"],
        "commission_percent": contract["commission_percent"],
        "commission_min": contract["commission_min"],
        "payment_plan": payment_plan,
        "conversation": conversation,
        "dispute": dispute,
        "files": [
            shape_contract_file(
                item,
                buyer_user_ids=buyer_user_ids,
                supplier_user_ids=supplier_user_ids,
            )
            for item in files
        ],
        "submissions": [
            {
                "id": item["id"],
                "contract_id": item["contract_id"],
                "type": item["type"],
                "note": item["note"],
                "status": item["status"],
                "submitted_at": item["submitted_at"],
                "file_names": list(item["file_names"] or [])
                or [asset["name"] for asset in submission_assets(item["file_names"], item["assets"])],
                "assets": submission_assets(item["file_names"], item["assets"]),
            }
            for item in submissions
        ],
    }


def as_contract_ref(row) -> SimpleNamespace:
    return SimpleNamespace(**dict(row))
