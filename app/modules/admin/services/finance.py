import csv
import io

from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.modules.admin.services.common import (
    actor_id_of,
    finance_view_sql,
    load_audit_history,
    load_owners,
    log_admin,
    normalize_query,
    notify_user,
    parse_json,
    resolve_owner_user_id,
    with_reason,
)


def _finance_search_sql(normalized: str, params: list) -> str:
    if not normalized:
        return ""
    params.append(f"%{normalized}%")
    idx = len(params)
    sql = f"""
        (
            pp.title ILIKE ${idx}
            OR pp.description ILIKE ${idx}
            OR pp.external_id ILIKE ${idx}
            OR pp.id::text ILIKE ${idx}
    """
    if normalized.isdigit():
        params.append(int(normalized))
        sql += f" OR pp.id = ${len(params)}"
    return sql + ")"


def _serialize_finance_list_item(payment, actor) -> dict:
    return {
        "id": payment["id"],
        "type": payment["type"],
        "status": payment["status"],
        "gateway": payment["gateway"],
        "amount": payment["amount"],
        "commission": payment["commission"],
        "currency": payment["currency"],
        "title": payment["title"],
        "invoice_id": payment["invoice_id"],
        "withdrawal_id": payment["withdrawal_id"],
        "contract_id": payment["contract_id"],
        "actor": actor,
        "created_at": payment["created_at"],
        "updated_at": payment["updated_at"],
    }


def _finance_action_notification(action: str, payment_title: str, reason: str | None):
    messages = {
        "mark_paid": (
            "Платёж отмечен оплаченным",
            f"Платёж «{payment_title}» отмечен как оплаченный.",
        ),
        "retry": (
            "Повтор платежа",
            f"Повторная обработка платежа «{payment_title}» выполнена.",
        ),
        "refund": (
            "Платёж возвращён",
            f"По платежу «{payment_title}» выполнен возврат.",
        ),
    }
    title, body = messages[action]
    return title, with_reason(body, reason)


FINANCE_LIST_SQL = """
    SELECT
        pp.id, pp.type::text AS type, pp.status::text AS status,
        pp.gateway::text AS gateway, pp.amount, pp.commission, pp.currency,
        pp.title, pp.invoice_id, pp.withdrawal_id, pp.contract_id, pp.actor_id,
        pp.created_at, pp.updated_at
    FROM platform_payments pp
"""


