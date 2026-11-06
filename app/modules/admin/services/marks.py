"""Manual subscription plans and reliability marks set by an admin."""

from datetime import datetime, timedelta, timezone

from app.core.exceptions import NotFoundError, ValidationError
from app.core.permissions import ensure_permission
from app.modules.admin.services.common import actor_id_of, log_admin
from app.modules.companies.services.common import row_to_public_supplier
from app.modules.companies.services.read import SUPPLIER_SELECT
from app.modules.subscription.services.common import PAID_PLANS, is_active_plan
from app.modules.trust.levels import TIER_LABELS_RU

PLAN_CHOICES = (*PAID_PLANS, "none")
REPUTATION_CHOICES = ("auto", *TIER_LABELS_RU)


def _clean(reason: str | None) -> str | None:
    return reason.strip() if reason and reason.strip() else None


class AdminMarksMixin:
    async def _subscription_of(self, conn, user_id: int) -> dict:
        row = await conn.fetchrow(
            "SELECT plan::text AS plan, active_until FROM supplier_subscriptions WHERE user_id = $1",
            user_id,
        )
        plan = row["plan"] if row else "none"
        until = row["active_until"] if row else None
        return {"plan": plan, "active_until": until, "is_active": is_active_plan(plan, until)}

    async def _supplier_marks(self, conn, where: str, arg: int) -> list[dict]:
        actors = await conn.fetch(
            f"""
            SELECT a.id, a.reputation_override, a.reputation_override_reason, a.reputation_override_at
            FROM actors a
            LEFT JOIN companies c ON c.id = a.company_id
            WHERE a.side::text = 'supplier' AND a.is_active = TRUE AND {where}
            ORDER BY a.id
            """,
            arg,
        )
        result = []
        for actor in actors:
            row = await conn.fetchrow(SUPPLIER_SELECT + " AND a.id = $1", actor["id"])
            if row is None:
                # Blocked owner or company: not public, the mark is still editable.
                name = await conn.fetchval("SELECT display_name FROM actors WHERE id = $1", actor["id"])
                auto = "none"
            else:
                data = dict(row)
                data["reputation_override"] = None
                auto = row_to_public_supplier(data).reputation_tier
                name = row_to_public_supplier(data).display_name
            override = actor["reputation_override"]
            result.append(
                {
                    "actor_id": actor["id"],
                    "display_name": name or f"Поставщик #{actor['id']}",
                    "tier_auto": auto,
                    "tier_override": override,
                    "tier": override or auto,
                    "override_reason": actor["reputation_override_reason"],
                    "override_at": actor["reputation_override_at"],
                }
            )
        return result

    async def get_user_marks(self, user_id: int, current_user) -> dict:
        ensure_permission(current_user, "admin_users.view")
        async with self.connection() as conn:
            if not await conn.fetchval("SELECT 1 FROM users WHERE id = $1", user_id):
                raise NotFoundError("User not found")
            return {
                "user_id": user_id,
                "subscription": await self._subscription_of(conn, user_id),
                "suppliers": await self._supplier_marks(
                    conn, "(a.user_id = $1 AND a.company_id IS NULL OR c.owner_id = $1)", user_id
                ),
            }

    async def get_company_marks(self, company_id: int, current_user) -> dict:
        ensure_permission(current_user, "admin_companies.view")
        async with self.connection() as conn:
            owner_id = await conn.fetchval("SELECT owner_id FROM companies WHERE id = $1", company_id)
            if owner_id is None:
                raise NotFoundError("Company not found")
            return {
                "user_id": owner_id,
                "subscription": await self._subscription_of(conn, owner_id),
                "suppliers": await self._supplier_marks(conn, "a.company_id = $1", company_id),
            }

    async def set_user_subscription(
        self, user_id: int, plan: str, current_user, *, months: int = 1, reason: str | None = None
    ) -> dict:
        """Grant a paid plan for `months` (from today) or remove it with plan='none'."""
        ensure_permission(current_user, "admin_users.update")
        if plan not in PLAN_CHOICES:
            raise ValidationError("Неизвестный тариф")
        if plan != "none" and not 1 <= months <= 24:
            raise ValidationError("Срок от 1 до 24 месяцев")
        active_until = None if plan == "none" else datetime.now(timezone.utc) + timedelta(days=30 * months)
        async with self.connection() as conn:
            if not await conn.fetchval("SELECT 1 FROM users WHERE id = $1", user_id):
                raise NotFoundError("User not found")
            previous = await self._subscription_of(conn, user_id)
            await conn.execute(
                """
                INSERT INTO supplier_subscriptions (user_id, plan, active_until, created_at)
                VALUES ($1, $2::subscription_plan, $3, NOW())
                ON CONFLICT (user_id) DO UPDATE
                SET plan = EXCLUDED.plan, active_until = EXCLUDED.active_until
                """,
                user_id,
                plan,
                active_until,
            )
            await log_admin(
                conn,
                actor_id_of(current_user),
                "admin.subscription.revoke" if plan == "none" else "admin.subscription.grant",
                "user",
                user_id,
                {
                    "plan": plan,
                    "months": None if plan == "none" else months,
                    "active_until": active_until.isoformat() if active_until else None,
                    "previous_plan": previous["plan"] if previous["is_active"] else "none",
                    "reason": _clean(reason),
                },
            )
            return await self._subscription_of(conn, user_id)

    async def set_supplier_reputation(
        self, actor_id: int, tier: str, current_user, *, reason: str | None = None
    ) -> dict:
        """Pin a reliability mark ('none' removes it) or return to the computed one ('auto')."""
        ensure_permission(current_user, "admin_companies.update")
        if tier not in REPUTATION_CHOICES:
            raise ValidationError("Неизвестный уровень знака")
        override = None if tier == "auto" else tier
        async with self.connection() as conn:
            actor = await conn.fetchrow(
                "SELECT id, side::text AS side, reputation_override FROM actors WHERE id = $1",
                actor_id,
            )
            if not actor or actor["side"] != "supplier":
                raise NotFoundError("Supplier not found")
            await conn.execute(
                """
                UPDATE actors
                SET reputation_override = $2::varchar,
                    reputation_override_reason = $3,
                    reputation_override_by = $4,
                    reputation_override_at = CASE WHEN $2::varchar IS NULL THEN NULL ELSE NOW() END,
                    updated_at = NOW()
                WHERE id = $1
                """,
                actor_id,
                override,
                _clean(reason) if override else None,
                actor_id_of(current_user) if override else None,
            )
            await log_admin(
                conn,
                actor_id_of(current_user),
                "admin.reputation.auto" if override is None else "admin.reputation.set",
                "actor",
                actor_id,
                {"tier": tier, "previous": actor["reputation_override"] or "auto", "reason": _clean(reason)},
            )
            marks = await self._supplier_marks(conn, "a.id = $1", actor_id)
        return marks[0]
