"""The public page of a private person: what they do, skills, rate, portfolio."""

import json

from app.core.exceptions import UnauthorizedError, ValidationError
from app.modules.auth.schemas import IndividualPage, IndividualPageUpdate
from app.utils.audit import log_event

PAGE_COLUMNS = """
    ip.headline, ip.about, ip.skills, ip.city, ip.website,
    ip.rate_amount, ip.rate_currency, ip.rate_unit, ip.portfolio, ip.accepts_orders
"""


def portfolio_from_db(value) -> list[dict]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return []
    return [item for item in (value or []) if isinstance(item, dict)]


def shape_page(row, supplier_actor_id: int | None) -> IndividualPage:
    data = dict(row) if row else {}
    return IndividualPage(
        headline=data.get("headline"),
        about=data.get("about"),
        skills=list(data.get("skills") or []),
        city=data.get("city"),
        website=data.get("website"),
        rate_amount=float(data["rate_amount"]) if data.get("rate_amount") is not None else None,
        rate_currency=data.get("rate_currency") or "TJS",
        rate_unit=data.get("rate_unit") or "project",
        portfolio=portfolio_from_db(data.get("portfolio")),
        accepts_orders=bool(data.get("accepts_orders")),
        supplier_actor_id=supplier_actor_id,
    )


class AuthPageMixin:
    async def _page(self, conn, user_id: int) -> IndividualPage:
        row = await conn.fetchrow(
            f"SELECT {PAGE_COLUMNS} FROM individual_profiles ip WHERE ip.user_id = $1",
            user_id,
        )
        actor_id = await conn.fetchval(
            """
            SELECT id FROM actors
            WHERE user_id = $1 AND kind::text = 'individual'
              AND side::text = 'supplier' AND is_active = TRUE
            """,
            user_id,
        )
        return shape_page(row, actor_id)

    async def get_page(self, user_id: int) -> IndividualPage:
        async with self.connection() as conn:
            return await self._page(conn, user_id)

    async def update_page(self, user_id: int, data: IndividualPageUpdate) -> IndividualPage:
        async with self.connection() as conn:
            async with conn.transaction():
                role = await conn.fetchval(
                    "SELECT role::text FROM users WHERE id = $1", user_id
                )
                if role is None:
                    raise UnauthorizedError("User not found")
                if data.accepts_orders and role not in ("supplier", "both"):
                    raise ValidationError(
                        "Чтобы принимать заказы как частное лицо, сначала включите профиль поставщика"
                    )
                await conn.execute(
                    """
                    INSERT INTO individual_profiles (
                        user_id, headline, about, skills, city, website,
                        rate_amount, rate_currency, rate_unit, portfolio, accepts_orders
                    )
                    VALUES ($1, $2, $3, $4::text[], $5, $6, $7, $8, $9, $10::jsonb, $11)
                    ON CONFLICT (user_id) DO UPDATE SET
                        headline = EXCLUDED.headline,
                        about = EXCLUDED.about,
                        skills = EXCLUDED.skills,
                        city = EXCLUDED.city,
                        website = EXCLUDED.website,
                        rate_amount = EXCLUDED.rate_amount,
                        rate_currency = EXCLUDED.rate_currency,
                        rate_unit = EXCLUDED.rate_unit,
                        portfolio = EXCLUDED.portfolio,
                        accepts_orders = EXCLUDED.accepts_orders,
                        updated_at = NOW()
                    """,
                    user_id,
                    data.headline,
                    data.about,
                    data.skills,
                    data.city,
                    data.website,
                    data.rate_amount,
                    data.rate_currency,
                    data.rate_unit,
                    # Pictures are stored as keys in our storage, never as links.
                    json.dumps(
                        [
                            {
                                "title": item.title,
                                "description": item.description,
                                "image": item.image,
                                "link": item.link,
                            }
                            for item in data.portfolio
                        ],
                        ensure_ascii=False,
                    ),
                    data.accepts_orders,
                )
                await log_event(
                    conn,
                    "user.page.update",
                    "user",
                    user_id,
                    user_id=user_id,
                    details={
                        "accepts_orders": data.accepts_orders,
                        "skills": len(data.skills),
                        "portfolio": len(data.portfolio),
                    },
                )
                return await self._page(conn, user_id)
