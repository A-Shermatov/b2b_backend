from datetime import datetime

from app.modules.finance.services.common import record_platform_payment


class FinanceLedgerMixin:
    async def record(
        self,
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
        async with self.connection() as conn:
            return await record_platform_payment(
                conn,
                payment_type=payment_type,
                amount=amount,
                title=title,
                status=status,
                gateway=gateway,
                currency=currency,
                commission=commission,
                description=description,
                actor_id=actor_id,
                invoice_id=invoice_id,
                withdrawal_id=withdrawal_id,
                contract_id=contract_id,
                subscription_user_id=subscription_user_id,
                external_id=external_id,
                metadata=metadata,
                paid_at=paid_at,
            )
