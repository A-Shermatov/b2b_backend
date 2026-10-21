import json
import uuid
from datetime import datetime, timedelta, timezone

from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError, ValidationError
from app.modules.catalog.schemas.offers import CatalogOfferCreate, CatalogOfferResponse
from app.modules.finance.services.commission import ensure_contract_amount_allowed
from app.modules.notifications.services import NotificationService
from app.modules.rfqs.services.common import insert_invites
from app.modules.settings.service import get_platform_settings


def resolve_list_price(pricing) -> tuple[float, str] | None:
    if not pricing:
        return None
    currency = pricing["currency"]
    pricing_type = pricing["pricing_type"]
    if pricing_type == "fixed" and pricing["fixed_price"] is not None:
        return float(pricing["fixed_price"]), currency
    if pricing_type == "hourly" and pricing["hourly_rate"] is not None:
        return float(pricing["hourly_rate"]), currency
    if pricing_type == "monthly" and pricing["monthly_rate"] is not None:
        return float(pricing["monthly_rate"]), currency
    if pricing_type == "tiered":
        tiers = pricing["tiers"]
        if isinstance(tiers, str):
            try:
                tiers = json.loads(tiers)
            except json.JSONDecodeError:
                tiers = []
        if tiers:
            prices = [float(t["price"]) for t in tiers if t.get("price") is not None]
            if prices:
                return min(prices), currency
    return None


class CatalogOfferMixin:
    async def create_offer(
        self,
        item_id: int,
        buyer_actor_id: int,
        created_by: str,
        data: CatalogOfferCreate,
    ) -> CatalogOfferResponse:
        message = (data.message or "").strip()
        async with self.connection() as conn:
            item = await conn.fetchrow(
                """
                SELECT
                    ci.id, ci.actor_id, ci.type::text AS type, ci.category_id,
                    ci.title, ci.description, ci.status::text AS status,
                    cat.slug AS category_slug
                FROM catalog_items ci
                LEFT JOIN categories cat ON cat.id = ci.category_id
                WHERE ci.id = $1 AND ci.status::text != 'deleted'
                """,
                item_id,
            )
            if not item:
                raise NotFoundError("Catalog item not found")
            if item["status"] != "active":
                raise ConflictError("Catalog item is not available")
            if item["actor_id"] == buyer_actor_id:
                raise ForbiddenError("Cannot create an offer on your own catalog item")

            pricing = await conn.fetchrow(
                """
                SELECT pricing_type::text AS pricing_type, currency,
                       fixed_price, hourly_rate, monthly_rate, tiers
                FROM item_pricing WHERE item_id = $1
                """,
                item_id,
            )
            list_price = resolve_list_price(pricing)

            if data.kind == "buy":
                if list_price is None:
                    raise ValidationError("У позиции нет опубликованной цены для покупки")
                price, currency = list_price
                if not message:
                    message = "Запрос на покупку с гарантией по опубликованной цене"
            else:
                if data.price is None or data.price <= 0:
                    raise ValidationError("Укажите цену для торга")
                if not message:
                    raise ValidationError("Укажите текст предложения")
                price = float(data.price)
                currency = pricing["currency"] if pricing else "TJS"

            ensure_contract_amount_allowed(
                price, currency, await get_platform_settings(conn)
            )

            supplier_actor_id = item["actor_id"]
            category_id = item["category_slug"] or str(item["category_id"])
            rfq_id = str(uuid.uuid4())
            deadline = (
                datetime.now(timezone.utc) + timedelta(days=14)
            ).strftime("%Y-%m-%d")
            description = item["description"] or item["title"]
            if data.kind == "negotiate":
                description = f"{description}\n\n—\nПредложение заказчика:\n{message}"

            async with conn.transaction():
                await conn.execute(
                    """
                    INSERT INTO rfqs (
                        id, actor_id, created_by, type, title, description, category_id,
                        budget_type, budget_from, budget_to, currency, deadline,
                        visibility, status,
                        quantity, delivery_country, delivery_city, delivery_address,
                        delivery_date,
                        project_duration, start_date, team_size_required, experience_required,
                        catalog_item_id,
                        created_at, updated_at
                    )
                    VALUES (
                        $1, $2, $3, $4::rfq_type, $5, $6, $7,
                        'fixed'::budget_type, $8, NULL, $9, $10,
                        'invited_only'::rfq_visibility, 'receiving_proposals'::rfq_status,
                        NULL, NULL, NULL, NULL, NULL,
                        NULL, NULL, NULL, NULL,
                        $11,
                        NOW(), NOW()
                    )
                    """,
                    rfq_id,
                    buyer_actor_id,
                    created_by,
                    item["type"],
                    item["title"],
                    description,
                    category_id,
                    price,
                    currency,
                    deadline,
                    item_id,
                )
                await insert_invites(conn, rfq_id, [supplier_actor_id])
                proposal = await conn.fetchrow(
                    """
                    INSERT INTO proposals (
                        rfq_id, supplier_actor_id, price, currency, delivery_time,
                        message, status, created_at
                    )
                    VALUES (
                        $1, $2, $3, $4::currency, NULL, $5,
                        'pending_supplier'::proposal_status, NOW()
                    )
                    RETURNING id
                    """,
                    rfq_id,
                    supplier_actor_id,
                    price,
                    currency,
                    message,
                )

        kind_label = "покупку с гарантией" if data.kind == "buy" else "торг"
        await NotificationService().notify_actor_members(
            supplier_actor_id,
            "rfq",
            f"Запрос на {kind_label}",
            f"Заказчик предлагает {price} {currency} за «{item['title']}»",
            f"/supplier/rfqs/{rfq_id}",
        )
        return CatalogOfferResponse(
            rfq_id=rfq_id,
            proposal_id=proposal["id"],
            kind=data.kind,
            price=price,
            currency=currency,
        )