class AdminFinanceMixin:
    async def list_finance(
        self,
        page: int = 1,
        page_size: int = 20,
        view: str = "platform_revenue",
        query: str | None = None,
    ) -> dict:
        normalized = normalize_query(query)
        search_params: list = []
        search_sql = _finance_search_sql(normalized, search_params)

        async def _count(conn, extra_sql: str | None):
            clauses = [search_sql] if search_sql else []
            if extra_sql:
                clauses.append(extra_sql)
            where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
            return int(
                await conn.fetchval(
                    f"SELECT COUNT(*) FROM platform_payments pp {where}",
                    *search_params,
                )
            )

        async with self.connection() as conn:
            view_sql = finance_view_sql(view)
            total = await _count(conn, view_sql)
            count_values = {}
            for count_view in (
                "platform_revenue",
                "subscriptions",
                "commission",
                "refunds",
                "payouts",
            ):
                count_values[count_view] = await _count(conn, finance_view_sql(count_view))
            params = list(search_params)
            clauses = [search_sql] if search_sql else []
            if view_sql:
                clauses.append(view_sql)
            where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
            params.extend([page_size, (page - 1) * page_size])
            items = await conn.fetch(
                f"""
                {FINANCE_LIST_SQL}
                {where}
                ORDER BY pp.created_at DESC, pp.id DESC
                LIMIT ${len(params) - 1} OFFSET ${len(params)}
                """,
                *params,
            )
            parties = await load_owners(
                conn, [item["actor_id"] for item in items if item["actor_id"]]
            )

        return {
            "items": [
                _serialize_finance_list_item(item, parties.get(item["actor_id"]))
                for item in items
            ],
            "total": total,
            "page": page,
            "page_size": page_size,
            "pages": max(1, (total + page_size - 1) // page_size) if total else 1,
            "view_counts": count_values,
        }

    async def get_finance_detail(self, payment_id: int) -> dict:
        async with self.connection() as conn:
            payment = await conn.fetchrow(
                f"{FINANCE_LIST_SQL} WHERE pp.id = $1",
                payment_id,
            )
            if not payment:
                raise NotFoundError("Payment not found")
            extra = await conn.fetchrow(
                """
                SELECT
                    pp.description, pp.external_id, pp.paid_at, pp.failed_at,
                    pp.refunded_at, pp.metadata, pp.subscription_user_id,
                    si.id AS invoice_pk, si.number, si.title AS invoice_title,
                    si.status::text AS invoice_status, si.amount AS invoice_amount,
                    si.currency AS invoice_currency,
                    w.id AS withdrawal_pk, w.amount AS withdrawal_amount,
                    w.status::text AS withdrawal_status, w.currency AS withdrawal_currency,
                    c.id AS contract_pk, c.title AS contract_title,
                    c.status::text AS contract_status
                FROM platform_payments pp
                LEFT JOIN supplier_invoices si ON si.id = pp.invoice_id
                LEFT JOIN withdrawals w ON w.id = pp.withdrawal_id
                LEFT JOIN contracts c ON c.id = pp.contract_id
                WHERE pp.id = $1
                """,
                payment_id,
            )
            parties = await load_owners(
                conn, [payment["actor_id"]] if payment["actor_id"] else []
            )
            history = await load_audit_history(conn, "platform_payment", str(payment_id))

        actor = parties.get(payment["actor_id"]) if payment["actor_id"] else None
        payload = _serialize_finance_list_item(payment, actor)
        payload.update(
            {
                "description": extra["description"],
                "external_id": extra["external_id"],
                "paid_at": extra["paid_at"],
                "failed_at": extra["failed_at"],
                "refunded_at": extra["refunded_at"],
                "metadata": parse_json(extra["metadata"]) or {},
                "actor": actor,
                "invoice": (
                    {
                        "id": extra["invoice_pk"],
                        "number": extra["number"],
                        "title": extra["invoice_title"],
                        "status": extra["invoice_status"],
                        "amount": extra["invoice_amount"],
                        "currency": extra["invoice_currency"],
                    }
                    if extra["invoice_pk"]
                    else None
                ),
                "withdrawal": (
                    {
                        "id": extra["withdrawal_pk"],
                        "amount": extra["withdrawal_amount"],
                        "status": extra["withdrawal_status"],
                        "currency": extra["withdrawal_currency"],
                    }
                    if extra["withdrawal_pk"]
                    else None
                ),
                "contract": (
                    {
                        "id": extra["contract_pk"],
                        "title": extra["contract_title"],
                        "status": extra["contract_status"],
                    }
                    if extra["contract_pk"]
                    else None
                ),
                "subscription_user_id": extra["subscription_user_id"],
                "history": history,
            }
        )
        return payload

    async def export_finance_csv(
        self,
        view: str = "platform_revenue",
        query: str | None = None,
        payment_id: int | None = None,
    ) -> str:
        if payment_id is not None:
            rows = [await self.get_finance_detail(payment_id)]
        else:
            data = await self.list_finance(page=1, page_size=5000, view=view, query=query)
            rows = data["items"]

        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(
            [
                "id",
                "type",
                "status",
                "gateway",
                "amount",
                "commission",
                "currency",
                "title",
                "invoice_id",
                "created_at",
            ]
        )
        for row in rows:
            writer.writerow(
                [
                    row.get("id"),
                    row.get("type"),
                    row.get("status"),
                    row.get("gateway"),
                    row.get("amount"),
                    row.get("commission"),
                    row.get("currency"),
                    row.get("title"),
                    row.get("invoice_id"),
                    row.get("created_at"),
                ]
            )
        return buffer.getvalue()

    async def apply_finance_action(
        self,
        payment_id: int,
        action: str,
        current_user,
        reason: str | None = None,
    ) -> dict:
        allowed = {"refund", "retry", "mark_paid"}
        if action not in allowed:
            raise ValidationError("Unsupported finance action")
        if action in {"refund", "retry"} and not (reason and reason.strip()):
            raise ValidationError("Reason is required for this action")

        async with self.connection() as conn:
            payment = await conn.fetchrow(
                """
                SELECT id, type::text AS type, status::text AS status, title,
                       actor_id, invoice_id, withdrawal_id
                FROM platform_payments WHERE id = $1
                """,
                payment_id,
            )
            if not payment:
                raise NotFoundError("Payment not found")
            previous_status = payment["status"]
            status = previous_status

            if action == "mark_paid":
                if payment["status"] not in {"pending", "processing"}:
                    raise ConflictError(
                        "Only pending/processing payments can be marked paid"
                    )
                status = "paid"
                await conn.execute(
                    """
                    UPDATE platform_payments
                    SET status = 'paid'::platform_payment_status, paid_at = NOW()
                    WHERE id = $1
                    """,
                    payment_id,
                )
                if payment["invoice_id"]:
                    await conn.execute(
                        """
                        UPDATE supplier_invoices
                        SET status = 'paid'::invoice_status, paid_at = NOW()
                        WHERE id = $1 AND status::text != 'paid'
                        """,
                        payment["invoice_id"],
                    )
                if payment["withdrawal_id"]:
                    await conn.execute(
                        """
                        UPDATE withdrawals
                        SET status = 'completed'::withdrawal_status, completed_at = NOW()
                        WHERE id = $1 AND status::text IN ('pending', 'processing', 'failed')
                        """,
                        payment["withdrawal_id"],
                    )
            elif action == "retry":
                if payment["status"] != "failed":
                    raise ConflictError("Only failed payments can be retried")
                await conn.execute(
                    """
                    UPDATE platform_payments
                    SET status = 'processing'::platform_payment_status, failed_at = NULL
                    WHERE id = $1
                    """,
                    payment_id,
                )
                status = "paid"
                await conn.execute(
                    """
                    UPDATE platform_payments
                    SET status = 'paid'::platform_payment_status, paid_at = NOW()
                    WHERE id = $1
                    """,
                    payment_id,
                )
                if payment["invoice_id"]:
                    await conn.execute(
                        """
                        UPDATE supplier_invoices
                        SET status = 'paid'::invoice_status, paid_at = NOW()
                        WHERE id = $1
                        """,
                        payment["invoice_id"],
                    )
                if payment["withdrawal_id"]:
                    await conn.execute(
                        """
                        UPDATE withdrawals
                        SET status = 'completed'::withdrawal_status, completed_at = NOW()
                        WHERE id = $1
                        """,
                        payment["withdrawal_id"],
                    )
            elif action == "refund":
                if payment["status"] != "paid":
                    raise ConflictError("Only paid payments can be refunded")
                if payment["type"] == "refund":
                    raise ConflictError("Refund payments cannot be refunded")
                status = "refunded"
                await conn.execute(
                    """
                    UPDATE platform_payments
                    SET status = 'refunded'::platform_payment_status, refunded_at = NOW()
                    WHERE id = $1
                    """,
                    payment_id,
                )
                if payment["invoice_id"]:
                    await conn.execute(
                        """
                        UPDATE supplier_invoices
                        SET status = 'cancelled'::invoice_status
                        WHERE id = $1
                        """,
                        payment["invoice_id"],
                    )
                if payment["withdrawal_id"]:
                    await conn.execute(
                        """
                        UPDATE withdrawals
                        SET status = 'cancelled'::withdrawal_status
                        WHERE id = $1 AND status::text != 'cancelled'
                        """,
                        payment["withdrawal_id"],
                    )

            if payment["actor_id"]:
                user_id = await resolve_owner_user_id(conn, payment["actor_id"])
                if user_id:
                    title, body = _finance_action_notification(action, payment["title"], reason)
                    await notify_user(conn, user_id, title, body, href="/supplier/finance")
            await log_admin(
                conn,
                actor_id_of(current_user),
                f"admin.finance.{action}",
                "platform_payment",
                payment_id,
                {
                    "reason": reason.strip() if reason else None,
                    "previous_status": previous_status,
                    "status": status,
                },
            )
        return {
            "id": payment_id,
            "action": action,
            "status": status,
            "type": payment["type"],
        }
