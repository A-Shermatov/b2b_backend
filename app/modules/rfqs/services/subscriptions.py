"""Notifications about new requests by category.

A supplier's employee ticks the categories they care about ("ИТ и разработка",
"Логистика"...). When a buyer publishes an open request in one of them, or in
one of its subcategories, the employee gets a notification. Subscriptions are
personal: every member of the company chooses for themselves.
"""

import logging

from app.core.exceptions import ValidationError
from app.database import pool
from app.modules.notifications.services import NotificationService
from app.utils.side_roles import side_access_sql

MAX_SUBSCRIPTIONS = 100
side_access = side_access_sql("cu", "'supplier'")
logger = logging.getLogger(__name__)


async def list_subscriptions(user_id: int, supplier_actor_id: int) -> list[int]:
    async with pool.connection() as conn:
        rows = await conn.fetch(
            """
            SELECT category_id FROM rfq_category_subscriptions
            WHERE user_id = $1 AND supplier_actor_id = $2
            ORDER BY category_id
            """,
            user_id,
            supplier_actor_id,
        )
    return [row["category_id"] for row in rows]


async def replace_subscriptions(
    user_id: int, supplier_actor_id: int, category_ids: list[int]
) -> list[int]:
    wanted = sorted({int(cid) for cid in category_ids})
    if len(wanted) > MAX_SUBSCRIPTIONS:
        raise ValidationError(f"Можно выбрать не больше {MAX_SUBSCRIPTIONS} категорий")
    async with pool.connection() as conn:
        async with conn.transaction():
            known = {
                row["id"]
                for row in await conn.fetch(
                    "SELECT id FROM categories WHERE id = ANY($1::int[])", wanted
                )
            }
            unknown = [cid for cid in wanted if cid not in known]
            if unknown:
                raise ValidationError("Такой категории нет: " + ", ".join(map(str, unknown)))
            await conn.execute(
                """
                DELETE FROM rfq_category_subscriptions
                WHERE user_id = $1 AND supplier_actor_id = $2
                  AND NOT (category_id = ANY($3::int[]))
                """,
                user_id,
                supplier_actor_id,
                wanted,
            )
            await conn.execute(
                """
                INSERT INTO rfq_category_subscriptions (user_id, supplier_actor_id, category_id)
                SELECT $1, $2, unnest($3::int[])
                ON CONFLICT DO NOTHING
                """,
                user_id,
                supplier_actor_id,
                wanted,
            )
    return wanted


async def resolve_category(conn, category_id: str | int | None):
    """A request stores its category as an id ("2") or a slug ("web")."""
    key = str(category_id or "").strip()
    if not key:
        return None
    return await conn.fetchrow(
        "SELECT id, parent_id, name FROM categories WHERE id::text = $1 OR slug = $1 LIMIT 1",
        key,
    )


async def subscribers_for_request(
    conn, category_id: str | int | None, buyer_actor_id: int, skip_actor_ids: list[int]
) -> list[tuple[int, int | None]]:
    """(user_id, company_id) of people to notify about an open request in this category.

    The request's own category and its parent both count. People are only
    notified while they are still members of the supplier company, never for
    their own company's requests, and suppliers that were invited personally
    already got their own notification.
    """
    category = await resolve_category(conn, category_id)
    if category is None:
        return []
    wanted = [category["id"]] + ([category["parent_id"]] if category["parent_id"] else [])
    rows = await conn.fetch(
        f"""
        SELECT DISTINCT s.user_id, sa.company_id
        FROM rfq_category_subscriptions s
        JOIN actors sa ON sa.id = s.supplier_actor_id AND sa.is_active
        JOIN actors ba ON ba.id = $2
        WHERE s.category_id = ANY($1::int[])
          AND NOT (s.supplier_actor_id = ANY($3::int[]))
          AND (sa.company_id IS NULL OR ba.company_id IS NULL OR sa.company_id <> ba.company_id)
          AND (sa.user_id IS NULL OR ba.user_id IS NULL OR sa.user_id <> ba.user_id)
          AND (
              sa.kind::text <> 'company'
              OR EXISTS (
                  SELECT 1 FROM company_users cu
                  WHERE cu.company_id = sa.company_id AND cu.user_id = s.user_id
                    AND {side_access}
              )
          )
        """,
        wanted,
        buyer_actor_id,
        [buyer_actor_id, *skip_actor_ids],
    )
    return [(row["user_id"], row["company_id"]) for row in rows]


async def notify_category_subscribers(
    rfq_id: str,
    title: str,
    category_id: str | int | None,
    buyer_actor_id: int,
    skip_actor_ids: list[int] | None = None,
) -> int:
    async with pool.connection() as conn:
        recipients = await subscribers_for_request(
            conn, category_id, buyer_actor_id, list(skip_actor_ids or [])
        )
        category = await resolve_category(conn, category_id)
        category_name = category["name"] if category else None
    service = NotificationService()
    body = f"«{title}»" + (f" в категории «{category_name}»" if category_name else "")
    for user_id, company_id in recipients:
        await service.notify_user(
            user_id,
            "rfq",
            "Новая заявка в вашей категории",
            body,
            f"/supplier/rfqs/{rfq_id}",
            company_id=company_id,
        )
    return len(recipients)


async def notify_subscribers_safely(
    rfq_id: str,
    title: str,
    category_id: str | int | None,
    buyer_actor_id: int,
    skip_actor_ids: list[int] | None = None,
) -> None:
    """The request is already saved: a failed notification must not turn it into an error."""
    try:
        await notify_category_subscribers(
            rfq_id, title, category_id, buyer_actor_id, skip_actor_ids
        )
    except Exception:  # noqa: BLE001 - logged, the request itself succeeded
        logger.exception("Category notifications failed for rfq %s", rfq_id)
