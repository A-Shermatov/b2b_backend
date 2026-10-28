import json
import logging
import secrets
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from urllib.parse import urlencode

from app.config import get_settings
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.modules.finance.services import SUBSCRIPTION_PLAN_AMOUNTS
from app.modules.payments.gateways.alif import AlifClient, AlifGatewayError, format_amount
from app.modules.payments.gateways.dc import DcGatewayError
from app.modules.payments.gateways.eskhata import EskhataGatewayError
from app.modules.payments.services.update import ALIF_NOT_CONFIGURED, GATEWAY_NOT_CONFIGURED
from app.modules.subscription.schemas import (
    SubscriptionCheckoutResponse,
    SubscriptionOrderSyncResponse,
    SubscriptionPaymentConfig,
    SubscriptionResponse,
)
from app.modules.subscription.services.common import shape_subscription

logger = logging.getLogger(__name__)

GATEWAY_ALIF = "alif"
GATEWAY_MOCK = "mock"
FINAL_STATUSES = ("ok", "failed", "canceled")
PAID_STATUSES = ("ok", "to_approve", "partially_approved")
PENDING_REUSE_MINUTES = 30
THIRTY_DAYS = timedelta(days=30)
VALID_PLANS = frozenset(SUBSCRIPTION_PLAN_AMOUNTS.keys())


