import json
from datetime import datetime, timezone

from app.modules.finance.schemas import (
    InvoiceSchema,
    WithdrawalDestinationSchema,
    WithdrawalSchema,
)

SUBSCRIPTION_PLAN_AMOUNTS: dict[str, float] = {
    "start": 1990.0,
    "pro": 4990.0,
    "business": 9990.0,
}


def enum_value(value) -> str:
    return value.value if hasattr(value, "value") else str(value)


def destination_schema(row) -> WithdrawalDestinationSchema:
    return WithdrawalDestinationSchema(
        id=row["id"],
        actor_id=row["actor_id"],
        type=row["type"],
        label=row["label"],
        details=row["details"],
        is_default=row["is_default"],
    )


def withdrawal_schema(row) -> WithdrawalSchema:
    bank = None
    try:
        bank = row["bank"]
    except (KeyError, IndexError):
        bank = None
    return WithdrawalSchema(
        id=row["id"],
        actor_id=row["actor_id"],
        destination_id=row["destination_id"],
        amount=row["amount"],
        currency=row["currency"],
        status=row["status"],
        bank=bank,
        created_at=row["created_at"],
        completed_at=row["completed_at"],
    )


def invoice_schema(row) -> InvoiceSchema:
    return InvoiceSchema(
        id=row["id"],
        actor_id=row["actor_id"],
        contract_id=row["contract_id"],
        number=row["number"],
        title=row["title"],
        amount=row["amount"],
        currency=row["currency"],
        status=row["status"],
        issued_at=row["issued_at"],
        due_at=row["due_at"],
        paid_at=row["paid_at"],
    )


async def record_platform_payment(
    conn,
    *,
    payment_type,
    amount: float,
    title: str,
    status="pending",
    gateway="manual",
    currency: str = "TJS",
    commission: float = 0.0,
    description: str | None = None,
    actor_id: int | None = None,
    invoice_id: int | None = None,
    withdrawal_id: int | None = None,
    contract_id: int | None = None,
    subscription_user_id: int | None = None,
    external_id: str | None = None,
    metadata: dict | None = None,
    paid_at: datetime | None = None,
):
    status_value = enum_value(status)
    now = datetime.now(timezone.utc)
    row = await conn.fetchrow(
        """
        INSERT INTO platform_payments (
            external_id, type, status, gateway, amount, commission, currency,
            title, description, actor_id, invoice_id, withdrawal_id, contract_id,
            subscription_user_id, metadata, paid_at, failed_at, refunded_at,
            created_at, updated_at
        )
        VALUES (
            $1, $2::platform_payment_type, $3::platform_payment_status,
            $4::platform_payment_gateway, $5, $6, $7,
            $8, $9, $10, $11, $12, $13,
            $14, $15::jsonb, $16, $17, $18, NOW(), NOW()
        )
        RETURNING id
        """,
        external_id,
        enum_value(payment_type),
        status_value,
        enum_value(gateway),
        amount,
        commission,
        currency,
        title,
        description,
        actor_id,
        invoice_id,
        withdrawal_id,
        contract_id,
        subscription_user_id,
        json.dumps(metadata) if metadata is not None else None,
        paid_at or (now if status_value == "paid" else None),
        now if status_value == "failed" else None,
        now if status_value == "refunded" else None,
    )
    return row
