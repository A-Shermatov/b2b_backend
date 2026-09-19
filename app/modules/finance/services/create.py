from datetime import datetime, timezone

from app.core.exceptions import ConflictError, NotFoundError
from app.modules.finance.schemas import WithdrawalCreate, WithdrawalDestinationCreate
from app.modules.finance.services.common import (
    destination_schema,
    invoice_schema,
    record_platform_payment,
    withdrawal_schema,
)
from app.modules.payments.services import PaymentService


class FinanceCreateMixin:
    async def create_destination(self, actor_id: int, data: WithdrawalDestinationCreate):
        async with self.connection() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO withdrawal_destinations (
                    actor_id, type, label, details, is_default
                )
                VALUES ($1, $2::withdrawal_destination_type, $3, $4, $5)
                RETURNING id, actor_id, type::text AS type, label, details, is_default
                """,
                actor_id,
                data.type,
                data.label,
                data.details,
                data.is_default,
            )
        return destination_schema(row)

    async def request_withdrawal(self, actor_id: int, data: WithdrawalCreate):
        balance = await PaymentService().supplier_balance(actor_id)
        async with self.connection() as conn:
            pending_amount = await conn.fetchval(
                """
                SELECT COALESCE(SUM(amount), 0)
                FROM withdrawals
                WHERE actor_id = $1 AND status::text = ANY($2::text[])
                """,
                actor_id,
                ["pending", "processing"],
            )
            available = balance.available - float(pending_amount or 0)
            if data.amount > available:
                raise ConflictError("Insufficient available balance")
            dest = await conn.fetchrow(
                """
                SELECT id FROM withdrawal_destinations
                WHERE id = $1 AND actor_id = $2
                """,
                data.destination_id,
                actor_id,
            )
            if not dest:
                raise NotFoundError("Destination not found")
            row = await conn.fetchrow(
                """
                INSERT INTO withdrawals (
                    actor_id, destination_id, amount, currency, status, created_at
                )
                VALUES ($1, $2, $3, $4, 'pending'::withdrawal_status, NOW())
                RETURNING id, actor_id, destination_id, amount, currency,
                          status::text AS status, created_at, completed_at
                """,
                actor_id,
                data.destination_id,
                data.amount,
                balance.currency,
            )
            await record_platform_payment(
                conn,
                payment_type="payout",
                amount=row["amount"],
                currency=row["currency"],
                title=f"Выплата #{row['id']}",
                description=f"Запрос вывода на destination #{row['destination_id']}",
                status="pending",
                actor_id=actor_id,
                withdrawal_id=row["id"],
            )
        return withdrawal_schema(row)

    async def create_invoice_for_milestone(
        self,
        actor_id: int,
        contract_id: int,
        milestone_id: int,
        title: str,
        amount: float,
        currency: str,
    ):
        paid_at = datetime.now(timezone.utc)
        async with self.connection() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO supplier_invoices (
                    actor_id, contract_id, number, title, amount, currency,
                    status, issued_at, paid_at
                )
                VALUES (
                    $1, $2, $3, $4, $5, $6,
                    'paid'::invoice_status, NOW(), $7
                )
                RETURNING id, actor_id, contract_id, number, title, amount, currency,
                          status::text AS status, issued_at, due_at, paid_at
                """,
                actor_id,
                contract_id,
                f"INV-{contract_id}-{milestone_id}",
                title,
                amount,
                currency,
                paid_at,
            )
            commission = round(amount * 0.05, 2)
            await record_platform_payment(
                conn,
                payment_type="platform_revenue",
                amount=amount,
                commission=commission,
                currency=currency,
                title=title,
                description=f"Счёт {row['number']}",
                status="paid",
                gateway="mock",
                actor_id=actor_id,
                invoice_id=row["id"],
                contract_id=contract_id,
                paid_at=paid_at,
                metadata={"milestone_id": milestone_id},
            )
            if commission > 0:
                await record_platform_payment(
                    conn,
                    payment_type="commission",
                    amount=commission,
                    commission=0,
                    currency=currency,
                    title=f"Комиссия · {row['number']}",
                    description=f"Комиссия платформы 5% по счёту {row['number']}",
                    status="paid",
                    gateway="mock",
                    actor_id=actor_id,
                    invoice_id=row["id"],
                    contract_id=contract_id,
                    paid_at=paid_at,
                )
        return invoice_schema(row)
