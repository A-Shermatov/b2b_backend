from datetime import datetime, timezone

from app.core.exceptions import NotFoundError
from app.modules.admin.services.common import ESCROW_LOCKED_STATUSES, parse_json
from app.modules.verification.common import (
    INDIVIDUAL_STATUS_LABELS,
    load_documents,
    load_individual_sides,
    load_verification_state,
)

RISK_LABELS = {"low": "Низкий", "medium": "Средний", "high": "Высокий"}


def _risk(signals: dict) -> dict:
    reasons: list[dict] = []

    def add(code: str, weight: int, label: str) -> None:
        reasons.append({"code": code, "weight": weight, "label": label})

    if signals["is_blocked"]:
        add("blocked", 5, "Аккаунт сейчас заблокирован")
    if signals["blocks_count"]:
        add("block_history", 2, f"Блокировок в истории: {signals['blocks_count']}")
    lost = signals["disputes_lost"]
    if lost:
        add("disputes_lost", 3 if lost >= 2 else 2, f"Проиграно споров: {lost}")
    against = signals["disputes_against"]
    if against >= 3:
        add("disputes_against", 2, f"Споров открыто против клиента: {against}")
    elif against:
        add("disputes_against", 1, f"Споров открыто против клиента: {against}")
    cancelled, total = signals["contracts_cancelled"], signals["contracts_total"]
    if cancelled >= 3 or (total >= 3 and cancelled / total >= 0.3):
        add("contracts_cancelled", 2, f"Отменённых сделок: {cancelled} из {total}")
    elif cancelled:
        add("contracts_cancelled", 1, f"Отменённых сделок: {cancelled}")
    complaints = signals["complaints_total"]
    if complaints >= 3:
        add("complaints", 2, f"Жалоб на объявления: {complaints}")
    elif signals["complaints_open"]:
        add("complaints", 1, f"Открытых жалоб: {signals['complaints_open']}")
    failed = signals["failed_logins_30d"]
    if failed >= 10:
        add("failed_logins", 2, f"Неудачных входов за 30 дней: {failed}")
    elif failed >= 5:
        add("failed_logins", 1, f"Неудачных входов за 30 дней: {failed}")
    if signals["account_age_days"] < 14:
        add("new_account", 1, f"Новый аккаунт ({signals['account_age_days']} дн.)")
    if signals["identity_status"] == "rejected":
        add("identity_rejected", 2, "Проверка личности отклонена")
    elif not signals["has_verification"]:
        add("unverified", 1, "Нет подтверждённой личности или компании")
    for side, label in (("supplier", "исполнитель"), ("buyer", "заказчик")):
        stats = signals["reviews"][side]
        if stats["count"] >= 3 and stats["avg"] is not None and stats["avg"] < 3:
            add(f"low_rating_{side}", 2, f"Низкий рейтинг как {label}: {stats['avg']:.1f}")

    score = sum(item["weight"] for item in reasons)
    if signals["is_blocked"] or score >= 5:
        level = "high"
    elif score >= 2:
        level = "medium"
    else:
        level = "low"
    reasons.sort(key=lambda item: item["weight"], reverse=True)
    return {"level": level, "label": RISK_LABELS[level], "score": score, "reasons": reasons}