class SubscriptionPaymentMixin:
    def payment_config(self) -> SubscriptionPaymentConfig:
        settings = get_settings()
        provider = "alif" if settings.alif_enabled else "mock"
        return SubscriptionPaymentConfig(
            provider=provider,
            currency=settings.alif_currency,
            methods=[
                {
                    "id": "alif",
                    "label": "Alif",
                    "supported": True,
                    "hint": "Оплата картой через защищённую страницу Alif",
                },
                {
                    "id": "dc",
                    "label": "DC",
                    "supported": False,
                    "hint": "Пока не поддерживается",
                },
                {
                    "id": "eskhata",
                    "label": "Eskhata",
                    "supported": False,
                    "hint": "Пока не поддерживается",
                },
            ],
        )

    async def checkout(
        self, user_id: int, plan: str, method: str = "alif"
    ) -> SubscriptionCheckoutResponse:
        if plan not in VALID_PLANS:
            raise ValidationError("Unknown subscription plan")
        method = (method or "alif").strip().lower()
        if method == "dc":
            raise DcGatewayError()
        if method == "eskhata":
            raise EskhataGatewayError()
        if method != "alif":
            raise ValidationError("Unsupported payment method")

        settings = get_settings()
        amount = float(SUBSCRIPTION_PLAN_AMOUNTS[plan])
        if amount <= 0:
            raise ValidationError("Plan has no price")

        if settings.alif_requested and not settings.alif_enabled:
            raise AlifGatewayError(ALIF_NOT_CONFIGURED)
        if not settings.alif_enabled:
            if not settings.allow_mock_payments:
                raise ConflictError(GATEWAY_NOT_CONFIGURED)
            sub = await self._activate_paid(
                user_id, plan, gateway=GATEWAY_MOCK, external_id=None, amount=amount
            )
            return SubscriptionCheckoutResponse(
                plan=sub.plan,
                active_until=sub.active_until,
                is_active=sub.is_active,
                payment_status="paid",
                gateway=GATEWAY_MOCK,
                message="Подписка активирована (демо-оплата)",
            )

        return await self._start_alif_checkout(user_id, plan, amount)

    async def _start_alif_checkout(
        self, user_id: int, plan: str, amount: float
    ) -> SubscriptionCheckoutResponse:
        settings = get_settings()
        if settings.alif_currency != "TJS":
            raise ConflictError(f"Оплата через Alif доступна только в {settings.alif_currency}")

        async with self.connection() as conn:
            existing = await conn.fetchrow(
                """
                SELECT order_id, payment_url, status, plan
                FROM subscription_payment_orders
                WHERE user_id = $1
                  AND plan = $2
                  AND gateway = $3
                  AND status = 'pending'
                  AND payment_url IS NOT NULL
                  AND created_at > NOW() - make_interval(mins => $4)
                ORDER BY created_at DESC
                LIMIT 1
                """,
                user_id,
                plan,
                GATEWAY_ALIF,
                PENDING_REUSE_MINUTES,
            )
            if existing:
                return SubscriptionCheckoutResponse(
                    plan=plan,
                    payment_status="pending",
                    order_id=existing["order_id"],
                    payment_url=existing["payment_url"],
                    gateway=GATEWAY_ALIF,
                )

            order_id = f"SUB{user_id}-{secrets.token_hex(6)}"
            amount_dec = Decimal(format_amount(amount))
            await conn.execute(
                """
                INSERT INTO subscription_payment_orders
                    (order_id, gateway, user_id, plan, amount, currency, status)
                VALUES ($1, $2, $3, $4, $5, $6, 'pending')
                """,
                order_id,
                GATEWAY_ALIF,
                user_id,
                plan,
                amount_dec,
                settings.alif_currency,
            )

        api_base = settings.public_api_url.rstrip("/")
        callback_url = f"{api_base}{settings.api_v1_prefix}/payments/alif/callback"
        query = urlencode({"order_id": order_id})
        return_url = f"{settings.frontend_url.rstrip('/')}/supplier/subscription/return?{query}"

        try:
            payment_url = await AlifClient(settings).create_payment(
                order_id=order_id,
                amount=amount_dec,
                callback_url=callback_url,
                return_url=return_url,
                info=f"Подписка Brend Market: тариф {plan}",
            )
        except AlifGatewayError:
            async with self.connection() as conn:
                await conn.execute(
                    """
                    UPDATE subscription_payment_orders
                    SET status = 'failed', updated_at = NOW()
                    WHERE order_id = $1
                    """,
                    order_id,
                )
            raise

        async with self.connection() as conn:
            await conn.execute(
                """
                UPDATE subscription_payment_orders
                SET payment_url = $2, updated_at = NOW()
                WHERE order_id = $1
                """,
                order_id,
                payment_url,
            )

        logger.info("Alif subscription payment created: order=%s user=%s plan=%s", order_id, user_id, plan)
        return SubscriptionCheckoutResponse(
            plan=plan,
            payment_status="pending",
            order_id=order_id,
            payment_url=payment_url,
            gateway=GATEWAY_ALIF,
        )

    async def apply_subscription_gateway_status(
        self,
        order_id: str,
        status: str,
        transaction_id,
        payload: dict,
        amount=None,
    ) -> dict:
        external_id = str(transaction_id) if transaction_id not in (None, "") else None
        raw = json.dumps(payload, ensure_ascii=False, default=str)
        normalized = status.strip().lower()

        async with self.connection() as conn:
            async with conn.transaction():
                tx = await conn.fetchrow(
                    """
                    SELECT id, order_id, user_id, plan, amount, status, currency
                    FROM subscription_payment_orders
                    WHERE order_id = $1
                    FOR UPDATE
                    """,
                    order_id,
                )
                if not tx:
                    raise NotFoundError("Subscription payment order not found")

                if tx["status"] == normalized or tx["status"] in FINAL_STATUSES:
                    return self._subscription_result(tx, tx["status"])

                if normalized in PAID_STATUSES:
                    if amount is not None and format_amount(amount) != format_amount(tx["amount"]):
                        logger.error(
                            "Alif subscription amount mismatch: order=%s expected=%s got=%s",
                            order_id,
                            tx["amount"],
                            amount,
                        )
                        await conn.execute(
                            """
                            UPDATE subscription_payment_orders
                            SET raw_callback = $2::jsonb, updated_at = NOW()
                            WHERE id = $1
                            """,
                            tx["id"],
                            raw,
                        )
                        return self._subscription_result(tx, tx["status"])

                    await conn.execute(
                        """
                        UPDATE subscription_payment_orders
                        SET status = 'ok',
                            external_transaction_id = COALESCE($2, external_transaction_id),
                            raw_callback = $3::jsonb,
                            paid_at = COALESCE(paid_at, NOW()),
                            updated_at = NOW()
                        WHERE id = $1
                        """,
                        tx["id"],
                        external_id,
                        raw,
                    )
                    await self._activate_paid_conn(
                        conn,
                        tx["user_id"],
                        tx["plan"],
                        gateway=GATEWAY_ALIF,
                        external_id=external_id or order_id,
                        amount=float(tx["amount"]),
                    )
                    tx = dict(tx)
                    tx["status"] = "ok"
                    return self._subscription_result(tx, "ok")

                if normalized in ("failed", "canceled", "partially_canceled"):
                    store = "canceled" if "cancel" in normalized else "failed"
                    await conn.execute(
                        """
                        UPDATE subscription_payment_orders
                        SET status = $2,
                            external_transaction_id = COALESCE($3, external_transaction_id),
                            raw_callback = $4::jsonb,
                            updated_at = NOW()
                        WHERE id = $1
                        """,
                        tx["id"],
                        store,
                        external_id,
                        raw,
                    )
                    return self._subscription_result(tx, store)

                await conn.execute(
                    """
                    UPDATE subscription_payment_orders
                    SET external_transaction_id = COALESCE($2, external_transaction_id),
                        raw_callback = $3::jsonb,
                        updated_at = NOW()
                    WHERE id = $1
                    """,
                    tx["id"],
                    external_id,
                    raw,
                )
                return self._subscription_result(tx, tx["status"])

    def _subscription_result(self, tx, payment_status: str) -> dict:
        return {
            "order_id": tx["order_id"],
            "payment_status": payment_status,
            "plan": tx.get("plan"),
            "user_id": tx.get("user_id"),
            "purpose": "subscription",
        }

    async def sync_subscription_order(self, order_id: str, user_id: int) -> SubscriptionOrderSyncResponse:
        async with self.connection() as conn:
            tx = await conn.fetchrow(
                """
                SELECT id, order_id, user_id, plan, amount, status
                FROM subscription_payment_orders
                WHERE order_id = $1 AND gateway = $2
                """,
                order_id,
                GATEWAY_ALIF,
            )
            if not tx or tx["user_id"] != user_id:
                raise NotFoundError("Payment order not found")
            if tx["status"] in FINAL_STATUSES or not get_settings().alif_enabled:
                sub = await self.get_subscription(user_id)
                return SubscriptionOrderSyncResponse(
                    order_id=tx["order_id"],
                    payment_status=tx["status"],
                    plan=sub.plan if sub.is_active else tx["plan"],
                    is_active=sub.is_active,
                    active_until=sub.active_until,
                )

        data = await AlifClient().check_transaction(order_id)
        if data is None:
            sub = await self.get_subscription(user_id)
            return SubscriptionOrderSyncResponse(
                order_id=order_id,
                payment_status=tx["status"],
                plan=tx["plan"],
                is_active=sub.is_active,
                active_until=sub.active_until,
            )
        await self.apply_subscription_gateway_status(
            order_id,
            str(data.get("status", "")),
            data.get("transactionId"),
            data,
            amount=data.get("amount"),
        )
        sub = await self.get_subscription(user_id)
        async with self.connection() as conn:
            status = await conn.fetchval(
                "SELECT status FROM subscription_payment_orders WHERE order_id = $1",
                order_id,
            )
        return SubscriptionOrderSyncResponse(
            order_id=order_id,
            payment_status=status or "pending",
            plan=sub.plan,
            is_active=sub.is_active,
            active_until=sub.active_until,
        )

    async def _activate_paid(
        self,
        user_id: int,
        plan: str,
        *,
        gateway: str,
        external_id: str | None,
        amount: float,
    ) -> SubscriptionResponse:
        async with self.connection() as conn:
            async with conn.transaction():
                return await self._activate_paid_conn(
                    conn, user_id, plan, gateway=gateway, external_id=external_id, amount=amount
                )

    async def _activate_paid_conn(
        self,
        conn,
        user_id: int,
        plan: str,
        *,
        gateway: str,
        external_id: str | None,
        amount: float,
    ) -> SubscriptionResponse:
        active_until = datetime.now(timezone.utc) + THIRTY_DAYS
        updated = await conn.fetchval(
            """
            UPDATE supplier_subscriptions
            SET plan = $1::subscription_plan, active_until = $2
            WHERE user_id = $3
            RETURNING id
            """,
            plan,
            active_until,
            user_id,
        )
        if not updated:
            await conn.execute(
                """
                INSERT INTO supplier_subscriptions (user_id, plan, active_until, created_at)
                VALUES ($1, $2::subscription_plan, $3, NOW())
                """,
                user_id,
                plan,
                active_until,
            )
        if amount > 0:
            from app.modules.finance.services.common import record_platform_payment

            await record_platform_payment(
                conn,
                payment_type="subscription",
                amount=amount,
                currency="TJS",
                title=f"Подписка {plan}",
                description=f"Активация тарифа {plan} на 30 дней",
                status="paid",
                gateway=gateway if gateway in ("mock", "alif", "manual") else "mock",
                subscription_user_id=user_id,
                external_id=external_id,
                metadata={"plan": plan},
            )
        row = await conn.fetchrow(
            """
            SELECT plan::text AS plan, active_until
            FROM supplier_subscriptions WHERE user_id = $1
            """,
            user_id,
        )
        return shape_subscription(row["plan"], row["active_until"])
