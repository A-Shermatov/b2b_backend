"""Manual subscription plans and reliability marks set by an admin.

A reliability mark belongs to a party: one user with their own profiles and
every company they own. It shows on all of them, buying and selling alike.
"""

from datetime import datetime, timedelta, timezone

from app.core.exceptions import NotFoundError, ValidationError
from app.core.permissions import ensure_permission
from app.modules.admin.services.common import actor_id_of, log_admin
from app.modules.companies.services.common import row_to_public_buyer, row_to_public_supplier
from app.modules.companies.services.read import BUYER_SELECT, SUPPLIER_SELECT
from app.modules.subscription.services.common import PAID_PLANS, is_active_plan
from app.modules.trust.levels import TIER_LABELS_RU, TIER_ORDER
from app.modules.trust.party import party_actor_ids_sql, party_owner_sql

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

    async def _computed_tier(self, conn, actor_id: int, side: str) -> tuple[str, str | None]:
        """(mark from deals and reviews, ignoring the admin's, public name) for one actor."""
        select, shape = (SUPPLIER_SELECT, row_to_public_supplier) if side == "supplier" else (
            BUYER_SELECT,
            row_to_public_buyer,
        )
        row = await conn.fetchrow(select + " AND a.id = $1", actor_id)
        if row is None:
            # Blocked owner or company: not public, the mark is still editable.
            return "none", None
        data = dict(row)
        data["party_override"] = None
        public = shape(data)
        return public.reputation_tier, public.display_name

    async def _party_marks(self, conn, owner_id: int) -> list[dict]:
        """The owner's one mark, with every profile it shows on (empty without profiles)."""
        members = await conn.fetch(
            """
            SELECT a.id, a.side::text AS side, a.display_name,
                   a.reputation_override, a.reputation_override_reason, a.reputation_override_at
            FROM actors a
            LEFT JOIN companies c ON c.id = a.company_id
            WHERE a.is_active = TRUE AND COALESCE(c.owner_id, a.user_id) = $1
            ORDER BY a.company_id NULLS FIRST, a.side DESC, a.id
            """,
            owner_id,
        )
        if not members:
            return []
        profiles = []
        for member in members:
            auto, public_name = await self._computed_tier(conn, member["id"], member["side"])
            profiles.append(
                {
                    "actor_id": member["id"],
                    "side": member["side"],
                    "display_name": public_name or member["display_name"] or f"Профиль #{member['id']}",
                    "tier_auto": auto,
                }
            )
        # The selling profile is the one buyers judge; fall back to the buying one.
        lead = next((p for p in profiles if p["side"] == "supplier"), profiles[0])
        auto = min((p["tier_auto"] for p in profiles), key=TIER_ORDER.index)
        pinned = max(
            (m for m in members if m["reputation_override"]),
            key=lambda m: (m["reputation_override_at"] is not None, m["reputation_override_at"] or 0, -m["id"]),
            default=None,
        )
        override = pinned["reputation_override"] if pinned else None
        return [
            {
                "actor_id": lead["actor_id"],
                "display_name": lead["display_name"],
                "sides": sorted({p["side"] for p in profiles}, reverse=True),
                "profiles": [{k: p[k] for k in ("actor_id", "side", "display_name")} for p in profiles],
                "tier_auto": auto,
                "tier_override": override,
                "tier": override or auto,
                "override_reason": pinned["reputation_override_reason"] if pinned else None,
                "override_at": pinned["reputation_override_at"] if pinned else None,
            }
        ]

    async def get_user_marks(self, user_id: int, current_user) -> dict:
        ensure_permission(current_user, "admin_users.view")
        async with self.connection() as conn:
            if not await conn.fetchval("SELECT 1 FROM users WHERE id = $1", user_id):
                raise NotFoundError("User not found")
            return {
                "user_id": user_id,
                "subscription": await self._subscription_of(conn, user_id),
                "suppliers": await self._party_marks(conn, user_id),
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
                "suppliers": await self._party_marks(conn, owner_id),
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
        """Pin a mark ('none' removes it) or return to the computed one ('auto').

        Applies to the whole party of the actor, so both profiles show it.
        """
        ensure_permission(current_user, "admin_companies.update")
        if tier not in REPUTATION_CHOICES:
            raise ValidationError("Неизвестный уровень знака")
        override = None if tier == "auto" else tier
        async with self.connection() as conn:
            actor = await conn.fetchrow(
                f"SELECT a.id, a.reputation_override, {party_owner_sql('a')} AS owner_id FROM actors a WHERE a.id = $1",
                actor_id,
            )
            if not actor:
                raise NotFoundError("Profile not found")
            await conn.execute(
                f"""
                UPDATE actors
                SET reputation_override = $2::varchar,
                    reputation_override_reason = $3,
                    reputation_override_by = $4,
                    reputation_override_at = CASE WHEN $2::varchar IS NULL THEN NULL ELSE NOW() END,
                    updated_at = NOW()
                WHERE id IN (SELECT a.id FROM actors me, LATERAL (
                    SELECT pa.id FROM actors pa WHERE pa.id IN {party_actor_ids_sql("me")}
                ) a WHERE me.id = $1)
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
            marks = await self._party_marks(conn, actor["owner_id"]) if actor["owner_id"] else []
        if not marks:
            raise NotFoundError("Profile not found")
        return marks[0]
