from app.modules.subscription.schemas import SubscriptionResponse
from app.modules.subscription.services.common import is_active_plan, shape_subscription


class SubscriptionReadMixin:
    async def _get_or_create_row(self, user_id: int) -> dict:
        async with self.connection() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, user_id, plan::text AS plan, active_until
                FROM supplier_subscriptions
                WHERE user_id = $1
                """,
                user_id,
            )
            if row:
                return dict(row)
            row = await conn.fetchrow(
                """
                INSERT INTO supplier_subscriptions (user_id, plan, active_until, created_at)
                VALUES ($1, 'none', NULL, NOW())
                RETURNING id, user_id, plan::text AS plan, active_until
                """,
                user_id,
            )
            return dict(row)

    async def get_subscription(self, user_id: int) -> SubscriptionResponse:
        row = await self._get_or_create_row(user_id)
        return shape_subscription(row["plan"], row["active_until"])

    async def get_effective_plan(self, user_id: int) -> str:
        row = await self._get_or_create_row(user_id)
        if is_active_plan(row["plan"], row["active_until"]):
            return row["plan"]
        return "none"
