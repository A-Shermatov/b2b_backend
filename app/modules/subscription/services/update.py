from app.core.exceptions import ConflictError
from app.modules.subscription.schemas import SubscriptionResponse
from app.modules.subscription.services.common import CATALOG_LIMITS, COMPANY_LIMITS


class SubscriptionUpdateMixin:
    async def activate(self, user_id: int, plan: str, method: str = "alif"):
        """Checkout + activate. Prefer checkout(); kept for backward compatibility."""
        return await self.checkout(user_id, plan, method=method)

    async def cancel(self, user_id: int) -> SubscriptionResponse:
        await self._get_or_create_row(user_id)
        async with self.connection() as conn:
            await conn.execute(
                """
                UPDATE supplier_subscriptions
                SET plan = 'none'::subscription_plan, active_until = NULL
                WHERE user_id = $1 AND side = $2
                """,
                user_id,
                self.side,
            )
        return await self.get_subscription(user_id)

    async def check_company_limit(self, user, *, exclude_company_id: int | None = None) -> None:
        """Supplier companies a user may own on the current plan (COMPANY_LIMITS).

        Counted for every route that creates a supplier company or adds the supplier
        side to an existing one, so the limit cannot be bypassed via the buyer cabinet.
        """
        user_id = user.id if hasattr(user, "id") else int(user)
        plan = await self.get_effective_plan(user_id)
        limit = COMPANY_LIMITS.get(plan, 1)
        if limit is None:
            return
        async with self.connection() as conn:
            count = await conn.fetchval(
                """
                SELECT COUNT(DISTINCT c.id)
                FROM companies c
                JOIN actors a
                  ON a.company_id = c.id
                 AND a.kind::text = 'company'
                 AND a.side::text = 'supplier'
                 AND a.is_active = TRUE
                WHERE c.owner_id = $1
                  AND ($2::bigint IS NULL OR c.id <> $2)
                """,
                user_id,
                exclude_company_id,
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
