import json
from typing import Any

from app.utils.storage import public_file_url

from app.modules.notifications.services import NotificationService
from app.utils.audit import AuditLogger

STAFF_RANK = {
    "moderator": 1,
    "admin": 2,
    "superadmin": 3,
}

USER_COLUMNS = """
    id, email, first_name, last_name, phone,
    role::text AS role, status::text AS status, created_at, updated_at
"""


def enum_value(value) -> str:
    return value.value if hasattr(value, "value") else str(value)


def actor_id_of(user) -> int:
    return user["id"] if isinstance(user, dict) else user.id


def actor_role_of(user) -> str:
    return enum_value(user["role"] if isinstance(user, dict) else user.role)


def pages_for(total: int, page_size: int) -> int:
    return max(1, (total + page_size - 1) // page_size) if total else 1


def normalize_query(query: str | None) -> str:
    return query.strip() if query else ""


def parse_json(value: Any) -> Any:
    if value is None:
        return {}
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return {}
    return {}


def file_url(value: str | None) -> str | None:
    if not value:
        return value
    return public_file_url(value)


def with_reason(body: str, reason: str | None) -> str:
    if reason and reason.strip():
        return f"{body} Причина: {reason.strip()}"
    return body


def company_status_sql(status: str) -> str | None:
    if status == "verified":
        return "c.verification_status::text = 'verified'"
    if status == "pending":
        return "c.verification_status::text IN ('pending', 'needs_documents')"
    if status == "rejected":
        return "c.verification_status::text = 'rejected'"
    if status == "blocked":
        return "c.operational_status::text = 'blocked'"
    return None


def catalog_view_sql(view: str) -> str | None:
    if view == "products":
        return "ci.type::text = 'product'"
    if view == "services":
        return "ci.type::text = 'service'"
    if view == "draft":
        return "ci.status::text IN ('draft', 'pending_review', 'changes_requested')"
    if view == "reported":
        return """
            EXISTS (
                SELECT 1 FROM catalog_item_reports r
                WHERE r.item_id = ci.id AND r.status::text = 'open'
            )
        """
    if view == "hidden":
        return "ci.status::text = 'hidden'"
    return None


def rfq_view_sql(view: str) -> str | None:
    if view == "published":
        return (
            "r.status::text IN ('published', 'receiving_proposals', "
            "'supplier_selected', 'contract_created', 'in_progress')"
        )
    if view == "closed":
        return "r.status::text IN ('completed', 'cancelled', 'expired', 'disputed')"
    if view == "draft":
        return "r.status::text = 'draft'"
    if view == "reported":
        return """
            EXISTS (
                SELECT 1 FROM rfq_reports rr
                WHERE rr.rfq_id = r.id AND rr.status::text = 'open'
            )
        """
    if view == "archived":
        return "r.status::text = 'archived'"
    return None


def proposal_view_sql(view: str) -> str | None:
    if view == "pending":
        return "p.status::text IN ('submitted', 'viewed', 'shortlisted')"
    if view == "accepted":
        return "p.status::text = 'accepted'"
    if view == "rejected":
        return "p.status::text IN ('rejected', 'withdrawn')"
    if view == "reported":
        return """
            EXISTS (
                SELECT 1 FROM proposal_reports pr
                WHERE pr.proposal_id = p.id AND pr.status::text = 'open'
            )
        """
    return None


def contract_view_sql(view: str) -> str | None:
    if view == "active":
        return "c.status::text IN ('pending_payment', 'active', 'delivered')"
    if view == "completed":
        return "c.status::text = 'completed'"
    if view == "cancelled":
        return "c.status::text = 'cancelled'"
    if view == "disputed":
        return "c.status::text = 'disputed'"
    return None


def dispute_view_sql(view: str) -> str | None:
    mapping = {
        "open": "d.status::text = 'open'",
        "under_review": "d.status::text = 'under_review'",
        "resolved": "d.status::text = 'resolved'",
        "appealed": "d.status::text = 'appealed'",
    }
    return mapping.get(view)


def finance_view_sql(view: str) -> str | None:
    mapping = {
        "platform_revenue": "platform_revenue",
        "subscriptions": "subscription",
        "commission": "commission",
        "refunds": "refund",
        "payouts": "payout",
    }
    payment_type = mapping.get(view)
    if payment_type is None:
        return None
    return f"pp.type::text = '{payment_type}'"


def escrow_summary(milestones: list, currency: str) -> dict:
    held_statuses = {"funded", "submitted", "in_progress", "awaiting_payment"}
    held = released = disputed = 0.0
    for milestone in milestones:
        status = milestone["status"] if isinstance(milestone, dict) else enum_value(milestone.status)
        amount = float(milestone["amount"] if isinstance(milestone, dict) else milestone.amount)
        if status == "released":
            released += amount
        elif status == "disputed":
            disputed += amount
        elif status in held_statuses:
            held += amount
    return {
        "held": held,
        "released": released,
        "disputed": disputed,
        "currency": currency,
    }


def apply_partial_refund(milestones: list, movable: set[str], buyer_amount: float) -> None:
    remaining_refund = buyer_amount
    for milestone in milestones:
        if milestone["status"] not in movable:
            continue
        if remaining_refund <= 1e-9:
            milestone["status"] = "released"
            continue
        if milestone["amount"] <= remaining_refund + 1e-9:
            milestone["status"] = "refunded"
            remaining_refund -= milestone["amount"]
        else:
            if remaining_refund >= milestone["amount"] / 2:
                milestone["status"] = "refunded"
            else:
                milestone["status"] = "released"
            remaining_refund = 0


def serialize_owner(row) -> dict:
    name = (row["name"] or "").strip() or row["display_name"]
    return {
        "actor_id": row["actor_id"],
        "actor_kind": row["actor_kind"],
        "display_name": row["display_name"],
        "company_id": row["company_id"],
        "company_title": row["company_title"],
        "user_id": row["user_id"],
        "email": row["email"],
        "name": name,
    }


def serialize_audit_entry(row) -> dict:
    actor = None
    if row["user_id"]:
        actor = {
            "id": row["user_id"],
            "email": row["email"],
            "name": f"{row['first_name']} {row['last_name']}".strip(),
        }
    return {
        "id": row["id"],
        "action": row["action"],
        "details": parse_json(row["details"]) or {},
        "created_at": row["created_at"],
        "actor": actor,
    }


async def load_owners(conn, actor_ids: list[int]) -> dict[int, dict]:
    ids = [actor_id for actor_id in actor_ids if actor_id]
    if not ids:
        return {}
    rows = await conn.fetch(
        """
        SELECT
            a.id AS actor_id,
            a.kind::text AS actor_kind,
            a.display_name,
            CASE WHEN a.kind::text = 'company' THEN a.company_id END AS company_id,
            CASE WHEN a.kind::text = 'company' THEN c.title END AS company_title,
            CASE
                WHEN a.kind::text = 'company' THEN ou.id
                ELSE iu.id
            END AS user_id,
            CASE
                WHEN a.kind::text = 'company' THEN ou.email
                ELSE iu.email
            END AS email,
            TRIM(
                CASE
                    WHEN a.kind::text = 'company'
                    THEN COALESCE(ou.first_name, '') || ' ' || COALESCE(ou.last_name, '')
                    ELSE COALESCE(iu.first_name, '') || ' ' || COALESCE(iu.last_name, '')
                END
            ) AS name
        FROM actors a
        LEFT JOIN companies c ON c.id = a.company_id
        LEFT JOIN users ou ON ou.id = c.owner_id
        LEFT JOIN users iu ON iu.id = a.user_id
        WHERE a.id = ANY($1::int[])
        """,
        ids,
    )
    owners = {}
    for row in rows:
        owner = serialize_owner(row)
        if owner["actor_kind"] == "company" and owner["company_id"]:
            if not owner["name"]:
                owner["name"] = owner["display_name"]
        owners[row["actor_id"]] = owner
    return owners


async def resolve_owner_user_id(conn, actor_id: int) -> int | None:
    owners = await load_owners(conn, [actor_id])
    owner = owners.get(actor_id)
    return owner.get("user_id") if owner else None


async def load_audit_history(conn, resource_type: str, resource_id: str, limit: int = 50) -> list[dict]:
    rows = await conn.fetch(
        """
        SELECT
            a.id, a.action, a.details, a.created_at,
            u.id AS user_id, u.email, u.first_name, u.last_name
        FROM audit_logs a
        LEFT JOIN users u ON u.id = a.user_id
        WHERE a.resource_type = $1 AND a.resource_id = $2
        ORDER BY a.created_at DESC, a.id DESC
        LIMIT $3
        """,
        resource_type,
        str(resource_id),
        limit,
    )
    return [serialize_audit_entry(row) for row in rows]


async def log_admin(conn, user_id: int, action: str, resource_type: str, resource_id, details: dict | None = None):
    await AuditLogger(conn).log_action(
        user_id=user_id,
        actor_id=None,
        company_id=None,
        action=action,
        resource_type=resource_type,
        resource_id=str(resource_id),
        details=details,
    )


async def notify_user(
    conn,
    user_id: int,
    title: str,
    body: str,
    href: str | None = None,
    company_id: int | None = None,
):
    await NotificationService(conn).notify_user(
        user_id,
        "system",
        title,
        body,
        href=href,
        company_id=company_id,
    )
