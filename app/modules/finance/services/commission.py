from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Iterable

from app.config import get_settings
from app.core.exceptions import ValidationError
from app.enums import Currency
from app.modules.finance.services.common import record_platform_payment
from app.modules.payments.gateways.alif import MIN_MARKETPLACE_PART, marketplace_commission
from app.modules.settings.service import SETTINGS_CURRENCY, PlatformSettings


@dataclass(frozen=True)
class CommissionQuote:
    amount: float
    percent: float
    minimum: float


_CENT = Decimal("0.01")


def _dec(value) -> Decimal:
    # str() first: Decimal(0.1) would carry the binary error of the float.
    return value if isinstance(value, Decimal) else Decimal(str(value or 0))


def _cents(value) -> Decimal:
    return _dec(value).quantize(_CENT, rounding=ROUND_HALF_UP)


def _money(value) -> float:
    """Round to cents, half up (round(2.675, 2) gives 2.67 on floats)."""
    return float(_cents(value))


def is_settings_currency(currency) -> bool:
    code = getattr(currency, "value", currency)
    return str(code or "").upper() == SETTINGS_CURRENCY


def calculate_commission(
    amount: float, currency, settings: PlatformSettings, *, bank_split: bool = False
) -> CommissionQuote:
    """Commission of a contract.

    bank_split: the payment is split by the bank into supplier + commission parts
    (Alif Marketplace), so each part must meet the bank minimum.
    """
    percent = max(0.0, float(settings.commission_percent))
    minimum = float(settings.commission_min) if is_settings_currency(currency) else 0.0
    if amount <= 0:
        return CommissionQuote(amount=0.0, percent=percent, minimum=minimum)
    total = _dec(amount)
    commission = min(max(_dec(minimum), total * _dec(percent) / 100), total)
    commission = _cents(commission)
    if bank_split and is_settings_currency(currency):
        commission = marketplace_commission(total, commission)
    return CommissionQuote(
        amount=_money(commission),
        percent=percent,
        minimum=minimum,
    )


def ensure_contract_amount_allowed(amount: float, currency, settings: PlatformSettings) -> None:
    code = str(getattr(currency, "value", currency) or "").upper()
    if code not in {item.value for item in Currency}:
        raise ValidationError("Неизвестная валюта")
    app_settings = get_settings()
    if app_settings.alif_enabled and code != app_settings.alif_currency.upper():
        # Alif accepts only its currency: a deal in another one could never be paid.
        raise ValidationError(
            f"Сейчас сделки оплачиваются только в {app_settings.alif_currency}"
        )
    limit = settings.max_contract_amount
    if limit is None or not is_settings_currency(currency):
        return
    if float(amount) > limit + 1e-9:
        raise ValidationError(
            f"Сумма договора не может превышать {limit:,.2f} {SETTINGS_CURRENCY}".replace(",", " ")
        )


def split_commission(
    total: float, milestone_amounts: list[float], *, bank_split: bool = False
) -> list[float]:
    if not milestone_amounts:
        return []
    amounts = [_dec(amount) for amount in milestone_amounts]
    base = sum(amounts, Decimal(0))
    total_dec = _cents(total)
    if total_dec <= 0 or base <= 0:
        return [0.0 for _ in milestone_amounts]
    cents = [_cents(total_dec * amount / base) for amount in amounts[:-1]]
    cents.append(max(Decimal(0), total_dec - sum(cents, Decimal(0))))
    parts = [float(part) for part in cents]
    if not bank_split:
        return parts
    return _merge_small_parts(parts)


def _merge_small_parts(parts: list[float]) -> list[float]:
    """Move commission parts below the bank minimum onto other milestones (total kept)."""
    minimum = MIN_MARKETPLACE_PART
    merged: list[Decimal] = []
    carry = Decimal(0)
    for raw in parts[:-1]:
        part = _cents(_dec(raw) + carry)
        carry = Decimal(0)
        if 0 < part < minimum:
            carry, part = part, Decimal(0)
        merged.append(part)
    last = _cents(_dec(parts[-1]) + carry)
    if 0 < last < minimum:
        previous = next((i for i in range(len(merged) - 1, -1, -1) if merged[i] > 0), None)
        if previous is not None:
            merged[previous] = merged[previous] + last
            last = Decimal(0)
    merged.append(last)
    return [float(part) for part in merged]


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
    paid_via_alif = await conn.fetchval(
        """
        SELECT 1 FROM payment_gateway_transactions
        WHERE milestone_id = $1 AND gateway = 'alif' AND status IN ('to_approve', 'ok')
        LIMIT 1
        """,
        row["id"],
    )
    gateway = "alif" if paid_via_alif else "mock"
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
        gateway=gateway,
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
            gateway=gateway,
            actor_id=row["supplier_actor_id"],
            invoice_id=invoice_id,
            contract_id=row["contract_id"],
            paid_at=paid_at,
            metadata={"milestone_id": row["id"]},
        )


async def record_milestone_releases(conn, milestone_ids: Iterable[int]) -> None:
    for milestone_id in milestone_ids:
        await record_milestone_release(conn, milestone_id)
