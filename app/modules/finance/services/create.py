from app.core.exceptions import ConflictError, NotFoundError
from app.modules.finance.schemas import WithdrawalCreate, WithdrawalDestinationCreate
from app.modules.finance.schemas.common import (
    AVAILABLE_WITHDRAWAL_BANKS,
    WITHDRAWAL_BANK_LABELS,
)
from app.modules.finance.services.common import (
    destination_schema,
    record_platform_payment,
    withdrawal_schema,
)
from app.modules.payments.services import PaymentService
from app.utils.audit import log_event


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
        bank = data.bank
        bank_label = WITHDRAWAL_BANK_LABELS.get(bank, bank)
        if bank not in AVAILABLE_WITHDRAWAL_BANKS:
            raise ConflictError(f"Вывод через {bank_label} пока недоступен")

        async with self.connection() as conn:
            async with conn.transaction():
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtext($1))",
                    f"withdraw:{actor_id}",
                )
                balance = await PaymentService(conn).supplier_balance(actor_id)
                if data.amount > balance.available:
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
                        actor_id, destination_id, amount, currency, status, bank, created_at
                    )
                    VALUES ($1, $2, $3, $4, 'pending'::withdrawal_status, $5, NOW())
                    RETURNING id, actor_id, destination_id, amount, currency,
                              status::text AS status, bank, created_at, completed_at
                    """,
                    actor_id,
                    data.destination_id,
                    data.amount,
                    balance.currency,
                    bank,
                )
                gateway = "alif" if bank == "alif" else "manual"
                await record_platform_payment(
                    conn,
                    payment_type="payout",
                    amount=row["amount"],
                    currency=row["currency"],
                    title=f"Выплата #{row['id']} через {bank_label}",
                    description=(
                        f"Запрос вывода на destination #{row['destination_id']} "
                        f"через {bank_label}"
                    ),
                    status="pending",
                    gateway=gateway,
                    actor_id=actor_id,
                    withdrawal_id=row["id"],
                    metadata={"bank": bank, "bank_label": bank_label},
                )
                await log_event(
                    conn,
                    "withdrawal.request",
                    "withdrawal",
                    row["id"],
                    actor_id=actor_id,
                    details={
                        "amount": row["amount"],
                        "currency": row["currency"],
                        "destination_id": row["destination_id"],
                        "bank": bank,
                        "available_before": balance.available,
                    },
                )
        return withdrawal_schema(row)
