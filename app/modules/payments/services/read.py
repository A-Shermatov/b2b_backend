from app.core.exceptions import ForbiddenError, NotFoundError
from app.modules.contracts.schemas import PaymentPlanSchema
from app.modules.payments.schemas import SupplierBalanceSchema
from app.modules.payments.services.common import history_item, pending_item


class PaymentReadMixin:
    async def get_milestones(self, contract_id: int, actor_id: int):
        async with self.connection() as conn:
            contract = await conn.fetchrow(
                """
                SELECT buyer_actor_id, supplier_actor_id
                FROM contracts WHERE id = $1
                """,
                contract_id,
            )
            if not contract:
                raise NotFoundError("Contract not found")
            if contract["buyer_actor_id"] != actor_id and contract["supplier_actor_id"] != actor_id:
                raise ForbiddenError("Access denied")
            plan = await conn.fetchrow(
                """
                SELECT id, contract_id, payment_type::text AS payment_type
                FROM payment_plans WHERE contract_id = $1
                """,
                contract_id,
            )
            if not plan:
                return None
            milestones = await conn.fetch(
                """
                SELECT id, contract_id, title, percentage, amount, trigger,
                       status::text AS status
                FROM payment_milestones
                WHERE payment_plan_id = $1
                ORDER BY id
                """,
                plan["id"],
            )
        return PaymentPlanSchema.model_validate(
            {
                "id": plan["id"],
                "contract_id": plan["contract_id"],
                "payment_type": plan["payment_type"],
                "milestones": [dict(row) for row in milestones],
            }
        )

    async def payment_history(self, buyer_id: int):
        async with self.connection() as conn:
            rows = await conn.fetch(
                """
                SELECT
                    m.id, m.contract_id, m.title, m.amount, m.status::text AS status,
                    c.currency::text AS currency, c.created_at
                FROM payment_milestones m
                JOIN contracts c ON c.id = m.contract_id
                WHERE c.buyer_actor_id = $1
                  AND m.status::text = ANY($2::text[])
                ORDER BY c.created_at DESC, m.id
                """,
                buyer_id,
                ["funded", "released", "refunded"],
            )
        return [
            history_item(
                row,
                event="funded" if row["status"] == "funded" else row["status"],
            )
            for row in rows
        ]

    async def pending_payments(self, buyer_id: int):
        async with self.connection() as conn:
            rows = await conn.fetch(
                """
                SELECT
                    m.id, m.contract_id, m.title, m.amount, m.status::text AS status,
                    c.currency::text AS currency
                FROM payment_milestones m
                JOIN contracts c ON c.id = m.contract_id
                WHERE c.buyer_actor_id = $1
                  AND m.status::text = ANY($2::text[])
                ORDER BY m.id
                """,
                buyer_id,
                ["pending", "awaiting_payment"],
            )
        return [pending_item(row) for row in rows]

    async def supplier_payment_history(self, supplier_actor_id: int):
        async with self.connection() as conn:
            rows = await conn.fetch(
                """
                SELECT
                    m.id, m.contract_id, m.title, m.amount, m.status::text AS status,
                    c.currency::text AS currency, c.created_at
                FROM payment_milestones m
                JOIN contracts c ON c.id = m.contract_id
                WHERE c.supplier_actor_id = $1
                  AND m.status::text = ANY($2::text[])
                ORDER BY c.created_at DESC, m.id
                """,
                supplier_actor_id,
                ["funded", "released", "refunded"],
            )
        return [history_item(row, event=row["status"]) for row in rows]

    async def supplier_pending_payouts(self, supplier_actor_id: int):
        async with self.connection() as conn:
            rows = await conn.fetch(
                """
                SELECT
                    m.id, m.contract_id, m.title, m.amount, m.status::text AS status,
                    c.currency::text AS currency
                FROM payment_milestones m
                JOIN contracts c ON c.id = m.contract_id
                WHERE c.supplier_actor_id = $1
                  AND m.status::text = ANY($2::text[])
                ORDER BY m.id
                """,
                supplier_actor_id,
                ["funded", "released"],
            )
        return [pending_item(row) for row in rows]

    async def supplier_balance(self, supplier_actor_id: int) -> SupplierBalanceSchema:
        async with self.connection() as conn:
            rows = await conn.fetch(
                """
                SELECT m.amount - COALESCE(m.commission_amount, 0) AS amount,
                       m.status::text AS status, c.currency::text AS currency
                FROM payment_milestones m
                JOIN contracts c ON c.id = m.contract_id
                WHERE c.supplier_actor_id = $1
                """,
                supplier_actor_id,
            )
            withdrawn = await conn.fetchval(
                """
                SELECT COALESCE(SUM(amount), 0)
                FROM withdrawals
                WHERE actor_id = $1
                  AND status::text = ANY($2::text[])
                """,
                supplier_actor_id,
                ["pending", "processing", "completed"],
            )
        available = 0.0
        pending = 0.0
        escrow_locked = 0.0
        currency = "TJS"
        for row in rows:
            currency = row["currency"]
            if row["status"] == "released":
                available += float(row["amount"])
            elif row["status"] == "funded":
                pending += float(row["amount"])
                escrow_locked += float(row["amount"])
        available = max(0.0, available - float(withdrawn or 0))
        return SupplierBalanceSchema(
            available=available,
            pending=pending,
            escrow_locked=escrow_locked,
            currency=currency,
        )
