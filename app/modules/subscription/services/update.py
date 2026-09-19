from datetime import datetime, timedelta, timezone

from app.core.exceptions import ConflictError
from app.modules.subscription.schemas import SubscriptionResponse
from app.modules.subscription.services.common import CATALOG_LIMITS, COMPANY_LIMITS

THIRTY_DAYS = timedelta(days=30)


class SubscriptionUpdateMixin:
    async def activate(self, user_id: int, plan: str) -> SubscriptionResponse:
        await self._get_or_create_row(user_id)
        active_until = datetime.now(timezone.utc) + THIRTY_DAYS
        async with self.connection() as conn:
            await conn.execute(
                """
                UPDATE supplier_subscriptions
                SET plan = $1::subscription_plan, active_until = $2
                WHERE user_id = $3
                """,
                plan,
                active_until,
                user_id,
            )

        from app.modules.finance.services import SUBSCRIPTION_PLAN_AMOUNTS, FinanceLedgerService

        amount = SUBSCRIPTION_PLAN_AMOUNTS.get(plan, 0.0)
        if amount > 0:
            await FinanceLedgerService().record(
                payment_type="subscription",
                amount=amount,
                currency="TJS",
                title=f"Подписка {plan}",
                description=f"Активация тарифа {plan} на 30 дней",
                status="paid",
                gateway="mock",
                subscription_user_id=user_id,
                metadata={"plan": plan},
            )
        return await self.get_subscription(user_id)

    async def cancel(self, user_id: int) -> SubscriptionResponse:
        await self._get_or_create_row(user_id)
        async with self.connection() as conn:
            await conn.execute(
                """
                UPDATE supplier_subscriptions
                SET plan = 'none'::subscription_plan, active_until = NULL
                WHERE user_id = $1
                """,
                user_id,
            )
        return await self.get_subscription(user_id)

    async def check_company_limit(self, user) -> None:
        user_id = user.id if hasattr(user, "id") else int(user)
        plan = await self.get_effective_plan(user_id)
        limit = COMPANY_LIMITS.get(plan, 1)
        if limit is None:
            return
        async with self.connection() as conn:
            count = await conn.fetchval(
                "SELECT COUNT(*) FROM companies WHERE owner_id = $1",
                user_id,
            )
        if int(count or 0) >= limit:
            raise ConflictError(f"Company limit reached for plan {plan}")

    async def check_catalog_limit(self, user_id: int, actor_id: int) -> None:
        plan = await self.get_effective_plan(user_id)
        limit = CATALOG_LIMITS.get(plan, 10)
        if limit is None:
            return
        async with self.connection() as conn:
            count = await conn.fetchval(
                """
                SELECT COUNT(*)
                FROM catalog_items
                WHERE actor_id = $1 AND status::text = 'active'
                """,
                actor_id,
            )
        if int(count or 0) >= limit:
            raise ConflictError(f"Active catalog item limit reached for plan {plan}")
