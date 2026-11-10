import json
import uuid
from datetime import datetime, timedelta, timezone

from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError, ValidationError
from app.modules.catalog.schemas.offers import CatalogOfferCreate, CatalogOfferResponse
from app.modules.finance.services.commission import ensure_contract_amount_allowed
from app.modules.notifications.services import NotificationService
from app.modules.proposals.services.common import ensure_not_self_dealing
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


def _tiers(pricing) -> list[dict]:
    tiers = pricing["tiers"] if pricing else None
    if isinstance(tiers, str):
        try:
            tiers = json.loads(tiers)
        except json.JSONDecodeError:
            tiers = []
    return [t for t in (tiers or []) if t.get("price") is not None]


def unit_price_for_qty(pricing, qty: int) -> tuple[float, str] | None:
    """The unit price a buyer pays for `qty`: the deepest tier the amount reaches."""
    if pricing and pricing["pricing_type"] == "tiered":
        tiers = sorted(_tiers(pricing), key=lambda t: int(t.get("min_qty") or 1))
        if tiers:
            unit = float(tiers[0]["price"])
            for tier in tiers:
                if qty >= int(tier.get("min_qty") or 1):
                    unit = float(tier["price"])
            return unit, pricing["currency"]
    return resolve_list_price(pricing)


def minimum_order(pricing) -> int:
    if pricing and pricing["pricing_type"] == "tiered":
        tiers = _tiers(pricing)
        if tiers:
            return max(1, min(int(t.get("min_qty") or 1) for t in tiers))
    return 1


class CatalogOfferMixin:
    async def create_offer(
        self,
        item_id: int,
        buyer_actor_id: int,
        created_by: str,
        data: CatalogOfferCreate,
        *,
        skip_approval_gate: bool = False,
        company_role: str | None = None,
        actor_kind: str | None = None,
    ) -> CatalogOfferResponse:
        # An offer with a price ("buy" or "negotiate") becomes a deal as soon as the
        # supplier accepts it, so for a company it is the same commitment as accepting
        # a proposal: director/admin only. Other members buy through the cart and its
        # purchase approval (skip_approval_gate is that fulfil path) or ask for a quote.
        if (
            data.kind in ("buy", "negotiate")
            and not skip_approval_gate
            and actor_kind == "company"
            and company_role not in {"director", "admin"}
        ):
            if data.kind == "buy":
                raise ForbiddenError(
                    "Покупка по каталогу для вашей роли требует согласования — оформите через корзину"
                )
            raise ForbiddenError(
                "Предложить свою цену может руководитель или администратор компании. "
                "Запросите цену у поставщика или оформите покупку через корзину"
            )

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
            await ensure_not_self_dealing(conn, buyer_actor_id, item["actor_id"])

            pricing = await conn.fetchrow(
                """
                SELECT pricing_type::text AS pricing_type, currency,
                       fixed_price, hourly_rate, monthly_rate, tiers
                FROM item_pricing WHERE item_id = $1
                """,
                item_id,
            )
            qty = data.qty
            if qty < minimum_order(pricing):
                raise ValidationError(f"Минимальный заказ — {minimum_order(pricing)} шт.")
            pay_label = (
                "оплата сразу поставщику"
                if data.payment_mode == "direct"
                else "оплата с гарантией площадки"
            )
            price: float | None
            if data.kind == "buy":
                unit = unit_price_for_qty(pricing, qty)
                if unit is None:
                    raise ValidationError("У позиции нет опубликованной цены для покупки")
                unit_price, currency = unit
                price = round(unit_price * qty, 2)
                order_line = f"Количество: {qty}. Цена за единицу: {unit_price:g} {currency}."
                message = "\n".join(
                    part
                    for part in (
                        f"Покупка по опубликованной цене, {pay_label}.",
                        order_line,
                        message,
                    )
                    if part
                )
            elif data.kind == "negotiate":
                if data.price is None or data.price <= 0:
                    raise ValidationError("Укажите цену для торга")
                if not message:
                    raise ValidationError("Укажите текст предложения")
                price = float(data.price)
                currency = pricing["currency"] if pricing else "TJS"
            else:
                price = None
                currency = pricing["currency"] if pricing else "TJS"

            if price is not None:
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
            elif data.kind == "quote":
                request = f"Количество: {qty}. Способ оплаты: {pay_label}."
                if message:
                    request = f"{request}\n{message}"
                description = f"{description}\n\n—\nЗапрос цены от заказчика:\n{request}"
            is_product = item["type"] == "product"

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
                        catalog_item_id, payment_mode,
                        created_at, updated_at
                    )
                    VALUES (
                        $1, $2, $3, $4::rfq_type, $5, $6, $7,
                        $12::budget_type, $8, NULL, $9, $10,
                        'invited_only'::rfq_visibility, 'receiving_proposals'::rfq_status,
                        $14, NULL, NULL, NULL, NULL,
                        NULL, NULL, NULL, NULL,
                        $11, $13,
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
                    "open" if price is None else "fixed",
                    data.payment_mode,
                    qty if is_product else None,
                )
                await insert_invites(conn, rfq_id, [supplier_actor_id])
                proposal = None
                if data.kind != "quote":
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

        if data.kind == "quote":
            title = "Запрос цены"
            body = f"Заказчик просит назвать цену за «{item['title']}», количество: {qty}"
        else:
            title = "Запрос на покупку" if data.kind == "buy" else "Запрос на торг"
            body = f"Заказчик предлагает {price:g} {currency} за «{item['title']}»"
        await NotificationService().notify_actor_members(
            supplier_actor_id,
            "rfq",
            title,
            body,
            f"/supplier/rfqs/{rfq_id}",
        )
        return CatalogOfferResponse(
            rfq_id=rfq_id,
            proposal_id=proposal["id"] if proposal else None,
            kind=data.kind,
            price=price,
            currency=currency,
            qty=qty,
            payment_mode=data.payment_mode,
        )