class AdminDossierMixin:
    async def get_user_dossier(self, user_id: int) -> dict:
        async with self.connection() as conn:
            user = await conn.fetchrow(
                """
                SELECT id, email, first_name, last_name, phone,
                       role::text AS role, status::text AS status,
                       banned_until, block_reason, created_at,
                       COALESCE(totp_enabled, FALSE) AS totp_enabled
                FROM users WHERE id = $1
                """,
                user_id,
            )
            if not user:
                raise NotFoundError("User not found")
            state = await load_verification_state(conn, user_id)
            documents = await load_documents(conn, user_id)
            sides = await load_individual_sides(conn, user_id)

            company_rows = await conn.fetch(
                """
                SELECT c.id, c.title, c.actor_type::text AS actor_type,
                       c.verification_status::text AS verification_status,
                       c.operational_status::text AS operational_status,
                       (c.owner_id = $1) AS is_owner,
                       COALESCE(cu.role::text, 'director') AS role,
                       COALESCE((
                           SELECT array_agg(DISTINCT a.side::text ORDER BY a.side::text)
                           FROM actors a
                           WHERE a.company_id = c.id AND a.kind::text = 'company' AND a.is_active
                       ), '{}'::text[]) AS sides
                FROM companies c
                LEFT JOIN company_users cu ON cu.company_id = c.id AND cu.user_id = $1
                WHERE c.owner_id = $1 OR cu.user_id IS NOT NULL
                ORDER BY (c.owner_id = $1) DESC, c.id
                """,
                user_id,
            )
            responsible_company_ids = [
                row["id"]
                for row in company_rows
                if row["is_owner"] or row["role"] == "director"
            ]
            actor_rows = await conn.fetch(
                """
                SELECT id, side::text AS side FROM actors
                WHERE (kind::text = 'individual' AND user_id = $1)
                   OR (kind::text = 'company' AND company_id = ANY($2::int[]))
                """,
                user_id,
                responsible_company_ids,
            )
            actor_ids = [row["id"] for row in actor_rows]

            contracts = await conn.fetchrow(
                """
                SELECT
                    COUNT(*) AS total,
                    COUNT(*) FILTER (WHERE status::text = 'cancelled') AS cancelled,
                    COUNT(*) FILTER (WHERE status::text = 'completed') AS completed,
                    COUNT(*) FILTER (
                        WHERE status::text IN ('pending_payment', 'active', 'delivered', 'disputed')
                    ) AS active,
                    COALESCE(SUM(agreed_amount) FILTER (
                        WHERE buyer_actor_id = ANY($1::int[]) AND status::text <> 'cancelled'
                    ), 0)::float AS buyer_turnover,
                    COALESCE(SUM(agreed_amount) FILTER (
                        WHERE supplier_actor_id = ANY($1::int[]) AND status::text <> 'cancelled'
                    ), 0)::float AS supplier_turnover,
                    COUNT(*) FILTER (WHERE buyer_actor_id = ANY($1::int[])) AS as_buyer,
                    COUNT(*) FILTER (WHERE supplier_actor_id = ANY($1::int[])) AS as_supplier
                FROM contracts
                WHERE buyer_actor_id = ANY($1::int[]) OR supplier_actor_id = ANY($1::int[])
                """,
                actor_ids,
            )
            disputes = await conn.fetchrow(
                """
                SELECT
                    COUNT(*) AS total,
                    COUNT(*) FILTER (WHERE d.opened_by_actor_id = ANY($1::int[])) AS opened_by,
                    COUNT(*) FILTER (
                        WHERE d.opened_by_actor_id IS NOT NULL
                          AND NOT (d.opened_by_actor_id = ANY($1::int[]))
                    ) AS opened_against,
                    COUNT(*) FILTER (WHERE d.status::text <> 'resolved') AS active,
                    COUNT(*) FILTER (
                        WHERE d.status::text = 'resolved' AND (
                            (c.buyer_actor_id = ANY($1::int[])
                                AND d.resolution::text = 'release_funds')
                            OR (c.supplier_actor_id = ANY($1::int[])
                                AND d.resolution::text = 'refund_buyer')
                        )
                    ) AS lost
                FROM disputes d
                JOIN contracts c ON c.id = d.contract_id
                WHERE c.buyer_actor_id = ANY($1::int[]) OR c.supplier_actor_id = ANY($1::int[])
                """,
                actor_ids,
            )
            complaints = await conn.fetchrow(
                """
                SELECT
                    (SELECT COUNT(*) FROM catalog_item_reports r
                     JOIN catalog_items ci ON ci.id = r.item_id
                     WHERE ci.actor_id = ANY($1::int[])) AS catalog_total,
                    (SELECT COUNT(*) FROM catalog_item_reports r
                     JOIN catalog_items ci ON ci.id = r.item_id
                     WHERE ci.actor_id = ANY($1::int[]) AND r.status::text = 'open') AS catalog_open,
                    (SELECT COUNT(*) FROM rfq_reports r
                     JOIN rfqs q ON q.id = r.rfq_id
                     WHERE q.actor_id = ANY($1::int[])) AS rfq_total,
                    (SELECT COUNT(*) FROM rfq_reports r
                     JOIN rfqs q ON q.id = r.rfq_id
                     WHERE q.actor_id = ANY($1::int[]) AND r.status::text = 'open') AS rfq_open,
                    (SELECT COUNT(*) FROM proposal_reports r
                     JOIN proposals p ON p.id = r.proposal_id
                     WHERE p.supplier_actor_id = ANY($1::int[])) AS proposal_total,
                    (SELECT COUNT(*) FROM proposal_reports r
                     JOIN proposals p ON p.id = r.proposal_id
                     WHERE p.supplier_actor_id = ANY($1::int[])
                       AND r.status::text = 'open') AS proposal_open,
                    (
                        (SELECT COUNT(*) FROM catalog_item_reports WHERE reporter_user_id = $2)
                      + (SELECT COUNT(*) FROM rfq_reports WHERE reporter_user_id = $2)
                      + (SELECT COUNT(*) FROM proposal_reports WHERE reporter_user_id = $2)
                    ) AS filed
                """,
                actor_ids,
                user_id,
            )
            escrow = await conn.fetchrow(
                """
                SELECT
                    COALESCE(SUM(pm.amount) FILTER (
                        WHERE c.buyer_actor_id = ANY($1::int[])
                    ), 0)::float AS as_buyer,
                    COALESCE(SUM(pm.amount) FILTER (
                        WHERE c.supplier_actor_id = ANY($1::int[])
                    ), 0)::float AS as_supplier
                FROM payment_milestones pm
                JOIN contracts c ON c.id = pm.contract_id
                WHERE pm.status::text = ANY($2::text[])
                  AND (c.buyer_actor_id = ANY($1::int[]) OR c.supplier_actor_id = ANY($1::int[]))
                """,
                actor_ids,
                list(ESCROW_LOCKED_STATUSES),
            )
            withdrawals = await conn.fetchrow(
                """
                SELECT COUNT(*) AS pending_count, COALESCE(SUM(amount), 0)::float AS pending_amount
                FROM withdrawals
                WHERE actor_id = ANY($1::int[]) AND status::text IN ('pending', 'processing')
                """,
                actor_ids,
            )
            review_rows = await conn.fetch(
                """
                SELECT a.side::text AS side, COUNT(*) AS count, AVG(r.rating)::float AS avg
                FROM reviews r
                JOIN actors a ON a.id = r.target_actor_id
                WHERE r.target_actor_id = ANY($1::int[])
                GROUP BY a.side
                """,
                actor_ids,
            )
            block_rows = await conn.fetch(
                """
                SELECT a.id, a.action, a.details, a.created_at,
                       u.email AS admin_email,
                       TRIM(COALESCE(u.first_name, '') || ' ' || COALESCE(u.last_name, ''))
                           AS admin_name
                FROM audit_logs a
                LEFT JOIN users u ON u.id = a.user_id
                WHERE a.resource_type = 'user' AND a.resource_id = $1
                  AND a.action LIKE 'admin.user.status.%'
                ORDER BY a.created_at DESC, a.id DESC
                LIMIT 20
                """,
                str(user_id),
            )
            logins = await conn.fetchrow(
                """
                SELECT
                    (SELECT COUNT(*) FROM audit_logs
                     WHERE action = 'login_failed' AND resource_type = 'user'
                       AND resource_id = $1
                       AND created_at >= NOW() - INTERVAL '30 days') AS failed_30d,
                    (SELECT COUNT(DISTINCT ip_address) FROM audit_logs
                     WHERE action = 'login' AND user_id = $2 AND ip_address IS NOT NULL
                       AND created_at >= NOW() - INTERVAL '30 days') AS distinct_ips_30d
                """,
                str(user_id),
                user_id,
            )
            last_login = await conn.fetchrow(
                """
                SELECT created_at, ip_address FROM audit_logs
                WHERE action = 'login' AND user_id = $1
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """,
                user_id,
            )

        reviews = {"buyer": {"count": 0, "avg": None}, "supplier": {"count": 0, "avg": None}}
        for row in review_rows:
            reviews[row["side"]] = {
                "count": int(row["count"] or 0),
                "avg": round(float(row["avg"]), 2) if row["avg"] is not None else None,
            }
        identity_status = (state["status"] if state else None) or "none"
        created_at = user["created_at"]
        account_age_days = max(0, (datetime.now(timezone.utc) - created_at).days) if created_at else 0
        blocks = [
            {
                "id": row["id"],
                "action": row["action"],
                "status": row["action"].rsplit(".", 1)[-1],
                "reason": parse_json(row["details"]).get("reason"),
                "created_at": row["created_at"],
                "admin": row["admin_name"] or row["admin_email"],
            }
            for row in block_rows
        ]
        companies = [
            {
                "id": row["id"],
                "title": row["title"],
                "role": "director" if row["is_owner"] else row["role"],
                "is_owner": bool(row["is_owner"]),
                "actor_type": row["actor_type"],
                "sides": list(row["sides"] or []),
                "verification_status": row["verification_status"],
                "operational_status": row["operational_status"],
            }
            for row in company_rows
        ]
        complaints_total = int(
            (complaints["catalog_total"] or 0)
            + (complaints["rfq_total"] or 0)
            + (complaints["proposal_total"] or 0)
        )
        complaints_open = int(
            (complaints["catalog_open"] or 0)
            + (complaints["rfq_open"] or 0)
            + (complaints["proposal_open"] or 0)
        )
        has_verification = identity_status == "verified" or any(
            company["verification_status"] == "verified" for company in companies
        )
        risk_signals = {
            "disputes_total": int(disputes["total"] or 0),
            "disputes_opened_by": int(disputes["opened_by"] or 0),
            "disputes_against": int(disputes["opened_against"] or 0),
            "disputes_active": int(disputes["active"] or 0),
            "disputes_lost": int(disputes["lost"] or 0),
            "contracts_total": int(contracts["total"] or 0),
            "contracts_cancelled": int(contracts["cancelled"] or 0),
            "contracts_completed": int(contracts["completed"] or 0),
            "contracts_active": int(contracts["active"] or 0),
            "complaints_total": complaints_total,
            "complaints_open": complaints_open,
            "complaints_catalog": int(complaints["catalog_total"] or 0),
            "complaints_rfq": int(complaints["rfq_total"] or 0),
            "complaints_proposal": int(complaints["proposal_total"] or 0),
            "complaints_filed": int(complaints["filed"] or 0),
            "blocks_count": sum(1 for block in blocks if block["status"] == "blocked"),
            "failed_logins_30d": int(logins["failed_30d"] or 0),
            "distinct_ips_30d": int(logins["distinct_ips_30d"] or 0),
            "account_age_days": account_age_days,
        }
        risk = _risk(
            {
                **risk_signals,
                "is_blocked": user["status"] == "blocked",
                "identity_status": identity_status,
                "has_verification": has_verification,
                "reviews": reviews,
            }
        )
        return {
            "user": {
                "id": user["id"],
                "email": user["email"],
                "name": f"{user['first_name']} {user['last_name']}".strip(),
                "phone": user["phone"],
                "role": user["role"],
                "status": user["status"],
                "banned_until": user["banned_until"],
                "block_reason": user["block_reason"],
                "created_at": created_at,
                "account_age_days": account_age_days,
                "totp_enabled": bool(user["totp_enabled"]),
                "last_login_at": last_login["created_at"] if last_login else None,
                "last_login_ip": last_login["ip_address"] if last_login else None,
            },
            "identity": {
                "status": identity_status,
                "status_label": INDIVIDUAL_STATUS_LABELS.get(identity_status, identity_status),
                "reason": state["reason"] if state else None,
                "submitted_at": state["submitted_at"] if state else None,
                "verified_at": state["verified_at"] if state else None,
                "sides": sides,
                "documents": documents,
            },
            "companies": companies,
            "signals": risk_signals,
            "money": {
                "currency": "TJS",
                "buyer_turnover": float(contracts["buyer_turnover"] or 0),
                "supplier_turnover": float(contracts["supplier_turnover"] or 0),
                "contracts_as_buyer": int(contracts["as_buyer"] or 0),
                "contracts_as_supplier": int(contracts["as_supplier"] or 0),
                "escrow_held_as_buyer": float(escrow["as_buyer"] or 0),
                "escrow_held_as_supplier": float(escrow["as_supplier"] or 0),
                "pending_withdrawals_count": int(withdrawals["pending_count"] or 0),
                "pending_withdrawals_amount": float(withdrawals["pending_amount"] or 0),
            },
            "reviews": reviews,
            "blocks": blocks,
            "risk": risk,
        }
