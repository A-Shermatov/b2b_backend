from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from app.core.exceptions import ValidationError
from app.modules.finance.services.common import record_platform_payment
from app.modules.settings.service import SETTINGS_CURRENCY, PlatformSettings


@dataclass(frozen=True)
class CommissionQuote:
    amount: float
    percent: float
    minimum: float


def _money(value: float) -> float:
    return round(float(value), 2)


def is_settings_currency(currency) -> bool:
    code = getattr(currency, "value", currency)
    return str(code or "").upper() == SETTINGS_CURRENCY


def calculate_commission(amount: float, currency, settings: PlatformSettings) -> CommissionQuote:
    percent = max(0.0, float(settings.commission_percent))
    minimum = float(settings.commission_min) if is_settings_currency(currency) else 0.0
    if amount <= 0:
        return CommissionQuote(amount=0.0, percent=percent, minimum=minimum)
    commission = max(minimum, amount * percent / 100)
    return CommissionQuote(
        amount=_money(min(commission, amount)),
        percent=percent,
        minimum=minimum,
    )


def ensure_contract_amount_allowed(amount: float, currency, settings: PlatformSettings) -> None:
    limit = settings.max_contract_amount
    if limit is None or not is_settings_currency(currency):
        return
    if float(amount) > limit + 1e-9:
        raise ValidationError(
            f"Сумма договора не может превышать {limit:,.2f} {SETTINGS_CURRENCY}".replace(",", " ")
        )


def split_commission(total: float, milestone_amounts: list[float]) -> list[float]:
    if not milestone_amounts:
        return []
    base = sum(milestone_amounts)
    if total <= 0 or base <= 0:
        return [0.0 for _ in milestone_amounts]
    parts = [_money(total * amount / base) for amount in milestone_amounts[:-1]]
    parts.append(max(0.0, _money(total - sum(parts))))
    return parts


def format_commission_terms(percent: float | None, minimum: float | None, currency: str) -> str:
    percent_label = f"{float(percent or 0):g}%"
    if minimum and minimum > 0:
        return f"{percent_label}, минимум {float(minimum):g} {currency}"
    return percent_label


async def record_milestone_release(conn, milestone_id: int) -> None:
    row = await conn.fetchrow(
        """
        SELECT m.id, m.contract_id, m.title, m.amount, m.commission_amount,
               c.supplier_actor_id, c.currency::text AS currency,
               c.commission_percent, c.commission_min
        FROM payment_milestones m
        JOIN contracts c ON c.id = m.contract_id
        WHERE m.id = $1
        """,
        milestone_id,
    )
    if not row or not row["supplier_actor_id"]:
        return
    number = f"INV-{row['contract_id']}-{row['id']}"
    already_recorded = await conn.fetchval(
        "SELECT 1 FROM supplier_invoices WHERE number = $1 LIMIT 1",
        number,
    )
    if already_recorded:
        return

    paid_at = datetime.now(timezone.utc)
    amount = float(row["amount"])
    commission = _money(row["commission_amount"] or 0)
    invoice_id = await conn.fetchval(
        """
        INSERT INTO supplier_invoices (
            actor_id, contract_id, number, title, amount, currency,
            status, issued_at, paid_at
        )
        VALUES ($1, $2, $3, $4, $5, $6, 'paid'::invoice_status, NOW(), $7)
        RETURNING id
        """,
        row["supplier_actor_id"],
        row["contract_id"],
        number,
        row["title"],
        amount,
        row["currency"],
        paid_at,
    )
    await record_platform_payment(
        conn,
        payment_type="platform_revenue",
        amount=amount,
        commission=commission,
        currency=row["currency"],
        title=row["title"],
        description=f"Счёт {number}",
        status="paid",
        gateway="mock",
        actor_id=row["supplier_actor_id"],
        invoice_id=invoice_id,
        contract_id=row["contract_id"],
        paid_at=paid_at,
        metadata={"milestone_id": row["id"]},
    )
    if commission > 0:
        terms = format_commission_terms(row["commission_percent"], row["commission_min"], row["currency"])
        await record_platform_payment(
            conn,
            payment_type="commission",
            amount=commission,
            commission=0,
            currency=row["currency"],
            title=f"Комиссия · {number}",
            description=f"Комиссия платформы ({terms}) по счёту {number}",
            status="paid",
            gateway="mock",
            actor_id=row["supplier_actor_id"],
            invoice_id=invoice_id,
            contract_id=row["contract_id"],
            paid_at=paid_at,
            metadata={"milestone_id": row["id"]},
        )


async def record_milestone_releases(conn, milestone_ids: Iterable[int]) -> None:
    for milestone_id in milestone_ids:
        await record_milestone_release(conn, milestone_id)
